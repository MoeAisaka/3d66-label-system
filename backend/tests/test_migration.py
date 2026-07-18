from __future__ import annotations

from app.migration import compare_results


def result(level: str, category: str, confidence: float = 0.9) -> dict:
    return {
        "level": level,
        "score": 82.0,
        "confidence": confidence,
        "needs_review": False,
        "precheck": {"classification": {"primary_category": category}},
    }


def test_same_high_confidence_result_can_auto_pass() -> None:
    comparison = compare_results(result("L4", "住宅设计"), result("L4", "住宅设计"))
    assert comparison["requires_review"] is False
    assert comparison["reasons"] == []


def test_level_change_requires_review() -> None:
    comparison = compare_results(result("L4", "住宅设计"), result("L3", "住宅设计"))
    assert comparison["requires_review"] is True
    assert comparison["level_delta"] == -1


def test_small_agreement_audit_requires_review() -> None:
    comparison = compare_results(
        result("L4", "住宅设计"), result("L4", "住宅设计"), audit_sample=True
    )
    assert comparison["requires_review"] is True
    assert "一致样本 5% 抽检" in comparison["reasons"]

