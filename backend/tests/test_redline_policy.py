from __future__ import annotations

import pytest

from app.redline_policy import (
    REDLINE_POLICY_FORMAT_VERSION,
    RedlinePolicyError,
    evaluate_redlines,
    validate_redline_policy,
)
from app.schema_adapter import PRODUCTION_REASON_VALUES


# Inspiration-image first redline set: 截图 / 随手拍 / 大面积文字 / 二维码.
def _inspiration_policy() -> dict:
    return {
        "format_version": REDLINE_POLICY_FORMAT_VERSION,
        "enabled": True,
        "hit_level": "L5",
        "hit_score_cap": 49,
        "rules": [
            {
                "key": "screenshot",
                "signal": "production_fields.reason",
                "match_any": ["是截图"],
                "exemptions": [],
            },
            {
                "key": "casual_snapshot",
                "signal": "production_fields.reason",
                "match_any": ["是随手拍"],
                "exemptions": [],
            },
            {
                "key": "large_text",
                "signal": "production_fields.reason",
                "match_any": ["有大面积文字说明"],
                "exemptions": [],
            },
            {
                "key": "qr_code",
                "signal": "production_fields.reason",
                "match_any": ["有二维码"],
                "exemptions": [],
            },
        ],
    }


def _precheck(reasons: list[str]) -> dict:
    return {"production_fields": {"reason": list(reasons)}}


def test_all_four_inspiration_redline_reasons_are_valid_enum_values() -> None:
    assert {
        "是截图",
        "是随手拍",
        "有大面积文字说明",
        "有二维码",
    } <= PRODUCTION_REASON_VALUES


def test_valid_policy_passes_validation() -> None:
    assert validate_redline_policy(_inspiration_policy()) is None


def test_hit_returns_hard_reject_with_level_and_cap() -> None:
    result = evaluate_redlines(_precheck(["是截图"]), policy=_inspiration_policy())
    assert result == {
        "hit": True,
        "hit_rules": ["screenshot"],
        "hit_level": "L5",
        "hit_score_cap": 49,
        "hard_reject": True,
    }


def test_no_matching_reason_does_not_hit() -> None:
    result = evaluate_redlines(_precheck(["是多拼图"]), policy=_inspiration_policy())
    assert result == {"hit": False, "hit_rules": [], "hard_reject": False}


def test_missing_reason_field_is_treated_as_empty() -> None:
    assert evaluate_redlines({}, policy=_inspiration_policy()) == {
        "hit": False,
        "hit_rules": [],
        "hard_reject": False,
    }
    assert evaluate_redlines(
        {"production_fields": {"reason": "是截图"}}, policy=_inspiration_policy()
    ) == {"hit": False, "hit_rules": [], "hard_reject": False}


def test_multiple_hits_return_ordered_deduped_keys() -> None:
    result = evaluate_redlines(
        _precheck(["有二维码", "是截图", "是截图"]),
        policy=_inspiration_policy(),
    )
    assert result["hit"] is True
    # Order follows policy rule order, not reason order; deduped.
    assert result["hit_rules"] == ["screenshot", "qr_code"]


def test_disabled_policy_short_circuits_without_hit() -> None:
    policy = _inspiration_policy()
    policy["enabled"] = False
    result = evaluate_redlines(_precheck(["是截图"]), policy=policy)
    assert result == {"hit": False, "hit_rules": [], "hard_reject": False}


def test_exemption_suppresses_a_hit() -> None:
    policy = _inspiration_policy()
    # large_text is exempted when the collage reason evidence co-occurs.
    policy["rules"][2]["exemptions"] = ["是多拼图"]

    # large_text matches on 有大面积文字说明 but the exemption evidence 是多拼图
    # is present, so that rule does not count as a hit.
    exempted = evaluate_redlines(
        _precheck(["有大面积文字说明", "是多拼图"]),
        policy=policy,
    )
    assert exempted == {"hit": False, "hit_rules": [], "hard_reject": False}

    # Without the exemption evidence present, the same reason still hits.
    not_exempted = evaluate_redlines(_precheck(["有大面积文字说明"]), policy=policy)
    assert not_exempted["hit"] is True
    assert not_exempted["hit_rules"] == ["large_text"]

    # Exemption applies per-rule: qr_code still hits even when large_text is exempted.
    mixed = evaluate_redlines(
        _precheck(["有大面积文字说明", "是多拼图", "有二维码"]),
        policy=policy,
    )
    assert mixed["hit_rules"] == ["qr_code"]


def test_evaluation_is_deterministic_for_same_input() -> None:
    policy = _inspiration_policy()
    precheck = _precheck(["是随手拍", "有二维码"])
    first = evaluate_redlines(precheck, policy=policy)
    second = evaluate_redlines(precheck, policy=policy)
    third = evaluate_redlines(precheck, policy=policy)
    assert first == second == third


def test_unsupported_signal_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["signal"] = "media_form.real_photo"
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "signal_unsupported"


def test_empty_rules_with_enabled_true_is_valid_and_never_hits() -> None:
    # Redline rules can be freely reduced to zero; an enabled stage with no
    # rules is legal and simply never eliminates anything.
    policy = _inspiration_policy()
    policy["rules"] = []
    assert validate_redline_policy(policy) is None
    result = evaluate_redlines(_precheck(["是截图"]), policy=policy)
    assert result == {"hit": False, "hit_rules": [], "hard_reject": False}


def test_empty_rules_allowed_when_disabled() -> None:
    policy = _inspiration_policy()
    policy["enabled"] = False
    policy["rules"] = []
    assert validate_redline_policy(policy) is None


def test_per_rule_disable_skips_that_rule() -> None:
    # A rule can be kept but toggled off via enabled=false; it must not hit.
    policy = _inspiration_policy()
    policy["rules"][0]["enabled"] = False  # disable screenshot
    disabled = evaluate_redlines(_precheck(["是截图"]), policy=policy)
    assert disabled == {"hit": False, "hit_rules": [], "hard_reject": False}
    # Other rules still fire; disabling one does not affect the rest.
    other = evaluate_redlines(_precheck(["是截图", "有二维码"]), policy=policy)
    assert other["hit"] is True
    assert other["hit_rules"] == ["qr_code"]


def test_all_rules_disabled_never_hits() -> None:
    policy = _inspiration_policy()
    for rule in policy["rules"]:
        rule["enabled"] = False
    assert validate_redline_policy(policy) is None
    result = evaluate_redlines(_precheck(["是截图", "有二维码"]), policy=policy)
    assert result == {"hit": False, "hit_rules": [], "hard_reject": False}


def test_explicit_enabled_true_rule_still_hits() -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["enabled"] = True
    result = evaluate_redlines(_precheck(["是截图"]), policy=policy)
    assert result["hit"] is True
    assert result["hit_rules"] == ["screenshot"]


def test_non_bool_rule_enabled_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["enabled"] = "yes"
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "rule_enabled_invalid"


def test_duplicate_rule_key_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["rules"][1]["key"] = "screenshot"
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "rule_key_duplicate"


def test_contract_owned_reason_value_is_not_blocked_by_platform_enum() -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["match_any"] = ["透明棋盘格"]

    assert validate_redline_policy(policy) is None
    result = evaluate_redlines(_precheck(["透明棋盘格"]), policy=policy)
    assert result["hit"] is True
    assert result["hit_rules"] == ["screenshot"]


@pytest.mark.parametrize("value", [None, 3, True, ""])
def test_malformed_reason_value_in_match_any_fails_closed(value: object) -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["match_any"] = [value]
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "match_value_invalid"


def test_invalid_hit_level_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["hit_level"] = "L6"
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "hit_level_invalid"


@pytest.mark.parametrize("cap", [-1, 101, 49.0, True, "49"])
def test_out_of_range_or_non_int_hit_score_cap_fails_closed(cap: object) -> None:
    policy = _inspiration_policy()
    policy["hit_score_cap"] = cap
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "hit_score_cap_out_of_range"


def test_bad_match_any_shape_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["rules"][0]["match_any"] = []
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "match_any_empty"


def test_bad_format_version_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["format_version"] = "redline-policy-v2"
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "format_version_unsupported"


def test_evaluate_validates_policy_before_matching() -> None:
    policy = _inspiration_policy()
    policy["hit_level"] = "bad"
    with pytest.raises(RedlinePolicyError):
        evaluate_redlines(_precheck(["是截图"]), policy=policy)


def test_casual_snapshot_requires_documented_perspective_or_composition_defect() -> None:
    policy = _inspiration_policy()
    policy["rules"][1]["requires_any_hard_defect"] = [
        "careless_composition",
        "distorted_viewpoint",
        "fisheye_distortion",
    ]
    casual_only = evaluate_redlines(
        {
            "production_fields": {"reason": ["是随手拍"]},
            "hard_defects": [],
        },
        policy=policy,
    )
    assert casual_only == {"hit": False, "hit_rules": [], "hard_reject": False}
    disorderly = evaluate_redlines(
        {
            "production_fields": {"reason": ["是随手拍"]},
            "hard_defects": ["careless_composition"],
        },
        policy=policy,
    )
    assert disorderly["hit"] is True
    assert disorderly["hit_rules"] == ["casual_snapshot"]


def test_unknown_required_hard_defect_fails_closed() -> None:
    policy = _inspiration_policy()
    policy["rules"][1]["requires_any_hard_defect"] = ["unknown_defect"]
    with pytest.raises(RedlinePolicyError) as excinfo:
        validate_redline_policy(policy)
    assert excinfo.value.code == "requires_any_hard_defect_invalid"
