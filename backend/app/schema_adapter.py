from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .category_pipeline import (
    dimension_selection_from_job_snapshot,
    project_dimension_definition,
)
from .dimension_schema_registry import SPACE_INPUT_DIMENSION_ALIASES
from .models import EvaluationResult
from .scoring import (
    ENGINE_VERSION,
    calculate_prompt_only_result,
    calculate_score,
    dimension_schema_from_strategy_snapshot,
    normalize_dimension_aliases,
)


QUALITY_RANK = {
    "normal": 0,
    "slight": 1,
    "moderate": 2,
    "severe": 3,
    "unusable": 4,
    "uncertain": 1,
}
PRODUCTION_REASON_VALUES = {
    "是截图",
    "有大面积文字说明",
    "是多拼图",
    "有二维码",
    "是随手拍",
    "是颠倒图",
}
PRODUCTION_TRAIT_VALUES = {"AI图", "实景照片", "3D数字效果图", "其它"}
QUALITY_SEVERITY_VALUES = {
    "normal",
    "slight",
    "moderate",
    "severe",
    "unusable",
    "uncertain",
}
MEDIA_STATUS_VALUES = {"yes", "no", "uncertain"}
PRODUCTION_FIELD_KEYS = (
    "title",
    "seotitle",
    "category",
    "style",
    "tags",
    "cons",
    "design",
    "score",
    "reason",
    "image_defects",
    "trait",
)

_INSPIRATION_REDLINE_REASON_MAP = {
    "screenshot": "是截图",
    "casual_photo": "是随手拍",
    "text_heavy": "有大面积文字说明",
    "qr_code_heavy": "有二维码",
}
_INSPIRATION_MEDIA_TRAIT_MAP = {
    "real_photo": "实景照片",
    "3d_render": "3D数字效果图",
    "ai_generated": "AI图",
    "other": "其它",
}
_INSPIRATION_HARD_DEFECT_VALUES = {
    "blurry_grayish",
    "careless_composition",
    "garish_color",
    "large_dead_black",
    "distorted_viewpoint",
    "fake_material",
    "fisheye_distortion",
    "invalid_black_border",
    "severe_color_cast",
    "known_real_photo_defect",
}
INSPIRATION_HARD_DEFECT_VALUES = frozenset(_INSPIRATION_HARD_DEFECT_VALUES)
_INSPIRATION_IMAGE_DEFECT_VALUES = {
    "corner_small_watermark",
    "subject_obscuring_watermark",
    "large_area_watermark",
}
_INSPIRATION_DECISIVE_FIELDS = (
    "redline_triggered",
    "reason",
    "hard_defects",
    "image_defects",
    "decisive_evidence",
    "decision_status",
    "uncertain_fields",
)


def adapt_inspiration_call_a_precheck(precheck: dict[str, Any]) -> dict[str, Any]:
    """Normalize inspiration Call-A facts and audit every decisive signal."""
    if not isinstance(precheck, dict) or isinstance(precheck.get("classification"), dict):
        return precheck
    redlines = precheck.get("redline_triggered")
    if not isinstance(redlines, dict) or "track_classification" not in precheck:
        return precheck

    review_reasons = [
        f"missing:{field}"
        for field in _INSPIRATION_DECISIVE_FIELDS
        if field not in precheck
    ]
    derived_reasons = [
        reason
        for key, reason in _INSPIRATION_REDLINE_REASON_MAP.items()
        if redlines.get(key) is True
    ]
    native_reasons = precheck.get("reason")
    if not isinstance(native_reasons, list) or any(
        item not in PRODUCTION_REASON_VALUES for item in native_reasons
    ):
        if "reason" in precheck:
            review_reasons.append("invalid:reason")
        reasons = derived_reasons
    else:
        reasons = list(native_reasons)

    for key, reason in _INSPIRATION_REDLINE_REASON_MAP.items():
        flag = redlines.get(key)
        if not isinstance(flag, bool):
            review_reasons.append(f"invalid:redline_triggered:{key}")
            continue
        if flag != (reason in reasons):
            review_reasons.append(f"redline_reason_conflict:{key}")

    hard_defects = precheck.get("hard_defects")
    if not isinstance(hard_defects, list) or any(
        not isinstance(item, str) or item not in _INSPIRATION_HARD_DEFECT_VALUES
        for item in hard_defects
    ):
        if "hard_defects" in precheck:
            review_reasons.append("invalid:hard_defects")
        hard_hits: set[str] = set()
    else:
        hard_hits = set(hard_defects)
        if hard_hits == {"known_real_photo_defect"}:
            review_reasons.append(
                "modifier_without_defect:known_real_photo_defect"
            )

    image_defects = precheck.get("image_defects")
    if not isinstance(image_defects, list) or any(
        not isinstance(item, str) or item not in _INSPIRATION_IMAGE_DEFECT_VALUES
        for item in image_defects
    ):
        if "image_defects" in precheck:
            review_reasons.append("invalid:image_defects")
        image_hits: set[str] = set()
    else:
        image_hits = set(image_defects)

    evidence = precheck.get("decisive_evidence")
    redline_evidence: dict[str, Any] = {}
    evidenced_hits = {"hard_defects": set(), "image_defects": set()}
    if isinstance(evidence, dict):
        raw_redline_evidence = evidence.get("redline_triggered")
        if isinstance(raw_redline_evidence, dict):
            redline_evidence = raw_redline_evidence
        else:
            review_reasons.append("invalid:evidence:redline_triggered")
        for source in ("hard_defects", "image_defects"):
            entries = evidence.get(source)
            if not isinstance(entries, list):
                review_reasons.append(f"invalid:evidence:{source}")
                continue
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("key"), str)
                    or not isinstance(entry.get("evidence"), str)
                    or not entry["evidence"].strip()
                ):
                    review_reasons.append(f"invalid:evidence:{source}")
                    continue
                evidenced_hits[source].add(entry["key"])
    elif "decisive_evidence" in precheck:
        review_reasons.append("invalid:decisive_evidence")

    for key in _INSPIRATION_REDLINE_REASON_MAP:
        entries = redline_evidence.get(key)
        if not isinstance(entries, list) or any(
            not isinstance(item, str) or not item.strip() for item in entries
        ):
            review_reasons.append(f"invalid:evidence:redline:{key}")
            continue
        if redlines.get(key) is True and not entries:
            review_reasons.append(f"missing_evidence:redline:{key}")
        if redlines.get(key) is False and entries:
            review_reasons.append(f"evidence_conflict:redline:{key}")

    for source, hits in (("hard_defects", hard_hits), ("image_defects", image_hits)):
        for key in sorted(hits - evidenced_hits[source]):
            review_reasons.append(f"missing_evidence:{source}:{key}")
        for key in sorted(evidenced_hits[source] - hits):
            review_reasons.append(f"evidence_without_hit:{source}:{key}")

    if precheck.get("decision_status") != "complete":
        if "decision_status" in precheck:
            review_reasons.append("decision_status:not_complete")
    uncertain_fields = precheck.get("uncertain_fields")
    if not isinstance(uncertain_fields, list) or any(
        not isinstance(item, str) or not item.strip() for item in uncertain_fields
    ):
        if "uncertain_fields" in precheck:
            review_reasons.append("invalid:uncertain_fields")
    elif uncertain_fields:
        review_reasons.extend(f"uncertain:{item}" for item in uncertain_fields)

    trait = precheck.get("trait")
    if trait not in PRODUCTION_TRAIT_VALUES:
        trait = _INSPIRATION_MEDIA_TRAIT_MAP.get(str(precheck.get("media_type")), "其它")
    confidence = precheck.get("classification_confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = precheck.get("track_confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = 0.0

    normalized = deepcopy(precheck)
    normalized["classification"] = {
        "scope_status": "in_scope",
        "primary_category": precheck.get("primary_category", "其它"),
        "primary_confidence": max(0.0, min(1.0, float(confidence))),
    }
    production = normalized.get("production_fields")
    if not isinstance(production, dict):
        production = {}
    production.update({"reason": reasons, "trait": trait})
    if isinstance(image_defects, list):
        production["image_defects"] = "有水印" if image_defects else ""
    normalized["production_fields"] = production
    existing_review_reasons = precheck.get("review_reasons")
    if not isinstance(existing_review_reasons, list):
        existing_review_reasons = []
    normalized["review_reasons"] = list(
        dict.fromkeys(
            [
                *(
                    item
                    for item in existing_review_reasons
                    if isinstance(item, str) and item
                ),
                *review_reasons,
            ]
        )
    )
    normalized["needs_review"] = bool(precheck.get("needs_review") is True or normalized["review_reasons"])
    normalized["decisive_signal_validation"] = {
        "status": "needs_review" if normalized["needs_review"] else "valid",
        "reasons": list(normalized["review_reasons"]),
    }
    return normalized


def _required_text(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ValueError(f"{field} 长度不能超过 {limit} 个字符")
    return normalized


def _validate_media_form(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError("media_form 必须是非空对象")
    for key, item in value.items():
        if not isinstance(item, dict):
            raise ValueError(f"media_form.{key} 必须是对象")
        if item.get("status") not in MEDIA_STATUS_VALUES:
            raise ValueError(f"media_form.{key}.status 包含未允许的枚举值")
        confidence = item.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError(f"media_form.{key}.confidence 必须在 0 至 1 之间")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in evidence
        ):
            raise ValueError(f"media_form.{key}.evidence 必须是可见证据字符串数组")


def validate_production_correction(field_key: str, value: Any) -> None:
    """Reject invalid human truth even when the API is called without the UI."""
    text_limits = {
        "production_fields.title": 10,
        "production_fields.seotitle": 28,
        "production_fields.category": 120,
        "production_fields.style": 80,
        "production_fields.cons": 1000,
        "production_fields.design": 1000,
    }
    if field_key in text_limits:
        _required_text(value, field=field_key, limit=text_limits[field_key])
    elif field_key == "production_fields.tags":
        if not isinstance(value, list) or len({
            item.strip() for item in value if isinstance(item, str) and item.strip()
        }) < 4:
            raise ValueError("production_fields.tags 至少包含 4 个主要标签")
    elif field_key == "production_fields.score":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            raise ValueError("production_fields.score 必须是 0 至 100 的整数")
    elif field_key == "production_fields.reason":
        if not isinstance(value, list) or any(
            item not in PRODUCTION_REASON_VALUES for item in value
        ):
            raise ValueError("production_fields.reason 包含未允许的枚举值")
    elif field_key == "production_fields.image_defects":
        if value not in {"", "有水印"}:
            raise ValueError("production_fields.image_defects 只能为空字符串或“有水印”")
    elif field_key == "production_fields.trait":
        if value not in PRODUCTION_TRAIT_VALUES:
            raise ValueError("production_fields.trait 包含未允许的枚举值")
    elif field_key == "image_quality.quality_severity":
        if value not in QUALITY_SEVERITY_VALUES:
            raise ValueError("image_quality.quality_severity 包含未允许的枚举值")
    elif field_key == "media_form":
        _validate_media_form(value)


def normalize_production_fields(
    precheck: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any]:
    """Normalize the search/recommendation contract without changing scoring."""
    source = precheck.get("production_fields")
    if not isinstance(source, dict):
        legacy = {key: precheck[key] for key in PRODUCTION_FIELD_KEYS if key in precheck}
        source = legacy or None
    if source is None:
        if required:
            raise ValueError("标准评分合同缺少 production_fields")
        return precheck

    missing = [key for key in PRODUCTION_FIELD_KEYS if key not in source]
    if required and missing:
        raise ValueError("production_fields 缺少字段：" + "、".join(missing))
    if missing:
        return precheck

    tags = source.get("tags")
    if not isinstance(tags, list):
        raise ValueError("production_fields.tags 必须是字符串数组")
    normalized_tags = list(dict.fromkeys(
        item.strip() for item in tags if isinstance(item, str) and item.strip()
    ))
    if len(normalized_tags) < 4:
        raise ValueError("production_fields.tags 至少包含 4 个主要标签")

    score = source.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError("production_fields.score 必须是 0 至 100 的整数")
    reasons = source.get("reason")
    if not isinstance(reasons, list) or any(
        reason not in PRODUCTION_REASON_VALUES for reason in reasons
    ):
        raise ValueError("production_fields.reason 包含未允许的枚举值")
    image_defects = source.get("image_defects")
    if image_defects not in {"", "有水印"}:
        raise ValueError("production_fields.image_defects 只能为空字符串或“有水印”")
    trait = source.get("trait")
    if trait not in PRODUCTION_TRAIT_VALUES:
        raise ValueError("production_fields.trait 包含未允许的枚举值")

    image_quality = precheck.get("image_quality")
    if not isinstance(image_quality, dict):
        raise ValueError("标准评分合同缺少 image_quality")
    if image_quality.get("quality_severity") not in QUALITY_SEVERITY_VALUES:
        raise ValueError("image_quality.quality_severity 包含未允许的枚举值")
    _validate_media_form(precheck.get("media_form"))

    precheck["production_fields"] = {
        "title": _required_text(source.get("title"), field="title", limit=10),
        "seotitle": _required_text(source.get("seotitle"), field="seotitle", limit=28),
        "category": _required_text(source.get("category"), field="category", limit=120),
        "style": _required_text(source.get("style"), field="style", limit=80),
        "tags": normalized_tags,
        "cons": _required_text(source.get("cons"), field="cons", limit=1000),
        "design": _required_text(source.get("design"), field="design", limit=1000),
        "score": score,
        "reason": list(dict.fromkeys(reasons)),
        "image_defects": image_defects,
        "trait": trait,
    }
    return precheck


def attach_semantic_candidates(
    precheck: dict[str, Any],
    *,
    route: Any,
    provider_payload: Mapping[str, Any],
    evidence_prefix: str,
) -> dict[str, Any]:
    """Attach normalized semantic evidence candidates without publishing facts."""
    from .semantic_tag_mapping import candidate_payload, normalize_semantic_candidates

    normalized = deepcopy(precheck)
    bundles = normalize_semantic_candidates(
        route=route,
        provider_payload=provider_payload,
        evidence_prefix=evidence_prefix,
    )
    normalized["semantic_candidates"] = {
        field_key: [candidate_payload(item) for item in bundle.values]
        for field_key, bundle in bundles.items()
    }
    return normalized


def normalize_precheck_business_rules(precheck: dict[str, Any]) -> dict[str, Any]:
    """Enforce stable 3D66 media and presentation-quality invariants on model output."""
    media = precheck.get("media_form")
    if not isinstance(media, dict):
        return precheck
    quality = precheck.get("image_quality")
    if not isinstance(quality, dict):
        quality = {}
        precheck["image_quality"] = quality

    def is_yes(key: str) -> bool:
        item = media.get(key)
        return isinstance(item, dict) and item.get("status") == "yes"

    def force_not_professional(reason: str) -> None:
        professional = media.get("professional_photography")
        if not isinstance(professional, dict):
            professional = {}
            media["professional_photography"] = professional
        professional.update({"status": "no", "confidence": 1.0, "evidence": [reason]})

    def force_documentary(reason: str) -> None:
        documentary = media.get("documentary_record")
        if not isinstance(documentary, dict):
            documentary = {}
            media["documentary_record"] = documentary
        documentary.update({"status": "yes", "confidence": 1.0, "evidence": [reason]})

    def ensure_quality_issue(issue: str, reason: str) -> None:
        severity = str(quality.get("quality_severity") or "uncertain")
        if QUALITY_RANK.get(severity, 1) < QUALITY_RANK["slight"]:
            quality["quality_severity"] = "slight"
        issues = quality.get("issues")
        if not isinstance(issues, list):
            issues = []
            quality["issues"] = issues
        if issue not in issues:
            issues.append(issue)
        evidence = quality.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
            quality["evidence"] = evidence
        if reason not in evidence:
            evidence.append(reason)
        if is_yes("real_photo") and quality.get("capture_quality") == "good":
            quality["capture_quality"] = "acceptable"
        if is_yes("rendering") and quality.get("render_fidelity") == "good":
            quality["render_fidelity"] = "acceptable"

    if is_yes("rendering") or is_yes("ai_generated"):
        force_not_professional("系统规则：效果图或AI图不属于专业摄影")

    professional = media.get("professional_photography")
    professional_evidence = (
        professional.get("evidence") if isinstance(professional, dict) else []
    )
    if is_yes("professional_photography") and len(professional_evidence or []) < 4:
        force_not_professional("系统规则：专业摄影缺少四类互不重复的可见证据")
        if is_yes("real_photo") and not is_yes("casual_snapshot"):
            force_documentary("系统规则：实景图的专业摄影证据不足，按现场记录处理")
    if is_yes("professional_photography") and is_yes("documentary_record"):
        force_not_professional("系统规则：专业摄影与现场记录不能同时为是")

    scene_scope = precheck.get("scene_scope")
    if isinstance(scene_scope, dict) and scene_scope.get("type") == "partial_space":
        ensure_quality_issue(
            "presentation_incomplete",
            "系统规则：当前仅呈现局部空间，素材呈现完整性未达到画质正常标准",
        )
        force_not_professional("系统规则：局部空间记录不标记为专业摄影")
        if is_yes("real_photo") and not is_yes("casual_snapshot"):
            force_documentary("系统规则：局部空间实景按现场记录处理")

    display_flags = precheck.get("display_flags")
    if isinstance(display_flags, dict) and (
        display_flags.get("watermark") is True or display_flags.get("decorative_border") is True
    ):
        ensure_quality_issue(
            "watermark_or_border",
            "系统规则：水印或大面积装饰边框降低当前素材的可用画质",
        )

    if is_yes("unfinished_scene"):
        ensure_quality_issue(
            "unfinished_presentation",
            "系统规则：可见未完工区域使当前素材呈现质量至少为轻微问题",
        )

    if str(quality.get("quality_severity") or "normal") != "normal":
        force_not_professional("系统规则：存在画质或呈现问题，不标记为专业摄影")

    return precheck


COMBINED_DIMENSION_ALIASES = SPACE_INPUT_DIMENSION_ALIASES


def normalize_aesthetic_dimensions_for_schema(
    aesthetic: dict[str, Any] | None,
    dimension_schema: dict[str, Any],
) -> dict[str, Any] | None:
    """Canonicalize supported aliases before scoring and persistence."""
    if aesthetic is None:
        return None

    normalized = deepcopy(aesthetic)
    dimensions = normalized.get("dimensions")
    output_contract = dimension_schema.get("output_contract")
    if not isinstance(dimensions, dict) or not isinstance(output_contract, dict):
        return normalized

    dimension_keys = output_contract.get("dimension_output_keys")
    if not isinstance(dimension_keys, list) or any(
        not isinstance(key, str) or not key for key in dimension_keys
    ):
        return normalized

    normalized["dimensions"] = normalize_dimension_aliases(
        dimensions,
        dimension_keys,
    )
    return normalized


def is_combined_aesthetic_response(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload.get("scope"), dict)
        and "is_in_scope" in payload["scope"]
        and isinstance(payload.get("classification"), dict)
    )


def _status(value: str, expected: str, confidence: float) -> dict[str, Any]:
    return {
        "status": "yes" if value == expected else "uncertain" if value == "uncertain" else "no",
        "confidence": confidence,
        "evidence": [],
    }


def adapt_combined_aesthetic_response(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapt the one-call space_aesthetic schema to the application's A/B contract."""
    confidence = float(payload.get("overall_confidence") or 0.0)
    scope = payload.get("scope") or {}
    source_classification = payload.get("classification") or {}
    media = payload.get("media_analysis") or {}
    flags = payload.get("special_flags") or {}
    quality = payload.get("quality_analysis") or {}
    decisions = payload.get("decision_rules") or {}

    classification = dict(source_classification)
    classification["scope_status"] = (
        "in_scope" if scope.get("is_in_scope") is True else "out_of_scope"
    )
    classification["primary_confidence"] = float(
        source_classification.get("category_confidence") or confidence
    )

    media_type = str(media.get("media_type") or "uncertain")
    shooting_style = str(media.get("shooting_style") or "uncertain")
    media_form = {
        "real_photo": _status(media_type, "real_photo", confidence),
        "rendering": _status(media_type, "render", confidence),
        "ai_generated": _status(str(media.get("ai_status") or "uncertain"), "yes", confidence),
        "casual_snapshot": _status(shooting_style, "casual_snapshot", confidence),
        "collage_or_multiview": _status(
            "yes" if flags.get("is_collage") or flags.get("is_multi_view_layout") else "no",
            "yes",
            confidence,
        ),
        "unfinished_scene": _status(
            "yes" if flags.get("is_unfinished_site") else "no", "yes", confidence
        ),
        "white_background_product": _status(
            "yes" if flags.get("is_pure_white_product") else "no", "yes", confidence
        ),
    }

    damage_to_severity = {
        "none": "normal",
        "mild": "slight",
        "moderate": "moderate",
        "severe": "severe",
        "uncertain": "uncertain",
    }
    review_reason = str(decisions.get("manual_review_reason") or "").strip()
    precheck = dict(payload)
    precheck.update(
        {
            "classification": classification,
            "media_form": media_form,
            "image_quality": {
                "quality_severity": damage_to_severity.get(
                    str(quality.get("asset_file_damage") or "uncertain"), "uncertain"
                ),
                "confidence": confidence,
                "issues": quality.get("quality_issue_codes") or [],
                "evidence": quality.get("observable_evidence") or [],
            },
            "needs_review": bool(decisions.get("manual_review_required")),
            "review_reasons": [review_reason] if review_reason else [],
        }
    )

    dimensions = dict(payload.get("dimensions") or {})
    for source_key, target_key in COMBINED_DIMENSION_ALIASES.items():
        if source_key in dimensions:
            dimensions[target_key] = dimensions[source_key]

    aesthetic = dict(payload)
    aesthetic.update(
        {
            "dimensions": dimensions,
            "scoring_profile": "space_aesthetic_v1.3",
            "assessment_confidence": confidence,
            "needs_review": bool(decisions.get("manual_review_required")),
            "review_reasons": [review_reason] if review_reason else [],
        }
    )
    return precheck, aesthetic


def repair_combined_aesthetic_results(db: Session) -> int:
    """Repair stored one-call results without making another model request."""
    repaired = 0
    results = db.scalars(
        select(EvaluationResult).where(EvaluationResult.aesthetic_json.is_(None))
    ).all()
    for result in results:
        try:
            original = json.loads(result.precheck_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not is_combined_aesthetic_response(original):
            continue
        precheck, aesthetic = adapt_combined_aesthetic_response(original)
        scoring = calculate_score(precheck, aesthetic)
        result.precheck_json = json.dumps(precheck, ensure_ascii=False)
        result.aesthetic_json = json.dumps(aesthetic, ensure_ascii=False)
        result.scoring_json = json.dumps(scoring, ensure_ascii=False)
        result.score = scoring.get("score")
        result.level = scoring.get("level")
        result.confidence = scoring.get("confidence")
        result.needs_review = bool(scoring.get("needs_review"))
        result.engine_version = ENGINE_VERSION
        repaired += 1
    if repaired:
        db.commit()
    return repaired


def rescore_stored_results(db: Session) -> int:
    """Apply the latest deterministic scoring rules to unreviewed stored model outputs."""
    rescored = 0
    results = db.scalars(
        select(EvaluationResult).where(EvaluationResult.engine_version != ENGINE_VERSION)
    ).all()
    for result in results:
        if result.reviews:
            continue
        try:
            precheck = json.loads(result.precheck_json)
            aesthetic = json.loads(result.aesthetic_json) if result.aesthetic_json else None
        except (TypeError, json.JSONDecodeError):
            continue
        try:
            selection = dimension_selection_from_job_snapshot(
                result.job.category_profile_snapshot_json
                if result.job is not None
                else None
            )
            definition = dimension_schema_from_strategy_snapshot(
                result.strategy_snapshot_json,
                aesthetic=aesthetic,
            )
            projected = (
                project_dimension_definition(definition, selection)
                if selection is not None
                else definition
            )
            scoring = (
                calculate_prompt_only_result(
                    precheck,
                    model_payload=precheck,
                    dimension_selection=selection,
                )
                if selection is not None
                and selection.get("mode") == "none"
                else calculate_score(
                    precheck,
                    aesthetic,
                    dimension_schema=projected,
                )
            )
        except (TypeError, ValueError):
            continue
        result.scoring_json = json.dumps(scoring, ensure_ascii=False)
        result.score = scoring.get("score")
        result.level = scoring.get("level")
        result.confidence = scoring.get("confidence")
        result.needs_review = bool(scoring.get("needs_review"))
        result.engine_version = ENGINE_VERSION
        rescored += 1
    if rescored:
        db.commit()
    return rescored
