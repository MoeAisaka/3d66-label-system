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
    COMMON_MODIFIERS_V2_FORMAT_VERSION,
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
)
from .redline_policy import REDLINE_POLICY_FORMAT_VERSION, evaluate_redlines
from .subcategory_resolver import (
    CLASSIFICATION_MAP_FORMAT_VERSION,
    resolve_subcategory,
)

from .inspiration_aesthetic_foundation import AESTHETIC_CALL_B_VERSION, ANCHORS, DIMENSION_KEYS, FOUNDATION_VERSION

INSPIRATION_SEED_VERSION = "inspiration-category-seed-v6-casual-semantics"
INSPIRATION_SPEC_VERSION = "inspiration-v3-aesthetic-evidence-v3-casual-semantics-20260806"
INSPIRATION_CALL_A_VERSION = "inspiration-a-v3-hard-defect-recall-rev4-20260805"
INSPIRATION_CALL_B_VERSION = AESTHETIC_CALL_B_VERSION
INSPIRATION_REV3_SPEC_VERSION = "inspiration-v2-human-calibrated-20260805"
INSPIRATION_REV3_CALL_A_VERSION = "inspiration-a-v2-human-calibrated-20260805"

# The three inspiration-image tracks (子类目) — score base / dimension full-marks
# / track cap follow the DingTalk rules: 一类 40+60=100, 二类 20+60=80, 三类
# 40+30=70.  ``class_three`` is the default (兜底) track.
TRACK_CLASS_ONE = "class_one"
TRACK_CLASS_TWO = "class_two"
TRACK_CLASS_THREE = "class_three"

# Redline hit action per the human-calibrated spec: 命中即终止，L5，总分 ≤20。
_REDLINE_HIT_LEVEL = "L5"
_REDLINE_HIT_SCORE_CAP = 20

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
                "requires_any_hard_defect": [
                    "careless_composition",
                    "distorted_viewpoint",
                    "fisheye_distortion",
                ],
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
    """媒介不降权；达到 80 且命中任一冻结硬伤时压至 79。"""
    return {
        "format_version": COMMON_MODIFIERS_FORMAT_VERSION,
        "media_type_penalty": {
            "enabled": False,
            "baseline": "real_photo",
            "penalties": {
                "real_photo": 0,
                "render_3d": -5,
                "ai_image": -15,
                "other": 0,
            },
        },
        "high_score_veto": {
            "enabled": True,
            "threshold": 80,
            "cap_to": 79,
            "rules": [
                {
                    "key": "blurry_grayish",
                    "description": "模糊发灰/低分辨率/压缩痕迹/噪点/材质廉价",
                },
                {
                    "key": "careless_composition",
                    "description": "构图敷衍/元素堆砌/无设计逻辑/比例严重失调",
                },
                {
                    "key": "garish_color",
                    "description": "色彩艳俗/过饱和/灯光刺眼/假白失真",
                },
                {
                    "key": "large_dead_black",
                    "description": "大面积死黑/暗部缺失/明暗断层/光影矛盾",
                },
                {
                    "key": "distorted_viewpoint",
                    "description": "视角怪异/透视畸形/主体严重裁切",
                },
                {
                    "key": "fake_material",
                    "description": "材质虚假/反光失真/纹理模糊/细节缺失",
                },
                {
                    "key": "fisheye_distortion",
                    "description": "鱼眼超广角畸变/垂直线倾斜/边缘扭曲",
                },
                {
                    "key": "invalid_black_border",
                    "description": "大面积无效黑边/生硬裁切线/主体被遮挡",
                },
                {
                    "key": "severe_color_cast",
                    "description": "色调严重偏色/光影违和/违背真实质感",
                },
                {
                    "key": "known_real_photo_defect",
                    "description": "知名落地实拍建筑素材出现以上硬伤同样无豁免",
                },
            ],
        },
    }


def _common_modifiers_v2() -> dict[str, Any]:
    """Versioned hard-defect severity policy for inspiration rev4."""
    return {
        "format_version": COMMON_MODIFIERS_V2_FORMAT_VERSION,
        "media_type_penalty": {
            "enabled": False,
            "baseline": "real_photo",
            "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0},
        },
        "high_score_veto": {
            "enabled": True,
            "policy_version": "hard-defect-severity-v1",
            "tiers": {
                "A": {"action": "cap", "cap_to": 20, "target_band": "L5"},
                "B": {"action": "cap", "cap_to": 60, "target_band": "L3-L4"},
                "record_only": {"action": "record_only", "cap_to": None, "target_band": "record_only"},
            },
            "escalation": {"source_tier": "B", "minimum_distinct_hits": 3, "target_tier": "A"},
            "rules": [
                {"key": "blurry_grayish", "source": "hard_defects", "severity": "A", "description": "模糊发灰/低分辨率/压缩痕迹/噪点"},
                {"key": "invalid_black_border", "source": "hard_defects", "severity": "A", "description": "大面积无效黑边/生硬裁切线/主体被遮挡"},
                {"key": "fisheye_distortion", "source": "hard_defects", "severity": "A", "description": "鱼眼超广角畸变/垂直线倾斜/边缘扭曲"},
                {"key": "subject_obscuring_watermark", "source": "image_defects", "severity": "A", "description": "水印遮挡主体"},
                {"key": "large_area_watermark", "source": "image_defects", "severity": "A", "description": "大面积水印"},
                {"key": "garish_color", "source": "hard_defects", "severity": "B", "description": "色彩艳俗/过饱和/灯光刺眼/假白失真"},
                {"key": "severe_color_cast", "source": "hard_defects", "severity": "B", "description": "色调严重偏色/光影违和/违背真实质感"},
                {"key": "fake_material", "source": "hard_defects", "severity": "B", "description": "材质虚假/反光失真/纹理模糊/细节缺失"},
                {"key": "large_dead_black", "source": "hard_defects", "severity": "B", "description": "大面积死黑/暗部缺失/明暗断层/光影矛盾"},
                {"key": "distorted_viewpoint", "source": "hard_defects", "severity": "B", "description": "视角怪异/透视畸形/主体严重裁切"},
                {"key": "careless_composition", "source": "hard_defects", "severity": "B", "description": "构图敷衍/元素堆砌/无设计逻辑/比例严重失调"},
                {"key": "corner_small_watermark", "source": "image_defects", "severity": "record_only", "description": "角落小水印，仅记录不压分"},
                {
                    "key": "known_real_photo_defect",
                    "source": "hard_defects",
                    "kind": "modifier",
                    "severity": "inherit",
                    "inherits_strongest": True,
                    "cancel_exemption": True,
                    "description": "知名落地实拍素材命中其它硬伤时取消豁免",
                },
            ],
        },
    }


def build_inspiration_v3_rev3_contract() -> dict[str, Any]:
    """Build the complete, self-consistent inspiration-image v3 contract.

    Assembles the three v3 blocks (``redline_policy`` / ``track_classification`` /
    ``common_modifiers``) into an ``evaluation-category-profile-v3`` contract and
    self-validates it via ``validate_category_evaluation_contract`` before
    returning — an illegal assembly raises from the shared validator rather than
    yielding a broken contract.  Pure and JSON-serializable.
    """
    contract = {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "spec_version": INSPIRATION_REV3_SPEC_VERSION,
        "category_key": "inspiration_image",
        "level_semantics_version": "doc-l5-worst-v1",
        "level_thresholds": [
            {"min_score": 81, "level": "L1"},
            {"min_score": 61, "level": "L2"},
            {"min_score": 41, "level": "L3"},
            {"min_score": 21, "level": "L4"},
            {"min_score": 0, "level": "L5"},
        ],
        "prompt_bindings": {
            "call_a_version": INSPIRATION_REV3_CALL_A_VERSION,
            "call_b_version": INSPIRATION_CALL_B_VERSION,
        },
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers(),
    }
    # Self-check: fail closed here if any block drifts out of spec.
    validate_category_evaluation_contract(contract)
    return contract


def build_inspiration_v3_contract() -> dict[str, Any]:
    """Build active inspiration rev4 without mutating the rev3 contract."""
    contract = {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "spec_version": INSPIRATION_SPEC_VERSION,
        "category_key": "inspiration_image",
        "level_semantics_version": "doc-l5-worst-v1",
        "level_thresholds": [
            {"min_score": 90, "level": "L1"},
            {"min_score": 75, "level": "L2"},
            {"min_score": 60, "level": "L3"},
            {"min_score": 0, "level": "L4"},
        ],
        "prompt_bindings": {
            "call_a_version": INSPIRATION_CALL_A_VERSION,
            "call_b_version": INSPIRATION_CALL_B_VERSION,
        },
        "authoritative_precheck_contract": {
            "format_version": "inspiration-authoritative-precheck-v1",
            "required_validation_status": "valid",
        },
        "aesthetic_foundation": {
            "format_version": FOUNDATION_VERSION,
            "call_b_version": AESTHETIC_CALL_B_VERSION,
            "calibration_status": "temporary_pending_calibration",
            "boundary_policy": "floor_to_lower_band",
            "score_thresholds": [
                {"min_score": 90, "level": "L1"},
                {"min_score": 75, "level": "L2"},
                {"min_score": 60, "level": "L3"},
                {"min_score": 0, "level": "L4"},
            ],
            "dimension_keys": list(DIMENSION_KEYS), "anchors": [dict(item) for item in ANCHORS],
        },
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": _common_modifiers_v2(),
    }
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


# --------------------------------------------------------------------------- #
# 方案 A（Owner 2026-08-04）：产品《【灵感图】-prompt》真实 6/5 维度体系。
#
# 每赛道的全部维度放入单一 **common_group（group_weight=1.0）**，specific_group
# 置空——引擎（``dimension_composition``）对空组的处理是「其权重被丢弃，非空组的
# group_weight 在彼此间重归一化并瓜分 dimension_max」，故单个满权重的 common_group
# 独占整块 dimension_max，无自由分泄漏。合同的 ``dimension_schema_ref`` 只是引用标签，
# 聚合器/组合器都不拿它跨校维度 key，所以在此直接承载真实维度不需要改任何引擎核心。
#
# grade_points 用线性锚点 {1:0,2:25,3:50,4:75,5:100}：grade5→该维度满分不扣、
# grade1→该维度 share 全扣（调用B 的 1-5 档直接线性映射到扣分比例）。
# --------------------------------------------------------------------------- #

# 线性锚点：grade5=满分（不扣该维度 share），grade1=0（全扣该维度 share）。
_LINEAR_GRADE_POINTS = {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0}

def _rule(rule_id: str, description: str, deduction: int) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "description": description,
        "deduction": deduction,
        "tags": [],
    }


# 直接存业务原始权重（池占总分比例），不把 0.60/0.30 重归一化为 1。
_CLASS_ONE_TWO_DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "visual_structure",
        "label": "视觉结构",
        "weight": 0.10,
        "rules": [
            _rule("r1", "边缘次要构件裁切/主体小幅偏移", 30),
            _rule("r2", "核心设计部位被截断/主体严重偏移", 50),
            _rule("r3", "透视错乱/画面层次混乱", 100),
        ],
    },
    {
        "key": "color_aesthetics",
        "label": "色彩美学",
        "weight": 0.10,
        "rules": [
            _rule("r1", "轻微色温饱和度偏差", 30),
            _rule("r2", "肉眼明显偏色/过艳发灰", 50),
            _rule("r3", "渲染脏斑/杂色/颜色溢出", 100),
        ],
    },
    {
        "key": "emotional_expression",
        "label": "情感表达",
        "weight": 0.05,
        "rules": [
            _rule("r1", "空间氛围平淡空洞/场景缺叙事逻辑", 50),
            _rule("r2", "场景僵硬冰冷/环境无情绪", 100),
        ],
    },
    {
        "key": "design_aesthetics",
        "label": "设计美学",
        "weight": 0.10,
        "rules": [
            _rule("r1", "局部尺度轻微失调/空间略局促", 30),
            _rule("r2", "尺度错误明显/配比违和", 50),
            _rule("r3", "严重比例失真/形体畸形/空间逻辑不合理", 100),
        ],
    },
    {
        "key": "originality",
        "label": "原创设计感",
        "weight": 0.10,
        "rules": [
            _rule("r1", "局部造型复刻/辨识度不足", 30),
            _rule("r2", "无颠覆创新/差异化弱", 50),
            _rule("r3", "重度同质化/高度复刻", 100),
        ],
    },
    {
        "key": "design_trendiness",
        "label": "设计流行度",
        "weight": 0.15,
        "rules": [
            _rule("r1", "少量元素不符主流审美", 30),
            _rule("r2", "整体过时网红制式/风格老旧", 50),
            _rule("r3", "全面淘汰老旧模板/过时造型", 100),
        ],
    },
]

_CLASS_THREE_DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "subject_focus",
        "label": "主题清晰",
        "weight": 0.06,
        "rules": [
            _rule("r1", "主体模糊/对焦失效", 50),
            _rule("r2", "主体被边缘裁切/核心区缺失", 50),
        ],
    },
    {
        "key": "mood_atmosphere",
        "label": "情绪氛围",
        "weight": 0.06,
        "rules": [
            _rule("r1", "色彩杂乱/大面积脏斑/异常色偏/色彩溢出", 100),
            _rule("r2", "光源逻辑冲突/光影方向矛盾", 100),
        ],
    },
    {
        "key": "composition_lighting",
        "label": "构图结构",
        "weight": 0.06,
        "rules": [
            _rule("r1", "主体严重偏移/主次层级颠倒", 100),
            _rule("r2", "透视扭曲倾斜/空间结构错乱", 100),
        ],
    },
    {
        "key": "reference_value",
        "label": "内容借鉴价值",
        "weight": 0.06,
        "rules": [
            _rule("r1", "局部元素与线上素材重合", 50),
            _rule("r2", "全网通用网红素材仅轻微改动", 100),
        ],
    },
    {
        "key": "visual_impact",
        "label": "视觉冲击力",
        "weight": 0.06,
        "rules": [
            _rule("r1", "基础画质完好但表现力普通/记忆点弱", 40),
            _rule("r2", "观感平淡乏味/缺吸引力", 50),
            _rule("r3", "画质瑕疵严重且毫无表现力", 100),
        ],
    },
]

# 每赛道的维度定义（满分）与 dimension_max。一类/二类共用同一套 6 维度。
_TRACK_DIMENSION_SPECS: dict[str, list[dict[str, Any]]] = {
    TRACK_CLASS_ONE: _CLASS_ONE_TWO_DIMENSIONS,
    TRACK_CLASS_TWO: _CLASS_ONE_TWO_DIMENSIONS,
    TRACK_CLASS_THREE: _CLASS_THREE_DIMENSIONS,
}

# 每赛道 dimension_max 与合同 track 一致（一类/二类 60，三类 30）。
_TRACK_DIMENSION_MAX = {
    TRACK_CLASS_ONE: 60,
    TRACK_CLASS_TWO: 60,
    TRACK_CLASS_THREE: 30,
}


def placeholder_deduction_rules(label: str) -> list[dict[str, Any]]:
    """Generic Chinese placeholder rules; operators replace them with specifics."""
    return [
        {
            "rule_id": "r1",
            "description": f"{label}存在明显硬伤，影响基础可用性",
            "deduction": 35.0,
            "tags": ["硬伤"],
        },
        {
            "rule_id": "r2",
            "description": f"{label}表现同质化或套路化，缺少辨识度",
            "deduction": 20.0,
            "tags": ["同质化"],
        },
        {
            "rule_id": "r3",
            "description": f"{label}完成度或视觉感染力不足",
            "deduction": 15.0,
            "tags": ["完成度"],
        },
    ]


def _dimensions_from_specs(
    specs: list[dict[str, Any]], dimension_max: int
) -> list[dict[str, Any]]:
    """把人工校准规格转成业务原始权重维度定义。"""
    dimensions: list[dict[str, Any]] = []
    for spec in specs:
        dimensions.append({
            "key": spec["key"],
            "label": spec["label"],
            "weight": spec["weight"],
            "deduction_rules": [dict(rule) for rule in spec["rules"]],
            # @deprecated: retained only for configs created before the rule
            # deduction migration and for explicit legacy fallback tests.
            "grade_points": dict(_LINEAR_GRADE_POINTS),
        })
    expected = float(dimension_max) / 100.0
    actual = sum(float(dimension["weight"]) for dimension in dimensions)
    if abs(actual - expected) > 1e-9:
        raise ValueError(f"维度业务权重和必须为 {expected}，实际 {actual}")
    return dimensions


def _common_group(*, dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """承载一个赛道全部真实维度的单一 common group（group_weight=1.0）。

    schema_key 复用 ``SPACE_SCHEMA_KEY``、version 复用 ``ACTIVE_V13_VERSION`` 以对齐
    合同 ``dimension_schema_ref``（仅作引用标签，引擎不据此跨校维度 key）。组内维度
    weight 之和严格 = 1。
    """
    return {
        "group_weight": 1.0,
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": SPACE_SCHEMA_KEY,
            "version": ACTIVE_V13_VERSION,
            "dimensions": dimensions,
        },
    }


def _empty_specific_group() -> dict[str, Any]:
    """空的 specific group：0 维度、不保留 dimension_max 份额（引擎支持空组）。"""
    return {
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": "inspiration_specific",
            "version": "v1",
            "dimensions": [],
        },
    }


def build_inspiration_subcategory_dimensions() -> dict[str, dict[str, Any]]:
    """Build a ``subcategory-dimensions-v1`` config per track (真实 6/5 维度，方案 A).

    每赛道的全部真实维度放入单一 common_group（group_weight=1.0）：一类/二类 6 维度
    （visual_structure/color_aesthetics/emotional_expression/design_aesthetics/
    originality/design_trendiness，dimension_max=60），三类 5 维度（subject_focus/
    mood_atmosphere/composition_lighting/reference_value/visual_impact，
    dimension_max=30）。specific_group 置空——非空的 common_group 独占整块
    dimension_max。组内 weight 之和严格 = 1（末位吸收浮点漂移）。每个 config 通过
    ``validate_subcategory_dimensions``。返回 ``{track_key: config}``。
    """
    configs: dict[str, dict[str, Any]] = {}
    for track_key, specs in _TRACK_DIMENSION_SPECS.items():
        dimension_max = _TRACK_DIMENSION_MAX[track_key]
        configs[track_key] = {
            "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
            "sub_category_key": track_key,
            "dimension_max": dimension_max,
            "common_group": _common_group(
                dimensions=_dimensions_from_specs(specs, dimension_max)
            ),
            "specific_group": _empty_specific_group(),
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
