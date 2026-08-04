"""ADR-0033 老类目 v3 **占位** config builder（能力就位、维度留空待补）。

产品决策（2026-08-04）：老类目（space_image / material_image / pdf_text）转 v3 时，
不预先定义各自的维度体系，而是先落一份 **8 维占位** v3 config、``status=draft``，
上线后由实验台使用者在前端「类目评测 v3 合同配置」页自由增删/改维度、红线、赛道，
补齐后自行激活。draft 状态下 worker 的 v3 权威闸门返回 None，老类目继续走未改动的
v1 引擎——**建占位=零生产风险**，激活是使用者的显式操作。

本模块是**纯函数**（无 IO/网络/DB/模型），复用 inspiration 冻结 builder 的红线/赛道/
媒介合同结构（只换 ``category_key``），维度换成 8 维等权线性占位。产出全部通过 CRUD API
所用的同一批确定性校验器。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .category_evaluation_contract import (
    validate_category_evaluation_contract,
)
from .dimension_composition import (
    SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
    validate_subcategory_dimensions,
)
from .dimension_schema_registry import ACTIVE_V13_VERSION, SPACE_SCHEMA_KEY
from .inspiration_category_seed import (
    TRACK_CLASS_ONE,
    TRACK_CLASS_THREE,
    TRACK_CLASS_TWO,
    build_inspiration_classification_map,
    build_inspiration_v3_contract,
)
from .subcategory_resolver import validate_classification_map


# 8 维占位（复用 space v13 的 8 个维度 key，等权）。使用者可在前端整组替换。
_PLACEHOLDER_DIMENSION_KEYS: list[tuple[str, str]] = [
    ("composition_viewpoint", "构图与视角"),
    ("lighting_atmosphere", "光影与氛围"),
    ("color_material", "色彩与材质"),
    ("spatial_design_furnishing", "空间设计与陈设"),
    ("visual_hierarchy", "视觉层次"),
    ("detail_completion", "细节与完成度"),
    ("inspiration_reference", "灵感参考价值"),
    ("presentation_integrity", "呈现完整性"),
]

# 线性锚点：grade5=满分不扣、grade1=全扣该维度 share。
_LINEAR_GRADE_POINTS = {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0}

# 每赛道 dimension_max 与 inspiration 合同一致（一类/二类 60，三类 30）。
_TRACK_DIMENSION_MAX = {
    TRACK_CLASS_ONE: 60,
    TRACK_CLASS_TWO: 60,
    TRACK_CLASS_THREE: 30,
}


def _placeholder_dimensions() -> list[dict[str, Any]]:
    """8 维等权占位；末位吸收浮点漂移使组内 weight 之和严格 = 1。"""
    count = len(_PLACEHOLDER_DIMENSION_KEYS)
    weight = 1.0 / float(count)
    dimensions: list[dict[str, Any]] = []
    for key, label in _PLACEHOLDER_DIMENSION_KEYS:
        dimensions.append({
            "key": key,
            "label": label,
            "weight": weight,
            "grade_points": dict(_LINEAR_GRADE_POINTS),
        })
    drift = 1.0 - sum(dimension["weight"] for dimension in dimensions)
    dimensions[-1]["weight"] += drift
    return dimensions


def _placeholder_common_group() -> dict[str, Any]:
    return {
        "group_weight": 1.0,
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": SPACE_SCHEMA_KEY,
            "version": ACTIVE_V13_VERSION,
            "dimensions": _placeholder_dimensions(),
        },
    }


def _empty_specific_group() -> dict[str, Any]:
    return {
        "schema_definition": {
            "format_version": "dimension-schema-definition-v1",
            "schema_key": "legacy_placeholder_specific",
            "version": "v1",
            "dimensions": [],
        },
    }


def build_placeholder_v3_contract(category_key: str) -> dict[str, Any]:
    """老类目占位合同：复用 inspiration 的红线/赛道/媒介结构，只换 category_key。"""
    contract = deepcopy(build_inspiration_v3_contract())
    contract["category_key"] = category_key
    validate_category_evaluation_contract(contract)
    return contract


def build_placeholder_classification_map() -> dict[str, Any]:
    """占位分类映射：复用 inspiration 的一级分类→赛道映射（使用者后续可改）。"""
    return build_inspiration_classification_map()


def build_placeholder_subcategory_dimensions() -> dict[str, dict[str, Any]]:
    """每赛道 8 维等权占位 common group + 空 specific group。"""
    configs: dict[str, dict[str, Any]] = {}
    for track_key, dimension_max in _TRACK_DIMENSION_MAX.items():
        config = {
            "format_version": SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
            "sub_category_key": track_key,
            "dimension_max": dimension_max,
            "common_group": _placeholder_common_group(),
            "specific_group": _empty_specific_group(),
        }
        validate_subcategory_dimensions(config)
        configs[track_key] = config
    return configs


def build_placeholder_v3_config(category_key: str) -> dict[str, Any]:
    """一次产出并自校验占位 config 的三件套 + track keys（供 seed 落库）。"""
    contract = build_placeholder_v3_contract(category_key)
    classification_map = build_placeholder_classification_map()
    subcategory_dimensions = build_placeholder_subcategory_dimensions()
    validate_classification_map(
        classification_map,
        valid_track_keys={
            track["key"] for track in contract["track_classification"]["tracks"]
        },
    )
    return {
        "contract": contract,
        "classification_map": classification_map,
        "subcategory_dimensions": subcategory_dimensions,
    }
