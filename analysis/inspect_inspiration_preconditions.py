"""Read-only test DB preflight for the inspiration golden regression."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import (
    BaselineSet,
    CategoryEvaluationV3Config,
    EvaluationCategoryProfile,
    ModelConfig,
    PromptVersion,
)


with SessionLocal() as db:
    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "inspiration_image"
        )
    )
    prompts = db.scalars(
        select(PromptVersion)
        .where(PromptVersion.category_key == "inspiration_image")
        .order_by(PromptVersion.id)
    ).all()
    configs = db.scalars(
        select(CategoryEvaluationV3Config)
        .where(CategoryEvaluationV3Config.category_key == "inspiration_image")
        .order_by(CategoryEvaluationV3Config.id)
    ).all()
    golden = db.scalar(
        select(BaselineSet).where(
            BaselineSet.name == "灵感图人工评级黄金集-20260724"
        )
    )
    print(
        json.dumps(
            {
                "profile": (
                    {
                        "id": profile.id,
                        "status": profile.status,
                        "prompt_a_id": profile.prompt_a_id,
                        "prompt_b_id": profile.prompt_b_id,
                        "model_config_id": profile.model_config_id,
                        "pipeline_config_json": profile.pipeline_config_json,
                    }
                    if profile
                    else None
                ),
                "prompts": [
                    {
                        "id": item.id,
                        "stage": item.stage,
                        "status": item.status,
                        "pipeline_scope": item.pipeline_scope,
                        "version": item.version,
                        "content_length": len(
                            (item.system_prompt or "") + (item.user_prompt or "")
                        ),
                        "content_sha256": hashlib.sha256(
                            (
                                (item.system_prompt or "")
                                + "\n"
                                + (item.user_prompt or "")
                            ).encode("utf-8")
                        ).hexdigest(),
                    }
                    for item in prompts
                ],
                "active_model_count": db.scalar(
                    select(func.count(ModelConfig.id)).where(
                        ModelConfig.active.is_(True)
                    )
                ),
                "v3_configs": [
                    {
                        "id": item.id,
                        "status": item.status,
                        "revision": item.revision,
                        "contract_hash": item.contract_hash,
                    }
                    for item in configs
                ],
                "golden_set": (
                    {
                        "id": golden.id,
                        "category_key": golden.category_key,
                        "fingerprint": golden.fingerprint,
                        "item_count": len(golden.items),
                    }
                    if golden
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
