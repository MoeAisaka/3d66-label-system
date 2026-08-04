"""Read-only compact status for the frozen inspiration baseline run."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import BaselineRegressionItem, BaselineRegressionRun, BaselineSet


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
                        "metrics": json.loads(run.metrics_json),
                    }
                    if run is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
