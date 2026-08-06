from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.proposal_text_aggregator import (
    ProposalTextAggregationError,
    aggregate_proposal_text_evaluation,
)
from tests.test_proposal_text_contract import call_b, passed_call_a


FIXTURE = Path(__file__).parent / "fixtures" / "proposal_text_contract_v1.json"


def contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("score", "level"),
    [(100, "L1"), (90, "L1"), (89, "L2"), (75, "L2"), (74, "L3"),
     (60, "L3"), (59, "L4"), (21, "L4"), (20, "L5"), (0, "L5")],
)
def test_additive_score_and_grade_edges_are_engine_deterministic(
    score: int, level: str
) -> None:
    visual = min(score, 45)
    narrative = min(score - visual, 45)
    innovation = score - visual - narrative
    payload = call_b()
    payload.update(
        visual_score=visual,
        narrative_score=narrative,
        innovation_timeliness_score=innovation,
    )
    result = aggregate_proposal_text_evaluation(
        contract(), passed_call_a(), payload
    )
    assert result["score"] == score
    assert result["proposal_aesthetic_score"] == score
    assert result["level"] == level


def test_original_aesthetic_sum_is_immutable_and_not_back_written() -> None:
    precheck = passed_call_a()
    scoring = call_b()
    precheck_before = copy.deepcopy(precheck)
    scoring_before = copy.deepcopy(scoring)
    result = aggregate_proposal_text_evaluation(contract(), precheck, scoring)
    assert result["proposal_aesthetic_score"] == 90
    assert result["score"] == 90
    assert precheck == precheck_before
    assert scoring == scoring_before


def test_redline_reads_call_a_chinese_signal_and_terminates_before_b() -> None:
    precheck = passed_call_a()
    precheck["预检结果"] = {
        "状态": "淘汰",
        "是否进入B": False,
        "结论说明": "命中竞品水印",
    }
    precheck["红线检查"] = {
        "是否命中": True,
        "命中项": [
            {
                "类型": "竞品水印",
                "说明": "第2页可见网址",
                "证据": [
                    {
                        "source": "sample.pdf",
                        "page": 2,
                        "observation": "可见竞品网址",
                    }
                ],
            }
        ],
    }
    precheck["信息提取"] = None
    result = aggregate_proposal_text_evaluation(contract(), precheck, None)
    assert result["status"] == "淘汰"
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["score"] == 20
    assert result["proposal_aesthetic_score"] is None
    assert result["redline_hits"] == ["竞品水印"]


def test_manual_review_is_fail_closed_without_grade() -> None:
    precheck = passed_call_a()
    precheck["预检结果"] = {
        "状态": "人工复核",
        "是否进入B": False,
        "结论说明": "元数据不可读",
    }
    precheck["材料扫描"]["总页数"] = None
    precheck["材料扫描"]["页面可读性"] = "无法判断"
    precheck["红线检查"] = {"是否命中": None, "命中项": []}
    precheck["信息提取"] = None
    precheck["待复核项"] = ["总页数"]
    result = aggregate_proposal_text_evaluation(contract(), precheck, None)
    assert result["status"] == "人工复核"
    assert result["needs_review"] is True
    assert result["score"] is None
    assert result["level"] is None


def test_passed_a_requires_valid_b_output() -> None:
    with pytest.raises(ProposalTextAggregationError):
        aggregate_proposal_text_evaluation(contract(), passed_call_a(), None)
