#!/usr/bin/env python3
"""Operate the inspiration golden baseline and periodic drift test.

Examples (run from repository root):

    DATA_DIR=/path/to/data backend/.venv/bin/python scripts/inspiration_golden_workflow.py create-golden
    DATA_DIR=/path/to/data backend/.venv/bin/python scripts/inspiration_golden_workflow.py create-run --baseline-set-id 12
    DATA_DIR=/path/to/data backend/.venv/bin/python scripts/inspiration_golden_workflow.py metrics --run-id 34
    DATA_DIR=/path/to/data backend/.venv/bin/python scripts/inspiration_golden_workflow.py apply-auto --run-id 34
    DATA_DIR=/path/to/data backend/.venv/bin/python scripts/inspiration_golden_workflow.py drift-test --run-id 34 --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _emit(payload: dict[str, Any], output: str | None = None) -> None:
    def serialize(value: Any) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=serialize,
    )
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _policy(args: argparse.Namespace):
    from app.inspiration_auto_correction import AutoCorrectionPolicy

    return AutoCorrectionPolicy(
        confidence_threshold=args.confidence_threshold,
        minimum_support=args.minimum_support,
        coverage_rate=args.coverage_rate,
        calibration_fraction=args.calibration_fraction,
        maximum_level_shift=args.maximum_level_shift,
    )


def _metrics(run: Any) -> dict[str, Any]:
    from app.baseline_regression import LEVELS, compute_level_metrics

    rows: list[dict[str, Any]] = []
    by_rating: dict[str, dict[str, Any]] = {}
    valid_total = 0
    rank_delta_sum = 0
    system_higher_better = 0
    system_lower_worse = 0
    for item in run.items:
        snapshot = json.loads(item.result_snapshot_json or "{}")
        predicted = snapshot.get("predicted_level")
        rows.append(
            {
                "status": item.status,
                "expected_level": item.expected_level,
                "predicted_level": predicted,
            }
        )
        asset_snapshot = json.loads(item.baseline_set_item.asset_snapshot_json)
        rating = str(asset_snapshot.get("human_rating") or item.expected_level)
        bucket = by_rating.setdefault(
            rating,
            {
                "count": 0,
                "valid": 0,
                "exact": 0,
                "system_higher_better": 0,
                "system_lower_worse": 0,
                "rank_delta_sum": 0,
            },
        )
        bucket["count"] += 1
        if predicted in LEVELS:
            bucket["valid"] += 1
            expected_rank = LEVELS.index(item.expected_level)
            predicted_rank = LEVELS.index(predicted)
            delta = predicted_rank - expected_rank
            valid_total += 1
            rank_delta_sum += delta
            system_higher_better += int(delta < 0)
            system_lower_worse += int(delta > 0)
            bucket["rank_delta_sum"] += delta
            bucket["exact"] += int(delta == 0)
            bucket["system_higher_better"] += int(delta < 0)
            bucket["system_lower_worse"] += int(delta > 0)
    for bucket in by_rating.values():
        valid = bucket["valid"]
        bucket["exact_accuracy"] = bucket["exact"] / valid if valid else 0.0
        bucket["mean_rank_delta"] = bucket["rank_delta_sum"] / valid if valid else None
        bucket["bias_direction"] = (
            "系统偏低（判得更差）"
            if bucket["rank_delta_sum"] > 0
            else "系统偏高（判得更好）"
            if bucket["rank_delta_sum"] < 0
            else "无净偏差"
        )
    return {
        "overall": compute_level_metrics(rows),
        "by_rating": by_rating,
        "systematic_bias": {
            "valid": valid_total,
            "mean_rank_delta": rank_delta_sum / valid_total if valid_total else None,
            "system_higher_better": system_higher_better,
            "system_lower_worse": system_lower_worse,
            "direction": (
                "系统整体偏低（判得更差）"
                if rank_delta_sum > 0
                else "系统整体偏高（判得更好）"
                if rank_delta_sum < 0
                else "无净偏差"
            ),
        },
    }


def _sample_details(run: Any, *, per_level: int) -> dict[str, Any]:
    """按人工等级稳定抽取实际评分明细，不改结果、不触发纠偏。"""
    from app.baseline_regression import LEVELS

    selected: list[dict[str, Any]] = []
    counts = {level: 0 for level in LEVELS}
    for item in sorted(run.items, key=lambda value: value.id):
        if item.status != "completed" or counts.get(item.expected_level, per_level) >= per_level:
            continue
        snapshot = json.loads(item.result_snapshot_json or "{}")
        scoring = (
            json.loads(item.evaluation.scoring_json or "{}")
            if item.evaluation is not None
            else {}
        )
        asset = json.loads(item.baseline_set_item.asset_snapshot_json or "{}")
        selected.append(
            {
                "baseline_item_id": item.id,
                "asset_id": item.asset_id,
                "name": asset.get("name"),
                "human_rating": asset.get("human_rating"),
                "expected_level": item.expected_level,
                "predicted_level": snapshot.get("predicted_level"),
                "score": snapshot.get("authoritative_score"),
                "track_key": scoring.get("track_key"),
                "dimension_scoring_mode": scoring.get("dimension_scoring_mode"),
                "dimension_evidence": scoring.get("dimension_evidence") or {},
                "caps": scoring.get("caps") or [],
                "needs_review": snapshot.get("needs_review"),
                "versions": snapshot.get("versions") or {},
            }
        )
        counts[item.expected_level] += 1
    return {
        "run_id": run.id,
        "selection": "baseline_item_id_ascending_per_expected_level",
        "per_level": per_level,
        "counts": counts,
        "items": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        help="Application DATA_DIR. Prefer setting DATA_DIR in the environment.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-golden")
    create.add_argument("--output")
    balanced = subparsers.add_parser("create-balanced-100")
    balanced.add_argument("--output")
    create_run = subparsers.add_parser("create-run")
    create_run.add_argument("--baseline-set-id", type=int, required=True)
    create_run.add_argument("--output")
    status = subparsers.add_parser("status")
    status.add_argument("--run-id", type=int, required=True)
    status.add_argument("--output")
    metrics = subparsers.add_parser("metrics")
    metrics.add_argument("--run-id", type=int, required=True)
    metrics.add_argument("--output")
    samples = subparsers.add_parser("sample-report")
    samples.add_argument("--run-id", type=int, required=True)
    samples.add_argument("--per-level", type=int, default=4)
    samples.add_argument("--output")
    for name in ("apply-auto", "drift-test"):
        command = subparsers.add_parser(name)
        command.add_argument("--run-id", type=int, required=True)
        command.add_argument("--confidence-threshold", type=float, default=0.85)
        command.add_argument("--minimum-support", type=int, default=30)
        command.add_argument("--coverage-rate", type=float, default=0.10)
        command.add_argument("--calibration-fraction", type=float, default=0.70)
        command.add_argument("--maximum-level-shift", type=int, default=1)
        command.add_argument("--output")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    from app.database import SessionLocal, init_database
    from app.inspiration_auto_correction import (
        apply_auto_correction_to_run,
        build_drift_report,
        ensure_inspiration_balanced_golden_set,
        ensure_inspiration_golden_set,
    )
    from app.models import BaselineRegressionRun

    init_database()
    with SessionLocal() as db:
        if args.command == "create-golden":
            golden, report = ensure_inspiration_golden_set(db)
            _emit({"baseline_set_id": golden.id, **report}, args.output)
            return 0
        if args.command == "create-balanced-100":
            golden, report = ensure_inspiration_balanced_golden_set(db)
            _emit({"baseline_set_id": golden.id, **report}, args.output)
            return 0
        if args.command == "create-run":
            # Reuse the production API service function so prompt/model/profile
            # validation and EvaluationJob.category_key are identical to UI runs.
            from app.main import BaselineRunCreateRequest, create_baseline_run

            payload = create_baseline_run(
                args.baseline_set_id,
                BaselineRunCreateRequest(execution_mode="structured"),
                SimpleNamespace(username="inspiration-golden-workflow"),
                db,
            )
            _emit(payload, args.output)
            return 0
        run = db.get(BaselineRegressionRun, args.run_id)
        if run is None:
            parser.error(f"baseline run #{args.run_id} 不存在")
        if args.command == "status":
            _emit(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "total": run.total,
                    "completed": run.completed,
                    "valid_predictions": run.valid_predictions,
                    "failed": run.failed,
                    "metrics": json.loads(run.metrics_json or "{}"),
                },
                args.output,
            )
        elif args.command == "metrics":
            _emit({"run_id": run.id, **_metrics(run)}, args.output)
        elif args.command == "sample-report":
            _emit(_sample_details(run, per_level=max(1, args.per_level)), args.output)
        elif args.command == "apply-auto":
            _emit(
                apply_auto_correction_to_run(db, run=run, policy=_policy(args)),
                args.output,
            )
        else:
            _emit(build_drift_report(run, policy=_policy(args)), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
