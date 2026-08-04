"""Append-only node correction API for v3 evaluation results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from .category_evaluation_contract import (
    NodeCorrection,
    NodeCorrectionEvidence,
)
from .database import get_db
from .evaluation_v3_pipeline import V3PipelineError, recompute_qualified_v3
from .models import EvaluationResult
from .worker_v3_authoritative import build_v3_authoritative_scoring


class CorrectNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_key: str | None = Field(default=None, max_length=120)
    node_type: str
    node_path: str
    old_value: Any
    new_value: Any
    evidence: list[NodeCorrectionEvidence] = Field(default_factory=list)
    reason: str

    @field_validator("node_type")
    @classmethod
    def _node_type(cls, value: str) -> str:
        allowed = {
            "precheck_field", "redline", "track", "dimension_rule", "final_level"
        }
        if value not in allowed:
            raise ValueError(f"node_type 必须是 {sorted(allowed)} 之一")
        return value

    @field_validator("node_path", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


def _coded(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _json_value(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _path_parts(path: str, *, prefix: str) -> list[str]:
    raw = path[len(prefix) :] if path.startswith(prefix) else path
    parts = [part for part in raw.split(".") if part]
    if not parts or any(not part.replace("_", "").isalnum() for part in parts):
        raise _coded(400, "node_path_invalid", f"非法节点路径：{path}")
    return parts


def _get_dict_path(root: dict[str, Any], parts: list[str]) -> Any:
    current: Any = root
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise _coded(400, "node_path_not_found", f"节点路径不存在：{'.'.join(parts)}")
        current = current[part]
    return current


def _set_dict_path(root: dict[str, Any], parts: list[str], value: Any) -> None:
    current: Any = root
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise _coded(400, "node_path_not_found", f"节点路径不存在：{'.'.join(parts)}")
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise _coded(400, "node_path_not_found", f"节点路径不存在：{'.'.join(parts)}")
    current[parts[-1]] = value


def _dimension_node(
    output: dict[str, Any], path: str
) -> tuple[Any, Callable[[Any], None]]:
    parts = _path_parts(path, prefix="dimension.")
    if len(parts) not in {2, 3} or parts[1] != "hit_rules":
        raise _coded(
            400,
            "node_path_invalid",
            "维度路径应为 dimension.<维度key>.hit_rules[.<rule_id>]",
        )
    dimension_key = parts[0]
    dimensions = output.get("dimensions")
    target = next(
        (
            item
            for item in dimensions or []
            if isinstance(item, dict) and item.get("dimension_key") == dimension_key
        ),
        None,
    )
    if target is None:
        raise _coded(400, "dimension_not_found", f"未找到维度 {dimension_key}")
    hits = target.setdefault("hit_rules", [])
    if len(parts) == 2:
        return hits, lambda value: target.__setitem__("hit_rules", value)
    rule_id = parts[2]
    index = next(
        (i for i, hit in enumerate(hits) if hit.get("rule_id") == rule_id), None
    )
    old = hits[index] if index is not None else None

    def assign(value: Any) -> None:
        if value is None:
            if index is not None:
                hits.pop(index)
            return
        if not isinstance(value, dict) or value.get("rule_id") != rule_id:
            raise _coded(400, "dimension_rule_invalid", "新规则命中必须是同 rule_id 的对象")
        if index is None:
            hits.append(value)
        else:
            hits[index] = value

    return old, assign


def _response(result: EvaluationResult, correction: dict[str, Any]) -> dict[str, Any]:
    return {
        "evaluation_result_id": result.id,
        "score": result.score,
        "level": result.level,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "precheck": _json_value(result.precheck_json, {}),
        "aesthetic": _json_value(result.aesthetic_json, None),
        "scoring": _json_value(result.scoring_json, {}),
        "correction": correction,
        "correction_history": _json_value(result.correction_history_json, []),
    }


def build_node_correction_router(require_reviewer: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["evaluation-node-correction"])

    @router.post("/api/evaluation-results/{evaluation_id}/correct-node")
    def correct_node(
        evaluation_id: int,
        payload: CorrectNodeRequest,
        user: Any = Depends(require_reviewer),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        result = db.get(EvaluationResult, evaluation_id)
        if result is None:
            raise _coded(404, "evaluation_result_not_found", "评测结果不存在")

        scoring = _json_value(result.scoring_json, {})
        context = scoring.get("v3_context") if isinstance(scoring, dict) else None
        if not isinstance(context, dict):
            raise _coded(409, "not_v3_rule_result", "该结果不含可重放的 v3 冻结上下文")
        history = _json_value(result.correction_history_json, [])
        if payload.correction_key:
            previous = next(
                (
                    item
                    for item in history
                    if item.get("correction_key") == payload.correction_key
                ),
                None,
            )
            if previous is not None:
                return _response(result, previous)

        precheck = _json_value(result.precheck_json, {})
        dimension_output = _json_value(result.aesthetic_json, {})
        previous_scoring = dict(scoring)
        track_override: str | None = None
        downstream_recomputed = payload.node_type != "final_level"

        if payload.node_type in {"precheck_field", "redline"}:
            prefix = "redline." if payload.node_path.startswith("redline.") else "precheck."
            parts = _path_parts(payload.node_path, prefix=prefix)
            old_value = _get_dict_path(precheck, parts)
            assign: Callable[[Any], None] = lambda value: _set_dict_path(
                precheck, parts, value
            )
        elif payload.node_type == "dimension_rule":
            if not isinstance(dimension_output, dict):
                raise _coded(409, "dimension_output_missing", "结果缺少维度规则命中输出")
            old_value, assign = _dimension_node(dimension_output, payload.node_path)
        elif payload.node_type == "track":
            old_value = scoring.get("track_key")
            if payload.node_path not in {"track", "track_key", "scoring.track_key"}:
                raise _coded(400, "node_path_invalid", "赛道节点路径必须是 track_key")
            if not isinstance(payload.new_value, str):
                raise _coded(400, "track_invalid", "新赛道必须是字符串")
            track_override = payload.new_value
            assign = lambda _value: None
        else:
            old_value = result.level
            if payload.node_path not in {"final_level", "level", "scoring.level"}:
                raise _coded(400, "node_path_invalid", "最终等级路径必须是 final_level")
            if payload.new_value not in {"L1", "L2", "L3", "L4", "L5"}:
                raise _coded(400, "level_invalid", "新等级必须是 L1-L5")
            assign = lambda _value: None

        if old_value != payload.old_value:
            raise _coded(
                409,
                "node_value_conflict",
                "节点当前值已变化，请刷新后重试",
            )
        assign(payload.new_value)

        if downstream_recomputed:
            try:
                replayed = recompute_qualified_v3(
                    v3_context=context,
                    precheck=precheck,
                    dimension_output=dimension_output,
                    track_key=track_override,
                )
            except (KeyError, TypeError, ValueError, V3PipelineError) as exc:
                raise _coded(
                    400,
                    getattr(exc, "code", "node_recompute_failed"),
                    f"节点纠偏无法重算：{exc}",
                ) from exc
            scoring = build_v3_authoritative_scoring(replayed, precheck=precheck)
            scoring.pop("_dimension_deduction_raw_payload", None)
            scoring["v3_context"] = context
            scoring["v3_config_revision"] = context.get("config_revision")
            scoring["dimension_mode"] = previous_scoring.get("dimension_mode", "all")
            scoring["dimension_selection"] = previous_scoring.get(
                "dimension_selection"
            )
            if isinstance(replayed.get("dimension_deduction_output"), dict):
                dimension_output = replayed["dimension_deduction_output"]
            result.precheck_json = json.dumps(precheck, ensure_ascii=False)
            result.aesthetic_json = json.dumps(dimension_output, ensure_ascii=False)
            result.scoring_json = json.dumps(scoring, ensure_ascii=False)
            result.score = scoring.get("score")
            result.level = scoring.get("level")
            result.confidence = scoring.get("confidence")
            result.needs_review = bool(scoring.get("needs_review"))
        else:
            scoring["level"] = payload.new_value
            scoring["manual_final_level"] = payload.new_value
            result.scoring_json = json.dumps(scoring, ensure_ascii=False)
            result.level = payload.new_value
            result.needs_review = False

        correction = NodeCorrection(
            correction_key=payload.correction_key,
            node_type=payload.node_type,
            node_path=payload.node_path,
            old_value=old_value,
            new_value=payload.new_value,
            evidence=payload.evidence,
            reason=payload.reason,
            corrector=str(getattr(user, "display_name", None) or getattr(user, "username", "unknown")),
            corrected_at=datetime.now(timezone.utc),
            downstream_recomputed=downstream_recomputed,
        ).model_dump(mode="json")
        history.append(correction)
        result.correction_history_json = json.dumps(history, ensure_ascii=False)
        db.commit()
        db.refresh(result)
        return _response(result, correction)

    return router
