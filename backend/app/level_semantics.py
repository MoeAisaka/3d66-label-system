"""ADR-0033 Task 2 安全脚手架：level 语义版本标记（**只读脚手架，绝不翻转数据**）。

现状 v1 的 ``scoring.py`` L 方向是 **L5=最高分**；ADR-0033 v3 是 **L5=最差**。两套
语义相反且必须并存。全局翻转已发布 PublishedLabel 是最高风险动作——**本模块坚决不做
翻转**，只提供**版本标记与纯数据说明**，让未来能双轨共存、可区分某条 level 属于哪套
语义。

本模块**故意不提供**任何「翻转 / 转换 level」的函数——那属于未来的门禁任务，不在此。
"""

from __future__ import annotations

from typing import Any

# v3 语义：直接复用聚合器里已存在的常量（值为 "doc-l5-worst-v1"，L5=最差），
# **import 复用、不重新定义**，避免两处漂移。
from .category_evaluation_aggregator import (
    LEVEL_SEMANTICS_VERSION as LEVEL_SEMANTICS_V3_L5_WORST,
)

# 现状 v1 语义：L5 是最高分档。此常量是**新命名**——v1 之前从未显式命名过它的语义
# 版本，Task 2 如实给它打上标签，不改任何算分或已发布 level 值。
LEVEL_SEMANTICS_V1_L5_BEST = "v1-l5-best"


def describe_level_semantics(version: str) -> dict[str, Any]:
    """返回某语义版本下 L1..L5 的方向说明（纯数据、无副作用、fail-closed）。

    - ``v1-l5-best``：L5 最优、L1 最差（现状 v1 scoring 方向）。
    - ``doc-l5-worst-v1``：L5 最差、L1 最优（ADR-0033 v3 doc 方向）。
    - 其它/未知版本：返回 ``{"version": ..., "known": False, ...}``，不抛错
      （fail-closed：调用方拿到 ``known=False`` 而非异常）。

    返回结构固定：``{"version", "known", "best_level", "worst_level",
    "direction", "levels"}``；``levels`` 是 ``{"L1".."L5": "best"|"worst"|"mid"}``。
    """
    if version == LEVEL_SEMANTICS_V1_L5_BEST:
        return {
            "version": version,
            "known": True,
            "best_level": "L5",
            "worst_level": "L1",
            "direction": "L5=最优, L1=最差",
            "levels": {
                "L1": "worst",
                "L2": "mid",
                "L3": "mid",
                "L4": "mid",
                "L5": "best",
            },
        }
    if version == LEVEL_SEMANTICS_V3_L5_WORST:
        return {
            "version": version,
            "known": True,
            "best_level": "L1",
            "worst_level": "L5",
            "direction": "L1=最优, L5=最差",
            "levels": {
                "L1": "best",
                "L2": "mid",
                "L3": "mid",
                "L4": "mid",
                "L5": "worst",
            },
        }
    return {
        "version": version,
        "known": False,
        "best_level": None,
        "worst_level": None,
        "direction": "unknown",
        "levels": {},
    }
