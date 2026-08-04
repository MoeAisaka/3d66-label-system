"""One-off pre-deploy bootstrap for the c0d20db test container.

Run only inside the existing application container.  It creates an immutable
cross-category-reference BaselineSet and starts (or reuses) its first
``inspiration_image`` run without changing any ``Asset.category_key`` value.
The production module supersedes this compatibility script after deployment.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from types import SimpleNamespace

from sqlalchemy import func, select

from app.baseline_regression import (
    baseline_set_fingerprint,
    canonical_json,
)
from app.database import SessionLocal
from app.main import BaselineRunCreateRequest, create_baseline_run
from app.models import (
    Asset,
    BaselineRegressionRun,
    BaselineSet,
    BaselineSetItem,
    EvaluationCategoryProfile,
    ModelConfig,
    PromptVersion,
)


NAME = "灵感图人工评级黄金集-20260724-v2"
TRUTH_SOURCE = "灵感图人工评级集-20260724"
RATING_TO_LEVEL = {
    "好": "L1",
    "中等": "L2",
    "中差": "L3",
    "极差": "L4",
    "过滤": "L5",
}
EXPECTED_DISTRIBUTION = {
    "好": 188,
    "中等": 622,
    "中差": 811,
    "极差": 237,
    "过滤": 427,
}
PATTERN = re.compile(r"(?:^|/|_)(好|中等|中差|极差|过滤)_")


with SessionLocal() as db:
    # Soft deletion keeps the binary and history by contract.  The frozen
    # human corpus therefore follows asset identity, independent of UI status.
    assets = db.scalars(select(Asset).order_by(Asset.id)).all()
    selected = []
    for asset in assets:
        match = PATTERN.search(asset.original_name or "")
        if match:
            rating = match.group(1)
            selected.append((asset, rating, RATING_TO_LEVEL[rating]))
    if not selected:
        raise SystemExit("no human-rated assets found")
    distribution = Counter(rating for _asset, rating, _level in selected)
    if distribution != EXPECTED_DISTRIBUTION:
        raise SystemExit(
            f"unexpected rating distribution: expected={EXPECTED_DISTRIBUTION}, actual={dict(distribution)}"
        )
    fingerprint = baseline_set_fingerprint(
        (
            {
                "asset_id": asset.id,
                "asset_sha256": asset.sha256,
                "expected_level": level,
            }
            for asset, _rating, level in selected
        ),
        category_key="inspiration_image",
    )
    golden = db.scalar(select(BaselineSet).where(BaselineSet.name == NAME))
    created = golden is None
    if golden is None:
        golden = BaselineSet(
            category_key="inspiration_image",
            name=NAME,
            description="图片级人工真值；原始 asset.category_key 保持不变。",
            default_expected_level="L3",
            fingerprint=fingerprint,
            created_by="inspiration-golden-predeploy",
        )
        db.add(golden)
        db.flush()
        for asset, rating, level in selected:
            db.add(
                BaselineSetItem(
                    baseline_set_id=golden.id,
                    asset_id=asset.id,
                    source_package_id=None,
                    expected_level=level,
                    asset_snapshot_json=canonical_json(
                        {
                            "schema_version": "baseline-asset-v1",
                            "asset_id": asset.id,
                            "category_key": "inspiration_image",
                            "asset_source_category_key": asset.category_key,
                            "name": asset.original_name,
                            "sha256": asset.sha256,
                            "mime_type": asset.mime_type,
                            "size_bytes": asset.size_bytes,
                            "width": asset.width,
                            "height": asset.height,
                            "source_package_id": None,
                            "expected_level_source": "human_filename_rating",
                            "human_rating": rating,
                            "truth_updated_by": TRUTH_SOURCE,
                            "truth_source": TRUTH_SOURCE,
                            "created_at": asset.created_at.isoformat(),
                        }
                    ),
                )
            )
            if len(db.new) >= 500:
                db.flush()
        db.flush()
        persisted_count = db.scalar(
            select(func.count(BaselineSetItem.id)).where(
                BaselineSetItem.baseline_set_id == golden.id
            )
        )
        if persisted_count != len(selected):
            raise SystemExit(
                f"incomplete baseline set: expected={len(selected)}, actual={persisted_count}"
            )
        db.commit()
        db.refresh(golden)
    elif golden.category_key != "inspiration_image" or golden.fingerprint != fingerprint:
        raise SystemExit("existing golden set does not match immutable fingerprint")

    profile = db.scalar(
        select(EvaluationCategoryProfile).where(
            EvaluationCategoryProfile.category_key == "inspiration_image"
        )
    )
    if profile is None:
        raise SystemExit("inspiration_image profile missing")
    source_prompts = {}
    for prompt in db.scalars(
        select(PromptVersion)
        .where(
            PromptVersion.category_key == "inspiration_image",
            PromptVersion.status == "published",
            PromptVersion.stage.in_(("A", "B")),
        )
        .order_by(PromptVersion.id.desc())
    ).all():
        source_prompts.setdefault(prompt.stage, prompt)
    if set(source_prompts) != {"A", "B"}:
        raise SystemExit("published inspiration A/B prompts missing")

    prompt_clones = {}
    for stage in ("A", "B"):
        version = f"inspiration-{stage.lower()}-v1-baseline"
        clone = db.scalar(
            select(PromptVersion).where(
                PromptVersion.category_key == "inspiration_image",
                PromptVersion.version == version,
            )
        )
        if clone is None:
            source = source_prompts[stage]
            clone = PromptVersion(
                category_key="inspiration_image",
                pipeline_scope="shared",
                stage=stage,
                name=f"{source.name}（基线回归共享）",
                version=version,
                system_prompt=source.system_prompt,
                user_prompt=source.user_prompt,
                rubric_version=source.rubric_version,
                status="published",
                source="manual",
                rollback_prompt_id=source.id,
            )
            db.add(clone)
            db.flush()
        prompt_clones[stage] = clone
    active_model = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.active.is_(True))
        .order_by(ModelConfig.id)
        .limit(1)
    )
    if active_model is None:
        raise SystemExit("active model config missing")
    old_profile_pointers = {
        "prompt_a_id": profile.prompt_a_id,
        "prompt_b_id": profile.prompt_b_id,
        "model_config_id": profile.model_config_id,
    }
    profile.prompt_a_id = prompt_clones["A"].id
    profile.prompt_b_id = prompt_clones["B"].id
    profile.model_config_id = active_model.id
    db.commit()

    running = db.scalar(
        select(BaselineRegressionRun)
        .where(
            BaselineRegressionRun.baseline_set_id == golden.id,
            BaselineRegressionRun.status == "running",
        )
        .order_by(BaselineRegressionRun.sequence_no.desc())
        .limit(1)
    )
    run_payload = (
        {
            "id": running.id,
            "status": running.status,
            "total": running.total,
            "job_ids": [item.job_id for item in running.items],
            "idempotent": True,
        }
        if running is not None
        else create_baseline_run(
            golden.id,
            BaselineRunCreateRequest(execution_mode="structured"),
            SimpleNamespace(username="inspiration-golden-predeploy"),
            db,
        )
    )
    print(
        json.dumps(
            {
                "baseline_set_id": golden.id,
                "baseline_set_created": created,
                "fingerprint": fingerprint,
                "item_count": len(selected),
                "distribution": distribution,
                "source_category_distribution": Counter(
                    asset.category_key for asset, _rating, _level in selected
                ),
                "asset_category_mutations": 0,
                "profile_old_pointers": old_profile_pointers,
                "profile_new_pointers": {
                    "prompt_a_id": profile.prompt_a_id,
                    "prompt_b_id": profile.prompt_b_id,
                    "model_config_id": profile.model_config_id,
                },
                "run": run_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
