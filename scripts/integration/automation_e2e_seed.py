#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image


DIMENSION_KEYS = (
    "composition_viewpoint",
    "lighting_atmosphere",
    "color_material",
    "spatial_design_furnishing",
    "visual_hierarchy",
    "detail_completion",
    "inspiration_reference",
    "presentation_integrity",
)
E2E_DATA_MARKER = ".automation-e2e-owned"


def _prepare_isolated_data_dir(
    data_dir: Path,
    *,
    repo_root: Path,
) -> Path:
    """Claim an isolated E2E directory without risking an existing database."""
    resolved = data_dir.resolve()
    forbidden = {Path("/"), Path("/data"), repo_root.resolve()}
    if resolved in forbidden:
        raise RuntimeError(f"seed_forbidden_data_directory:{resolved}")

    marker = resolved / E2E_DATA_MARKER
    database_path = resolved / "database" / "app.db"
    if database_path.exists() and not marker.is_file():
        raise RuntimeError(f"seed_unowned_existing_database:{database_path}")
    if marker.exists() and not marker.is_file():
        raise RuntimeError(f"seed_invalid_ownership_marker:{marker}")

    resolved.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text("automation-e2e-owned-v1\n", encoding="utf-8")
    return resolved


def _bind_profile_baseline_contract(
    *,
    profile: Any,
    baseline_bundle: Any,
    dimension_schema_key: str,
    dimension_schema_version: str,
) -> dict[str, Any]:
    """Bind one category profile to identities frozen by its baseline bundle."""
    try:
        dimension_set = json.loads(
            baseline_bundle.dimension_schema_set_snapshot or "{}"
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("seed_baseline_dimension_contract_invalid") from exc
    schemas = (
        dimension_set.get("schemas")
        if isinstance(dimension_set, dict)
        else None
    )
    matching_schemas = [
        item
        for item in schemas or []
        if isinstance(item, dict)
        and item.get("schema_key") == dimension_schema_key
        and item.get("version") == dimension_schema_version
    ]
    if len(matching_schemas) != 1:
        raise RuntimeError("seed_baseline_dimension_contract_missing")

    try:
        automation_config = json.loads(profile.automation_config_json or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("seed_profile_automation_config_invalid") from exc
    if not isinstance(automation_config, dict):
        raise RuntimeError("seed_profile_automation_config_not_object")
    automation_config["baseline_strategy_bundle_id"] = baseline_bundle.id
    profile.automation_config_json = json.dumps(
        automation_config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    profile.dimension_schema_key = dimension_schema_key
    profile.dimension_schema_version = dimension_schema_version
    return {
        "baseline_strategy_bundle_id": baseline_bundle.id,
        "dimension_schema_key": dimension_schema_key,
        "dimension_schema_version": dimension_schema_version,
    }


def _precheck() -> dict[str, Any]:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "住宅设计",
            "primary_confidence": 0.95,
        },
        "image_quality": {
            "quality_severity": "normal",
            "confidence": 0.95,
            "evidence": ["人工确认素材清晰"],
        },
        "media_form": {
            "real_photo": {"status": "yes", "confidence": 0.95},
            "rendering": {"status": "no", "confidence": 0.95},
            "ai_generated": {"status": "no", "confidence": 0.95},
            "professional_photography": {"status": "no", "confidence": 0.95},
            "casual_snapshot": {"status": "no", "confidence": 0.95},
            "documentary_record": {"status": "no", "confidence": 0.95},
            "collage_or_multiview": {"status": "no", "confidence": 0.95},
            "unfinished_scene": {"status": "no", "confidence": 0.95},
            "white_background_product": {"status": "no", "confidence": 0.95},
        },
    }


def _aesthetic(*, color_grade: int = 3) -> dict[str, Any]:
    return {
        "dimensions": {
            key: {
                "grade": color_grade if key == "color_material" else 3,
                "evidence": [f"人工冻结的 {key} 证据"],
                "defects": [],
            }
            for key in DIMENSION_KEYS
        },
        "decision_rules": {
            "hard_gate_triggered": False,
            "hard_gate_target": "none",
            "hard_gate_reasons": [],
            "level_cap": "none",
            "level_cap_reasons": [],
        },
        "needs_review": False,
        "review_reasons": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model-base-url", default="http://127.0.0.1:19091/v1")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    data_dir = _prepare_isolated_data_dir(
        args.data_dir,
        repo_root=repo_root,
    )
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["API_KEY_MASTER_KEY_FILE"] = str(data_dir / "secrets" / "master.key")
    sys.path.insert(0, str(repo_root / "backend"))

    from sqlalchemy import select

    from app.database import init_database, session_scope
    from app.dimension_schema_registry import (
        ACTIVE_V13_VERSION,
        SPACE_SCHEMA_KEY,
    )
    from app.models import (
        Asset,
        AutomationPolicy,
        EvaluationCategoryProfile,
        EvaluationJob,
        EvaluationResult,
        HumanReview,
        ModelConfig,
        ModelNodeBinding,
        OptimizerConfig,
        PromptVersion,
        SampleSet,
        SampleSetItem,
        SamplingPolicy,
        User,
    )
    from app.production_feedback import ingest_production_feedback
    from app.scoring import ENGINE_VERSION
    from app.security import (
        MODEL_CONFIG_KEYCHAIN_ACCOUNT,
        OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT,
        _protect_file_aead,
        hash_password,
    )
    from app.seed import seed_defaults
    from app.strategy_bundle import (
        build_evaluation_strategy_snapshot,
        get_or_create_bundle,
    )
    from app.optimization_automation import category_bundle_contract_errors

    init_database()
    with session_scope() as db:
        seed_defaults(db)
        e2e_password = os.getenv("LABEL_LAB_E2E_PASSWORD")
        if e2e_password:
            e2e_user = db.scalar(select(User).where(User.username == "sol"))
            if e2e_user is None:
                raise RuntimeError("seed_admin_user_missing")
            e2e_user.password_hash = hash_password(e2e_password)
        model = db.scalar(select(ModelConfig).order_by(ModelConfig.id.asc()))
        optimizer = db.scalar(select(OptimizerConfig).order_by(OptimizerConfig.id.asc()))
        profile = db.scalar(
            select(EvaluationCategoryProfile).where(
                EvaluationCategoryProfile.category_key == "space_image"
            )
        )
        policy = db.get(AutomationPolicy, 1)
        sampling = db.get(SamplingPolicy, 1)
        assert model is not None and optimizer is not None
        assert profile is not None and policy is not None and sampling is not None

        prompt_a = db.scalar(
            select(PromptVersion).where(PromptVersion.version == "automation-e2e-a-v1")
        )
        if prompt_a is None:
            prompt_a = PromptVersion(
                stage="A",
                name="自动化全链路验收 A",
                version="automation-e2e-a-v1",
                system_prompt="E2E_STAGE_A：返回范围、分类、画质与素材形态 JSON。",
                user_prompt="评测素材 {{image_metadata}}。",
                rubric_version="automation-e2e-rubric-v1",
                status="published",
                source="integration_test",
                created_by="automation-e2e",
            )
            db.add(prompt_a)
        prompt_b = db.scalar(
            select(PromptVersion).where(PromptVersion.version == "automation-e2e-b-v1")
        )
        if prompt_b is None:
            prompt_b = PromptVersion(
                stage="B",
                name="自动化全链路验收 B",
                version="automation-e2e-b-v1",
                system_prompt="E2E_STAGE_B：返回八维美感 JSON。",
                user_prompt="根据 {{precheck_json}} 评测，规则 {{rubric_version}}。",
                rubric_version="automation-e2e-rubric-v1",
                status="published",
                source="integration_test",
                created_by="automation-e2e",
            )
            db.add(prompt_b)
        db.flush()

        model.name = "自动化 E2E 评测模型"
        model.provider = "mock-compatible"
        model.protocol = "openai_chat"
        model.base_url = args.model_base_url
        model.api_path = "/chat/completions"
        model.model_id = "automation-e2e-evaluator"
        model.encrypted_api_key = _protect_file_aead(
            "e2e-model-key", MODEL_CONFIG_KEYCHAIN_ACCOUNT
        )
        model.temperature = 0.1
        model.max_tokens = 4096
        model.timeout_seconds = 10
        model.max_retries = 0
        model.max_concurrency = 1
        model.structured_output = True
        model.high_risk_review_enabled = False
        model.input_micros_per_million_tokens = 1000
        model.output_micros_per_million_tokens = 2000
        model.max_input_tokens = 2048
        model.active = True

        optimizer.name = "自动化 E2E 优化模型"
        optimizer.provider = "mock-compatible"
        optimizer.protocol = "openai_chat"
        optimizer.base_url = args.model_base_url
        optimizer.api_path = "/chat/completions"
        optimizer.model_id = "automation-e2e-optimizer"
        optimizer.encrypted_api_key = _protect_file_aead(
            "e2e-optimizer-key", OPTIMIZER_CONFIG_KEYCHAIN_ACCOUNT
        )
        optimizer.temperature = 0.1
        optimizer.max_tokens = 4096
        optimizer.timeout_seconds = 10
        optimizer.max_retries = 0
        optimizer.structured_output = True
        optimizer.input_micros_per_million_tokens = 1000
        optimizer.output_micros_per_million_tokens = 2000
        optimizer.max_input_tokens = 2048

        profile.prompt_a_id = prompt_a.id
        profile.prompt_b_id = prompt_b.id
        pipeline_config = json.loads(profile.pipeline_config_json or "{}")
        pipeline_config["prompt_mode"] = "ab"
        profile.pipeline_config_json = json.dumps(
            pipeline_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        profile.model_config_id = model.id
        profile.optimizer_config_id = optimizer.id
        profile.rubric_version = prompt_b.rubric_version
        profile.automation_config_json = json.dumps(
            {
                "enabled": True,
                "case_threshold": 1,
                "cooldown_seconds": 0,
                "max_candidates": 1,
            },
            ensure_ascii=False,
        )
        for binding in db.scalars(select(ModelNodeBinding)).all():
            if binding.category_key is None:
                binding.model_config_id = model.id

        policy.enabled = True
        policy.dry_run = False
        policy.case_threshold = 1
        policy.immediate_severities_json = '["P0","P1"]'
        policy.daily_budget_micros = 1_000_000
        policy.cooldown_seconds = 0
        policy.max_candidates = 1
        policy.lease_seconds = 30
        policy.max_attempts = 3
        policy.base_retry_seconds = 1
        policy.last_triggered_at = None
        policy.updated_by = "automation-e2e"
        policy.revision += 1

        baseline_bundle = get_or_create_bundle(
            db,
            model,
            prompt_a,
            prompt_b,
            prompt_b.rubric_version,
            ENGINE_VERSION,
            None,
            sampling,
        )
        baseline_contract = _bind_profile_baseline_contract(
            profile=profile,
            baseline_bundle=baseline_bundle,
            dimension_schema_key=SPACE_SCHEMA_KEY,
            dimension_schema_version=ACTIVE_V13_VERSION,
        )
        db.flush()
        baseline_contract_errors = category_bundle_contract_errors(
            db,
            profile=profile,
            bundle=baseline_bundle,
            require_complete=True,
            require_prompt_b=True,
            enforce_baseline_id=True,
        )
        if baseline_contract_errors:
            raise RuntimeError(
                "seed_profile_baseline_contract_mismatch:"
                + ",".join(baseline_contract_errors)
            )
        sample_set = SampleSet(
            name="自动化 E2E 三角色黄金集",
            description="真实 Worker 与自动配对回归隔离验收",
            kind="golden",
            status="locked",
            category_key="space_image",
            created_by="automation-e2e",
        )
        db.add(sample_set)
        db.flush()

        uploads = data_dir / "images"
        uploads.mkdir(parents=True, exist_ok=True)
        result_ids: dict[str, int] = {}
        for index, role in enumerate(
            ("target_error", "stable_control", "blind_holdout"), start=1
        ):
            stored_name = f"automation-e2e-{role}.jpg"
            image_path = uploads / stored_name
            Image.new("RGB", (64, 64), (60 * index, 90, 150)).save(
                image_path, format="JPEG"
            )
            raw = image_path.read_bytes()
            asset = Asset(
                original_name=stored_name,
                stored_name=stored_name,
                mime_type="image/jpeg",
                size_bytes=len(raw),
                width=64,
                height=64,
                sha256=hashlib.sha256(raw).hexdigest(),
                category_key="space_image",
                status="evaluated",
            )
            db.add(asset)
            db.flush()
            job = EvaluationJob(
                asset_id=asset.id,
                category_key="space_image",
                prompt_a_id=prompt_a.id,
                prompt_b_id=prompt_b.id,
                strategy_bundle_id=baseline_bundle.id,
                status="completed",
                stage="done",
                progress=100,
            )
            db.add(job)
            db.flush()
            aesthetic = _aesthetic(color_grade=5 if role == "target_error" else 3)
            result = EvaluationResult(
                asset_id=asset.id,
                job_id=job.id,
                strategy_bundle_id=baseline_bundle.id,
                strategy_snapshot_json=build_evaluation_strategy_snapshot(
                    db=db,
                    bundle=baseline_bundle,
                    prompt_a=prompt_a,
                    prompt_b=prompt_b,
                    sampling_policy=sampling,
                    aesthetic=aesthetic,
                    dimension_schema_key=SPACE_SCHEMA_KEY,
                    dimension_schema_version=ACTIVE_V13_VERSION,
                ),
                preprocess_json='{"schema_version":"automation-e2e-seed-v1"}',
                precheck_json=json.dumps(_precheck(), ensure_ascii=False),
                aesthetic_json=json.dumps(aesthetic, ensure_ascii=False),
                scoring_json=json.dumps(
                    {"formal": True, "level": "L4" if role == "target_error" else "L3", "caps": []},
                    ensure_ascii=False,
                ),
                raw_response_a="{}",
                raw_response_b="{}",
                score=82.0 if role == "target_error" else 65.0,
                level="L4" if role == "target_error" else "L3",
                confidence=0.95,
                needs_review=False,
                review_stage="completed",
                review_revision=1,
                model_id=model.model_id,
                prompt_a_version=prompt_a.version,
                prompt_b_version=prompt_b.version,
                rubric_version=prompt_b.rubric_version,
                engine_version=ENGINE_VERSION,
            )
            db.add(result)
            db.flush()
            review = HumanReview(
                evaluation_id=result.id,
                reviewer_name="automation-e2e-reviewer",
                stage="initial",
                decision="corrected" if role == "target_error" else "approved",
                corrected_level="L3" if role == "target_error" else None,
                corrected_score=65.0 if role == "target_error" else None,
                note="目标错例纠偏" if role == "target_error" else "稳定真值确认",
                corrections_json=(
                    json.dumps(
                        [
                            {
                                "target_type": "dimension",
                                "field_key": "color_material",
                                "model_value": 5,
                                "human_value": 3,
                                "reason_codes": ["overrated"],
                                "note": "普通材质不应高估",
                            }
                        ],
                        ensure_ascii=False,
                    )
                    if role == "target_error"
                    else "[]"
                ),
            )
            db.add(review)
            db.flush()
            db.add(
                SampleSetItem(
                    sample_set_id=sample_set.id,
                    asset_id=asset.id,
                    source_result_id=result.id,
                    expected_level="L3",
                    expected_category="住宅设计",
                    truth_json='{"level":"L3"}',
                    truth_revision=1,
                    truth_updated_by="automation-e2e-reviewer",
                    note=role,
                    added_by="automation-e2e",
                )
            )
            result_ids[role] = result.id

        event, case, duplicate = ingest_production_feedback(
            db,
            event_id="automation-e2e-success-1",
            schema_version="production-feedback-v1",
            event_type="human_correction_finalized",
            source_system="automation-e2e",
            occurred_at=result.created_at,
            payload={
                "production_case_id": "automation-e2e-case-1",
                "category_key": "space_image",
                "prompt_version": prompt_b.version,
                "severity": "P2",
                "model_output": {"level": "L4"},
                "human_truth": {"level": "L3"},
                "reason_codes": ["over_scored"],
                "production_applied": True,
            },
            received_by="automation-e2e",
        )
        db.flush()
        print(
            json.dumps(
                {
                    "data_dir": str(data_dir),
                    "model_id": model.id,
                    "optimizer_id": optimizer.id,
                    "prompt_a_id": prompt_a.id,
                    "prompt_b_id": prompt_b.id,
                    "baseline_bundle_id": baseline_bundle.id,
                    "baseline_contract": baseline_contract,
                    "baseline_contract_errors": baseline_contract_errors,
                    "sample_set_id": sample_set.id,
                    "source_result_ids": result_ids,
                    "feedback_event_id": event.id,
                    "optimization_case_id": case.id,
                    "feedback_duplicate": duplicate,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
