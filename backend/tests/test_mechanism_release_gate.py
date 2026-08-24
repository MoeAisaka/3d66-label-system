from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.mechanism_release_gate import (
    CandidateReleaseGateError,
    evaluate_candidate_release_gate,
)


class _Db:
    pass


def _run(
    *,
    run_id: int,
    metrics: dict,
    previous: SimpleNamespace | None,
    candidate_id: int = 22,
    category_key: str = "inspiration_image",
    contract_hash: str = "c" * 64,
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id,
        status=status,
        previous_run_id=previous.id if previous else None,
        previous_run=previous,
        category_key=category_key,
        baseline_set_fingerprint="baseline-fp",
        metrics_json=json.dumps(metrics),
        execution_snapshot_json=json.dumps(
            {
                "v3_authoritative_bundle": {
                    "category_key": category_key,
                    "candidate_revision_id": candidate_id,
                    "contract_hash": contract_hash,
                    "prompt_bindings": {"a": 10, "b": 11},
                }
            }
        ),
    )


def _metrics(
    *,
    exact: float = 0.9,
    adjacent: float = 0.95,
    failed: int = 0,
    total: int = 100,
    denominator: int | None = None,
) -> dict:
    # 样本量取真实量级（基准集是 50-100 条）。此前写死 10 条，低于门禁要求的
    # 最小可比分母，而这些用例测的是 delta 与质量码，不是样本量本身。
    return {
        "denominator": total if denominator is None else denominator,
        "exact_accuracy": exact,
        "adjacent_accuracy": adjacent,
        "failed": failed,
        "total": total,
        "valid_predictions": total - failed,
    }


def _objects() -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    baseline = _run(run_id=1, metrics=_metrics(), previous=None, candidate_id=0)
    candidate_run = _run(run_id=2, metrics=_metrics(), previous=baseline)
    projected = SimpleNamespace(
        id=100,
        category_key="inspiration_image",
        status="active",
        revision=7,
        contract_hash="p" * 64,
        projected_revision_id=100,
    )
    candidate = SimpleNamespace(
        id=22,
        category_key="inspiration_image",
        status="candidate",
        parent_revision_id=100,
        revision=8,
        contract_hash="c" * 64,
        contract_json=json.dumps({"prompt_bindings": {"a": 10, "b": 11}}),
    )
    return projected, candidate, baseline, candidate_run


def test_valid_candidate_release_returns_metric_deltas_and_allows_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projected, candidate, baseline, candidate_run = _objects()
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.field_metric_release_regressions",
        lambda _before, _after: [],
    )

    report = evaluate_candidate_release_gate(
        _Db(),
        category_key="inspiration_image",
        projected=projected,
        candidate=candidate,
        regression_run=candidate_run,
        expected_projected_revision=7,
        expected_projected_contract_hash="p" * 64,
    )

    assert report["approval_allowed"] is True
    assert report["recommendation"] == "approve"
    assert report["exact_accuracy_delta"] == 0.0
    assert report["adjacent_accuracy_delta"] == 0.0
    assert report["regressions"] == []


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda run: setattr(run, "previous_run", None), "regression_not_comparable"),
        (lambda run: setattr(run, "status", "partial_failed"), "candidate_run_incomplete"),
        (lambda run: run.metrics_json, "exact_accuracy_regressed"),
    ],
)
def test_candidate_release_gate_returns_stable_quality_codes(
    monkeypatch: pytest.MonkeyPatch,
    mutator,
    code: str,
) -> None:
    projected, candidate, baseline, candidate_run = _objects()
    if code == "exact_accuracy_regressed":
        candidate_run.metrics_json = json.dumps(_metrics(exact=0.7))
    else:
        mutator(candidate_run)
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.field_metric_release_regressions",
        lambda _before, _after: [],
    )

    if code in {"regression_not_comparable", "candidate_run_incomplete"}:
        with pytest.raises(CandidateReleaseGateError) as exc_info:
            evaluate_candidate_release_gate(
                _Db(),
                category_key="inspiration_image",
                projected=projected,
                candidate=candidate,
                regression_run=candidate_run,
                expected_projected_revision=7,
                expected_projected_contract_hash="p" * 64,
            )
        assert exc_info.value.code == code
    else:
        report = evaluate_candidate_release_gate(
            _Db(),
            category_key="inspiration_image",
            projected=projected,
            candidate=candidate,
            regression_run=candidate_run,
            expected_projected_revision=7,
            expected_projected_contract_hash="p" * 64,
        )
        assert report["approval_allowed"] is False
        assert report["recommendation"] == "reject"
        assert code in {item["code"] for item in report["regressions"]}


def _snapshot(
    *,
    candidate_id: int,
    category_key: str = "inspiration_image",
    contract_hash: str = "c" * 64,
    bindings: dict | None = None,
    override: dict | None = None,
) -> str:
    bundle: dict = {
        "category_key": category_key,
        "candidate_revision_id": candidate_id,
        "contract_hash": contract_hash,
        "contract": {"prompt_bindings": bindings if bindings is not None else {"a": 10, "b": 11}},
    }
    if override is not None:
        bundle["prompt_binding_override"] = override
    return json.dumps({"v3_authoritative_bundle": bundle})


def test_candidate_release_gate_allows_operator_chosen_prompt_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运营用自选 A/B 验证过的候选可以发布：这是评测机制实验的完整闭环。

    快照记录的是实际执行对，与候选出厂声明不同属预期；只要覆盖记录自洽地
    说明「声明是什么、实际跑了什么」，门禁就不应拦。
    """
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.execution_snapshot_json = _snapshot(
        candidate_id=candidate.id,
        bindings={"a": 20, "b": 21},
        override={
            "declared": {"a": 10, "b": 11},
            "executed": {"a": 20, "b": 21},
            "actor": "operator",
        },
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.field_metric_release_regressions",
        lambda _before, _after: [],
    )

    report = evaluate_candidate_release_gate(
        _Db(),
        category_key="inspiration_image",
        projected=projected,
        candidate=candidate,
        regression_run=candidate_run,
        expected_projected_revision=7,
        expected_projected_contract_hash="p" * 64,
    )

    assert report["approval_allowed"] is True
    assert report["recommendation"] == "approve"


def test_candidate_release_gate_rejects_diverged_bindings_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无覆盖记录的任意绑定仍须拒绝：放开的是运营自由，不是伪造快照。"""
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.execution_snapshot_json = _snapshot(
        candidate_id=candidate.id,
        bindings={"a": 99, "b": 98},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    with pytest.raises(CandidateReleaseGateError) as exc_info:
        evaluate_candidate_release_gate(
            _Db(),
            category_key="inspiration_image",
            projected=projected,
            candidate=candidate,
            regression_run=candidate_run,
            expected_projected_revision=7,
            expected_projected_contract_hash="p" * 64,
        )
    assert exc_info.value.code == "candidate_snapshot_mismatch"


def test_candidate_release_gate_rejects_inconsistent_override_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """覆盖记录必须自洽：declared 要对上候选声明，executed 要对上快照实跑对。"""
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.execution_snapshot_json = _snapshot(
        candidate_id=candidate.id,
        bindings={"a": 20, "b": 21},
        override={
            # declared 谎报成别的版本，与候选合同声明的 {"a": 10, "b": 11} 不符
            "declared": {"a": 77, "b": 76},
            "executed": {"a": 20, "b": 21},
            "actor": "operator",
        },
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    with pytest.raises(CandidateReleaseGateError) as exc_info:
        evaluate_candidate_release_gate(
            _Db(),
            category_key="inspiration_image",
            projected=projected,
            candidate=candidate,
            regression_run=candidate_run,
            expected_projected_revision=7,
            expected_projected_contract_hash="p" * 64,
        )
    assert exc_info.value.code == "candidate_snapshot_mismatch"


def test_candidate_release_gate_rejects_snapshot_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.execution_snapshot_json = json.dumps(
        {
            "v3_authoritative_bundle": {
                "category_key": "inspiration_image",
                "candidate_revision_id": candidate.id,
                "contract_hash": "d" * 64,
                "prompt_bindings": {"a": 10, "b": 11},
            }
        }
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    with pytest.raises(CandidateReleaseGateError) as exc_info:
        evaluate_candidate_release_gate(
            _Db(),
            category_key="inspiration_image",
            projected=projected,
            candidate=candidate,
            regression_run=candidate_run,
            expected_projected_revision=7,
            expected_projected_contract_hash="p" * 64,
        )
    assert exc_info.value.code == "candidate_snapshot_mismatch"


@pytest.mark.parametrize(
    "metrics_kwargs",
    [
        # 2/100 有效：审计里那个"completed · 准确率 100%"的形态
        {"total": 100, "denominator": 2, "failed": 98},
        # 60/100：run52 的真实形态
        {"total": 100, "denominator": 60, "failed": 40},
    ],
)
def test_candidate_release_gate_blocks_low_coverage_regression(
    monkeypatch: pytest.MonkeyPatch,
    metrics_kwargs: dict,
) -> None:
    """有效预测覆盖率过低的回归不得批准发布，且必须走软失败而非硬抛异常。

    此前唯一的样本量门槛是"分母 > 0"，一个 2/100 有效的回归只要 delta 恰好为正
    就 approval_allowed —— 把两条样本的巧合当成机制改进。用 regressions 软失败是
    因为孤立纠偏重跑本来只跑 1 条样本，硬抛会把那条运营流程整个堵死。
    """
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.metrics_json = json.dumps(_metrics(**metrics_kwargs))
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.field_metric_release_regressions",
        lambda _before, _after: [],
    )

    report = evaluate_candidate_release_gate(
        _Db(),
        category_key="inspiration_image",
        projected=projected,
        candidate=candidate,
        regression_run=candidate_run,
        expected_projected_revision=7,
        expected_projected_contract_hash="p" * 64,
    )

    # 报告照常返回（不阻断流程），但不允许批准
    assert report["approval_allowed"] is False
    assert report["recommendation"] == "reject"
    assert "regression_coverage_too_low" in {item["code"] for item in report["regressions"]}


@pytest.mark.parametrize("total", [1, 20, 100])
def test_candidate_release_gate_allows_fully_covered_small_runs(
    monkeypatch: pytest.MonkeyPatch,
    total: int,
) -> None:
    """全覆盖的小样本 run 不得被覆盖率门槛误伤 —— 孤立纠偏重跑只跑 1 条。"""
    projected, candidate, _baseline, candidate_run = _objects()
    candidate_run.metrics_json = json.dumps(_metrics(total=total))
    monkeypatch.setattr(
        "app.mechanism_release_gate.build_baseline_field_metrics",
        lambda _db, run: {"run_id": run.id, "fields": {}},
    )
    monkeypatch.setattr(
        "app.mechanism_release_gate.field_metric_release_regressions",
        lambda _before, _after: [],
    )

    report = evaluate_candidate_release_gate(
        _Db(),
        category_key="inspiration_image",
        projected=projected,
        candidate=candidate,
        regression_run=candidate_run,
        expected_projected_revision=7,
        expected_projected_contract_hash="p" * 64,
    )
    assert "regression_coverage_too_low" not in {
        item["code"] for item in report["regressions"]
    }


def test_candidate_release_gate_allows_healthy_coverage() -> None:
    """真实健康 run 的覆盖率在 92% 以上，不得被新门槛误伤。"""
    from app.mechanism_release_gate import MIN_COMPARABLE_COVERAGE

    # run58 的真实形态：99/100 有效
    assert 99 / 100 >= MIN_COMPARABLE_COVERAGE
    # run52 的病态形态：60/100 —— 必须被拦
    assert 60 / 100 < MIN_COMPARABLE_COVERAGE
