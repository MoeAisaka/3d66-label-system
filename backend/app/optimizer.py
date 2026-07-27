from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .doubao import DoubaoClient
from .models import OptimizerConfig, PromptOptimizationRun
from .regression import latest_review_for_result


DIMENSION_LABELS = {
    "composition_viewpoint": "构图与机位",
    "lighting_atmosphere": "光影与氛围",
    "color_material": "色彩与材质",
    "spatial_design_furnishing": "空间设计与家具软装",
    "visual_hierarchy": "视觉层级",
    "detail_completion": "细节完成度",
    "inspiration_reference": "灵感与参考价值",
    "presentation_integrity": "画面呈现完整性",
}


def _review_record(item: Any) -> dict[str, Any] | None:
    result = item.source_result
    review = latest_review_for_result(result)
    if not review or review.decision not in {"approved", "corrected"}:
        return None
    aesthetic = json.loads(result.aesthetic_json) if result.aesthetic_json else {}
    corrections = json.loads(review.corrections_json or "[]")
    return {
        "asset_id": item.asset_id,
        "asset_name": item.asset.original_name,
        "source_evaluation_id": result.id,
        "source_review_id": review.id,
        "decision": review.decision,
        "model_level": result.level,
        "human_level": review.corrected_level or result.level,
        "model_dimensions": (aesthetic or {}).get("dimensions", {}),
        "human_corrections": corrections,
        "human_note": review.note,
        "model_id": result.model_id,
        "prompt_version": result.prompt_b_version,
    }


def _select_records(items: list[Any]) -> tuple[list[tuple[Any, dict[str, Any]]], list[int], int]:
    eligible: list[tuple[Any, dict[str, Any]]] = []
    for item in items:
        record = _review_record(item)
        if record:
            eligible.append((item, record))
    eligible.sort(key=lambda pair: pair[0].asset_id)

    holdout_ids: list[int] = []
    analysis_pool: list[tuple[Any, dict[str, Any]]] = []
    for index, pair in enumerate(eligible):
        if len(eligible) >= 10 and index % 5 == 4:
            holdout_ids.append(pair[0].asset_id)
        else:
            analysis_pool.append(pair)

    corrected = [pair for pair in analysis_pool if pair[1]["decision"] == "corrected"]
    controls = [pair for pair in analysis_pool if pair[1]["decision"] == "approved"]
    selected = corrected[:24] + controls[:8]
    for _, record in selected:
        record["sample_role"] = (
            "target_error"
            if record["decision"] == "corrected"
            else "stable_control"
        )
    return selected, holdout_ids, len(eligible)


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


async def run_prompt_optimization(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(PromptOptimizationRun, run_id)
        config = db.scalar(select(OptimizerConfig).limit(1))
        if not run or not config:
            return
        run.status = "running"
        run.progress = 3
        db.commit()

        prompt = run.base_prompt
        sample_set = run.sample_set
        selected, holdout_ids, eligible_count = _select_records(list(sample_set.items))
        corrected_count = sum(1 for _, record in selected if record["decision"] == "corrected")
        if not selected:
            raise ValueError("样本集中没有已完成人工确认的图片")
        if corrected_count == 0:
            raise ValueError("至少需要一张带有人工纠错的图片，只有确认正确的样本无法定位提示词问题")

        run.sample_count = eligible_count
        run.corrected_count = corrected_count
        db.commit()

        client = DoubaoClient(config)
        diagnostic_system = (
            "你是空间与建筑图片美感评测提示词的错误分析专家。"
            "你只负责根据模型结果与人工结果的差异定位问题，不生成最终提示词。"
            "人工纠错是参考标准；已确认正确的样本是防退化对照。"
            "区分视觉识别问题、提示词规则问题和外部评分引擎问题。"
            "不得为单张图片编写特殊规则，只有重复规律才能进入修改建议。"
            "只输出合法JSON，字段为 summary、cases、patterns、prompt_risks。"
        )
        chunk_results: list[dict[str, Any]] = []
        settings = get_settings()
        groups = _chunks(selected, 6)
        for index, group in enumerate(groups):
            samples = []
            for item, record in group:
                sample_text = json.dumps(
                    {
                        "task": "诊断这张图片的模型判断与人工纠错差异",
                        "dimension_names": DIMENSION_LABELS,
                        "sample": record,
                    },
                    ensure_ascii=False,
                )
                samples.append(
                    (
                        sample_text,
                        settings.upload_dir / item.asset.stored_name,
                        item.asset.mime_type,
                    )
                )
            response = await client.chat_json_images(diagnostic_system, samples)
            chunk_results.append(response.parsed)
            run.progress = min(68, 8 + int(((index + 1) / len(groups)) * 60))
            db.commit()

        synthesis_system = (
            "你是3D66美感评测提示词优化专家。根据人工校验样本的分组诊断，"
            "对当前提示词做最小范围、可回归验证的修改。"
            "锁定并保留原有JSON输出结构、字段名、业务分类、安全边界和调用变量。"
            "优先修改维度定义、等级边界、常见误判规则和正反例。"
            "证据不足时保持原文；不得针对单张图片增加特例。"
            "输出合法JSON，必须包含 summary、diagnosis、prompt_changes、"
            "candidate_system_prompt、candidate_user_prompt、change_note、validation_focus。"
        )
        synthesis_input = json.dumps(
            {
                "base_prompt": {
                    "stage": prompt.stage,
                    "version": prompt.version,
                    "system_prompt": prompt.system_prompt,
                    "user_prompt": prompt.user_prompt,
                },
                "sample_policy": {
                    "eligible_count": eligible_count,
                    "analysis_count": len(selected),
                    "corrected_count": corrected_count,
                    "control_count": len(selected) - corrected_count,
                    "blind_holdout_count": len(holdout_ids),
                    "blind_holdout_asset_ids": holdout_ids,
                    "regression_roles": [
                        "target_error",
                        "stable_control",
                        "blind_holdout",
                    ],
                    "note": "盲测样本没有发送给提示词生成模型，后续用于豆包回测。",
                },
                "batch_diagnoses": chunk_results,
            },
            ensure_ascii=False,
        )
        run.progress = 76
        db.commit()
        response = await client.chat_json(synthesis_system, synthesis_input)
        result = response.parsed
        candidate_system = str(result.get("candidate_system_prompt") or "").strip()
        candidate_user = str(result.get("candidate_user_prompt") or "").strip()
        if not candidate_system or not candidate_user:
            raise ValueError("诊断模型没有生成完整的候选 System Prompt 和 User Prompt")

        run.diagnosis_json = json.dumps(
            {
                **result,
                "sample_policy": {
                    "eligible_count": eligible_count,
                    "analysis_count": len(selected),
                    "corrected_count": corrected_count,
                    "control_count": len(selected) - corrected_count,
                    "blind_holdout_count": len(holdout_ids),
                    "blind_holdout_asset_ids": holdout_ids,
                    "regression_roles": [
                        "target_error",
                        "stable_control",
                        "blind_holdout",
                    ],
                },
            },
            ensure_ascii=False,
        )
        run.candidate_system_prompt = candidate_system
        run.candidate_user_prompt = candidate_user
        run.change_note = str(result.get("change_note") or "SOL 根据人工校验样本生成的候选提示词")
        run.status = "completed"
        run.progress = 100
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(PromptOptimizationRun, run_id)
        if run:
            run.status = "failed"
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
