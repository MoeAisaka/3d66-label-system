from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
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
    canonical_hash,
    canonical_json,
)
from app.migrations import run_migrations
from app.models import (
    DimensionRoutePolicy,
    DimensionSchema,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
)
from app.routed_strategy import (
    RESOLVED_PROFILE_FORMAT_VERSION,
    RoutedStrategyContractError,
    build_routed_evaluation_strategy_snapshot,
    load_frozen_evaluation_profiles,
    resolve_frozen_evaluation_profile,
)
from app.scoring import ENGINE_VERSION
from app.strategy_bundle import (
    build_evaluation_profile_set,
    build_frozen_evaluation_profile,
    get_or_create_bundle,
    get_or_create_routed_bundle,
)


@pytest.fixture
def db(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'route-resolution.db'}",
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
) -> tuple[
    ModelConfig,
    PromptVersion,
    PromptVersion,
    PromptVersion,
    SamplingPolicy,
]:
    model = ModelConfig(
        name="Route Resolution Model",
        provider="doubao",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        model_id="route-resolution-model",
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
        name="Route A",
        version="route-A-v1",
        system_prompt="System A",
        user_prompt="User A {{image_metadata}}",
        rubric_version="route-rubric-v1",
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


def _bundle(
    db: Session,
    *,
    include_product_prompt: bool,
) -> tuple[
    StrategyBundle,
    PromptVersion,
    SamplingPolicy,
]:
    model, prompt_a, space_b, product_b, sampling = _seed_inputs(db)
    policy, space, core, product = _registry(db)
    profiles = [
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
            prompt_b=product_b if include_product_prompt else None,
        ),
    ]
    profile_set = build_evaluation_profile_set(
        profiles=profiles,
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


def _precheck(
    *,
    scope_status: str = "in_scope",
    primary_category: str = "住宅设计",
    primary_confidence: float = 0.92,
    scene_scope: str = "full_space",
    white_background: str = "no",
    quality_severity: str = "normal",
    needs_review: bool = False,
) -> dict:
    return {
        "classification": {
            "scope_status": scope_status,
            "primary_category": primary_category,
            "primary_confidence": primary_confidence,
        },
        "scene_scope": {"type": scene_scope},
        "media_form": {
            "white_background_product": {
                "status": white_background,
            }
        },
        "image_quality": {
            "quality_severity": quality_severity,
        },
        "needs_review": needs_review,
        "must_not_freeze": {"raw_model_payload": "not part of route input"},
    }


def _rehash_without_canonical_hash(payload: dict) -> None:
    payload["canonical_hash"] = canonical_hash(
        {
            key: value
            for key, value in payload.items()
            if key != "canonical_hash"
        }
    )


def _rehash_profile_set(payload: dict, profile_key: str) -> None:
    _rehash_without_canonical_hash(payload["profiles"][profile_key])
    _rehash_without_canonical_hash(payload)


def test_space_route_selects_frozen_space_profile(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=False,
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=_precheck(),
    )
    profile = resolved["resolved_evaluation_profile"]
    assert resolved["format_version"] == RESOLVED_PROFILE_FORMAT_VERSION
    assert resolved["resolution_status"] == "resolved"
    assert profile["profile_key"] == "space"
    assert profile["prompt_b"]["version"] == "space-B-v1"
    assert resolved["blocked_reasons"] == []
    assert "must_not_freeze" not in resolved["route_input_snapshot"]


def test_product_route_without_prompt_is_explicitly_blocked(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=False,
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=_precheck(
            scope_status="boundary",
            primary_category="软装家具",
            scene_scope="object_only",
        ),
    )
    assert resolved["resolution_status"] == "blocked"
    assert resolved["resolved_evaluation_profile"]["profile_key"] == "product"
    assert resolved["blocked_reasons"] == ["prompt_contract_missing"]
    assert resolved["needs_review"] is True


def test_product_candidate_with_prompt_can_run_only_as_calibration(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=_precheck(
            scope_status="out_of_scope",
            primary_category="无法确定",
            scene_scope="object_only",
            white_background="yes",
        ),
    )
    profile = resolved["resolved_evaluation_profile"]
    assert resolved["execution_context"] == "calibration"
    assert resolved["resolution_status"] == "resolved"
    assert profile["status"] == "candidate"
    assert profile["prompt_b"]["version"] == "product-B-v0.1-candidate.1"
    assert profile["release_gate"]["publishing_blocked"] is True


@pytest.mark.parametrize(
    ("precheck", "expected_status", "expected_profile"),
    [
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="平面设计",
                scene_scope="uncertain",
            ),
            "core_fallback",
            "common",
        ),
        (
            _precheck(
                scope_status="out_of_scope",
                primary_category="意向图",
                scene_scope="uncertain",
            ),
            "core_fallback",
            "common",
        ),
        (
            _precheck(quality_severity="unusable"),
            "unassessable",
            None,
        ),
    ],
)
def test_fallback_and_unassessable_are_explicit(
    db: Session,
    precheck: dict,
    expected_status: str,
    expected_profile: str | None,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=False,
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=precheck,
    )
    profile = resolved["resolved_evaluation_profile"]
    assert resolved["resolution_status"] == expected_status
    assert (
        profile["profile_key"]
        if isinstance(profile, dict)
        else None
    ) == expected_profile
    assert resolved["needs_review"] is True


def test_resolution_is_repeatable_from_frozen_bundle(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    precheck = _precheck(
        scope_status="boundary",
        primary_category="灯具照明",
        scene_scope="object_only",
    )
    expected = canonical_json(
        resolve_frozen_evaluation_profile(
            bundle=bundle,
            precheck=precheck,
        )
    )
    assert {
        canonical_json(
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=precheck,
            )
        )
        for _ in range(100)
    } == {expected}


def test_profile_storage_order_does_not_change_resolution(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    precheck = _precheck(
        scope_status="boundary",
        primary_category="灯具照明",
        scene_scope="object_only",
    )
    expected = canonical_json(
        resolve_frozen_evaluation_profile(
            bundle=bundle,
            precheck=precheck,
        )
    )
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["profiles"] = {
        key: payload["profiles"][key]
        for key in reversed(list(payload["profiles"]))
    }
    bundle.evaluation_profile_set_snapshot = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        assert canonical_json(
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=precheck,
            )
        ) == expected
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_publishing_new_registry_objects_does_not_change_old_bundle(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    precheck = _precheck(
        scope_status="boundary",
        primary_category="灯具照明",
        scene_scope="object_only",
    )
    expected = canonical_json(
        resolve_frozen_evaluation_profile(
            bundle=bundle,
            precheck=precheck,
        )
    )
    db.add(
        PromptVersion(
            stage="B",
            name="Future Product B",
            version="product-B-v99",
            system_prompt="future system",
            user_prompt="future user",
            rubric_version="future-rubric",
            status="published",
        )
    )
    db.commit()
    assert canonical_json(
        resolve_frozen_evaluation_profile(
            bundle=bundle,
            precheck=precheck,
        )
    ) == expected


def test_space_product_signal_conflict_uses_core_fallback(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    resolved = resolve_frozen_evaluation_profile(
        bundle=bundle,
        precheck=_precheck(
            primary_category="住宅设计",
            scene_scope="object_only",
        ),
    )
    assert resolved["resolution_status"] == "core_fallback"
    assert resolved["route_decision_snapshot"]["route_reason"] == (
        "space_product_signal_conflict"
    )
    assert resolved["resolved_evaluation_profile"]["profile_key"] == "common"


def test_tampered_route_policy_body_is_rejected(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.dimension_route_policy_snapshot
    payload = json.loads(original)
    payload["definition"]["tampered"] = True
    bundle.dimension_route_policy_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="路由策略规范哈希无效",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(),
            )
    finally:
        bundle.dimension_route_policy_snapshot = original


def test_route_policy_cannot_expand_snapshot_input_allowlist(
    db: Session,
) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.dimension_route_policy_snapshot
    payload = json.loads(original)
    payload["definition"]["input_contract"]["allowed_paths"].append(
        "must_not_freeze.raw_model_payload"
    )
    payload["canonical_hash"] = canonical_hash(payload["definition"])
    bundle.dimension_route_policy_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="允许输入字段不符合安全合同",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(),
            )
    finally:
        bundle.dimension_route_policy_snapshot = original


def test_tampered_profile_hash_is_rejected(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["profiles"]["product"]["canonical_hash"] = "0" * 64
    _rehash_without_canonical_hash(payload)
    bundle.evaluation_profile_set_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="EvaluationProfile product 规范哈希无效",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(
                    primary_category="软装家具",
                    scene_scope="object_only",
                ),
            )
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_tampered_schema_body_is_rejected(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["profiles"]["product"]["dimension_schema"]["definition"][
        "tampered"
    ] = True
    _rehash_profile_set(payload, "product")
    bundle.evaluation_profile_set_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="Schema 哈希无效",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(
                    primary_category="软装家具",
                    scene_scope="object_only",
                ),
            )
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_tampered_prompt_b_body_is_rejected(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["profiles"]["product"]["prompt_b"]["system_prompt"] = "tampered"
    _rehash_profile_set(payload, "product")
    bundle.evaluation_profile_set_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="B 哈希无效",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(
                    primary_category="软装家具",
                    scene_scope="object_only",
                ),
            )
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_tampered_label_field_set_is_rejected(db: Session) -> None:
    bundle, _prompt_a, _sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.evaluation_profile_set_snapshot
    payload = json.loads(original)
    payload["profiles"]["product"]["label_field_set"][
        "label_fields_snapshot"
    ].append("tampered_label")
    _rehash_profile_set(payload, "product")
    bundle.evaluation_profile_set_snapshot = canonical_json(payload)
    try:
        with pytest.raises(
            RoutedStrategyContractError,
            match="标签哈希无效",
        ):
            resolve_frozen_evaluation_profile(
                bundle=bundle,
                precheck=_precheck(
                    primary_category="软装家具",
                    scene_scope="object_only",
                ),
            )
    finally:
        bundle.evaluation_profile_set_snapshot = original


def test_full_resolution_snapshot_is_hash_replayable(db: Session) -> None:
    bundle, prompt_a, sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    payload = json.loads(
        build_routed_evaluation_strategy_snapshot(
            bundle=bundle,
            prompt_a=prompt_a,
            sampling_policy=sampling,
            precheck=_precheck(
                scope_status="boundary",
                primary_category="软装饰品",
                scene_scope="object_only",
            ),
            resolution_timestamp=datetime(
                2026,
                7,
                31,
                1,
                2,
                3,
                tzinfo=timezone.utc,
            ),
        )
    )
    resolution_keys = {
        "format_version",
        "execution_context",
        "route_policy_hash",
        "route_input_snapshot",
        "route_decision_snapshot",
        "resolution_status",
        "needs_review",
        "blocked_reasons",
        "strategy_bundle_id",
        "strategy_bundle_hash",
        "resolved_evaluation_profile_key",
        "resolved_evaluation_profile_hash",
        "resolved_dimension_schema_key",
        "resolved_dimension_schema_version",
        "resolved_dimension_schema_hash",
        "resolved_dimensions_snapshot",
        "resolved_prompt_b_id",
        "resolved_prompt_b_version",
        "resolved_prompt_b_hash",
        "resolved_label_field_set_hash",
        "resolution_timestamp",
    }
    resolution = {key: payload[key] for key in resolution_keys}
    assert payload["resolved_snapshot_hash"] == canonical_hash(resolution)
    assert payload["schema_version"] == "strategy-bundle-v3"
    assert payload["prompt_b"] is None
    assert payload["resolved_evaluation_profile_key"] == "product"
    assert payload["resolved_prompt_b_version"] == (
        "product-B-v0.1-candidate.1"
    )


def test_resolution_snapshot_rejects_tampered_bundle_hash(
    db: Session,
) -> None:
    bundle, prompt_a, sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    original = bundle.canonical_hash
    bundle.canonical_hash = "0" * 64
    try:
        with pytest.raises(
            ValueError,
            match="StrategyBundle 不一致",
        ):
            build_routed_evaluation_strategy_snapshot(
                bundle=bundle,
                prompt_a=prompt_a,
                sampling_policy=sampling,
                precheck=_precheck(),
                resolution_timestamp=datetime.now(timezone.utc),
            )
    finally:
        bundle.canonical_hash = original


def test_resolution_snapshot_rejects_wrong_prompt_a(db: Session) -> None:
    bundle, _prompt_a, sampling = _bundle(
        db,
        include_product_prompt=True,
    )
    wrong = PromptVersion(
        stage="A",
        name="Wrong A",
        version="wrong-A-v1",
        system_prompt="wrong",
        user_prompt="wrong",
        rubric_version="wrong",
        status="draft",
    )
    db.add(wrong)
    db.commit()
    with pytest.raises(ValueError, match="StrategyBundle 不一致"):
        build_routed_evaluation_strategy_snapshot(
            bundle=bundle,
            prompt_a=wrong,
            sampling_policy=sampling,
            precheck=_precheck(),
            resolution_timestamp=datetime.now(timezone.utc),
        )


def test_v2_bundle_cannot_enter_v3_resolver(db: Session) -> None:
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
    assert bundle.strategy_schema_version == "strategy-bundle-v2"
    with pytest.raises(
        RoutedStrategyContractError,
        match="只有 strategy-bundle-v3",
    ):
        load_frozen_evaluation_profiles(bundle)
