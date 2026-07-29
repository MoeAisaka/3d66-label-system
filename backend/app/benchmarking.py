from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import append_audit_event, canonical_json
from .models import ModelBenchmarkExperiment, ModelBenchmarkVariant


MODEL_KEYS = ("sol", "terra", "luna")


class BenchmarkAdapter(Protocol):
    def evaluate(
        self,
        *,
        model_key: str,
        frozen_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]: ...


class DisabledBenchmarkAdapter:
    def evaluate(
        self,
        *,
        model_key: str,
        frozen_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del model_key, frozen_snapshot
        raise RuntimeError("benchmark_executor_not_configured")


@dataclass(frozen=True)
class DeterministicBenchmarkAdapter:
    observations: dict[str, list[dict[str, Any]]]

    def evaluate(
        self,
        *,
        model_key: str,
        frozen_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del frozen_snapshot
        if model_key not in self.observations:
            raise ValueError(f"测试观测缺少 {model_key}")
        return self.observations[model_key]


def snapshot_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def calculate_variant_metrics(
    observations: list[dict[str, Any]],
    pricing: dict[str, int],
    *,
    low_confidence_threshold: float,
) -> dict[str, Any]:
    if not observations:
        raise ValueError("横评观测不能为空")
    required = {
        "correct",
        "error_severity",
        "confidence",
        "needs_human",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "retry_count",
    }
    for row in observations:
        if not required.issubset(row):
            raise ValueError("横评观测缺少指标字段")
        if not 0 <= float(row["confidence"]) <= 1:
            raise ValueError("横评 confidence 超出范围")
        if int(row["latency_ms"]) < 0 or int(row["retry_count"]) < 0:
            raise ValueError("横评耗时或重试次数非法")

    total = len(observations)
    correct = sum(bool(row["correct"]) for row in observations)
    p0_p1 = sum(
        row["error_severity"] in {"P0", "P1"} for row in observations
    )
    low_confidence = sum(
        float(row["confidence"]) < low_confidence_threshold
        for row in observations
    )
    human = sum(bool(row["needs_human"]) for row in observations)
    retried = sum(int(row["retry_count"]) > 0 for row in observations)
    input_tokens = sum(int(row["input_tokens"]) for row in observations)
    output_tokens = sum(int(row["output_tokens"]) for row in observations)
    model_cost = round(
        input_tokens
        * int(pricing.get("input_micros_per_million_tokens", 0))
        / 1_000_000
        + output_tokens
        * int(pricing.get("output_micros_per_million_tokens", 0))
        / 1_000_000
    )
    human_cost = human * int(pricing.get("human_review_cost_micros", 0))
    latencies = [float(row["latency_ms"]) for row in observations]
    return {
        "sample_size": total,
        "quality_accuracy": correct / total,
        "p0_p1_error_count": p0_p1,
        "low_confidence_rate": low_confidence / total,
        "human_review_rate": human / total,
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "model_cost_micros": model_cost,
        "total_cost_with_human_micros": model_cost + human_cost,
        "retry_stability": 1 - retried / total,
    }


def _passes_gate(
    metrics: dict[str, Any], quality_gate: dict[str, Any]
) -> bool:
    return bool(
        metrics["quality_accuracy"]
        >= float(quality_gate["min_quality_accuracy"])
        and metrics["p0_p1_error_count"]
        <= int(quality_gate["max_p0_p1_errors"])
        and metrics["retry_stability"]
        >= float(quality_gate.get("min_retry_stability", 0))
    )


def select_benchmark_candidate(
    variants: list[dict[str, Any]],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    gated = [
        item
        for item in variants
        if _passes_gate(item["metrics"], quality_gate)
    ]
    if not gated:
        return {
            "recommendation": "none",
            "reason": "no_variant_passed_quality_gate",
            "quality_gate": quality_gate,
            "pareto_model_keys": [],
        }

    pareto: list[dict[str, Any]] = []
    for candidate in gated:
        candidate_metrics = candidate["metrics"]
        dominated = any(
            other["model_key"] != candidate["model_key"]
            and other["metrics"]["quality_accuracy"]
            >= candidate_metrics["quality_accuracy"]
            and other["metrics"]["total_cost_with_human_micros"]
            <= candidate_metrics["total_cost_with_human_micros"]
            and other["metrics"]["latency_p95_ms"]
            <= candidate_metrics["latency_p95_ms"]
            and (
                other["metrics"]["quality_accuracy"]
                > candidate_metrics["quality_accuracy"]
                or other["metrics"]["total_cost_with_human_micros"]
                < candidate_metrics["total_cost_with_human_micros"]
                or other["metrics"]["latency_p95_ms"]
                < candidate_metrics["latency_p95_ms"]
            )
            for other in gated
        )
        if not dominated:
            pareto.append(candidate)

    max_cost = max(
        item["metrics"]["total_cost_with_human_micros"] for item in pareto
    ) or 1
    max_latency = max(item["metrics"]["latency_p95_ms"] for item in pareto) or 1
    scored = []
    for item in pareto:
        metrics = item["metrics"]
        score = (
            metrics["quality_accuracy"] * 0.60
            + (1 - metrics["total_cost_with_human_micros"] / max_cost) * 0.25
            + (1 - metrics["latency_p95_ms"] / max_latency) * 0.10
            + metrics["retry_stability"] * 0.05
        )
        scored.append((score, item))
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1]["metrics"]["total_cost_with_human_micros"],
            pair[1]["model_key"],
        )
    )
    winner = scored[0][1]
    return {
        "recommendation": winner["model_key"],
        "reason": "quality_gate_then_pareto_composite_cost",
        "quality_gate": quality_gate,
        "pareto_model_keys": [item["model_key"] for item in pareto],
        "composite_score": round(scored[0][0], 6),
        "requires_human_decision": True,
        "automatically_changes_production": False,
    }


def run_benchmark_experiment(
    db: Session,
    *,
    experiment: ModelBenchmarkExperiment,
    adapter: BenchmarkAdapter,
    actor: str,
) -> ModelBenchmarkExperiment:
    if experiment.execution_mode != "test":
        raise ValueError("横评执行器未配置；只允许显式测试模式")
    if experiment.status not in {"draft", "failed"}:
        raise ValueError("当前横评状态不可执行")
    now = datetime.now(timezone.utc)
    snapshot = json.loads(experiment.frozen_snapshot_json)
    quality_gate = json.loads(experiment.quality_gate_json)
    experiment.status = "running"
    experiment.started_at = now
    variants = db.scalars(
        select(ModelBenchmarkVariant)
        .where(ModelBenchmarkVariant.experiment_id == experiment.id)
        .order_by(ModelBenchmarkVariant.model_key.asc())
    ).all()
    summaries: list[dict[str, Any]] = []
    try:
        for variant in variants:
            variant.status = "running"
            variant.started_at = now
            observations = adapter.evaluate(
                model_key=variant.model_key,
                frozen_snapshot=snapshot,
            )
            metrics = calculate_variant_metrics(
                observations,
                json.loads(variant.pricing_json),
                low_confidence_threshold=float(
                    quality_gate.get("low_confidence_threshold", 0.7)
                ),
            )
            variant.observations_json = canonical_json(observations)
            variant.metrics_json = canonical_json(metrics)
            variant.status = "completed"
            variant.finished_at = now
            summaries.append(
                {"model_key": variant.model_key, "metrics": metrics}
            )
        experiment.decision_json = canonical_json(
            select_benchmark_candidate(summaries, quality_gate)
        )
        experiment.status = "completed"
        experiment.finished_at = now
        append_audit_event(
            db,
            category="model_benchmark",
            action="completed",
            subject_type="model_benchmark_experiment",
            subject_id=experiment.id,
            actor=actor,
            payload=json.loads(experiment.decision_json),
            event_key=f"model-benchmark-completed:{experiment.experiment_key}",
        )
    except Exception as exc:
        experiment.status = "failed"
        experiment.finished_at = now
        for variant in variants:
            if variant.status == "running":
                variant.status = "failed"
                variant.error_message = str(exc)[:300]
                variant.finished_at = now
        append_audit_event(
            db,
            category="model_benchmark",
            action="failed",
            subject_type="model_benchmark_experiment",
            subject_id=experiment.id,
            actor=actor,
            payload={"error": str(exc)[:300]},
            event_key=f"model-benchmark-failed:{experiment.experiment_key}",
        )
        raise
    return experiment
