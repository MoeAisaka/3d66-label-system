"""灵感图质量规则机制（可配置版）测试。

三类断言：

1. **单一职责守卫**——外来机制键混进本块必须报错，且报错要说明该键归哪。
   这是把"只管两项"钉成机器可查的红线，不靠注释约束。
2. **解锁能力**——旧 ``_validated_quality_rules`` 把内容写死了（关键词必须精确等于
   ``["是随手拍"]``、豁免必须恰好 1 条、key/关键词/维度全部锁定）。新机制必须放行
   运营的这些改动，否则拆分没换来任何能力。
3. **语义等价**——默认块翻译出的内部形状必须与旧路径对生产合同的输出逐字节一致，
   这是执行层零改动的前提。
"""

from __future__ import annotations

import copy
import json

import pytest

from app.inspiration_quality_rules import (
    CONTRACT_BLOCK_KEY,
    QualityRulesError,
    assert_quality_rules_isolated,
    default_quality_rules_block,
    load_quality_rules,
    validate_quality_rules_block,
)


def _block() -> dict:
    return default_quality_rules_block()


def _contract(block: dict | None = None) -> dict:
    return {CONTRACT_BLOCK_KEY: block if block is not None else _block()}


# ---------------------------------------------------------------------------
# 1. 单一职责守卫
# ---------------------------------------------------------------------------

FOREIGN_KEYS_WITH_HOME = [
    ("score_thresholds", "level_thresholds"),
    ("level_thresholds", "合同顶层"),
    ("level_scale", "level_scale"),
    ("bands", "level_thresholds"),
    ("anchors", "anchor_mechanism"),
    ("anchor_samples", "anchor_mechanism"),
    ("anchor_mechanism", "独立块"),
    ("dimensions", "dimensions"),
    ("dimension_keys", "dimensions"),
    ("dimension_weights", "dimensions"),
    ("redline_policy", "redline_policy"),
    ("redlines", "redline_policy"),
    ("boundary_policy", "零实现"),
    ("prompt_template", "提示词管理"),
    ("call_b_version", "合同顶层"),
    ("calibration_status", "合同顶层"),
    ("aesthetic_foundation", "被替代"),
]


@pytest.mark.parametrize("key,home_hint", FOREIGN_KEYS_WITH_HOME)
def test_foreign_key_rejected_at_block_level(key: str, home_hint: str) -> None:
    """外来机制键混进块顶层必须被拦，并说明它该去哪。"""
    block = _block()
    block[key] = {"whatever": True}
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "quality_rules_foreign_key"
    message = str(excinfo.value)
    assert key in message
    assert home_hint in message


@pytest.mark.parametrize("key,_home", FOREIGN_KEYS_WITH_HOME)
def test_foreign_key_rejected_inside_snapshot_limit(key: str, _home: str) -> None:
    """外来键藏进随手拍限分子块同样要被拦。"""
    block = _block()
    block["snapshot_limit"][key] = {"whatever": True}
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "quality_rules_foreign_key"


@pytest.mark.parametrize("key,_home", FOREIGN_KEYS_WITH_HOME)
def test_foreign_key_rejected_inside_defect_exception(key: str, _home: str) -> None:
    """外来键藏进硬伤例外条目同样要被拦。"""
    block = _block()
    block["defect_exceptions"][0][key] = {"whatever": True}
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "quality_rules_foreign_key"


def test_unknown_field_rejected() -> None:
    """改个名字夹带也不行——白名单兜住。"""
    block = _block()
    block["mystery_knob"] = 1
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "quality_rules_unknown_field"
    assert "mystery_knob" in str(excinfo.value)


def test_unknown_field_rejected_in_snapshot_limit() -> None:
    block = _block()
    block["snapshot_limit"]["secret_cap"] = 3
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "snapshot_limit_unknown_field"


def test_unknown_field_rejected_in_defect_exception() -> None:
    block = _block()
    block["defect_exceptions"][0]["secret_rule"] = True
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "defect_exception_unknown_field"


def test_block_must_be_object() -> None:
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(["not", "a", "dict"])
    assert excinfo.value.code == "quality_rules_not_object"


# ---------------------------------------------------------------------------
# 2. 解锁能力：旧校验拒绝、新机制必须放行
# ---------------------------------------------------------------------------


def test_unlock_custom_snapshot_keywords() -> None:
    """旧校验要求关键词精确等于 ["是随手拍"]；运营改写法必须放行。"""
    block = _block()
    block["snapshot_limit"]["when_reason_contains"] = ["随手拍", "手机快照", "生活照"]
    soft_cap, _ = load_quality_rules(_contract(block))
    assert soft_cap is not None
    assert soft_cap["match_any"] == ["随手拍", "手机快照", "生活照"]


def test_unlock_custom_cap_score() -> None:
    """运营调整限分值必须放行。"""
    block = _block()
    block["snapshot_limit"]["max_score"] = 45
    soft_cap, _ = load_quality_rules(_contract(block))
    assert soft_cap["cap_to"] == 45


def test_unlock_multiple_defect_exceptions() -> None:
    """旧校验要求恰好 1 条豁免；运营加第二条必须放行。"""
    block = _block()
    second = copy.deepcopy(block["defect_exceptions"][0])
    second["name"] = "logo_edge_crop"
    second["defect"] = "subject_cropped"
    second["when_evidence_contains"] = ["边缘裁切"]
    block["defect_exceptions"].append(second)
    _, exemptions = load_quality_rules(_contract(block))
    assert len(exemptions) == 2
    assert {item["key"] for item in exemptions} == {
        "subject_obscuring_brand_wordmark",
        "logo_edge_crop",
    }


def test_unlock_custom_exception_name_and_evidence() -> None:
    """旧校验把 key 与关键词写死；运营自定义必须放行。"""
    block = _block()
    exception = block["defect_exceptions"][0]
    exception["name"] = "运营自定义豁免"
    exception["when_evidence_contains"] = ["店铺招牌", "门头字"]
    _, exemptions = load_quality_rules(_contract(block))
    assert exemptions[0]["key"] == "运营自定义豁免"
    assert exemptions[0]["evidence_contains_any"] == ["店铺招牌", "门头字"]


def test_unlock_custom_requirement_dimensions() -> None:
    """旧校验锁定两个维度；运营换维度、换档位必须放行。"""
    block = _block()
    block["defect_exceptions"][0]["require_dimensions"] = [
        {"dimension": "composition", "min_grade": 3},
        {"dimension": "lighting", "min_grade": 5, "no_shortcomings": True},
    ]
    _, exemptions = load_quality_rules(_contract(block))
    requirements = exemptions[0]["foundation_requirements"]
    assert requirements == {
        "composition": {"min_grade": 3, "shortcomings_empty": False},
        "lighting": {"min_grade": 5, "shortcomings_empty": True},
    }


def test_unlock_cap_to_level_with_dimension_ceilings() -> None:
    """按等级封顶 + 维度分上限这条路径也要能配。"""
    block = _block()
    block["snapshot_limit"].pop("max_score")
    block["snapshot_limit"]["max_level"] = "L4"
    block["snapshot_limit"]["dimension_ceilings"] = {"composition": 3, "lighting": 2}
    soft_cap, _ = load_quality_rules(_contract(block))
    assert soft_cap["cap_to_level"] == "L4"
    assert soft_cap["filter_escalation"] == {
        "cap_to_level": "L4",
        "dimensions_at_most": {"composition": 3, "lighting": 2},
    }


def test_snapshot_limit_can_be_disabled() -> None:
    """运营可以单独关掉随手拍限分而保留豁免。"""
    block = _block()
    block["snapshot_limit"]["enabled"] = False
    soft_cap, exemptions = load_quality_rules(_contract(block))
    assert soft_cap is None
    assert len(exemptions) == 1


def test_whole_block_can_be_disabled() -> None:
    block = _block()
    block["enabled"] = False
    soft_cap, exemptions = load_quality_rules(_contract(block))
    assert soft_cap is None
    assert exemptions == []


def test_empty_exceptions_allowed() -> None:
    """允许一条豁免都不配。"""
    block = _block()
    block["defect_exceptions"] = []
    soft_cap, exemptions = load_quality_rules(_contract(block))
    assert soft_cap is not None
    assert exemptions == []


# ---------------------------------------------------------------------------
# 3. 语义等价：默认块必须翻译出与生产旧路径一致的内部形状
# ---------------------------------------------------------------------------


def test_default_block_matches_production_shape() -> None:
    """默认块的翻译结果必须与旧路径对生产合同的输出一致。

    这些期望值抄自生产库 inspiration_image 合同经 ``_validated_quality_rules``
    的实际输出，是执行层零改动的前提。
    """
    soft_cap, exemptions = load_quality_rules(_contract())
    assert soft_cap == {
        "key": "casual_snapshot_soft_cap",
        "signal": "production_fields.reason",
        "match_any": ["是随手拍"],
        "cap_to": 59,
    }
    assert exemptions == [
        {
            "key": "subject_obscuring_brand_wordmark",
            "source": "image_defects",
            "defect_key": "subject_obscuring_watermark",
            "evidence_contains_any": ["品牌文字", "品牌字样"],
            "foundation_requirements": {
                "detail_completion": {"min_grade": 4, "shortcomings_empty": True},
                "presentation_integrity": {"min_grade": 4, "shortcomings_empty": True},
            },
        }
    ]


def test_default_block_equals_legacy_output_on_production_contract() -> None:
    """与旧实现在生产配置上逐字节等价（防止翻译层漂移）。"""
    from backend.app.inspiration_aesthetic_foundation import _validated_quality_rules

    legacy_contract = {
        "aesthetic_foundation": {
            "casual_snapshot_soft_cap": {
                "key": "casual_snapshot_soft_cap",
                "signal": "production_fields.reason",
                "match_any": ["是随手拍"],
                "cap_to": 59,
            },
            "hard_defect_exemptions": [
                {
                    "key": "subject_obscuring_brand_wordmark",
                    "source": "image_defects",
                    "defect_key": "subject_obscuring_watermark",
                    "evidence_contains_any": ["品牌文字", "品牌字样"],
                    "foundation_requirements": {
                        "detail_completion": {
                            "min_grade": 4,
                            "shortcomings_empty": True,
                        },
                        "presentation_integrity": {
                            "min_grade": 4,
                            "shortcomings_empty": True,
                        },
                    },
                }
            ],
        }
    }
    legacy_soft, legacy_exem = _validated_quality_rules(legacy_contract)
    new_soft, new_exem = load_quality_rules(_contract())
    assert json.dumps(new_soft, sort_keys=True) == json.dumps(legacy_soft, sort_keys=True)
    assert json.dumps(new_exem, sort_keys=True) == json.dumps(legacy_exem, sort_keys=True)


# ---------------------------------------------------------------------------
# 4. 取值域校验
# ---------------------------------------------------------------------------


def test_missing_block_returns_none() -> None:
    """合同没有本块时返回 None，让调用方回落旧路径。"""
    assert load_quality_rules({}) is None


def test_snapshot_requires_at_least_one_keyword() -> None:
    block = _block()
    block["snapshot_limit"]["when_reason_contains"] = []
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_reasons_empty"


def test_snapshot_requires_a_target() -> None:
    block = _block()
    block["snapshot_limit"].pop("max_score")
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_target_missing"


def test_snapshot_rejects_conflicting_targets() -> None:
    block = _block()
    block["snapshot_limit"]["max_level"] = "L4"
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_target_conflict"


@pytest.mark.parametrize("bad_score", [-1, 101, 200])
def test_snapshot_score_out_of_range(bad_score: int) -> None:
    block = _block()
    block["snapshot_limit"]["max_score"] = bad_score
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_score_range"


def test_snapshot_score_rejects_bool() -> None:
    """True 在 Python 里是 int 的子类，必须显式排除。"""
    block = _block()
    block["snapshot_limit"]["max_score"] = True
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_score_invalid"


def test_snapshot_rejects_invalid_level() -> None:
    block = _block()
    block["snapshot_limit"].pop("max_score")
    block["snapshot_limit"]["max_level"] = "L9"
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_level_invalid"


@pytest.mark.parametrize("bad_limit", [0, 6, True])
def test_dimension_ceiling_out_of_range(bad_limit: object) -> None:
    block = _block()
    block["snapshot_limit"].pop("max_score")
    block["snapshot_limit"]["max_level"] = "L4"
    block["snapshot_limit"]["dimension_ceilings"] = {"composition": bad_limit}
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "snapshot_limit_ceiling_value_invalid"


def test_exception_requires_name() -> None:
    block = _block()
    block["defect_exceptions"][0]["name"] = "  "
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_name_invalid"


def test_exception_requires_defect() -> None:
    block = _block()
    block["defect_exceptions"][0]["defect"] = ""
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_defect_invalid"


def test_exception_rejects_unknown_source() -> None:
    block = _block()
    block["defect_exceptions"][0]["defect_source"] = "made_up_source"
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_source_invalid"


def test_exception_requires_evidence() -> None:
    block = _block()
    block["defect_exceptions"][0]["when_evidence_contains"] = []
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_evidence_empty"


def test_exception_requires_dimension_gate() -> None:
    """不许无条件豁免——必须至少一条维度门槛。"""
    block = _block()
    block["defect_exceptions"][0]["require_dimensions"] = []
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_requirements_empty"


@pytest.mark.parametrize("bad_grade", [0, 6, True, "4"])
def test_exception_requirement_grade_invalid(bad_grade: object) -> None:
    block = _block()
    block["defect_exceptions"][0]["require_dimensions"] = [
        {"dimension": "composition", "min_grade": bad_grade}
    ]
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_requirement_grade_invalid"


def test_exception_requirement_duplicate_dimension() -> None:
    block = _block()
    block["defect_exceptions"][0]["require_dimensions"] = [
        {"dimension": "composition", "min_grade": 3},
        {"dimension": "composition", "min_grade": 4},
    ]
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_requirement_duplicate"


def test_duplicate_exception_names_rejected() -> None:
    block = _block()
    block["defect_exceptions"].append(copy.deepcopy(block["defect_exceptions"][0]))
    with pytest.raises(QualityRulesError) as excinfo:
        load_quality_rules(_contract(block))
    assert excinfo.value.code == "defect_exception_name_duplicate"


def test_exceptions_must_be_list() -> None:
    block = _block()
    block["defect_exceptions"] = {"not": "a list"}
    with pytest.raises(QualityRulesError) as excinfo:
        assert_quality_rules_isolated(block)
    assert excinfo.value.code == "defect_exceptions_not_list"


def test_validate_helper_accepts_default_block() -> None:
    """合同保存前校验：默认块必须放行。"""
    validate_quality_rules_block(_contract())


def test_validate_helper_rejects_foreign_key() -> None:
    block = _block()
    block["score_thresholds"] = {"L1": 90}
    with pytest.raises(QualityRulesError):
        validate_quality_rules_block(_contract(block))


# ---------------------------------------------------------------------------
# 5. 执行层接线：配了必须真生效，关了必须不崩
# ---------------------------------------------------------------------------


def _run_rules(quality_rules_block: dict | None, *, snapshot_reason: bool):
    """跑一遍真实评分链路，返回结果。

    ``quality_rules_block`` 为 ``None`` 时不写入本块，用于验证回落旧基座路径。
    """
    from app.inspiration_aesthetic_foundation import apply_aesthetic_v3_rules
    from test_inspiration_aesthetic_foundation import (
        build_inspiration_classification_map,
        build_inspiration_v3_contract,
        precheck,
        valid_payload,
    )

    contract = build_inspiration_v3_contract()
    if quality_rules_block is not None:
        contract[CONTRACT_BLOCK_KEY] = quality_rules_block

    prepared_precheck = precheck()
    if snapshot_reason:
        prepared_precheck["production_fields"]["reason"] = ["是随手拍"]

    return apply_aesthetic_v3_rules(
        contract=contract,
        classification_map=build_inspiration_classification_map(),
        precheck=prepared_precheck,
        foundation=copy.deepcopy(valid_payload()),
    )


def _unclamped_score() -> int:
    """无限分作用时的原始分，作为"没被压分"的对照值。"""
    return _run_rules(None, snapshot_reason=False)["score"]


def test_legacy_path_still_caps_at_59() -> None:
    """合同不带本块时回落旧基座，生产现状零变化。"""
    assert _run_rules(None, snapshot_reason=True)["score"] == 59


def test_configured_cap_takes_effect_end_to_end() -> None:
    """运营配的限分值必须真的压到那个分数——特征值 37 排除巧合。"""
    block = _block()
    block["snapshot_limit"]["max_score"] = 37
    assert _run_rules(block, snapshot_reason=True)["score"] == 37


def test_configured_keywords_take_effect_end_to_end() -> None:
    """运营自定义关键词命中后按新限分压分。"""
    block = _block()
    block["snapshot_limit"]["when_reason_contains"] = ["手机快照", "是随手拍"]
    block["snapshot_limit"]["max_score"] = 42
    assert _run_rules(block, snapshot_reason=True)["score"] == 42


def test_non_matching_keywords_do_not_cap() -> None:
    """关键词对不上就不该压分。"""
    block = _block()
    block["snapshot_limit"]["when_reason_contains"] = ["完全不匹配的词"]
    assert _run_rules(block, snapshot_reason=True)["score"] == _unclamped_score()


def test_whole_block_disabled_does_not_crash_or_cap() -> None:
    """整块关闭时装载器返回 soft_cap=None，执行层必须容得下（曾在此崩过）。"""
    block = _block()
    block["enabled"] = False
    assert _run_rules(block, snapshot_reason=True)["score"] == _unclamped_score()


def test_snapshot_limit_disabled_does_not_crash_or_cap() -> None:
    """只关限分、保留豁免时同样不能崩。"""
    block = _block()
    block["snapshot_limit"]["enabled"] = False
    assert _run_rules(block, snapshot_reason=True)["score"] == _unclamped_score()


def test_non_snapshot_image_unaffected_by_cap() -> None:
    """不是随手拍就不受限分影响。"""
    block = _block()
    block["snapshot_limit"]["max_score"] = 37
    assert _run_rules(block, snapshot_reason=False)["score"] == _unclamped_score()
