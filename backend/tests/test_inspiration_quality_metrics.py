from app.inspiration_quality_metrics import (
    compute_inspiration_quality_metrics,
    quality_gate,
    three_bucket_fallback_metrics,
)


def test_recommendation_merges_l1_l2_and_three_bucket_fallback():
    metrics = compute_inspiration_quality_metrics([
        {"truth": "L1", "pred": "L2"},
        {"truth": "L2", "pred": "L1"},
        {"truth": "L3", "pred": "L4"},
        {"truth": "L5", "pred": "L4"},
    ])
    assert metrics["recommendation"]["denominator"] == 2
    assert metrics["three_bucket"]["filter"]["recall"] == 0.0
    assert metrics["diagnostics"]["l1_overpromotion_cost"] > 0


def test_quality_gate_rejects_recommendation_share_over_thirty_five_percent():
    metrics = compute_inspiration_quality_metrics(
        [{"truth": "L1", "pred": "L1"}] * 36
        + [{"truth": "L3", "pred": "L3"}] * 64
    )
    failures = quality_gate(metrics)
    assert any(item["gate"] == "recommendation_share" for item in failures)


def test_fallback_metrics_use_three_business_buckets():
    result = three_bucket_fallback_metrics([
        {"truth": "L2", "pred": "L1"},
        {"truth": "L4", "pred": "L3"},
        {"truth": "L5", "pred": "L5"},
    ])
    assert set(result) == {"recommendation", "ordinary", "filter"}
    assert result["filter"]["recall"] == 1.0
