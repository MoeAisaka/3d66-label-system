"""锚点图机制：只负责「哪些图片代表哪个等级」。

架构约束（2026-08-24 裁决，不得回退）
====================================
本模块唯一职责是各等级的锚点参照图片。严禁混入任何其它机制：

============================  =====================================
不属于本模块的东西            它的正确归属
============================  =====================================
分数到等级的阈值              合同顶层 ``level_scale``
维度定义与权重                Call B 的 ``dimensions``
红线与硬伤封顶                Call A ``authoritative_precheck_contract``
                              与顶层 ``redline_policy``
随手拍软封顶／硬伤豁免        同上（属于扣分与封顶层）
边界下沉等兜底策略            同上
============================  =====================================

为什么必须隔离：一个模块同时管锚图和阈值／封顶时，调试就失去可归因性——
换一张锚图后看到分数变化，无法判断是参照图起了作用，还是模块内部那套阈值
或封顶规则起了作用。运营要的是「换一张锚图，只有锚图这一个变量在动」。

反面样本是 ``inspiration_aesthetic_foundation.py``（合同块 ``aesthetic_foundation``）：
一个模块里塞了八维定义、自带阈值、软封顶、硬伤豁免、边界策略，且前端零编辑
入口，于是既没法自定义也没法调试。

``assert_anchor_mechanism_isolated`` 是这条约束的机器化守卫，配套测试会在混入
外来机制时失败。改动本模块前先读这段注释。
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .inspiration_anchor_contract import (
    ANCHOR_LEVELS,
    ANCHOR_MIME_TYPES,
    SHA256_PATTERN,
    InspirationAnchorContractError,
)

# 合同块名与规格版本
ANCHOR_MECHANISM_KEY = "anchor_mechanism"
ANCHOR_MECHANISM_SPEC_VERSION = "anchor-mechanism-v1"

# 块内允许出现的键——白名单，多一个都不行
ANCHOR_MECHANISM_KEYS = frozenset({
    "spec_version",
    "enabled",
    "max_anchor_images",
    "anchors",
})

# 单条锚图允许的键
ANCHOR_ITEM_KEYS = frozenset({
    "level",
    "asset_id",
    "mime_type",
    "sha256",
    "note",
})

# 送图数量上限的上限（防止把整个素材库塞进提示词）
MAX_ANCHOR_IMAGES_CEILING = 20

# 外来机制键 → 正确归属。守卫命中时直接把去处告诉调用方。
FOREIGN_MECHANISM_KEYS: dict[str, str] = {
    # 阈值层
    "score_thresholds": "合同顶层 level_scale",
    "level_thresholds": "合同顶层 level_scale",
    "thresholds": "合同顶层 level_scale",
    "level_scale": "合同顶层 level_scale",
    # 维度层
    "dimension_keys": "Call B 的 dimensions",
    "dimensions": "Call B 的 dimensions",
    "dimension_scoring_mode": "Call B 的 dimensions",
    "weights": "Call B 的 dimensions（每维 weight）",
    # 红线与封顶层
    "redline_policy": "Call A 与顶层 redline_policy",
    "redlines": "Call A 与顶层 redline_policy",
    "hard_defect_exemptions": "Call A 与顶层 redline_policy",
    "hard_defect_cap": "Call A 与顶层 redline_policy",
    "score_cap": "Call A 与顶层 redline_policy",
    "casual_snapshot_soft_cap": "Call A 与顶层 redline_policy",
    "quality_rules": "Call A 与顶层 redline_policy",
    # 兜底策略层
    "boundary_policy": "Call A 与顶层 redline_policy",
    "fallback_policy": "Call A 与顶层 redline_policy",
    "floor_to_lower_band": "Call A 与顶层 redline_policy",
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def assert_anchor_mechanism_isolated(block: object) -> None:
    """守卫：锚点机制块里出现任何外来机制就 fail-closed。

    这是 2026-08-24 架构裁决的机器化执行点。命中时的报错会直接说明该键的正确
    归属，避免下一个人重新把规则塞回来。
    """
    if not isinstance(block, Mapping):
        return
    intruders: list[str] = []
    for key in block:
        if not isinstance(key, str):
            continue
        home = FOREIGN_MECHANISM_KEYS.get(key)
        if home is not None:
            intruders.append(f"{key}（应放在{home}）")
    if intruders:
        raise InspirationAnchorContractError(
            "anchor_mechanism_not_isolated",
            "锚点图机制只能承载各等级锚点图片，不得混入其它机制："
            + "、".join(sorted(intruders))
            + "。混入会让调试失去可归因性：换锚图后分数变化无法定位来源。",
        )


def validate_anchor_mechanism(contract: object) -> dict[str, Any] | None:
    """校验并规范化合同里的锚点机制块。

    只做形状与完整性校验，不做任何定级判断。缺块返回 ``None``（机制未启用）。
    """
    if not isinstance(contract, Mapping):
        return None
    raw = contract.get(ANCHOR_MECHANISM_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid", "锚点图机制必须是对象"
        )

    assert_anchor_mechanism_isolated(raw)

    unknown = sorted(set(raw) - ANCHOR_MECHANISM_KEYS)
    if unknown:
        raise InspirationAnchorContractError(
            "anchor_mechanism_unknown_field",
            f"锚点图机制含未知字段：{'、'.join(map(str, unknown))}",
        )

    spec_version = raw.get("spec_version", ANCHOR_MECHANISM_SPEC_VERSION)
    if spec_version != ANCHOR_MECHANISM_SPEC_VERSION:
        raise InspirationAnchorContractError(
            "anchor_mechanism_spec_version_invalid",
            f"锚点图机制规格版本必须为 {ANCHOR_MECHANISM_SPEC_VERSION}",
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid", "锚点图机制 enabled 必须是布尔值"
        )

    max_images = raw.get("max_anchor_images", len(ANCHOR_LEVELS))
    if not _is_int(max_images) or max_images < 1:
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid", "max_anchor_images 必须是正整数"
        )
    if max_images > MAX_ANCHOR_IMAGES_CEILING:
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid",
            f"max_anchor_images 不得超过 {MAX_ANCHOR_IMAGES_CEILING}",
        )

    anchors = _validated_anchors(raw.get("anchors"), max_images=max_images)
    if enabled and not anchors:
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid", "启用锚点图机制时至少要配一张锚点图片"
        )

    return {
        "spec_version": ANCHOR_MECHANISM_SPEC_VERSION,
        "enabled": enabled,
        "max_anchor_images": max_images,
        "anchors": anchors,
    }


def _validated_anchors(
    raw: object, *, max_images: int
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid", "anchors 必须是数组"
        )
    if len(raw) > max_images:
        raise InspirationAnchorContractError(
            "anchor_mechanism_invalid",
            f"锚点图片数 {len(raw)} 超过 max_anchor_images={max_images}",
        )

    seen_assets: set[int] = set()
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid", f"第 {index + 1} 条锚点图必须是对象"
            )
        unknown = sorted(set(entry) - ANCHOR_ITEM_KEYS)
        if unknown:
            raise InspirationAnchorContractError(
                "anchor_mechanism_unknown_field",
                f"第 {index + 1} 条锚点图含未知字段：{'、'.join(map(str, unknown))}",
            )

        level = entry.get("level")
        if level not in ANCHOR_LEVELS:
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid",
                f"第 {index + 1} 条锚点图等级必须是 {'/'.join(ANCHOR_LEVELS)} 之一",
            )

        asset_id = entry.get("asset_id")
        if not _is_int(asset_id) or asset_id < 1:
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid",
                f"第 {index + 1} 条锚点图 asset_id 必须是正整数",
            )
        if asset_id in seen_assets:
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid",
                f"素材 {asset_id} 被重复配为锚点图",
            )
        seen_assets.add(asset_id)

        mime_type = entry.get("mime_type")
        if mime_type not in ANCHOR_MIME_TYPES:
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid",
                f"第 {index + 1} 条锚点图 mime_type 必须是 "
                f"{'/'.join(sorted(ANCHOR_MIME_TYPES))} 之一",
            )

        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or not SHA256_PATTERN.match(sha256):
            raise InspirationAnchorContractError(
                "anchor_mechanism_invalid",
                f"第 {index + 1} 条锚点图 sha256 必须是 64 位小写十六进制",
            )

        item: dict[str, Any] = {
            "level": level,
            "asset_id": asset_id,
            "mime_type": mime_type,
            "sha256": sha256,
        }
        note = entry.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise InspirationAnchorContractError(
                    "anchor_mechanism_invalid",
                    f"第 {index + 1} 条锚点图 note 必须是字符串",
                )
            trimmed = note.strip()
            if trimmed:
                item["note"] = trimmed
        items.append(item)

    # 按等级序、同级按 asset_id 排序，保证送图顺序确定
    order = {level: rank for rank, level in enumerate(ANCHOR_LEVELS)}
    items.sort(key=lambda it: (order[it["level"]], it["asset_id"]))
    return items


def anchor_mechanism_request(
    contract: object,
    target: Path,
    target_mime: str | None,
    *,
    assets_by_id: Mapping[int, object],
    asset_path_resolver: Callable[[object], Path],
) -> tuple[list[tuple[str, Path, str | None]], int] | None:
    """按锚点机制块装配 Call B 的图片载荷。

    机制缺失、关闭或没有锚点图时返回 ``None``，调用方据此走无锚图路径。

    这里**故意不复用** ``inspiration_aesthetic_foundation.anchor_samples``：那条
    路径内部会调 ``validate_anchor_contract``，它要求 Owner 锚图必须 L1→L4 或
    L1→L5 齐全且有序、不许替换——正是本次拆分要解开的限制。实测该校验会拒绝
    「只配两档」「换掉 L3 的图」「乱序」这三种运营必需的操作，所以锚点机制
    自带装载器，只保留内容身份校验（sha256），不再约束等级组合。
    """
    view = validate_anchor_mechanism(contract)
    if view is None or not view["enabled"] or not view["anchors"]:
        return None

    samples: list[tuple[str, Path, str | None]] = []
    for anchor in view["anchors"]:
        asset_id = int(anchor["asset_id"])
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise InspirationAnchorContractError(
                "anchor_asset_missing", f"锚点图素材 {asset_id} 不在库中"
            )
        try:
            path = asset_path_resolver(asset)
        except Exception as exc:  # noqa: BLE001 - 来源不可用统一转成合同错误
            raise InspirationAnchorContractError(
                "anchor_source_unavailable", f"锚点图素材 {asset_id} 来源不可用"
            ) from exc
        if not path.is_file():
            raise InspirationAnchorContractError(
                "anchor_missing", f"锚点图素材 {asset_id} 文件不存在"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != anchor["sha256"]:
            raise InspirationAnchorContractError(
                "anchor_hash_mismatch",
                f"锚点图素材 {asset_id} 内容哈希不匹配，图片可能已被替换",
            )
        label = f"锚点图 {anchor['level']}（素材 {asset_id}）"
        note = anchor.get("note")
        if note:
            label = f"{label}：{note}"
        samples.append((label, path, anchor["mime_type"]))

    samples.append(("待评图片（禁止把锚点图等级直接当作输出）", target, target_mime))
    return samples, len(samples)


def anchor_levels_covered(block: object) -> list[str]:
    """返回已配锚图的等级，按 L1→L5 排序。供前端与回归报告展示覆盖情况。"""
    if not isinstance(block, Mapping):
        return []
    anchors = block.get("anchors")
    if not isinstance(anchors, Sequence):
        return []
    covered = {
        entry.get("level")
        for entry in anchors
        if isinstance(entry, Mapping) and entry.get("level") in ANCHOR_LEVELS
    }
    return [level for level in ANCHOR_LEVELS if level in covered]
