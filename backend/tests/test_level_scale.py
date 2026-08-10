from __future__ import annotations

import pytest

from app.level_scale import (
    DEFAULT_LEVEL_SCALE_VERSION,
    LevelScaleError,
    resolve_level_scale,
    score_to_level,
)


def _four_level_scale() -> dict:
    return {
        "version": DEFAULT_LEVEL_SCALE_VERSION,
        "levels": [
            {"level": "L1", "enabled": True, "min_score": 80, "display_name": "优选"},
            {"level": "L2", "enabled": True, "min_score": 60, "display_name": "良好"},
            {"level": "L3", "enabled": True, "min_score": 40, "display_name": "常规"},
            {"level": "L4", "enabled": True, "min_score": 0, "display_name": "过滤"},
            {"level": "L5", "enabled": False, "display_name": "停用"},
        ],
    }


def test_missing_scale_keeps_default_five_levels() -> None:
    resolved = resolve_level_scale({})
    assert resolved["enabled_levels"] == ["L1", "L2", "L3", "L4", "L5"]
    assert resolved["thresholds"][-1] == {"min_score": 0, "level": "L5"}
    assert score_to_level(0, resolved) == "L5"


def test_category_can_disable_l5_and_make_l4_the_catch_all() -> None:
    resolved = resolve_level_scale({"level_scale": _four_level_scale()})
    assert resolved["enabled_levels"] == ["L1", "L2", "L3", "L4"]
    assert resolved["disabled_levels"] == ["L5"]
    assert resolved["thresholds"][-1] == {"min_score": 0, "level": "L4"}
    assert score_to_level(0, resolved) == "L4"
    assert score_to_level(39, resolved) == "L4"
    assert "L5" not in {score_to_level(score, resolved) for score in range(101)}


def test_category_can_disable_a_middle_level_without_reversing_direction() -> None:
    scale = _four_level_scale()
    scale["levels"][1] = {"level": "L2", "enabled": False, "display_name": "停用"}
    scale["levels"][2]["min_score"] = 60
    resolved = resolve_level_scale({"level_scale": scale})
    assert resolved["enabled_levels"] == ["L1", "L3", "L4"]
    assert [item["level"] for item in resolved["thresholds"]] == ["L1", "L3", "L4"]


def test_legacy_level_thresholds_remain_read_compatible() -> None:
    resolved = resolve_level_scale(
        {
            "level_thresholds": [
                {"min_score": 75, "level": "L1"},
                {"min_score": 45, "level": "L2"},
                {"min_score": 0, "level": "L3"},
            ]
        }
    )
    assert resolved["source"] == "legacy_level_thresholds"
    assert resolved["enabled_levels"] == ["L1", "L2", "L3", "L4", "L5"]
    assert resolved["disabled_levels"] == []
    assert score_to_level(44, resolved) == "L3"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda scale: scale.update(version="unknown"), "version_unsupported"),
        (lambda scale: scale.update(levels=[]), "levels_invalid"),
        (
            lambda scale: scale["levels"].append(
                {"level": "L1", "enabled": True, "min_score": 10}
            ),
            "level_duplicate",
        ),
        (
            lambda scale: scale["levels"][4].update(enabled=True, min_score=10),
            "threshold_order_invalid",
        ),
        (
            lambda scale: scale["levels"][3].update(min_score=1),
            "catch_all_missing",
        ),
        (
            lambda scale: scale["levels"][4].update(min_score=0),
            "disabled_level_threshold_present",
        ),
    ],
)
def test_invalid_scale_fails_closed(mutate, code: str) -> None:
    scale = _four_level_scale()
    mutate(scale)
    with pytest.raises(LevelScaleError) as excinfo:
        resolve_level_scale({"level_scale": scale})
    assert excinfo.value.code == code


def test_level_scale_and_legacy_thresholds_cannot_coexist() -> None:
    with pytest.raises(LevelScaleError) as excinfo:
        resolve_level_scale(
            {
                "level_scale": _four_level_scale(),
                "level_thresholds": [{"min_score": 0, "level": "L1"}],
            }
        )
    assert excinfo.value.code == "ambiguous_configuration"


def test_enabled_level_display_names_are_preserved() -> None:
    resolved = resolve_level_scale({"level_scale": _four_level_scale()})
    assert resolved["display_names"] == {
        "L1": "优选",
        "L2": "良好",
        "L3": "常规",
        "L4": "过滤",
    }
