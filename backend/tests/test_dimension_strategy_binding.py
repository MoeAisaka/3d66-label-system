from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    HISTORICAL_DEFAULT_VERSION,
    SPACE_SCHEMA_KEY,
)
from app.migrations import run_migrations
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
    StrategySnapshotRequiredError,
)
from app.strategy_bundle import (
    LEGACY_STRATEGY_SCHEMA_VERSION,
    STRATEGY_SCHEMA_VERSION,
    build_evaluation_strategy_snapshot,
    build_strategy_snapshot,
    get_or_create_bundle,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dimension-binding.db'}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        run_migrations(connection)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_inputs(
    db: Session,
) -> tuple[ModelConfig, PromptVersion, PromptVersion, SamplingPolicy]:
    model = ModelConfig(
        name="dimension-test-model",
        provider="test",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        model_id="dimension-test-model",
        encrypted_api_key="encrypted",
        temperature=0.1,
        max_tokens=4096,
        timeout_seconds=120,
        max_retries=1,
        max_concurrency=2,
        structured_output=True,
        high_risk_review_enabled=False,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="dimension-a",
        version="dimension-a-v1",
        system_prompt="system A",
        user_prompt="user A",
        rubric_version="space-rubric-v1.4",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="dimension-b",
        version="dimension-b-v1",
        system_prompt="system B",
        user_prompt="user B",
        rubric_version="space-rubric-v1.4",
        status="published",
    )
    policy = SamplingPolicy(
        id=1,
        revision=3,
        sample_rate=10,
        low_confidence_threshold=0.7,
        medium_confidence_threshold=0.9,
        cold_start_required_count=5,
        high_level_required_from=4,
    )
    db.add_all([model, prompt_a, prompt_b, policy])
    db.commit()
    return model, prompt_a, prompt_b, policy


def _v2_bundle(
    db: Session,
) -> tuple[StrategyBundle, PromptVersion, PromptVersion, SamplingPolicy]:
    model, prompt_a, prompt_b, policy = _seed_inputs(db)
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=prompt_b.rubric_version,
        engine_version="engine-v2.5.0",
        risk_review_version=None,
        sampling_policy=policy,
    )
    db.commit()
    return bundle, prompt_a, prompt_b, policy


def _asset_job(db: Session) -> tuple[Asset, EvaluationJob]:
    asset = Asset(
        original_name="dimension.jpg",
        stored_name="dimension.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        sha256="d" * 64,
    )
    db.add(asset)
    db.flush()
    job = EvaluationJob(asset_id=asset.id, status="completed")
    db.add(job)
    db.commit()
    return asset, job


def _result(
    *,
    asset: Asset,
    job: EvaluationJob,
    bundle: StrategyBundle,
    snapshot: str,
) -> EvaluationResult:
    return EvaluationResult(
        asset_id=asset.id,
        job_id=job.id,
        strategy_bundle_id=bundle.id,
        strategy_snapshot_json=snapshot,
        precheck_json="{}",
        aesthetic_json="{}",
        scoring_json="{}",
        raw_response_a="{}",
        raw_response_b="{}",
        model_id=bundle.model_id,
        prompt_a_version=bundle.prompt_a_version,
        prompt_b_version=bundle.prompt_b_version,
        risk_review_version=bundle.risk_review_version,
        rubric_version=bundle.rubric_version,
        engine_version=bundle.engine_version,
    )


def test_v2_bundle_freezes_schema_set_and_result_resolves_active_revision(
    db: Session,
) -> None:
    bundle, prompt_a, prompt_b, policy = _v2_bundle(db)
    assert bundle.strategy_schema_version == STRATEGY_SCHEMA_VERSION

    base_snapshot = json.loads(
        build_strategy_snapshot(bundle, prompt_a, prompt_b, policy)
    )
    versions = {
        item["version"]
        for item in base_snapshot["dimension_schema_set"]["schemas"]
    }
    assert versions == {
        HISTORICAL_DEFAULT_VERSION,
        ACTIVE_V13_VERSION,
    }
    assert "resolved_dimension_schema_id" not in base_snapshot

    result_snapshot = build_evaluation_strategy_snapshot(
        db=db,
        bundle=bundle,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        sampling_policy=policy,
        aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
    )
    payload = json.loads(result_snapshot)
    assert payload["resolved_dimension_schema_key"] == SPACE_SCHEMA_KEY
    assert (
        payload["resolved_dimension_schema_version"]
        == ACTIVE_V13_VERSION
    )
    assert (
        payload["route_decision_snapshot"]["reason"]
        == "scoring_profile_matches_active_v1_3"
    )

    asset, job = _asset_job(db)
    db.add(
        _result(
            asset=asset,
            job=job,
            bundle=bundle,
            snapshot=result_snapshot,
        )
    )
    db.commit()


def test_same_bundle_resolves_historical_revision_without_identity_drift(
    db: Session,
) -> None:
    bundle, prompt_a, prompt_b, policy = _v2_bundle(db)
    active = json.loads(
        build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
        )
    )
    historical = json.loads(
        build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic={"scoring_profile": "legacy"},
        )
    )
    assert active["canonical_hash"] == historical["canonical_hash"]
    assert (
        historical["resolved_dimension_schema_version"]
        == HISTORICAL_DEFAULT_VERSION
    )
    assert (
        active["resolved_snapshot_hash"]
        != historical["resolved_snapshot_hash"]
    )


@pytest.mark.parametrize(
    "tamper",
    ("schema_hash", "definition", "prompt_hash", "resolution_hash"),
)
def test_v2_result_rejects_tampered_dimension_identity(
    db: Session,
    tamper: str,
) -> None:
    bundle, prompt_a, prompt_b, policy = _v2_bundle(db)
    payload = json.loads(
        build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
        )
    )
    if tamper == "schema_hash":
        payload["resolved_dimension_schema_hash"] = "f" * 64
    elif tamper == "definition":
        payload["resolved_dimensions_snapshot"]["dimensions"][0][
            "label"
        ] = "伪造维度"
    elif tamper == "prompt_hash":
        payload["resolved_prompt_b_hash"] = "f" * 64
    else:
        payload["resolved_snapshot_hash"] = "f" * 64

    asset, job = _asset_job(db)
    db.add(
        _result(
            asset=asset,
            job=job,
            bundle=bundle,
            snapshot=_canonical_json(payload),
        )
    )
    with pytest.raises(StrategySnapshotRequiredError):
        db.commit()
    db.rollback()


def test_database_trigger_rejects_nonexistent_resolved_schema(
    db: Session,
) -> None:
    bundle, prompt_a, prompt_b, policy = _v2_bundle(db)
    payload = json.loads(
        build_evaluation_strategy_snapshot(
            db=db,
            bundle=bundle,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            sampling_policy=policy,
            aesthetic={"scoring_profile": "space_aesthetic_v1.3"},
        )
    )
    payload["resolved_dimension_schema_id"] = 999999
    payload["route_decision_snapshot"]["dimension_schema_id"] = 999999
    resolution = {
        key: payload[key]
        for key in (
            "resolved_dimension_schema_id",
            "resolved_dimension_schema_key",
            "resolved_dimension_schema_version",
            "resolved_dimension_schema_hash",
            "resolved_dimensions_snapshot",
            "resolved_prompt_b_hash",
            "route_decision_snapshot",
        )
    }
    payload["resolved_snapshot_hash"] = _sha256(resolution)
    asset, job = _asset_job(db)

    with pytest.raises(IntegrityError, match="strategy binding"):
        db.connection().exec_driver_sql(
            """
            INSERT INTO evaluation_results (
                asset_id, job_id, strategy_bundle_id,
                strategy_snapshot_json, precheck_json, aesthetic_json,
                scoring_json, raw_response_a, raw_response_b,
                needs_review, review_stage, review_revision,
                model_id, prompt_a_version, prompt_b_version,
                rubric_version, engine_version, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, '{}', '{}', '{}', '{}', '{}',
                0, 'initial', 0, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (
                asset.id,
                job.id,
                bundle.id,
                _canonical_json(payload),
                bundle.model_id,
                bundle.prompt_a_version,
                bundle.prompt_b_version,
                bundle.rubric_version,
                bundle.engine_version,
            ),
        )
    db.rollback()


def test_legacy_v1_bundle_and_result_remain_readable_and_insertable(
    db: Session,
) -> None:
    model, prompt_a, prompt_b, policy = _seed_inputs(db)
    model_snapshot = {
        "name": model.name,
        "provider": model.provider,
        "base_url": model.base_url,
        "api_path": model.api_path,
        "model_id": model.model_id,
        "temperature": model.temperature,
        "max_tokens": model.max_tokens,
        "timeout_seconds": model.timeout_seconds,
        "max_retries": model.max_retries,
        "max_concurrency": model.max_concurrency,
        "structured_output": model.structured_output,
        "high_risk_review_enabled": model.high_risk_review_enabled,
    }
    prompt = lambda item: {
        "id": item.id,
        "stage": item.stage,
        "version": item.version,
        "name": item.name,
        "rubric_version": item.rubric_version,
        "system_prompt": item.system_prompt,
        "user_prompt": item.user_prompt,
    }
    definition = {
        "schema_version": LEGACY_STRATEGY_SCHEMA_VERSION,
        "model_id": model.model_id,
        "model_config": model_snapshot,
        "prompt_a": prompt(prompt_a),
        "prompt_b": prompt(prompt_b),
        "rubric_version": prompt_b.rubric_version,
        "engine_version": "engine-v2.5.0",
        "sampling_policy": {
            "id": policy.id,
            "revision": policy.revision,
            "sample_rate": policy.sample_rate,
            "low_confidence_threshold": policy.low_confidence_threshold,
            "medium_confidence_threshold": policy.medium_confidence_threshold,
            "cold_start_required_count": policy.cold_start_required_count,
            "high_level_required_from": policy.high_level_required_from,
        },
        "risk_review_version": None,
        "agent_plan_version": "controlled-agent-plan-v1",
    }
    bundle = StrategyBundle(
        canonical_hash=_sha256(definition),
        strategy_schema_version=LEGACY_STRATEGY_SCHEMA_VERSION,
        model_id=model.model_id,
        model_config_snapshot=_canonical_json(model_snapshot),
        prompt_a_version=prompt_a.version,
        prompt_b_version=prompt_b.version,
        rubric_version=prompt_b.rubric_version,
        engine_version="engine-v2.5.0",
        sampling_policy_revision=policy.revision,
        risk_review_version=None,
        agent_plan_version="controlled-agent-plan-v1",
    )
    db.add(bundle)
    db.flush()
    snapshot = _canonical_json(
        {
            "bundle_id": bundle.id,
            "canonical_hash": bundle.canonical_hash,
            **definition,
        }
    )
    asset, job = _asset_job(db)
    db.add(
        _result(
            asset=asset,
            job=job,
            bundle=bundle,
            snapshot=snapshot,
        )
    )
    db.commit()
    assert db.scalar(
        select(StrategyBundle).where(StrategyBundle.id == bundle.id)
    ) is not None
