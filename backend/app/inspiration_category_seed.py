"""ADR-0033 Phase 3.8 inspiration-image v3 contract seed (framework-first, pure).

This is the **final framework piece**: it assembles the six prior ADR-0033
framework modules — the redline pre-filter, the v3 evaluation contract, the
deterministic aggregator, the grade→deduction bridge, the common+specific
dimension composition, and the subcategory (track) classifier — into one
complete, self-consistent, end-to-end runnable **inspiration-image** sample
configuration, plus a deterministic "score one image" orchestrator.

Like every ADR-0033 framework layer this module is a set of **pure functions**:
no IO, no network, no database and no model calls.  Every builder returns a
plain, JSON-serializable ``dict`` and ``evaluate_one`` is referentially
transparent for a given input.  Grades are always **inputs** (they simulate 调用B's
output); this module never invents "how good" an image is, it only assembles the
frozen rules and runs the deterministic scoring chain.

Everything built here is designed to pass the existing validators unchanged
(``validate_category_evaluation_contract``, ``validate_classification_map``,
``validate_subcategory_dimensions``) — this module imports and reuses those
modules rather than re-implementing any of their logic.

Rule source: ``docs/reference/category-inspiration-image-rules-20260803.md``
(four redlines, three tracks 40/60/100 · 20/60/80 · 40/30/70, media penalties,
80-score veto → 79, level-1 categories).
"""

from __future__ import annotations

from typing import Any

from .category_evaluation_aggregator import aggregate_category_evaluation
from .category_evaluation_contract import (
    CATEGORY_EVALUATION_CONTRACT_VERSION,
    COMMON_MODIFIERS_FORMAT_VERSION,
    TRACK_CLASSIFICATION_FORMAT_VERSION,
    validate_category_evaluation_contract,
)
from .dimension_composition import (
    SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
    compose_deductions,
)
from .dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    space_schema_definition_for_version,
)
from .redline_policy import REDLINE_POLICY_FORMAT_VERSION, evaluate_redlines
from .subcategory_resolver import (
    CLASSIFICATION_MAP_FORMAT_VERSION,
    resolve_subcategory,
)


INSPIRATION_SEED_VERSION = "inspiration-category-seed-v1"

# The three inspiration-image tracks (子类目) — score base / dimension full-marks
# / track cap follow the DingTalk rules: 一类 40+60=100, 二类 20+60=80, 三类
# 40+30=70.  ``class_three`` is the default (兜底) track.
TRACK_CLASS_ONE = "class_one"
TRACK_CLASS_TWO = "class_two"
TRACK_CLASS_THREE = "class_three"

# Redline hit action per the doc: 命中 → L5（最差/废图档）、hard_reject、总分 ≤49.
_REDLINE_HIT_LEVEL = "L5"
_REDLINE_HIT_SCORE_CAP = 49

# Classifier gate: below this 调用A confidence we fall back to the default track.
_MIN_CONFIDENCE = 0.6


def _redline_policy() -> dict[str, Any]:
    """Four inspiration-image redlines over the frozen 调用A ``reason`` enum.

    Signal is always ``production_fields.reason``; ``match_any`` values are drawn
    from the real reason enum (``schema_adapter.PRODUCTION_REASON_VALUES``).  The
    large-text rule carries documented exemptions (专业海报 / 轻量化效果图辅助文字);
    the qr-code rule exempts a tiny corner code.
    """
    return {
        "format_version": REDLINE_POLICY_FORMAT_VERSION,
        "enabled": True,
        "hit_level": _REDLINE_HIT_LEVEL,
        "hit_score_cap": _REDLINE_HIT_SCORE_CAP,
        "rules": [
            {
                "key": "screenshot",
                "label": "截图",
                "signal": "production_fields.reason",
                "match_any": ["是截图"],
                "exemptions": [],
                "enabled": True,
            },
            {
                "key": "casual_snapshot",
                "label": "随手拍（透视杂乱）",
                "signal": "production_fields.reason",
                "match_any": ["是随手拍"],
                "exemptions": [],
                "enabled": True,
            },
            {
                "key": "large_text",
                "label": "大面积文字说明（≥40%）",
                "signal": "production_fields.reason",
                "match_any": ["有大面积文字说明"],
                "exemptions": ["专业海报", "轻量化效果图辅助文字"],
                "enabled": True,
            },
            {
                "key": "qr_code",
                "label": "大面积二维码（≥50%）",
                "signal": "production_fields.reason",
                "match_any": ["有二维码"],
                "exemptions": ["角落极小二维码"],
                "enabled": True,
            },
        ],
    }


def _track_schema_ref() -> dict[str, str]:
    """The frozen dimension-schema reference shared by every track."""
    return {"schema_key": SPACE_SCHEMA_KEY, "version": ACTIVE_V13_VERSION}


def _track_classification() -> dict[str, Any]:
    """Three tracks with doc score bands; default falls to the catch-all 三类."""
    return {
        "format_version": TRACK_CLASSIFICATION_FORMAT_VERSION,
        "default_track": TRACK_CLASS_THREE,
        "tracks": [
            {
                "key": TRACK_CLASS_ONE,
                "label": "一类（建筑/室内/景观/规划）",
                "base_score": 40,
                "dimension_max": 60,
                "track_cap": 100,
                "dimension_schema_ref": _track_schema_ref(),
            },
            {
                "key": TRACK_CLASS_TWO,
                "label": "二类（产品/雕塑/装置/美术/游戏原画）",
                "base_score": 20,
                "dimension_max": 60,
                "track_cap": 80,
                "dimension_schema_ref": _track_schema_ref(),
            },
            {
                "key": TRACK_CLASS_THREE,
                "label": "三类（其它杂图）",
                "base_score": 40,
                "dimension_max": 30,
                "track_cap": 70,
                "dimension_schema_ref": _track_schema_ref(),
            },
        ],
    }


def _common_modifiers() -> dict[str, Any]:
    """Media penalty (real_photo baseline, render_3d -5, ai_image -15) + 80→79 veto."""
    return {
        "format_version": COMMON_MODIFIERS_FORMAT_VERSION,
        "media_type_penalty": {
            "baseline": "real_photo",
            "penalties": {
                "real_photo": 0,
                "render_3d": -5,
                "ai_image": -15,
                "other": 0,
            },
        },
        "high_score_veto": {"threshold": 80, "cap_to": 79},
    }


def build_inspiration_v3_contract() -> dict[str, Any]:
    """Build the complete, self-consistent inspiration-image v3 contract.

    Assembles the three v3 blocks (``redline_policy`` / ``track_classification`` /
    ``common_modifiers``) into an ``evaluation-category-profile-v3`` contract and
    self-validates it via ``validate_category_evaluation_contract`` before
    returning — an illegal assembly raises from the shared validator rather than
    yielding a broken contract.  Pure and JSON-serializable.
    """
    contract = {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "category_key": "inspiration_image",
        "level_semantics_version": "doc-l5-worst-v1",
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers(),
    }
    # Self-check: fail closed here if any block drifts out of spec.
    validate_category_evaluation_contract(contract)
    return contract


# 灵感图一级分类词 → 赛道映射（doc 一级分类枚举，两套并存、互不替代）：
#   建筑/室内/景观/规划族 → class_one；产品/雕塑/装置/美术/游戏族 → class_two；
#   其它一律落 class_three。目标全部是合同 track key。
_CATEGORY_TO_SUBCATEGORY: dict[str, str] = {
    # 一类：建筑 / 室内 / 景观 / 规划
    "建筑设计": TRACK_CLASS_ONE,
    "景观设计": TRACK_CLASS_ONE,
    "规划设计": TRACK_CLASS_ONE,
    "居住空间": TRACK_CLASS_ONE,
    "酒店民宿": TRACK_CLASS_ONE,
    "办公空间": TRACK_CLASS_ONE,
    "商业空间": TRACK_CLASS_ONE,
    "公共空间": TRACK_CLASS_ONE,
    "展示设计": TRACK_CLASS_ONE,
    "软装设计": TRACK_CLASS_ONE,
    "硬装结构": TRACK_CLASS_ONE,
    # 二类：产品家具 / 雕塑 / 装置 / 美术 / 游戏原画
    "产品设计": TRACK_CLASS_TWO,
    "美术类": TRACK_CLASS_TWO,
    "游戏设计": TRACK_CLASS_TWO,
    "视觉设计": TRACK_CLASS_TWO,
    # 三类：意向图等其它杂图明确落三类（其余未命中项也由兜底落三类）
    "意向图": TRACK_CLASS_THREE,
    "其它": TRACK_CLASS_THREE,
}


def build_inspiration_classification_map() -> dict[str, Any]:
    """Build the standalone ``subcategory-classification-map-v1`` for 灵感图.

    ``min_confidence`` is 0.6; every mapping target and the out-of-scope target
    is a contract track key.  Out-of-scope routes to ``class_three`` (兜底赛道).
    Pure and JSON-serializable.
    """
    return {
        "format_version": CLASSIFICATION_MAP_FORMAT_VERSION,
        "min_confidence": _MIN_CONFIDENCE,
        "category_to_subcategory": dict(_CATEGORY_TO_SUBCATEGORY),
        "out_of_scope_subcategory": TRACK_CLASS_THREE,
    }


def _common_group_from_v13() -> dict[str, Any]:
    """A non-empty common group drawn from the v13 space schema's core dimensions.

    Uses the schema's ``core_dimension_keys`` subset (a non-empty slice of the
    v13 dimensions), re-normalizing their weights to sum to 1 inside the group so
    the reused grade bridge accepts it.  Each dimension keeps its frozen
    ``grade_points`` from the registry.
    """
    schema = space_schema_definition_for_version(ACTIVE_V13_VERSION)
    core_keys = list(schema["core_dimension_keys"])
    by_key = {dimension["key"]: dimension for dimension in schema["dimensions"]}
    selected = [by_key[key] for key in core_keys]

    weight_total = sum(float(dimension["weight"]) for dimension in selected)
    dimensions = []
    for dimension in selected:
        normalized_weight = float(dimension["weight"]) / weight_total
        dimensions.append({
            "key": dimension["key"],
            "label": dimension["label"],
            "weight": normalized_weight,
            "grade_points": dict(dimension["grade_points"]),
        })
    # Absorb any float drift onto the last weight so the group sums to exactly 1.
    drift = 1.0 - sum(dimension["weight"] for dimension in dimensions)
    dimensions[-1]["weight"] += drift

    return {
        "group_weight": 0.6,
        "schema_definition": {
            "format_version": schema["format_version"],
            "schema_key": SPACE_SCHEMA_KEY,
            "version": ACTIVE_V13_VERSION,
            "dimensions": dimensions,
        },
    }


def _specific_group(*, dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """A subcategory-owned specific group with in-group weights summing to 1."""
    return {
        "group_weight": 0.4,
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": "inspiration_specific",
            "version": "v1",
            "dimensions": dimensions,
        },
    }


# Per-track specific dimensions.  grade_points reuse the doc's shared 1..5 anchor
# scale; weights inside each specific group sum to 1.  Keys are disjoint from the
# common group's core keys (no dimension_key_overlap).
_SPECIFIC_GRADE_POINTS = {"1": 20.0, "2": 45.0, "3": 65.0, "4": 82.0, "5": 95.0}

_SPECIFIC_DIMENSIONS: dict[str, list[dict[str, Any]]] = {
    TRACK_CLASS_ONE: [
        {
            "key": "spatial_originality",
            "label": "空间原创设计感",
            "weight": 0.5,
            "grade_points": dict(_SPECIFIC_GRADE_POINTS),
        },
        {
            "key": "design_trendiness",
            "label": "设计流行度",
            "weight": 0.5,
            "grade_points": dict(_SPECIFIC_GRADE_POINTS),
        },
    ],
    TRACK_CLASS_TWO: [
        {
            "key": "product_form_language",
            "label": "产品形态语言",
            "weight": 0.5,
            "grade_points": dict(_SPECIFIC_GRADE_POINTS),
        },
        {
            "key": "artistic_expression",
            "label": "美术表现力",
            "weight": 0.5,
            "grade_points": dict(_SPECIFIC_GRADE_POINTS),
        },
    ],
    TRACK_CLASS_THREE: [
        {
            "key": "visual_impact",
            "label": "强视觉冲击力",
            "weight": 1.0,
            "grade_points": dict(_SPECIFIC_GRADE_POINTS),
        },
    ],
}

# Each track's dimension_max mirrors its contract track (一类/二类 60, 三类 30).
_TRACK_DIMENSION_MAX = {
    TRACK_CLASS_ONE: 60,
    TRACK_CLASS_TWO: 60,
    TRACK_CLASS_THREE: 30,
}


def build_inspiration_subcategory_dimensions() -> dict[str, dict[str, Any]]:
    """Build a ``subcategory-dimensions-v1`` config per track (common + specific).

    Each config carries a non-empty common group (from the v13 space schema's
    core dimensions, group_weight 0.6) and a non-empty specific group
    (subcategory-owned, group_weight 0.4); group_weights sum to 1 and each
    group's dimension weights sum to 1.  ``dimension_max`` matches the track
    (一类/二类 60, 三类 30).  Every config passes
    ``validate_subcategory_dimensions``.  Returns ``{track_key: config}``.
    """
    configs: dict[str, dict[str, Any]] = {}
    for track_key, specific in _SPECIFIC_DIMENSIONS.items():
        configs[track_key] = {
            "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
            "sub_category_key": track_key,
            "dimension_max": _TRACK_DIMENSION_MAX[track_key],
            "common_group": _common_group_from_v13(),
            "specific_group": _specific_group(dimensions=specific),
        }
    return configs


def evaluate_one(
    *,
    contract: dict,
    classification_map: dict,
    subcategory_dimensions: dict[str, dict],
    precheck: dict,
    common_grades_by_track: dict[str, dict[str, int]],
    specific_grades_by_track: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """End-to-end deterministic "score one image" orchestrator (pure function).

    No model calls: ``common_grades_by_track`` / ``specific_grades_by_track``
    supply the per-track 调用B grades (simulating 调用B's output).  Chain:

    1. ``evaluate_redlines`` — on a hit, run the aggregator's redline branch and
       return immediately (hard_reject, L5, score ≤49, terminated_at=redline).
    2. Otherwise ``resolve_subcategory`` picks the track_key → pull that track's
       ``subcategory-dimensions-v1`` config → ``compose_deductions`` (using that
       track's common/specific grades) → ``aggregate_category_evaluation`` with
       the resolved ``track_key``.

    Returns ``{"redline", "resolved", "result"}`` — a fixed-shape,
    JSON-serializable dict.  ``resolved`` is ``None`` on a redline hit.
    """
    redline = evaluate_redlines(precheck, policy=contract["redline_policy"])
    if redline.get("hit"):
        # Redline short-circuit: the aggregator owns the deterministic redline
        # branch; feed it an empty dimension_result since scoring is terminated.
        result = aggregate_category_evaluation(
            contract, precheck, {"deductions": {}}, track_key=None
        )
        return {"redline": redline, "resolved": None, "result": result}

    resolved = resolve_subcategory(
        precheck,
        classification_map=classification_map,
        track_classification=contract["track_classification"],
    )
    track_key = resolved["track_key"]
    if track_key not in subcategory_dimensions:
        raise KeyError(f"缺少 track {track_key} 的 subcategory_dimensions 配置")

    composed = compose_deductions(
        config=subcategory_dimensions[track_key],
        common_grades=common_grades_by_track.get(track_key),
        specific_grades=specific_grades_by_track.get(track_key),
    )
    result = aggregate_category_evaluation(
        contract, precheck, composed, track_key=track_key
    )
    return {"redline": redline, "resolved": resolved, "result": result}
