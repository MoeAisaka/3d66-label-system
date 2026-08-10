from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models  # noqa: F401
from app.database import Base
from app.dimension_calibration import (
    DimensionCalibrationContractError,
    DimensionCalibrationStateError,
    claim_dimension_calibration_item,
    complete_dimension_calibration_item,
    create_dimension_calibration_run,
    fail_dimension_calibration_item,
)
from app.dimension_route_registry import (
    CORE_SCHEMA_KEY,
    CORE_SCHEMA_VERSION,
    PRODUCT_SCHEMA_KEY,
    PRODUCT_SCHEMA_VERSION,
    ROUTE_POLICY_KEY,
    ROUTE_POLICY_VERSION,
)
from app.dimension_schema_registry import (
    ACTIVE_V13_VERSION,
    SPACE_SCHEMA_KEY,
    canonical_hash,
    canonical_json,
)
from app.migrations import run_migrations
from app.models import (
    Asset,
    DimensionCalibrationFrozenError,
    DimensionCalibrationItem,
    DimensionCalibrationRun,
    DimensionRoutePolicy,
    DimensionSchema,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    PromptVersion,
    ReviewPanel,
    SamplingPolicy,
    StrategyBundle,
)
from app.routed_strategy import (
    build_routed_evaluation_strategy_snapshot,
)
from app.scoring import ENGINE_VERSION
from app.strategy_bundle import (
    build_evaluation_profile_set,
    build_frozen_evaluation_profile,
    get_or_create_bundle,
    get_or_create_routed_bundle,
)


@pytest.fixture
def database(tmp_path) -> tuple[Session, object]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dimension-calibration.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        run_migrations(connection)
    session = Session(engine, expire_on_commit=False, autoflush=False)
    try:
        yield session, engine
    finally:
        session.close()
        engine.dispose()


def _seed_inputs(
    db: Session,
) -> tuple[
    ModelConfig,
    PromptVersion,
    PromptVersion,
    PromptVersion,
    SamplingPolicy,
]:
    model = ModelConfig(
        name="Calibration Model",
        provider="doubao",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        model_id="calibration-model",
        encrypted_api_key="encrypted-placeholder",
        temperature=0.1,
        max_tokens=4096,
        timeout_seconds=120,
        max_retries=1,
        max_concurrency=2,
        structured_output=True,
        high_risk_review_enabled=True,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="Calibration A",
        version="calibration-A-v1",
        system_prompt="System A",
        user_prompt="User A {{image_metadata}}",
        rubric_version="calibration-rubric-v1",
        status="published",
    )
    space_b = PromptVersion(
        stage="B",
        name="Space B",
        version="space-B-v1",
        system_prompt="System Space B",
        user_prompt="Space {{precheck_json}} {{rubric_version}}",
        rubric_version="space-rubric-v1",
        status="published",
    )
    product_b = PromptVersion(
        stage="B",
        name="Product B candidate",
        version="product-B-v0.1-candidate.1",
        system_prompt="System Product B",
        user_prompt="Product {{precheck_json}} {{rubric_version}}",
        rubric_version="product-rubric-v0.1",
        status="draft",
    )
    sampling = SamplingPolicy(
        id=1,
        revision=1,
        sample_rate=10,
        low_confidence_threshold=0.7,
        medium_confidence_threshold=0.9,
        cold_start_required_count=5,
        high_level_required_from=4,
    )
    db.add_all([model, prompt_a, space_b, product_b, sampling])
    db.commit()
    return model, prompt_a, space_b, product_b, sampling


def _registry(
    db: Session,
) -> tuple[
    DimensionRoutePolicy,
    DimensionSchema,
    DimensionSchema,
    DimensionSchema,
]:
    policy = db.scalar(
        select(DimensionRoutePolicy).where(
            DimensionRoutePolicy.policy_key == ROUTE_POLICY_KEY,
            DimensionRoutePolicy.version == ROUTE_POLICY_VERSION,
        )
    )
    space = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == SPACE_SCHEMA_KEY,
            DimensionSchema.version == ACTIVE_V13_VERSION,
        )
    )
    core = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == CORE_SCHEMA_KEY,
            DimensionSchema.version == CORE_SCHEMA_VERSION,
        )
    )
    product = db.scalar(
        select(DimensionSchema).where(
            DimensionSchema.schema_key == PRODUCT_SCHEMA_KEY,
            DimensionSchema.version == PRODUCT_SCHEMA_VERSION,
        )
    )
    assert policy and space and core and product
    return policy, space, core, product


def _routed_bundle(
    db: Session,
    *,
    include_product_prompt: bool = True,
) -> tuple[StrategyBundle, PromptVersion, SamplingPolicy]:
    model, prompt_a, space_b, product_b, sampling = _seed_inputs(db)
    policy, space, core, product = _registry(db)
    profile_set = build_evaluation_profile_set(
        profiles=[
            build_frozen_evaluation_profile(
                profile_key="space",
                schema=space,
                prompt_b=space_b,
            ),
            build_frozen_evaluation_profile(
                profile_key="common",
                schema=core,
                prompt_b=None,
            ),
            build_frozen_evaluation_profile(
                profile_key="product",
                schema=product,
                prompt_b=(
                    product_b if include_product_prompt else None
                ),
            ),
        ],
        execution_context="calibration",
        default_profile_key="common",
    )
    bundle = get_or_create_routed_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        route_policy=policy,
        evaluation_profile_set=profile_set,
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    return bundle, prompt_a, sampling


def _asset(db: Session, number: int, *, status: str = "uploaded") -> Asset:
    asset = Asset(
        original_name=f"asset-{number}.jpg",
        stored_name=f"asset-{number}.jpg",
        mime_type="image/jpeg",
        size_bytes=100 + number,
        width=1000,
        height=800,
        sha256=f"{number:064x}",
        status=status,
    )
    db.add(asset)
    db.commit()
    return asset


def _product_precheck() -> dict:
    return {
        "classification": {
            "scope_status": "boundary",
            "primary_category": "软装家具",
            "primary_confidence": 0.92,
        },
        "scene_scope": {"type": "object_only"},
        "media_form": {
            "white_background_product": {
                "status": "yes",
                "confidence": 0.95,
            }
        },
        "image_quality": {
            "quality_severity": "normal",
            "confidence": 0.95,
            "evidence": [],
        },
        "needs_review": False,
        "review_reasons": [],
    }


def _precheck_for(
    *,
    category: str,
    quality: str = "normal",
) -> dict:
    payload = _product_precheck()
    payload["classification"]["primary_category"] = category
    payload["scene_scope"]["type"] = (
        "uncertain" if category in {"平面设计", "意向图"} else "object_only"
    )
    payload["media_form"]["white_background_product"]["status"] = (
        "no" if category in {"平面设计", "意向图"} else "yes"
    )
    payload["image_quality"]["quality_severity"] = quality
    return payload


def _product_aesthetic() -> dict:
    keys = (
        "presentation_integrity",
        "visual_hierarchy",
        "inspiration_reference",
        "product_form_proportion",
        "material_craft_detail",
        "functional_clarity",
        "scene_styling_fit",
    )
    return {
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 可见证据"],
                "reason": f"{key} 基础成立",
            }
            for key in keys
        },
        "assessment_confidence": 0.88,
        "needs_review": False,
        "review_reasons": [],
    }


def _run(
    db: Session,
    *,
    bundle: StrategyBundle,
    assets: list[Asset],
    run_key: str,
) -> DimensionCalibrationRun:
    run = create_dimension_calibration_run(
        db,
        run_key=run_key,
        strategy_bundle_id=bundle.id,
        asset_ids=[asset.id for asset in assets],
        created_by="owner",
    )
    db.commit()
    return run


def _resolution(
    *,
    bundle: StrategyBundle,
    prompt_a: PromptVersion,
    sampling: SamplingPolicy,
    precheck: dict,
    now: datetime,
) -> str:
    return build_routed_evaluation_strategy_snapshot(
        bundle=bundle,
        prompt_a=prompt_a,
        sampling_policy=sampling,
        precheck=precheck,
        resolution_timestamp=now,
    )


def test_create_run_is_idempotent_and_conflict_safe(database) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    first = _asset(db, 1)
    second = _asset(db, 2)
    run = _run(
        db,
        bundle=bundle,
        assets=[first],
        run_key="calibration:stable",
    )
    replay = create_dimension_calibration_run(
        db,
        run_key="calibration:stable",
        strategy_bundle_id=bundle.id,
        asset_ids=[first.id],
        created_by="another-user",
    )
    assert replay.id == run.id
    assert len(run.items) == 1
    with pytest.raises(
        DimensionCalibrationContractError,
        match="已绑定不同定义",
    ):
        create_dimension_calibration_run(
            db,
            run_key="calibration:stable",
            strategy_bundle_id=bundle.id,
            asset_ids=[second.id],
            created_by="owner",
        )


@pytest.mark.parametrize("asset_ids", [[], list(range(1, 102))])
def test_create_run_enforces_asset_count(database, asset_ids: list[int]) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    with pytest.raises(
        DimensionCalibrationContractError,
        match="1–100",
    ):
        create_dimension_calibration_run(
            db,
            run_key=f"calibration:count:{len(asset_ids)}",
            strategy_bundle_id=bundle.id,
            asset_ids=asset_ids,
            created_by="owner",
        )


def test_create_run_rejects_duplicate_and_unavailable_assets(database) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    with pytest.raises(
        DimensionCalibrationContractError,
        match="不得重复",
    ):
        create_dimension_calibration_run(
            db,
            run_key="calibration:duplicate",
            strategy_bundle_id=bundle.id,
            asset_ids=[asset.id, asset.id],
            created_by="owner",
        )
    unavailable = _asset(db, 2, status="queued")
    with pytest.raises(
        DimensionCalibrationContractError,
        match="状态不可用",
    ):
        create_dimension_calibration_run(
            db,
            run_key="calibration:unavailable",
            strategy_bundle_id=bundle.id,
            asset_ids=[unavailable.id],
            created_by="owner",
        )


def test_create_run_rejects_v2_bundle(database) -> None:
    db, _engine = database
    model, prompt_a, space_b, _product_b, sampling = _seed_inputs(db)
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=space_b,
        rubric_version=space_b.rubric_version,
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    asset = _asset(db, 1)
    with pytest.raises(
        DimensionCalibrationContractError,
        match="strategy-bundle-v3",
    ):
        create_dimension_calibration_run(
            db,
            run_key="calibration:v2",
            strategy_bundle_id=bundle.id,
            asset_ids=[asset.id],
            created_by="owner",
        )


def test_create_run_rejects_production_context(database) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["execution_context"] = "production"
    payload["canonical_hash"] = canonical_hash(
        {
            key: value
            for key, value in payload.items()
            if key != "canonical_hash"
        }
    )
    bundle.evaluation_profile_set_snapshot = canonical_json(payload)
    try:
        with pytest.raises(DimensionCalibrationContractError):
            create_dimension_calibration_run(
                db,
                run_key="calibration:production",
                strategy_bundle_id=bundle.id,
                asset_ids=[asset.id],
                created_by="owner",
            )
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_claim_is_conditional_and_single_owner(database) -> None:
    db, engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:claim",
    )
    first = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert first is not None
    db.commit()
    with Session(engine, expire_on_commit=False, autoflush=False) as second_db:
        assert claim_dimension_calibration_item(
            second_db,
            run_id=run.id,
            worker_id="worker-2",
        ) is None
    assert first.status == "processing"
    assert run.status == "running"
    assert run.processing == 1


def test_completed_result_is_isolated_and_replay_verified(database) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:completed",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    db.commit()
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _product_precheck()
    result = complete_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        terminal_status="completed",
        precheck=precheck,
        resolution_snapshot=_resolution(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling=sampling,
            precheck=precheck,
            now=now,
        ),
        aesthetic=_product_aesthetic(),
        raw_response_a='{"stage":"A"}',
        raw_response_b='{"stage":"B"}',
        now=now,
    )
    db.commit()
    assert result.status == "completed"
    assert result.level in {"L1", "L2", "L3", "L4", "L5"}
    assert result.score is not None
    assert run.status == "completed"
    assert run.completed == 1
    assert run.finished_at is not None
    assert db.scalar(select(func.count(EvaluationJob.id))) == 0
    assert db.scalar(select(func.count(EvaluationResult.id))) == 0
    assert db.scalar(select(func.count(HumanReview.id))) == 0
    assert db.scalar(select(func.count(ReviewPanel.id))) == 0


@pytest.mark.parametrize(
    ("category", "quality", "terminal_status"),
    [
        ("平面设计", "normal", "core_fallback"),
        ("软装家具", "unusable", "unassessable"),
    ],
)
def test_non_resolved_business_outcomes_do_not_store_scores(
    database,
    category: str,
    quality: str,
    terminal_status: str,
) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key=f"calibration:{terminal_status}",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _precheck_for(category=category, quality=quality)
    result = complete_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        terminal_status=terminal_status,
        precheck=precheck,
        resolution_snapshot=_resolution(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling=sampling,
            precheck=precheck,
            now=now,
        ),
        raw_response_a='{"stage":"A"}',
        now=now,
    )
    db.commit()
    assert result.status == terminal_status
    assert result.aesthetic_json is None
    assert result.scoring_json is None
    assert result.score is None
    assert result.level is None
    assert result.confidence is None
    assert result.needs_review is True


def test_blocked_product_without_b_is_persisted_without_score(
    database,
) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(
        db,
        include_product_prompt=False,
    )
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:blocked",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    db.commit()
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _product_precheck()
    result = complete_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        terminal_status="blocked",
        precheck=precheck,
        resolution_snapshot=_resolution(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling=sampling,
            precheck=precheck,
            now=now,
        ),
        now=now,
    )
    db.commit()
    assert result.status == "blocked"
    assert run.blocked == 1
    assert run.status == "completed"


def test_result_contract_rejects_wrong_terminal_or_b_payload(database) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:bad-result",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    db.commit()
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _product_precheck()
    snapshot = _resolution(
        bundle=bundle,
        prompt_a=prompt_a,
        sampling=sampling,
        precheck=precheck,
        now=now,
    )
    with pytest.raises(
        DimensionCalibrationContractError,
        match="状态不一致",
    ):
        complete_dimension_calibration_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            terminal_status="blocked",
            precheck=precheck,
            resolution_snapshot=snapshot,
            now=now,
        )
    with pytest.raises(
        DimensionCalibrationContractError,
        match="必须包含 B",
    ):
        complete_dimension_calibration_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            terminal_status="completed",
            precheck=precheck,
            resolution_snapshot=snapshot,
            now=now,
        )


def test_tampered_resolution_snapshot_is_rejected(database) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:tampered-resolution",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _product_precheck()
    payload = json.loads(
        _resolution(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling=sampling,
            precheck=precheck,
            now=now,
        )
    )
    payload["resolution_status"] = "blocked"
    with pytest.raises(
        DimensionCalibrationContractError,
        match="不能由冻结 Bundle",
    ):
        complete_dimension_calibration_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            terminal_status="blocked",
            precheck=precheck,
            resolution_snapshot=payload,
            now=now,
        )


def test_failure_is_bounded_and_run_becomes_failed(database) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:failed",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    result = fail_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        error_type="provider_timeout",
        error_message="x" * 800,
    )
    db.commit()
    assert result.status == "failed"
    assert result.error_type == "provider_timeout"
    assert len(result.error_message) == 500
    assert run.status == "failed"
    assert run.failed == 1


def test_run_summary_becomes_partial_failed(database) -> None:
    db, _engine = database
    bundle, prompt_a, sampling = _routed_bundle(db)
    first = _asset(db, 1)
    second = _asset(db, 2)
    run = _run(
        db,
        bundle=bundle,
        assets=[first, second],
        run_key="calibration:partial",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    now = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    precheck = _precheck_for(category="平面设计")
    complete_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        terminal_status="core_fallback",
        precheck=precheck,
        resolution_snapshot=_resolution(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling=sampling,
            precheck=precheck,
            now=now,
        ),
        now=now,
    )
    second_item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert second_item is not None
    fail_dimension_calibration_item(
        db,
        item_id=second_item.id,
        worker_id="worker-1",
        error_type="provider_timeout",
        error_message="timeout",
        now=now,
    )
    db.commit()
    assert run.status == "partial_failed"
    assert run.core_fallback == 1
    assert run.failed == 1


def test_terminal_item_rejects_second_write_and_orm_mutation(database) -> None:
    db, _engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    run = _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:terminal",
    )
    item = claim_dimension_calibration_item(
        db,
        run_id=run.id,
        worker_id="worker-1",
    )
    assert item is not None
    fail_dimension_calibration_item(
        db,
        item_id=item.id,
        worker_id="worker-1",
        error_type="provider_timeout",
        error_message="timeout",
    )
    db.commit()
    with pytest.raises(
        DimensionCalibrationStateError,
        match="不处于 processing",
    ):
        fail_dimension_calibration_item(
            db,
            item_id=item.id,
            worker_id="worker-1",
            error_type="provider_timeout",
            error_message="again",
        )
    item.error_message = "changed"
    with pytest.raises(DimensionCalibrationFrozenError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        (
            "UPDATE dimension_calibration_runs "
            "SET definition_hash = lower(hex(randomblob(32))) WHERE id = 1"
        ),
        "DELETE FROM dimension_calibration_runs WHERE id = 1",
        (
            "UPDATE dimension_calibration_items "
            "SET asset_snapshot_json = '{}' WHERE id = 1"
        ),
        "DELETE FROM dimension_calibration_items WHERE id = 1",
    ],
)
def test_database_triggers_reject_frozen_mutation(
    database,
    statement: str,
) -> None:
    db, engine = database
    bundle, _prompt_a, _sampling = _routed_bundle(db)
    asset = _asset(db, 1)
    _run(
        db,
        bundle=bundle,
        assets=[asset],
        run_key="calibration:trigger",
    )
    with pytest.raises(IntegrityError, match="DimensionCalibration"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def test_latest_migration_and_dimension_triggers_are_installed(database) -> None:
    _db, engine = database
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT max(version) FROM schema_migrations"
        ).scalar_one() == 60
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 54"
        ).scalar_one() == "add_evaluation_result_level_semantics"
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 53"
        ).scalar_one() == "add_evaluation_result_v3_shadow"
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 52"
        ).scalar_one() == "add_category_evaluation_v3_configs"
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 51"
        ).scalar_one() == "raise_default_max_concurrency"
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 50"
        ).scalar_one() == "repair_optimizer_protocol_columns"
        assert connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 30"
        ).scalar_one() == "add_dimension_calibration_results"
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM evaluation_category_profiles"
        ).scalar_one() == 3
        trigger_count = connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='trigger' "
            "AND name LIKE 'trg_dimension_calibration_%'"
        ).scalar_one()
        assert trigger_count == 10
