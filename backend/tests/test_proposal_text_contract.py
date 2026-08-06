from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.proposal_text_contract import (
    PROPOSAL_REDLINE_TYPES,
    ProposalTextContractError,
    validate_proposal_call_a_output,
    validate_proposal_call_b_output,
    validate_proposal_text_contract,
)


FIXTURE = Path(__file__).parent / "fixtures" / "proposal_text_contract_v1.json"


def contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def passed_call_a(category: str = "A") -> dict:
    return {
        "预检结果": {"状态": "通过", "是否进入B": True, "结论说明": "材料完整"},
        "材料扫描": {
            "文件列表": ["sample.pdf"],
            "文件格式": ["PDF"],
            "总页数": 18,
            "页面可读性": "正常",
        },
        "红线检查": {"是否命中": False, "命中项": []},
        "信息提取": {
            "项目分类": {
                "审核类别": category,
                "一级分类": "建筑设计",
                "二级分类": "公共建筑",
                "分类依据": "页面可见",
            },
            "项目基本信息": {
                "项目名称": "示例",
                "专业标题": "公共建筑",
                "SEO标题": "示例公共建筑设计方案",
                "设计主题": "共生",
                "概念摘要": "围绕场地形成完整叙事",
                "风格": "现代",
                "标签": ["建筑"],
                "设计师或设计公司": "示例事务所",
                "所在城市": "上海",
                "项目或文本年份": 2025,
                "项目工期": "2年",
            },
            "图像统计": {"效果图数量": 8, "分析图数量": 5, "意向图数量": 3},
            "内容完整性": {
                "项目背景": "是",
                "场地或问题分析": "是",
                "概念推导": "是",
                "空间策略": "是",
                "动线展示": "是",
                "效果图": "是",
            },
        },
        "待复核项": [],
        "置信度": 0.9,
    }


def call_b(track: str = "A") -> dict:
    return {
        "scoring_track": track,
        "visual_score": 40,
        "narrative_score": 42,
        "innovation_timeliness_score": 8,
        "reason": "图文叙事完整",
        "evidence_notes": ["第3页概念推导", "第12页效果图"],
    }


def test_contract_accepts_frozen_text_proposal_profile() -> None:
    validate_proposal_text_contract(contract())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("profile_type",), "rule-deduction-v1"),
        (("track_classification", "tracks", "A", "visual_max"), 44),
        (("grade_bands", "L1"), [91, 100]),
        (("redline_policy", "signal"), "production_fields.reason"),
    ],
)
def test_contract_rejects_changed_frozen_semantics(
    path: tuple[str, ...], value: object
) -> None:
    candidate = copy.deepcopy(contract())
    target = candidate
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ProposalTextContractError):
        validate_proposal_text_contract(candidate)


def test_contract_freezes_all_six_chinese_redline_enums() -> None:
    values = {
        value
        for rule in contract()["redline_policy"]["rules"]
        for value in rule["match_any"]
    }
    assert values == set(PROPOSAL_REDLINE_TYPES)


def test_call_a_validator_accepts_pass_and_rejects_enum_overflow() -> None:
    validated = validate_proposal_call_a_output(passed_call_a())
    assert validated["预检结果"]["状态"] == "通过"
    invalid = passed_call_a()
    invalid["红线检查"] = {
        "是否命中": True,
        "命中项": [{"类型": "未知红线", "说明": "x", "证据": []}],
    }
    invalid["预检结果"] = {"状态": "淘汰", "是否进入B": False, "结论说明": "x"}
    invalid["信息提取"] = None
    with pytest.raises(ProposalTextContractError):
        validate_proposal_call_a_output(invalid)


def test_call_a_validator_requires_state_consistency() -> None:
    invalid = passed_call_a()
    invalid["预检结果"]["是否进入B"] = False
    with pytest.raises(ProposalTextContractError):
        validate_proposal_call_a_output(invalid)


@pytest.mark.parametrize(
    ("category", "track", "visual", "narrative"),
    [
        ("A", "A", 45, 45),
        ("B", "B", 27, 63),
        ("C", "C", 63, 27),
        ("其他", "balanced", 45, 45),
    ],
)
def test_call_b_validator_applies_track_bounds(
    category: str, track: str, visual: int, narrative: int
) -> None:
    payload = call_b(track)
    payload["visual_score"] = visual
    payload["narrative_score"] = narrative
    assert validate_proposal_call_b_output(
        payload, contract=contract(), audit_category=category
    )["scoring_track"] == track


def test_call_b_validator_rejects_extra_total_and_does_not_truncate() -> None:
    payload = call_b()
    payload["visual_score"] = 46
    payload["score"] = 100
    with pytest.raises(ProposalTextContractError):
        validate_proposal_call_b_output(
            payload, contract=contract(), audit_category="A"
        )
    assert payload["visual_score"] == 46
    assert payload["score"] == 100


def test_call_b_validator_rejects_bool_as_integer() -> None:
    payload = call_b()
    payload["visual_score"] = True
    with pytest.raises(ProposalTextContractError):
        validate_proposal_call_b_output(
            payload, contract=contract(), audit_category="A"
        )
