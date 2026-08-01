from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EvaluationJob, ModelConfig, ModelNodeBinding


def model_has_credentials(model: ModelConfig | None) -> bool:
    return bool(
        model is not None
        and model.encrypted_api_key
        and model.encrypted_api_key.strip()
    )


def default_evaluation_model(db: Session) -> ModelConfig | None:
    binding = db.scalar(
        select(ModelNodeBinding).where(
            ModelNodeBinding.node_key == "evaluation_main",
            ModelNodeBinding.category_key.is_(None),
            ModelNodeBinding.enabled.is_(True),
        )
    )
    if (
        binding is not None
        and binding.model.active
        and model_has_credentials(binding.model)
    ):
        return binding.model
    for model in db.scalars(
        select(ModelConfig)
        .where(ModelConfig.active.is_(True))
        .order_by(ModelConfig.id.asc())
    ):
        if model_has_credentials(model):
            return model
    return None


def frozen_job_model_ids(job: EvaluationJob) -> tuple[int, ...] | None:
    if not job.category_profile_snapshot_json:
        return None
    try:
        snapshot = json.loads(job.category_profile_snapshot_json)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(snapshot, dict):
        return ()
    main_id = snapshot.get("model_config_id")
    if not isinstance(main_id, int) or isinstance(main_id, bool) or main_id < 1:
        return ()
    model_ids = [main_id]
    summary_id = snapshot.get("pdf_summary_model_config_id")
    if summary_id is not None:
        if (
            not isinstance(summary_id, int)
            or isinstance(summary_id, bool)
            or summary_id < 1
        ):
            return ()
        model_ids.append(summary_id)
    return tuple(dict.fromkeys(model_ids))


def job_primary_model(db: Session, job: EvaluationJob) -> ModelConfig | None:
    model_ids = frozen_job_model_ids(job)
    if not model_ids:
        return None
    return db.get(ModelConfig, model_ids[0])


def job_has_required_credentials(
    db: Session,
    job: EvaluationJob,
    *,
    fallback_model: ModelConfig | None,
) -> bool:
    model_ids = frozen_job_model_ids(job)
    if model_ids is None:
        return model_has_credentials(fallback_model)
    if not model_ids:
        return False
    return all(model_has_credentials(db.get(ModelConfig, model_id)) for model_id in model_ids)
