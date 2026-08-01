from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models  # noqa: F401
from app.database import Base
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
)
from app.migrations import run_migrations
from app.migrations.runner import MIGRATIONS
from app.models import (
    DimensionRoutePolicy,
    DimensionSchema,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
)
from app.scoring import ENGINE_VERSION
from app.strategy_bundle import (
    ROUTED_SCHEMA_CONTRACT_VERSION,
    ROUTED_STRATEGY_SCHEMA_VERSION,
    build_evaluation_profile_set,
    build_frozen_evaluation_profile,
    build_strategy_snapshot,
    get_or_create_bundle,
    get_or_create_routed_bundle,
)


@pytest.fixture
def db(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'routed-strategy.db'}",
        connect_args={"check_same_thread": False},
    )
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
        name="Routed Test Model",
        provider="doubao",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        model_id="routed-test-model",
        encrypted_api_key="encrypted-key-never-snapshot",
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
        name="Route A",
        version="route-A-v1",
        system_prompt="System A",
        user_prompt="User A",
        rubric_version="route-rubric-v1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="Space B",
        version="space-B-v1",
        system_prompt="System B",
        user_prompt="User B",
        rubric_version="space-rubric-v1",
        status="published",
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
    db.add_all([model, prompt_a, prompt_b, sampling])
    db.commit()
    return model, prompt_a, prompt_b, sampling


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


def _profile_set(
    db: Session,
    prompt_b: PromptVersion,
    *,
    reverse: bool = False,
    execution_context: str = "calibration",
) -> dict:
    _, space, core, product = _registry(db)
    profiles = [
        build_frozen_evaluation_profile(
            profile_key="space",
            schema=space,
            prompt_b=prompt_b,
        ),
        build_frozen_evaluation_profile(
            profile_key="common",
            schema=core,
            prompt_b=None,
        ),
        build_frozen_evaluation_profile(
            profile_key="product",
            schema=product,
            prompt_b=None,
        ),
    ]
    if reverse:
        profiles.reverse()
    return build_evaluation_profile_set(
        profiles=profiles,
        execution_context=execution_context,
        default_profile_key="common",
    )


def test_v3_calibration_bundle_freezes_all_profiles(db: Session) -> None:
    model, prompt_a, prompt_b, sampling = _seed_inputs(db)
    policy, _, _, _ = _registry(db)
    profile_set = _profile_set(db, prompt_b)
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

    assert bundle.strategy_schema_version == ROUTED_STRATEGY_SCHEMA_VERSION
    assert bundle.prompt_b_version is None
    assert (
        bundle.resolved_schema_contract_version
        == ROUTED_SCHEMA_CONTRACT_VERSION
    )
    assert bundle.dimension_route_policy_id == (
        f"{ROUTE_POLICY_KEY}@{ROUTE_POLICY_VERSION}"
    )
    stored_profiles = json.loads(bundle.evaluation_profile_set_snapshot)
    assert list(stored_profiles["profiles"]) == [
        "common",
        "product",
        "space",
    ]
    assert stored_profiles["execution_context"] == "calibration"
    assert (
        stored_profiles["profiles"]["product"]["release_gate"][
            "publishing_blocked"
        ]
        is True
    )
    assert stored_profiles["profiles"]["product"]["prompt_b"] is None
    snapshot = json.loads(
        build_strategy_snapshot(
            bundle,
            prompt_a,
            None,
            sampling,
        )
    )
    assert snapshot["schema_version"] == ROUTED_STRATEGY_SCHEMA_VERSION
    assert snapshot["prompt_b"] is None
    assert snapshot["evaluation_profile_set_snapshot"] == stored_profiles
    assert (
        snapshot["dimension_route_policy_snapshot"]["policy_key"]
        == ROUTE_POLICY_KEY
    )


def test_v3_profile_order_is_canonical_and_reused(db: Session) -> None:
    model, prompt_a, prompt_b, sampling = _seed_inputs(db)
    policy, _, _, _ = _registry(db)
    first = get_or_create_routed_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        route_policy=policy,
        evaluation_profile_set=_profile_set(db, prompt_b),
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    second = get_or_create_routed_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        route_policy=policy,
        evaluation_profile_set=_profile_set(
            db,
            prompt_b,
            reverse=True,
        ),
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    assert second.id == first.id
    assert second.canonical_hash == first.canonical_hash


def test_v3_prompt_rotation_changes_bundle_hash(db: Session) -> None:
    model, prompt_a, prompt_b, sampling = _seed_inputs(db)
    policy, _, _, _ = _registry(db)
    first = get_or_create_routed_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        route_policy=policy,
        evaluation_profile_set=_profile_set(db, prompt_b),
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    rotated = PromptVersion(
        stage="B",
        name="Space B v2",
        version="space-B-v2",
        system_prompt="System B changed",
        user_prompt="User B",
        rubric_version="space-rubric-v1",
        status="published",
    )
    db.add(rotated)
    db.commit()
    second = get_or_create_routed_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        route_policy=policy,
        evaluation_profile_set=_profile_set(db, rotated),
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    assert second.id != first.id
    assert second.canonical_hash != first.canonical_hash


def test_v3_rejects_production_candidate_policy(db: Session) -> None:
    model, prompt_a, prompt_b, sampling = _seed_inputs(db)
    policy, _, _, _ = _registry(db)
    profile_set = _profile_set(
        db,
        prompt_b,
        execution_context="production",
    )
    with pytest.raises(
        ValueError,
        match="生产 Bundle 只能冻结已发布的生产路由策略",
    ):
        get_or_create_routed_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt_a,
            route_policy=policy,
            evaluation_profile_set=profile_set,
            engine_version=ENGINE_VERSION,
            risk_review_version=None,
            sampling_policy=sampling,
        )


def test_v3_contract_rejects_tampered_profile_hash(db: Session) -> None:
    _model, _prompt_a, prompt_b, _sampling = _seed_inputs(db)
    profile_set = _profile_set(db, prompt_b)
    profile_set["profiles"]["space"]["prompt_b"]["system_prompt"] = (
        "tampered"
    )
    policy, _, _, _ = _registry(db)
    with pytest.raises(ValueError, match="规范哈希无效"):
        get_or_create_routed_bundle(
            db=db,
            model_config=_model,
            prompt_a=_prompt_a,
            route_policy=policy,
            evaluation_profile_set=profile_set,
            engine_version=ENGINE_VERSION,
            risk_review_version=None,
            sampling_policy=_sampling,
        )


def test_v2_bundle_remains_unchanged_after_migration_29(db: Session) -> None:
    model, prompt_a, prompt_b, sampling = _seed_inputs(db)
    bundle = get_or_create_bundle(
        db=db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version="space-rubric-v1",
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=sampling,
    )
    db.commit()
    assert bundle.strategy_schema_version == "strategy-bundle-v2"
    assert bundle.dimension_route_policy_snapshot is None
    assert bundle.evaluation_profile_set_snapshot is None


def test_migration_29_trigger_rejects_malformed_v3(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.execute(
            StrategyBundle.__table__.insert().values(
                canonical_hash="f" * 64,
                strategy_schema_version="strategy-bundle-v3",
                model_id="bad",
                model_config_snapshot="{}",
                prompt_a_version="A",
                prompt_b_version=None,
                rubric_version="R",
                engine_version="E",
                agent_plan_version="P",
                dimension_route_policy_id="bad@v1",
                dimension_schema_set_snapshot='{"schemas":[{}]}',
                label_field_set_snapshot='{"sets":[]}',
                resolved_schema_contract_version="v2",
                dimension_route_policy_snapshot="{}",
                evaluation_profile_set_snapshot="{}",
            )
        )
        db.flush()


def test_migration_29_upgrades_real_v2_shape_without_rewriting(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pre-v29.db'}",
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for migration in MIGRATIONS:
                if migration.version >= 29:
                    break
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations(version, name) "
                    "VALUES (?, ?)",
                    (migration.version, migration.name),
                )
            connection.exec_driver_sql("""
                CREATE TABLE strategy_bundles (
                    id INTEGER PRIMARY KEY,
                    canonical_hash VARCHAR(64) NOT NULL UNIQUE,
                    strategy_schema_version VARCHAR(40) NOT NULL,
                    model_id VARCHAR(200) NOT NULL,
                    model_config_snapshot TEXT NOT NULL,
                    prompt_a_version VARCHAR(40) NOT NULL,
                    prompt_b_version VARCHAR(40),
                    rubric_version VARCHAR(40) NOT NULL,
                    engine_version VARCHAR(40) NOT NULL,
                    sampling_policy_revision INTEGER,
                    risk_review_version VARCHAR(40),
                    agent_plan_version VARCHAR(80) NOT NULL,
                    dimension_route_policy_id VARCHAR(100),
                    dimension_schema_set_snapshot TEXT,
                    label_field_set_snapshot TEXT,
                    resolved_schema_contract_version VARCHAR(80)
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO strategy_bundles (
                    id, canonical_hash, strategy_schema_version,
                    model_id, model_config_snapshot,
                    prompt_a_version, prompt_b_version,
                    rubric_version, engine_version,
                    agent_plan_version, dimension_route_policy_id,
                    dimension_schema_set_snapshot,
                    label_field_set_snapshot,
                    resolved_schema_contract_version
                ) VALUES (
                    1, ?, 'strategy-bundle-v2',
                    'model', '{}', 'A1', 'B1', 'R1', 'E1', 'P1',
                    'space-static-by-scoring-profile-v1',
                    '{"schemas":[{"schema_key":"space"}]}',
                    '{"sets":[]}',
                    'dimension-resolution-v1'
                )
            """, ("a" * 64,))

            migration_29 = next(
                item for item in MIGRATIONS if item.version == 29
            )
            migration_29.up(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration_29.version, migration_29.name),
            )
            migration_29.up(connection)

            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(strategy_bundles)"
                )
            }
            assert {
                "dimension_route_policy_snapshot",
                "evaluation_profile_set_snapshot",
            } <= columns
            assert connection.exec_driver_sql(
                "SELECT strategy_schema_version, "
                "dimension_route_policy_snapshot, "
                "evaluation_profile_set_snapshot "
                "FROM strategy_bundles WHERE id=1"
            ).one() == ("strategy-bundle-v2", None, None)
            assert connection.exec_driver_sql(
                "SELECT max(version) FROM schema_migrations"
            ).scalar_one() == 29
            with pytest.raises(
                IntegrityError,
                match="routed dimension contract is invalid",
            ):
                connection.exec_driver_sql("""
                    INSERT INTO strategy_bundles (
                        id, canonical_hash, strategy_schema_version,
                        model_id, model_config_snapshot,
                        prompt_a_version, prompt_b_version,
                        rubric_version, engine_version,
                        agent_plan_version, dimension_route_policy_id,
                        dimension_schema_set_snapshot,
                        label_field_set_snapshot,
                        resolved_schema_contract_version,
                        dimension_route_policy_snapshot,
                        evaluation_profile_set_snapshot
                    ) VALUES (
                        2, ?, 'strategy-bundle-v3',
                        'model', '{}', 'A1', NULL, 'R1', 'E1', 'P1',
                        'bad@v1', '{"schemas":[{}]}', '{"sets":[]}',
                        'dimension-route-resolution-v2', '{}', '{}'
                    )
                """, ("b" * 64,))
    finally:
        engine.dispose()
