"""Read-only compact status for the frozen inspiration baseline run."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    BaselineRegressionItem,
    BaselineRegressionRun,
    BaselineSet,
    CircuitBreaker,
    EvaluationControl,
    EvaluationJob,
    QueueSchedulerState,
)


with SessionLocal() as db:
    golden = db.scalar(
        select(BaselineSet).where(
            BaselineSet.name == "灵感图人工评级黄金集-20260724-v2"
        )
    )
    run = (
        db.scalar(
            select(BaselineRegressionRun)
            .where(BaselineRegressionRun.baseline_set_id == golden.id)
            .order_by(BaselineRegressionRun.sequence_no.desc())
            .limit(1)
        )
        if golden is not None
        else None
    )
    item_status = (
        dict(
            db.execute(
                select(BaselineRegressionItem.status, func.count())
                .where(BaselineRegressionItem.run_id == run.id)
                .group_by(BaselineRegressionItem.status)
            ).all()
        )
        if run is not None
        else {}
    )
    error_samples = (
        db.execute(
            select(
                BaselineRegressionItem.id,
                BaselineRegressionItem.asset_id,
                BaselineRegressionItem.error_message,
                EvaluationJob.technical_error_type,
                EvaluationJob.error_message,
            )
            .join(EvaluationJob, EvaluationJob.id == BaselineRegressionItem.job_id)
            .where(
                BaselineRegressionItem.run_id == run.id,
                BaselineRegressionItem.status == "failed",
            )
            .order_by(BaselineRegressionItem.id)
            .limit(10)
        ).all()
        if run is not None
        else []
    )
    job_status = (
        dict(
            db.execute(
                select(EvaluationJob.status, func.count())
                .join(
                    BaselineRegressionItem,
                    BaselineRegressionItem.job_id == EvaluationJob.id,
                )
                .where(BaselineRegressionItem.run_id == run.id)
                .group_by(EvaluationJob.status)
            ).all()
        )
        if run is not None
        else {}
    )
    control = db.get(EvaluationControl, 1)
    scheduler = db.get(QueueSchedulerState, 1)
    open_breakers = db.scalars(
        select(CircuitBreaker)
        .where(CircuitBreaker.state == "open")
        .order_by(CircuitBreaker.id)
    ).all()
    print(
        json.dumps(
            {
                "baseline_set_id": golden.id if golden is not None else None,
                "run": (
                    {
                        "id": run.id,
                        "sequence_no": run.sequence_no,
                        "category_key": run.category_key,
                        "status": run.status,
                        "total": run.total,
                        "completed": run.completed,
                        "valid_predictions": run.valid_predictions,
                        "failed": run.failed,
                        "item_status": item_status,
                        "job_status": job_status,
                        "error_samples": [
                            {
                                "item_id": item_id,
                                "asset_id": asset_id,
                                "error": error,
                                "technical_error_type": technical_error_type,
                                "job_error": job_error,
                            }
                            for (
                                item_id,
                                asset_id,
                                error,
                                technical_error_type,
                                job_error,
                            ) in error_samples
                        ],
                        "metrics": json.loads(run.metrics_json),
                        "evaluation_paused": control.paused if control else None,
                        "scheduler_global_limit": (
                            scheduler.global_limit if scheduler else None
                        ),
                        "open_breakers": [
                            {
                                "scope_type": breaker.scope_type,
                                "scope_key": breaker.scope_key,
                                "failure_count": breaker.failure_count,
                                "reason": breaker.reason,
                                "cooldown_until": (
                                    breaker.cooldown_until.isoformat()
                                    if breaker.cooldown_until
                                    else None
                                ),
                            }
                            for breaker in open_breakers
                        ],
                    }
                    if run is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
