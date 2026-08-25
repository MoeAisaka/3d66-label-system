"""质量规则（quality_rules 块）在无基座聚合路径上的消费测试。

旧基座 aesthetic_foundation 已拆分：软封顶与硬伤例外挪进运营可配置的
``quality_rules`` 块。生产合同普遍不带基座、走 ``aggregate_category_evaluation``，
所以该块必须在这条路径真实生效，而不是只在带基座的旧修订上生效。
"""
from __future__ import annotations

import pytest

from app.category_evaluation_aggregator import (
    CategoryEvaluationAggregatorError,
    aggregate_category_evaluation,
)
from app.category_evaluation_contract import CATEGORY_EVALUATION_CONTRACT_VERSION
from app.redline_policy import REDLINE_POLICY_FORMAT_VERSION


def _redline_policy() -> dict:
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
            }
        ],
    }


def _track_classification() -> dict:
    return {
        "format_version": "track-classification-v1",
        "tracks": [
            {
                "key": "class_one",
                "label": "一类",
                "base_score": 40,
                "dimension_max": 60,
                "track_cap": 100,
                "dimension_schema_ref": {"schema_key": "space_aesthetic", "version": "1.3.0"},
            },
        ],
        "default_track": "class_one",
    }


def _common_modifiers_v1() -> dict:
    return {
        "format_version": "common-modifiers-v1",
        "media_type_penalty": {
            "baseline": "real_photo",
            "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0},
        },
        "high_score_veto": {"threshold": 80, "cap_to": 79},
    }


def _common_modifiers_v2() -> dict:
    return {
        "format_version": "common-modifiers-v2",
        "media_type_penalty": {
            "baseline": "real_photo",
            "penalties": {"real_photo": 0, "render_3d": -5, "ai_image": -15, "other": 0},
        },
        "high_score_veto": {
            "policy_version": "hard-defect-policy-v2",
            "tiers": {
                "severe": {"action": "cap", "cap_to": 59},
                "record": {"action": "record_only"},
            },
            "rules": [
                {
                    "key": "subject_obscuring_watermark",
                    "source": "image_defects",
                    "severity": "severe",
                    "description": "主体被水印遮挡",
                },
            ],
        },
    }


def _contract(*, modifiers: dict | None = None) -> dict:
    return {
        "schema_version": CATEGORY_EVALUATION_CONTRACT_VERSION,
        "redline_policy": _redline_policy(),
        "track_classification": _track_classification(),
        "common_modifiers": modifiers or _common_modifiers_v1(),
    }


def _level_scale() -> dict:
    return {
        "version": "category-level-scale-v1",
        "levels": [
            {"level": "L1", "enabled": True, "min_score": 90},
            {"level": "L2", "enabled": True, "min_score": 75},
            {"level": "L3", "enabled": True, "min_score": 60},
            {"level": "L4", "enabled": True, "min_score": 40},
            {"level": "L5", "enabled": True, "min_score": 0},
        ],
    }


def _precheck(*, reason=None, image_defects=None) -> dict:
    precheck: dict = {
        "production_fields": {"trait": "实景照片", "reason": list(reason or [])},
        "decisive_evidence": {"notes": "画面中央出现品牌文字水印"},
    }
    if image_defects is not None:
        precheck["image_defects"] = image_defects
    return precheck


def _snapshot_limit(**overrides) -> dict:
    block = {
        "enabled": True,
        "name": "随手拍限分",
        "when_reason_contains": ["是随手拍"],
        "max_score": 59,
    }
    block.update(overrides)
    return block


def test_soft_cap_max_score_applies_on_reason_hit() -> None:
    contract = _contract()
    contract["quality_rules"] = {"enabled": True, "snapshot_limit": _snapshot_limit()}
    result = aggregate_category_evaluation(
        contract,
        _precheck(reason=["是随手拍"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 59
    assert result["quality_rules_evidence"]["soft_cap_applied"] is True
    assert any(c.get("cap") == "casual_snapshot_soft_cap" for c in result["caps"])


def test_soft_cap_accepts_operator_defined_keywords() -> None:
    """关键词可自定义，不限于「是随手拍」——这是拆分要换来的能力。"""
    contract = _contract()
    contract["quality_rules"] = {
        "enabled": True,
        "snapshot_limit": _snapshot_limit(when_reason_contains=["工地照", "手机快照"]),
    }
    result = aggregate_category_evaluation(
        contract,
        _precheck(reason=["手机快照"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 59


def test_soft_cap_max_level_resolves_threshold_upper_bound() -> None:
    contract = _contract()
    contract["level_scale"] = _level_scale()
    contract["quality_rules"] = {
        "enabled": True,
        "snapshot_limit": _snapshot_limit(max_score=None, max_level="L4"),
    }
    result = aggregate_category_evaluation(
        contract,
        _precheck(reason=["是随手拍"]),
        {"deductions": {}},
        track_key="class_one",
    )
    # L4 的上界 = L3.min_score - 1 = 59
    assert result["score"] == 59
    assert result["level"] == "L4"


def test_soft_cap_no_hit_leaves_score_unchanged() -> None:
    contract = _contract()
    contract["quality_rules"] = {"enabled": True, "snapshot_limit": _snapshot_limit()}
    result = aggregate_category_evaluation(
        contract,
        _precheck(reason=["构图完整"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 100  # 无硬伤信号，veto 不触发，与无质量规则时一致
    assert result["quality_rules_evidence"]["soft_cap_applied"] is False


def test_quality_rules_disabled_block_is_inert() -> None:
    contract = _contract()
    contract["quality_rules"] = {
        "enabled": False,
        "snapshot_limit": _snapshot_limit(),
    }
    result = aggregate_category_evaluation(
        contract,
        _precheck(reason=["是随手拍"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 100
    assert result["quality_rules_evidence"]["soft_cap_applied"] is False


def test_contract_without_quality_rules_behaves_as_before() -> None:
    result = aggregate_category_evaluation(
        _contract(),
        _precheck(reason=["是随手拍"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 100
    assert result["quality_rules_evidence"] == {
        "soft_cap_applied": False,
        "exemptions_applied": [],
        "notes": [],
    }


def test_invalid_quality_rules_fails_closed_with_prefixed_code() -> None:
    contract = _contract()
    contract["quality_rules"] = {"enabled": True, "anchors": []}
    with pytest.raises(CategoryEvaluationAggregatorError) as excinfo:
        aggregate_category_evaluation(
            contract, _precheck(), {"deductions": {}}, track_key="class_one"
        )
    # 合同校验层先拦（contract.quality_rules.*）；万一它放行，装载层兜底（quality_rules.*）
    assert "quality_rules." in excinfo.value.code


def test_defect_exemption_without_dimension_evidence_fails_closed_with_note() -> None:
    """维度门槛需要八维档位证据；本路径拿不到就不豁免，但必须向运营说明。"""
    contract = _contract(modifiers=_common_modifiers_v2())
    contract["quality_rules"] = {
        "enabled": True,
        "defect_exceptions": [
            {
                "name": "品牌文字遮挡豁免",
                "defect": "subject_obscuring_watermark",
                "defect_source": "image_defects",
                "when_evidence_contains": ["品牌文字"],
                "require_dimensions": [
                    {"dimension": "detail_completion", "min_grade": 4},
                ],
            }
        ],
    }
    result = aggregate_category_evaluation(
        contract,
        _precheck(image_defects=["subject_obscuring_watermark"]),
        {"deductions": {}},
        track_key="class_one",
    )
    # 豁免未生效：severe tier 仍把分压到 59
    assert result["score"] == 59
    assert result["quality_rules_evidence"]["exemptions_applied"] == []
    assert any("维度门槛无法核实" in note for note in result["quality_rules_evidence"]["notes"])
    assert any(step["step"] == "quality_exemption_skipped" for step in result["steps"])


def test_defect_exemption_not_noted_when_evidence_keywords_miss() -> None:
    contract = _contract(modifiers=_common_modifiers_v2())
    contract["quality_rules"] = {
        "enabled": True,
        "defect_exceptions": [
            {
                "name": "品牌文字遮挡豁免",
                "defect": "subject_obscuring_watermark",
                "defect_source": "image_defects",
                "when_evidence_contains": ["不存在的佐证词"],
                "require_dimensions": [
                    {"dimension": "detail_completion", "min_grade": 4},
                ],
            }
        ],
    }
    result = aggregate_category_evaluation(
        contract,
        _precheck(image_defects=["subject_obscuring_watermark"]),
        {"deductions": {}},
        track_key="class_one",
    )
    assert result["score"] == 59
    assert result["quality_rules_evidence"]["notes"] == []
