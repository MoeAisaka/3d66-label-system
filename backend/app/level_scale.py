"""Category-owned L-level scale with one global direction.

All categories share the machine meaning ``L1 = best`` and larger L numbers
mean lower quality.  A category may disable one or more of the canonical
``L1``..``L5`` buckets (for example use only L1-L4), rename their display
labels and move score cut-points.  It may not reverse the direction.

The module is pure and owns both backward-compatible ``level_thresholds``
parsing and the versioned ``level_scale`` contract.
"""

from __future__ import annotations

from typing import Any


DEFAULT_LEVEL_SCALE_VERSION = "category-level-scale-v1"
CANONICAL_LEVELS: tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5")
DEFAULT_THRESHOLDS: tuple[dict[str, Any], ...] = (
    {"min_score": 80, "level": "L1"},
    {"min_score": 60, "level": "L2"},
    {"min_score": 40, "level": "L3"},
    {"min_score": 20, "level": "L4"},
    {"min_score": 0, "level": "L5"},
)


class LevelScaleError(ValueError):
    """Stable fail-closed validation error for category level scales."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _level_index(level: str) -> int:
    return CANONICAL_LEVELS.index(level)


def _normalize_thresholds(
    thresholds: Any, *, source: str
) -> dict[str, Any]:
    if not isinstance(thresholds, (list, tuple)) or not thresholds:
        raise LevelScaleError("levels_invalid", "等级阈值必须是非空数组")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in thresholds:
        if not isinstance(entry, dict):
            raise LevelScaleError("level_invalid", "每个等级必须是对象")
        level = entry.get("level")
        min_score = entry.get("min_score")
        if level not in CANONICAL_LEVELS:
            raise LevelScaleError("level_unknown", "等级只能是 L1 至 L5")
        if level in seen:
            raise LevelScaleError("level_duplicate", f"等级重复：{level}")
        if not _is_int(min_score) or not 0 <= min_score <= 100:
            raise LevelScaleError(
                "min_score_invalid", f"{level}.min_score 必须是 0 至 100 的整数"
            )
        seen.add(level)
        normalized.append({"min_score": min_score, "level": level})

    normalized.sort(key=lambda item: _level_index(item["level"]))
    scores = [item["min_score"] for item in normalized]
    if any(left <= right for left, right in zip(scores, scores[1:])):
        raise LevelScaleError(
            "threshold_order_invalid", "等级阈值必须随 L 序号增大而严格下降"
        )
    if normalized[-1]["min_score"] != 0:
        raise LevelScaleError(
            "catch_all_missing", "最差的启用等级必须使用 min_score=0 兜底"
        )

    enabled = [item["level"] for item in normalized]
    return {
        "version": DEFAULT_LEVEL_SCALE_VERSION,
        "source": source,
        "enabled_levels": enabled,
        "disabled_levels": [level for level in CANONICAL_LEVELS if level not in seen],
        "thresholds": normalized,
        "display_names": {level: level for level in enabled},
    }


def _normalize_level_scale(scale: Any) -> dict[str, Any]:
    if not isinstance(scale, dict):
        raise LevelScaleError("scale_not_object", "level_scale 必须是对象")
    if scale.get("version") != DEFAULT_LEVEL_SCALE_VERSION:
        raise LevelScaleError(
            "version_unsupported",
            f"level_scale.version 必须是 {DEFAULT_LEVEL_SCALE_VERSION}",
        )
    levels = scale.get("levels")
    if not isinstance(levels, list) or not levels:
        raise LevelScaleError("levels_invalid", "level_scale.levels 必须是非空数组")

    seen: set[str] = set()
    enabled_entries: list[dict[str, Any]] = []
    display_names: dict[str, str] = {}
    disabled: list[str] = []
    for entry in levels:
        if not isinstance(entry, dict):
            raise LevelScaleError("level_invalid", "level_scale.levels 每项必须是对象")
        level = entry.get("level")
        if level not in CANONICAL_LEVELS:
            raise LevelScaleError("level_unknown", "等级只能是 L1 至 L5")
        if level in seen:
            raise LevelScaleError("level_duplicate", f"等级重复：{level}")
        seen.add(level)

        enabled = entry.get("enabled")
        if not isinstance(enabled, bool):
            raise LevelScaleError("enabled_invalid", f"{level}.enabled 必须是布尔值")
        display_name = entry.get("display_name", level)
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 40:
            raise LevelScaleError(
                "display_name_invalid", f"{level}.display_name 必须是 1 至 40 字符"
            )

        if not enabled:
            if entry.get("min_score") is not None:
                raise LevelScaleError(
                    "disabled_level_threshold_present",
                    f"停用等级 {level} 不允许配置 min_score",
                )
            disabled.append(level)
            continue

        min_score = entry.get("min_score")
        if not _is_int(min_score) or not 0 <= min_score <= 100:
            raise LevelScaleError(
                "min_score_invalid", f"{level}.min_score 必须是 0 至 100 的整数"
            )
        enabled_entries.append({"min_score": min_score, "level": level})
        display_names[level] = display_name.strip()

    missing = [level for level in CANONICAL_LEVELS if level not in seen]
    if missing:
        raise LevelScaleError(
            "levels_incomplete", f"level_scale 必须显式声明 L1 至 L5；缺少：{','.join(missing)}"
        )
    if not enabled_entries:
        raise LevelScaleError("no_enabled_level", "至少启用一个等级")

    normalized = _normalize_thresholds(enabled_entries, source="level_scale")
    normalized["disabled_levels"] = [
        level for level in CANONICAL_LEVELS if level not in normalized["enabled_levels"]
    ]
    normalized["display_names"] = display_names
    return normalized


def resolve_level_scale(contract: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized scale from a frozen category contract."""
    has_scale = contract.get("level_scale") is not None
    has_legacy = contract.get("level_thresholds") is not None
    if has_scale and has_legacy:
        raise LevelScaleError(
            "ambiguous_configuration",
            "level_scale 与旧 level_thresholds 不能同时配置",
        )
    if has_scale:
        return _normalize_level_scale(contract["level_scale"])
    if has_legacy:
        resolved = _normalize_thresholds(
            contract["level_thresholds"], source="legacy_level_thresholds"
        )
        # Historical threshold lists describe score buckets only.  Some active
        # contracts intentionally omit L5 from score mapping while reserving
        # it for redline output, so omission cannot be reinterpreted as an
        # explicit disabled level.  Only versioned ``level_scale`` can close a
        # level.
        resolved["enabled_levels"] = list(CANONICAL_LEVELS)
        resolved["disabled_levels"] = []
        resolved["display_names"] = {level: level for level in CANONICAL_LEVELS}
        return resolved
    return _normalize_thresholds(DEFAULT_THRESHOLDS, source="default")


def score_to_level(score: int, resolved_scale: dict[str, Any]) -> str:
    """Map a clamped score to an enabled level."""
    for entry in resolved_scale["thresholds"]:
        if score >= entry["min_score"]:
            return entry["level"]
    return resolved_scale["thresholds"][-1]["level"]


def is_level_enabled(level: Any, resolved_scale: dict[str, Any]) -> bool:
    return isinstance(level, str) and level in resolved_scale["enabled_levels"]
