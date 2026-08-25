"""Append-only node correction API for v3 evaluation results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from .category_evaluation_contract import (
    DeductionRuleHit,
    DimensionDeductionOutput,
    NodeCorrection,
    NodeCorrectionEvidence,
)
from .database import get_db
from .evaluation_v3_pipeline import V3PipelineError, recompute_qualified_v3
from .models import EvaluationResult
from .schema_adapter import validate_production_correction
from .worker_v3_authoritative import build_v3_authoritative_scoring


CALL_A_FIELDS = {
    "score",
    "grade",
    "title",
    "seotitle",
    "category",
    "style",
    "tags",
    "cons",
    "design",
    "reason",
    "image_defects",
    "trait",
}


class CorrectNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_key: str | None = Field(default=None, max_length=120)
    node_type: str
    node_path: str
    old_value: Any
    new_value: Any
    evidence: list[NodeCorrectionEvidence] = Field(default_factory=list)
    reason: str
    # 结构化归因码，供纠偏分析聚合；自由文本的 reason 无法统计。
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("node_type")
    @classmethod
    def _node_type(cls, value: str) -> str:
        allowed = {
            "call_a_field",
            "precheck_field",
            "redline",
            "track",
            "dimension_rule",
            "aesthetic_score",
            "final_level",
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


def _grade_for_score(score: int) -> str:
    if score >= 81:
        return "L1"
    if score >= 61:
        return "L2"
    if score >= 41:
        return "L3"
    if score >= 21:
        return "L4"
    return "L5"


def _call_a_field(path: str) -> str:
    parts = _path_parts(path, prefix="call_a.")
    if not path.startswith("call_a.") or len(parts) != 1 or parts[0] not in CALL_A_FIELDS:
        raise _coded(
            400,
            "node_path_invalid",
            "调用A字段路径必须是 call_a.<字段名>",
        )
    return parts[0]


def _dimension_definition(
    config: Any, dimension_key: str
) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    for group_name in ("common_group", "specific_group"):
        group = config.get(group_name)
        if not isinstance(group, dict):
            continue
        schema = group.get("schema_definition")
        dimensions = schema.get("dimensions") if isinstance(schema, dict) else None
        if not isinstance(dimensions, list):
            dimensions = group.get("dimensions")
        if not isinstance(dimensions, list):
            continue
        for dimension in dimensions:
            if isinstance(dimension, dict) and dimension.get("key") == dimension_key:
                return dimension
    return None


def _configured_dimension_rule_ids(
    config: Any, *, dimension_key: str, hit_field: str
) -> set[str]:
    dimension = _dimension_definition(config, dimension_key)
    rule_field = "bonus_rules" if hit_field == "hit_bonus_rules" else "deduction_rules"
    raw_rules = dimension.get(rule_field) if isinstance(dimension, dict) else None
    if not isinstance(raw_rules, list):
        return set()
    return {
        str(rule["rule_id"])
        for rule in raw_rules
        if isinstance(rule, dict) and isinstance(rule.get("rule_id"), str)
    }


def _dimension_node(
    output: dict[str, Any], path: str, *, frozen_config: Any = None
) -> tuple[Any, Callable[[Any], None]]:
    parts = _path_parts(path, prefix="dimension.")
    if len(parts) not in {2, 3} or parts[1] not in {
        "hit_rules",
        "hit_bonus_rules",
    }:
        raise _coded(
            400,
            "node_path_invalid",
            "维度路径应为 dimension.<维度key>.hit_rules[.<rule_id>] 或 "
            "dimension.<维度key>.hit_bonus_rules[.<rule_id>]",
        )
    dimension_key = parts[0]
    hit_field = parts[1]
    dimensions = output.get("dimensions")
    if isinstance(dimensions, dict):
        target = dimensions.get(dimension_key)
    else:
        # Results written by the first bridge-v1 deployment used an array.
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
    hits = target.setdefault(hit_field, [])
    if not isinstance(hits, list):
        raise _coded(400, "dimension_rule_invalid", "规则命中必须是数组")
    configured_rule_ids = _configured_dimension_rule_ids(
        frozen_config,
        dimension_key=dimension_key,
        hit_field=hit_field,
    )

    def validate_configured(rule_id: str) -> None:
        if rule_id not in configured_rule_ids:
            polarity = "加分" if hit_field == "hit_bonus_rules" else "扣分"
            raise _coded(
                400,
                "rule_unknown",
                f"维度 {dimension_key} 未配置{polarity}规则 {rule_id}",
            )

    if len(parts) == 2:
        def assign_all(value: Any) -> None:
            try:
                parsed = DimensionDeductionOutput.model_validate(
                    {
                        "dimension_key": dimension_key,
                        hit_field: value,
                    }
                )
            except ValidationError as exc:
                raise _coded(
                    400,
                    "dimension_rule_invalid",
                    "规则命中必须使用 high/medium/low 置信度枚举",
                ) from exc
            parsed_hits = getattr(parsed, hit_field)
            for item in parsed_hits:
                validate_configured(item.rule_id)
            target[hit_field] = [
                item.model_dump(mode="json") for item in parsed_hits
            ]

        return hits, assign_all
    rule_id = parts[2]
    validate_configured(rule_id)
    index = next(
        (i for i, hit in enumerate(hits) if hit.get("rule_id") == rule_id), None
    )
    old = hits[index] if index is not None else None

    def assign(value: Any) -> None:
        if value is None:
            if index is not None:
                hits.pop(index)
            return
        try:
            parsed = DeductionRuleHit.model_validate(value)
        except ValidationError as exc:
            raise _coded(
                400,
                "dimension_rule_invalid",
                "新规则命中必须使用 high/medium/low 置信度枚举",
            ) from exc
        if parsed.rule_id != rule_id:
            raise _coded(400, "dimension_rule_invalid", "新规则命中必须是同 rule_id 的对象")
        value = parsed.model_dump(mode="json")
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


def apply_node_correction(
    db: Session,
    *,
    result: EvaluationResult,
    payload: CorrectNodeRequest,
    corrector: str,
    corrector_confidence: float | None = None,
    corrector_policy: str | None = None,
) -> dict[str, Any]:
    """Apply one correction through the same append-only v3 replay path.

    The caller owns the surrounding transaction.  Both the authenticated API
    and the automatic corrector use this function so automatic changes remain
    visible in the existing node-correction editor and can be overridden by a
    later human event.
    """

    scoring = _json_value(result.scoring_json, {})
    if not isinstance(scoring, dict):
        scoring = {}
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
    if not isinstance(precheck, dict):
        precheck = {}

    if payload.node_type == "call_a_field":
        field = _call_a_field(payload.node_path)
        production_fields = precheck.get("production_fields")
        if not isinstance(production_fields, dict):
            production_fields = {}

        if field == "score":
            old_value = result.score
        elif field == "grade":
            old_value = result.level
        else:
            if field not in production_fields:
                raise _coded(
                    409,
                    "call_a_field_missing",
                    f"旧评测未存储调用A字段 {field}，请重跑后再纠偏",
                )
            old_value = production_fields[field]

        if old_value != payload.old_value:
            raise _coded(409, "node_value_conflict", "节点当前值已变化，请刷新后重试")

        downstream_recomputed = False
        if field == "score":
            try:
                validate_production_correction(
                    "production_fields.score", payload.new_value
                )
            except ValueError as exc:
                raise _coded(400, "call_a_field_invalid", str(exc)) from exc
            new_score = int(payload.new_value)
            new_grade = _grade_for_score(new_score)
            result.score = new_score
            result.level = new_grade
            result.needs_review = False
            scoring["score"] = new_score
            scoring["level"] = new_grade
            scoring["manual_call_a_score"] = new_score
            scoring.pop("manual_call_a_grade", None)
            result.scoring_json = json.dumps(scoring, ensure_ascii=False)
            if "score" in production_fields:
                production_fields["score"] = new_score
            downstream_recomputed = True
        elif field == "grade":
            if payload.new_value not in {"L1", "L2", "L3", "L4", "L5"}:
                raise _coded(400, "level_invalid", "新等级必须是 L1-L5")
            result.level = payload.new_value
            result.needs_review = False
            scoring["level"] = payload.new_value
            scoring["manual_call_a_grade"] = payload.new_value
            result.scoring_json = json.dumps(scoring, ensure_ascii=False)
        else:
            try:
                validate_production_correction(
                    f"production_fields.{field}", payload.new_value
                )
            except ValueError as exc:
                raise _coded(400, "call_a_field_invalid", str(exc)) from exc
            production_fields[field] = payload.new_value

        if production_fields:
            precheck["production_fields"] = production_fields
            result.precheck_json = json.dumps(precheck, ensure_ascii=False)

        correction = NodeCorrection(
            correction_key=payload.correction_key,
            node_type=payload.node_type,
            node_path=payload.node_path,
            old_value=old_value,
            new_value=payload.new_value,
            evidence=payload.evidence,
            reason=payload.reason,
            reason_codes=payload.reason_codes,
            corrector=corrector,
            corrector_confidence=corrector_confidence,
            corrector_policy=corrector_policy,
            corrected_at=datetime.now(timezone.utc),
            downstream_recomputed=downstream_recomputed,
        ).model_dump(mode="json")
        history.append(correction)
        result.correction_history_json = json.dumps(history, ensure_ascii=False)
        db.flush()
        return _response(result, correction)

    context = scoring.get("v3_context")
    if not isinstance(context, dict):
        raise _coded(409, "not_v3_rule_result", "该结果不含可重放的 v3 冻结上下文")
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
        dimensions_by_track = context.get("subcategory_dimensions")
        frozen_config = (
            dimensions_by_track.get(scoring.get("track_key"))
            if isinstance(dimensions_by_track, dict)
            else None
        )
        old_value, assign = _dimension_node(
            dimension_output,
            payload.node_path,
            frozen_config=frozen_config,
        )
    elif payload.node_type == "aesthetic_score":
        if not isinstance(dimension_output, dict):
            raise _coded(
                409, "dimension_output_missing", "结果缺少调用B输出，无法纠偏美感分"
            )
        if payload.node_path not in {
            "aesthetic.aesthetic_score",
            "aesthetic_score",
            "call_b.aesthetic_score",
        }:
            raise _coded(
                400, "node_path_invalid", "美感分路径必须是 aesthetic.aesthetic_score"
            )
        old_value = dimension_output.get("aesthetic_score")
        if (
            isinstance(payload.new_value, bool)
            or not isinstance(payload.new_value, int)
            or not 0 <= payload.new_value <= 100
        ):
            raise _coded(400, "aesthetic_score_invalid", "美感分必须是 0-100 的整数")

        def _assign_aesthetic_score(value: Any) -> None:
            dimension_output["aesthetic_score"] = value
            # 美感基座校验要求非空可见证据，否则重放会 fail-closed 直接失败。
            # 人工填写的纠偏理由就是这个新分数的证据，同时保留模型原有证据供对照。
            existing = dimension_output.get("aesthetic_evidence")
            texts = [
                item.strip()
                for item in (existing if isinstance(existing, list) else [])
                if isinstance(item, str) and item.strip()
            ]
            human_note = payload.reason.strip()
            if human_note and human_note not in texts:
                texts.insert(0, human_note)
            dimension_output["aesthetic_evidence"] = texts or [
                f"人工判定美感分为 {value}"
            ]
            # 人工真值不再沿用模型置信度，否则模型的低置信会被误读成人工不确定。
            dimension_output["aesthetic_confidence"] = 1.0
            dimension_output["manual_aesthetic_score"] = True

        assign = _assign_aesthetic_score
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
        raise _coded(409, "node_value_conflict", "节点当前值已变化，请刷新后重试")
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
        scoring["dimension_selection"] = previous_scoring.get("dimension_selection")
        manual_score = previous_scoring.get("manual_call_a_score")
        if (
            isinstance(manual_score, int)
            and not isinstance(manual_score, bool)
            and 0 <= manual_score <= 100
        ):
            scoring["manual_call_a_score"] = manual_score
            scoring["score"] = manual_score
            scoring["level"] = _grade_for_score(manual_score)
        manual_grade = previous_scoring.get("manual_call_a_grade")
        if manual_grade in {"L1", "L2", "L3", "L4", "L5"}:
            scoring["manual_call_a_grade"] = manual_grade
            scoring["level"] = manual_grade
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
        scoring["manual_call_a_grade"] = payload.new_value
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
        reason_codes=payload.reason_codes,
        corrector=corrector,
        corrector_confidence=corrector_confidence,
        corrector_policy=corrector_policy,
        corrected_at=datetime.now(timezone.utc),
        downstream_recomputed=downstream_recomputed,
    ).model_dump(mode="json")
    history.append(correction)
    result.correction_history_json = json.dumps(history, ensure_ascii=False)
    db.flush()
    return _response(result, correction)


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
        response = apply_node_correction(
            db,
            result=result,
            payload=payload,
            corrector=str(
                getattr(user, "display_name", None)
                or getattr(user, "username", "unknown")
            ),
        )
        db.commit()
        db.refresh(result)
        return response

    return router
