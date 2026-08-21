"""Shared quality gate for candidate v3 mechanism releases."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from .baseline_regression import (
    build_baseline_field_metrics,
    field_metric_release_regressions,
)


class CandidateReleaseGateError(ValueError):
    """Stable, user-actionable candidate release failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _candidate_prompt_bindings(candidate: Any) -> dict[str, Any]:
    contract = _json_object(getattr(candidate, "contract_json", None))
    for key in ("prompt_bindings", "candidate_prompt_bindings"):
        value = contract.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    correction = contract.get("correction_contract")
    if isinstance(correction, Mapping):
        value = correction.get("prompt_bindings")
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _snapshot_bundle(regression_run: Any) -> dict[str, Any]:
    snapshot = _json_object(getattr(regression_run, "execution_snapshot_json", None))
    bundle = snapshot.get("v3_authoritative_bundle")
    if not isinstance(bundle, Mapping):
        raise CandidateReleaseGateError(
            "candidate_snapshot_missing",
            "候选回归缺少冻结的 v3 合同快照",
        )
    return dict(bundle)


def _metric_value(metrics: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(metrics.get(key, default))
    except (TypeError, ValueError):
        return default


def evaluate_candidate_release_gate(
    db: Session,
    *,
    category_key: str,
    projected: Any,
    candidate: Any,
    regression_run: Any,
    expected_projected_revision: int,
    expected_projected_contract_hash: str,
) -> dict[str, Any]:
    """Validate one immutable candidate against one completed baseline run.

    The function only reads persisted state and returns an auditable report;
    callers own the transaction that activates the candidate.
    """
    if projected is None or getattr(projected, "status", None) != "active":
        raise CandidateReleaseGateError(
            "runtime_projection_required",
            "当前类目没有可执行的现役运行时合同",
        )
    if getattr(projected, "category_key", None) != category_key:
        raise CandidateReleaseGateError(
            "projected_category_conflict",
            "现役运行时合同不属于当前类目",
        )
    if (
        getattr(projected, "revision", None) != expected_projected_revision
        or getattr(projected, "contract_hash", None)
        != expected_projected_contract_hash
    ):
        raise CandidateReleaseGateError(
            "projected_cas_conflict",
            "现役合同已变化，请刷新后重试",
        )
    if candidate is None or getattr(candidate, "category_key", None) != category_key:
        raise CandidateReleaseGateError(
            "candidate_category_conflict",
            "候选 revision 不属于当前类目",
        )
    already_active = (
        getattr(candidate, "status", None) == "active"
        and getattr(projected, "projected_revision_id", None)
        == getattr(candidate, "id", None)
    )
    if getattr(candidate, "status", None) != "candidate" and not already_active:
        raise CandidateReleaseGateError(
            "candidate_status_conflict",
            "只有候选状态的 revision 可以启用",
        )
    if not already_active and getattr(candidate, "parent_revision_id", None) != getattr(
        projected, "projected_revision_id", None
    ):
        raise CandidateReleaseGateError(
            "candidate_ancestry_conflict",
            "候选 revision 不在当前现役版本的候选链上",
        )
    if regression_run is None or getattr(regression_run, "category_key", None) != category_key:
        raise CandidateReleaseGateError(
            "regression_category_conflict",
            "候选回归不属于当前类目",
        )
    if getattr(regression_run, "status", None) != "completed":
        raise CandidateReleaseGateError(
            "candidate_run_incomplete",
            "候选回归尚未完成或存在失败条目",
        )

    bundle = _snapshot_bundle(regression_run)
    snapshot_prompt_bindings = bundle.get("prompt_bindings")
    if not isinstance(snapshot_prompt_bindings, Mapping):
        snapshot_contract = bundle.get("contract")
        snapshot_prompt_bindings = (
            snapshot_contract.get("prompt_bindings")
            if isinstance(snapshot_contract, Mapping)
            else None
        )
    if (
        bundle.get("candidate_revision_id") != getattr(candidate, "id", None)
        or bundle.get("category_key") != category_key
        or bundle.get("contract_hash") != getattr(candidate, "contract_hash", None)
        or snapshot_prompt_bindings != _candidate_prompt_bindings(candidate)
    ):
        raise CandidateReleaseGateError(
            "candidate_snapshot_mismatch",
            "候选回归快照与候选合同不一致",
        )

    previous = getattr(regression_run, "previous_run", None)
    if previous is None or getattr(regression_run, "previous_run_id", None) is None:
        raise CandidateReleaseGateError(
            "regression_not_comparable",
            "候选回归缺少同一基准集的对照运行",
        )
    if getattr(regression_run, "baseline_set_fingerprint", None) != getattr(
        previous, "baseline_set_fingerprint", None
    ):
        raise CandidateReleaseGateError(
            "regression_not_comparable",
            "候选回归与基准运行的基准集不一致",
        )

    baseline_metrics = _json_object(getattr(previous, "metrics_json", None))
    candidate_metrics = _json_object(getattr(regression_run, "metrics_json", None))
    if _metric_value(baseline_metrics, "denominator") <= 0 or _metric_value(
        candidate_metrics, "denominator"
    ) <= 0:
        raise CandidateReleaseGateError(
            "regression_not_comparable",
            "候选回归缺少有效的正样本分母",
        )

    exact_delta = _metric_value(candidate_metrics, "exact_accuracy") - _metric_value(
        baseline_metrics, "exact_accuracy"
    )
    adjacent_delta = _metric_value(
        candidate_metrics, "adjacent_accuracy"
    ) - _metric_value(baseline_metrics, "adjacent_accuracy")
    regressions: list[dict[str, Any]] = []
    if exact_delta < 0:
        regressions.append(
            {
                "code": "exact_accuracy_regressed",
                "message": "Exact Accuracy 低于基准",
                "delta": exact_delta,
            }
        )
    if adjacent_delta < 0:
        regressions.append(
            {
                "code": "adjacent_accuracy_regressed",
                "message": "Adjacent Accuracy 低于基准",
                "delta": adjacent_delta,
            }
        )
    if int(candidate_metrics.get("failed", 0) or 0) > int(
        baseline_metrics.get("failed", 0) or 0
    ):
        regressions.append(
            {
                "code": "failed_count_regressed",
                "message": "候选回归失败条目增加",
            }
        )

    baseline_field_metrics = build_baseline_field_metrics(db, previous)
    candidate_field_metrics = build_baseline_field_metrics(db, regression_run)
    regressions.extend(
        field_metric_release_regressions(
            baseline_field_metrics,
            candidate_field_metrics,
        )
    )
    approval_allowed = not regressions
    return {
        "schema_version": "baseline-correction-regression-v1",
        "run_id": getattr(regression_run, "id", None),
        "status": getattr(regression_run, "status", None),
        "comparable": True,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "baseline_field_metrics": baseline_field_metrics,
        "candidate_field_metrics": candidate_field_metrics,
        "exact_accuracy_delta": exact_delta,
        "adjacent_accuracy_delta": adjacent_delta,
        "regressions": regressions,
        "recommendation": "approve" if approval_allowed else "reject",
        "approval_allowed": approval_allowed,
        "idempotent": already_active,
    }
