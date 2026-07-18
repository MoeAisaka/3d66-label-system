from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EvaluationResult
from .scoring import ENGINE_VERSION, calculate_score


QUALITY_RANK = {
    "normal": 0,
    "slight": 1,
    "moderate": 2,
    "severe": 3,
    "unusable": 4,
    "uncertain": 1,
}


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

    scene_scope = precheck.get("scene_scope")
    if isinstance(scene_scope, dict) and scene_scope.get("type") == "partial_space":
        ensure_quality_issue(
            "presentation_incomplete",
            "系统规则：当前仅呈现局部空间，素材呈现完整性未达到画质正常标准",
        )
        force_not_professional("系统规则：局部空间记录不标记为专业摄影")
        if is_yes("real_photo") and not is_yes("casual_snapshot"):
            documentary = media.get("documentary_record")
            if not isinstance(documentary, dict):
                documentary = {}
                media["documentary_record"] = documentary
            documentary.update(
                {
                    "status": "yes",
                    "confidence": 1.0,
                    "evidence": ["系统规则：局部空间实景按现场记录处理"],
                }
            )

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


COMBINED_DIMENSION_ALIASES = {
    "spatial_design_coherence": "spatial_design_furnishing",
    "detail_finish": "detail_completion",
    "contemporary_relevance": "inspiration_reference",
}


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
            scoring = calculate_score(precheck, aesthetic)
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
