"""Acceptance tests for immutable StrategyBundle history."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401
from app import strategy_bundle as strategy_bundle_module
from app.database import Base
from app.migrations import run_migrations
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    ModelConfig,
    PromptVersion,
    SamplingPolicy,
    StrategyBundle,
    StrategyBundleImmutableError,
    StrategySnapshotRequiredError,
)
from app.strategy_bundle import (
    REDACTED,
    StrategySecretError,
    _redact_secrets,
    build_strategy_snapshot,
    get_or_create_bundle,
)


@pytest.fixture
def db(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'strategy.db'}",
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


def _seed_strategy_inputs(
    db: Session,
    *,
    base_url: str = "https://example.test/v1",
    api_path: str = "/chat/completions",
    prompt_a_system: str = "System A",
    prompt_a_user: str = "User A",
) -> tuple[ModelConfig, PromptVersion, PromptVersion, SamplingPolicy]:
    model_config = ModelConfig(
        name="Test Model",
        provider="doubao",
        base_url=base_url,
        api_path=api_path,
        model_id="test-model-v1",
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
        name="Test A",
        version="A-v1",
        system_prompt=prompt_a_system,
        user_prompt=prompt_a_user,
        rubric_version="rubric-v1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="Test B",
        version="B-v1",
        system_prompt="System B",
        user_prompt="User B",
        rubric_version="rubric-v1",
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
    db.add_all([model_config, prompt_a, prompt_b, policy])
    db.commit()
    return model_config, prompt_a, prompt_b, policy


def _bundle(
    db: Session,
    model_config: ModelConfig,
    prompt_a: PromptVersion,
    prompt_b: PromptVersion | None,
    policy: SamplingPolicy | None,
    *,
    rubric_version: str = "rubric-v1",
    engine_version: str = "engine-v2.5.0",
    risk_review_version: str | None = "risk-v1",
) -> StrategyBundle:
    bundle = get_or_create_bundle(
        db=db,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version=rubric_version,
        engine_version=engine_version,
        risk_review_version=risk_review_version,
        sampling_policy=policy,
    )
    db.commit()
    return bundle


def _asset_and_job(db: Session) -> tuple[Asset, EvaluationJob]:
    asset = Asset(
        original_name="test.jpg",
        stored_name="stored.jpg",
        mime_type="image/jpeg",
        size_bytes=1000,
        sha256="a" * 64,
    )
    db.add(asset)
    db.flush()
    job = EvaluationJob(asset_id=asset.id, status="completed")
    db.add(job)
    db.commit()
    return asset, job


def _evaluation(
    *,
    asset: Asset,
    job: EvaluationJob,
    bundle_id: int | None,
    snapshot_json: str | None,
) -> EvaluationResult:
    return EvaluationResult(
        asset_id=asset.id,
        job_id=job.id,
        strategy_bundle_id=bundle_id,
        strategy_snapshot_json=snapshot_json,
        precheck_json="{}",
        scoring_json="{}",
        raw_response_a="{}",
        model_id="test-model-v1",
        prompt_a_version="A-v1",
        prompt_b_version="B-v1",
        risk_review_version="risk-v1",
        rubric_version="rubric-v1",
        engine_version="engine-v2.5.0",
    )


def test_identical_canonical_definition_reuses_one_bundle(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)

    first = _bundle(db, model_config, prompt_a, prompt_b, policy)
    second = _bundle(db, model_config, prompt_a, prompt_b, policy)

    assert second.id == first.id
    assert second.canonical_hash == first.canonical_hash
    assert db.scalar(select(func.count()).select_from(StrategyBundle)) == 1


def test_concurrent_sqlite_workers_reuse_the_same_bundle(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bundle-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        run_migrations(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        model_config, prompt_a, prompt_b, policy = (
            _seed_strategy_inputs(db)
        )
        input_ids = (
            model_config.id,
            prompt_a.id,
            prompt_b.id,
            policy.id,
        )

    barrier = Barrier(2)
    original_insert = strategy_bundle_module._insert_bundle_if_absent

    def synchronized_insert(session, values):
        barrier.wait(timeout=5)
        return original_insert(session, values)

    monkeypatch.setattr(
        strategy_bundle_module,
        "_insert_bundle_if_absent",
        synchronized_insert,
    )

    def create_from_worker() -> int:
        with factory() as db:
            model_config = db.get(ModelConfig, input_ids[0])
            prompt_a = db.get(PromptVersion, input_ids[1])
            prompt_b = db.get(PromptVersion, input_ids[2])
            policy = db.get(SamplingPolicy, input_ids[3])
            assert model_config and prompt_a and prompt_b and policy
            bundle = get_or_create_bundle(
                db,
                model_config,
                prompt_a,
                prompt_b,
                "rubric-v1",
                "engine-v2.5.0",
                "risk-v1",
                policy,
            )
            db.commit()
            return bundle.id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: create_from_worker(), range(2)))
        assert ids[0] == ids[1]
        with factory() as db:
            assert (
                db.scalar(
                    select(func.count()).select_from(StrategyBundle)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_noncanonical_integrity_error_is_not_swallowed(
    db: Session,
    monkeypatch,
) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    original_values = strategy_bundle_module._bundle_values

    def invalid_values(**kwargs):
        values = original_values(**kwargs)
        values["model_id"] = None
        return values

    monkeypatch.setattr(
        strategy_bundle_module,
        "_bundle_values",
        invalid_values,
    )
    with pytest.raises(IntegrityError, match="model_id"):
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )
    db.rollback()
    assert db.scalar(select(func.count()).select_from(StrategyBundle)) == 0


@pytest.mark.parametrize(
    "leak_value",
    [
        "client_secret=late-bundle-value-sentinel",
        "API-KEY/late-bundle-value-sentinel",
    ],
)
def test_final_recursive_gate_rejects_late_bundle_value_leak(
    db: Session,
    monkeypatch,
    leak_value: str,
) -> None:
    sentinel = "late-bundle-value-sentinel"
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    original_values = strategy_bundle_module._bundle_values

    def unsafe_values(**kwargs):
        values = original_values(**kwargs)
        values["model_config_snapshot"] = json.dumps(
            {
                "base_url": "https://example.test/v1",
                "note": leak_value,
            }
        )
        return values

    monkeypatch.setattr(
        strategy_bundle_module,
        "_bundle_values",
        unsafe_values,
    )
    with pytest.raises(StrategySecretError) as caught:
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )

    assert sentinel not in str(caught.value)
    bundle_database_text = json.dumps(
        [
            {
                key: value
                for key, value in vars(bundle).items()
                if not key.startswith("_")
            }
            for bundle in db.scalars(select(StrategyBundle)).all()
        ],
        ensure_ascii=False,
        default=str,
    )
    assert sentinel not in bundle_database_text
    assert (
        db.scalar(select(func.count()).select_from(StrategyBundle))
        == 0
    )


def test_prompt_persistent_id_and_exact_body_are_part_of_hash(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    original = _bundle(db, model_config, prompt_a, prompt_b, policy)

    same_content_new_id = PromptVersion(
        stage=prompt_a.stage,
        name=prompt_a.name,
        version=prompt_a.version,
        system_prompt=prompt_a.system_prompt,
        user_prompt=prompt_a.user_prompt,
        rubric_version=prompt_a.rubric_version,
        status="published",
    )
    db.add(same_content_new_id)
    db.commit()
    different_identity = _bundle(
        db, model_config, same_content_new_id, prompt_b, policy
    )
    assert different_identity.canonical_hash != original.canonical_hash

    prompt_a.system_prompt = "System A changed without changing version"
    changed_body = _bundle(db, model_config, prompt_a, prompt_b, policy)
    assert changed_body.canonical_hash != original.canonical_hash
    assert changed_body.id not in {original.id, different_identity.id}

    with pytest.raises(ValueError, match="当前策略定义"):
        build_strategy_snapshot(original, prompt_a, prompt_b, policy)


def test_hash_covers_model_rubric_engine_risk_and_sampling(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    hashes = {
        _bundle(db, model_config, prompt_a, prompt_b, policy).canonical_hash
    }

    model_config.temperature = 0.2
    hashes.add(_bundle(db, model_config, prompt_a, prompt_b, policy).canonical_hash)

    policy.sample_rate = 25
    hashes.add(_bundle(db, model_config, prompt_a, prompt_b, policy).canonical_hash)

    hashes.add(
        _bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            policy,
            rubric_version="rubric-v2",
        ).canonical_hash
    )
    hashes.add(
        _bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            policy,
            engine_version="engine-v3",
        ).canonical_hash
    )
    hashes.add(
        _bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            policy,
            risk_review_version="risk-v2",
        ).canonical_hash
    )

    assert len(hashes) == 6


def test_secret_rotation_does_not_change_hash(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    first = _bundle(db, model_config, prompt_a, prompt_b, policy)

    model_config.encrypted_api_key = "different-encrypted-secret"
    second = _bundle(db, model_config, prompt_a, prompt_b, policy)

    assert second.id == first.id


def test_strategy_creation_rejects_endpoint_and_header_credentials(
    db: Session,
) -> None:
    secrets = {
        "user": "endpoint-user",
        "password": "endpoint-password",
        "query": "query-api-secret",
        "auth_query": "query-auth-secret",
        "path": "path-credential-secret",
        "path_query": "path-token-secret",
        "prompt_auth": "prompt-bearer-secret",
        "prompt_key": "prompt-api-secret",
        "nested": "nested-cookie-secret",
    }
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        base_url=(
            f"https://{secrets['user']}:{secrets['password']}@Example.Test:443"
            f"/v1/token/{secrets['path']}?region=cn&api_key={secrets['query']}"
            f"&auth={secrets['auth_query']}"
        ),
        api_path=f"/chat/completions?token={secrets['path_query']}&format=json",
        prompt_a_system=f"Authorization: Bearer {secrets['prompt_auth']}",
        prompt_a_user=f"x-api-key: {secrets['prompt_key']}",
    )
    with pytest.raises(StrategySecretError) as caught:
        _bundle(db, model_config, prompt_a, prompt_b, policy)
    for secret in secrets.values():
        assert secret not in str(caught.value)
    assert db.scalar(select(func.count()).select_from(StrategyBundle)) == 0

    sanitized = _redact_secrets(
        {
            "base_url": model_config.base_url,
            "api_path": model_config.api_path,
            "system_prompt": prompt_a.system_prompt,
            "user_prompt": prompt_a.user_prompt,
        }
    )
    base_url = urlsplit(sanitized["base_url"])
    assert base_url.netloc == "example.test"
    assert base_url.path == f"/v1/token/{REDACTED}"
    assert parse_qs(base_url.query) == {
        "api_key": [REDACTED],
        "auth": [REDACTED],
        "region": ["cn"],
    }
    api_path = urlsplit(sanitized["api_path"])
    assert api_path.path == "/chat/completions"
    assert parse_qs(api_path.query) == {
        "format": ["json"],
        "token": [REDACTED],
    }
    assert sanitized["system_prompt"] == f"Authorization: {REDACTED}"
    assert sanitized["user_prompt"] == f"x-api-key: {REDACTED}"

    nested = _redact_secrets(
        {
            "transport": {
                "headers": {
                    "Authorization": "Basic dXNlcjpwYXNz",
                    "Cookie": f"session={secrets['nested']}",
                    "Accept": "application/json",
                }
            }
        }
    )
    assert nested["transport"]["headers"] == {
        "Authorization": REDACTED,
        "Cookie": REDACTED,
        "Accept": "application/json",
    }


def test_serialized_prompt_credentials_fail_closed(
    db: Session,
) -> None:
    api_secret = "serialized-api-key-secret"
    auth_secret = "serialized-authorization-secret"
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        prompt_a_system=json.dumps(
            {
                "api_key": api_secret,
                "authorization_header": (
                    f"Bearer {auth_secret}"
                ),
                "business_key": "catalog-entry",
                "keyword": "tokenization quality",
            }
        ),
    )
    with pytest.raises(StrategySecretError) as serialized_error:
        _bundle(db, model_config, prompt_a, prompt_b, policy)
    assert api_secret not in str(serialized_error.value)
    assert auth_secret not in str(serialized_error.value)
    assert db.scalar(select(func.count()).select_from(StrategyBundle)) == 0

    prompt_a.system_prompt = (
        '{"api_key":"malformed-secret-must-not-echo"'
    )
    before = db.scalar(
        select(func.count()).select_from(StrategyBundle)
    )
    with pytest.raises(StrategySecretError) as caught:
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )
    assert "malformed-secret-must-not-echo" not in str(caught.value)
    assert (
        db.scalar(select(func.count()).select_from(StrategyBundle))
        == before
    )


def test_business_auth_language_and_safe_json_prompt_remain_exact(
    db: Session,
) -> None:
    business_description = (
        "该供应商使用 Basic authentication 模式，无任何真实凭据；"
        "Bearer token 机制也仅为业务描述。"
        "Bearer token mechanism and Bearer authentication mode "
        "are documentation terms."
    )
    safe_json_prompt = (
        '{ "business_key": "business_key/foo", '
        '"keyword": "keyword/path" }'
    )
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        prompt_a_system=business_description,
        prompt_a_user=safe_json_prompt,
    )

    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    snapshot = json.loads(
        build_strategy_snapshot(bundle, prompt_a, prompt_b, policy)
    )

    assert snapshot["prompt_a"]["system_prompt"] == business_description
    assert snapshot["prompt_a"]["user_prompt"] == safe_json_prompt
    assert json.loads(bundle.model_config_snapshot)["base_url"] == (
        "https://example.test/v1"
    )


@pytest.mark.parametrize(
    "credential_text",
    [
        "Authorization: Basic dXNlcjpwYXNz",
        (
            "Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature123"
        ),
        "Bearer sk-example-secret",
        "Bearer abcdefghijklmnopq",
        "Basic dXNlcjpwYXNz",
    ],
)
def test_credential_shaped_basic_and_bearer_prompts_are_rejected(
    db: Session,
    credential_text: str,
) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        prompt_a_system=credential_text,
    )

    with pytest.raises(StrategySecretError) as caught:
        _bundle(db, model_config, prompt_a, prompt_b, policy)

    assert credential_text not in str(caught.value)
    assert db.scalar(select(func.count()).select_from(StrategyBundle)) == 0


def test_slash_secret_aliases_fail_closed_across_strategy_sources_and_bytes(
    tmp_path,
) -> None:
    database_path = tmp_path / "strategy-slash.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(bind=engine)
    session = Session(engine, expire_on_commit=False)
    sentinels: list[str] = []
    try:
        model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
            session
        )
        aliases = (
            "AUTHORIZATION_HEADER",
            "Authorization-Header",
            "CLIENT_SECRET",
            "Client-Secret",
            "X_AUTH_TOKEN",
            "X-Auth-Token",
            "API_KEY",
            "Api-Key",
            "COOKIE",
        )
        locations = (
            "config",
            "model",
            "prompt",
            "nested_prompt",
            "url_path",
            "url_query",
        )

        for alias_index, alias in enumerate(aliases):
            for location_index, location in enumerate(locations):
                sentinel = (
                    f"strategy-slash-{alias_index}-{location_index}-sentinel"
                )
                sentinels.append(sentinel)
                malicious_value = f"{alias}/{sentinel}"
                if location == "config":
                    model_config.name = malicious_value
                elif location == "model":
                    model_config.model_id = malicious_value
                elif location == "prompt":
                    prompt_a.system_prompt = malicious_value
                elif location == "nested_prompt":
                    prompt_a.system_prompt = json.dumps(
                        {"outer": {"note": malicious_value}}
                    )
                elif location == "url_path":
                    model_config.base_url = (
                        f"https://example.test/v1/{malicious_value}"
                    )
                else:
                    model_config.base_url = (
                        "https://example.test/v1"
                        f"?next={malicious_value}"
                    )

                with pytest.raises(StrategySecretError) as caught:
                    get_or_create_bundle(
                        session,
                        model_config,
                        prompt_a,
                        prompt_b,
                        "rubric-v1",
                        "engine-v2.5.0",
                        "risk-v1",
                        policy,
                    )
                assert alias not in str(caught.value)
                assert sentinel not in str(caught.value)
                session.rollback()

        assert (
            session.scalar(select(func.count()).select_from(StrategyBundle))
            == 0
        )
    finally:
        session.close()
        engine.dispose()

    raw_database = database_path.read_bytes()
    for sentinel in sentinels:
        assert sentinel.encode() not in raw_database


@pytest.mark.parametrize(
    "base_url_template",
    [
        "https://example.test:bad/v1?client_secret={sentinel}",
        "https://example.test:bad/v1?authorization_header={sentinel}",
        "https://example.test:bad/v1?API-key={sentinel}",
        "https://user:{sentinel}@example.test:bad/v1",
    ],
)
def test_malformed_endpoint_credentials_fail_closed_before_bundle_write(
    db: Session,
    base_url_template: str,
) -> None:
    sentinel = "malformed-endpoint-sentinel"
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        base_url=base_url_template.format(sentinel=sentinel),
    )
    before = db.scalar(select(func.count()).select_from(StrategyBundle))

    with pytest.raises(StrategySecretError) as caught:
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )

    assert sentinel not in str(caught.value)
    bundles = db.scalars(select(StrategyBundle)).all()
    bundle_database_text = json.dumps(
        [
            {
                key: value
                for key, value in vars(bundle).items()
                if not key.startswith("_")
            }
            for bundle in bundles
        ],
        ensure_ascii=False,
        default=str,
    )
    assert sentinel not in bundle_database_text
    assert len(bundles) == before


def test_nested_serialized_endpoint_and_prompt_credentials_fail_closed(
    db: Session,
) -> None:
    endpoint_sentinel = "nested-endpoint-sentinel"
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        base_url=json.dumps(
            {
                "transport": {
                    "url": (
                        "https://example.test/v1"
                        f"?client_secret={endpoint_sentinel}"
                    )
                }
            }
        ),
    )
    with pytest.raises(StrategySecretError) as endpoint_error:
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )
    assert endpoint_sentinel not in str(endpoint_error.value)

    model_config.base_url = "https://example.test/v1"
    prompt_sentinel = "nested-prompt-sentinel"
    prompt_a.system_prompt = json.dumps(
        {
            "outer": {
                "note": f"client-secret:{prompt_sentinel}",
                "business_key": "catalog-entry",
                "keyword": "tokenization quality",
            }
        }
    )
    with pytest.raises(StrategySecretError) as prompt_error:
        get_or_create_bundle(
            db,
            model_config,
            prompt_a,
            prompt_b,
            "rubric-v1",
            "engine-v2.5.0",
            "risk-v1",
            policy,
        )
    assert prompt_sentinel not in str(prompt_error.value)
    assert (
        db.scalar(select(func.count()).select_from(StrategyBundle))
        == 0
    )


def test_credential_free_endpoint_is_persisted_unchanged(db: Session) -> None:
    base_url = "https://Example.Test:443/v1?region=cn&keyword=render"
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(
        db,
        base_url=base_url,
    )
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    snapshot = json.loads(bundle.model_config_snapshot)

    assert snapshot["base_url"] == (
        "https://example.test/v1?keyword=render&region=cn"
    )


_IMMUTABLE_FIELD_CHANGES = [
    ("canonical_hash", "f" * 64),
    ("model_id", "changed-model"),
    ("model_config_snapshot", '{"changed":true}'),
    ("prompt_a_version", "changed-a"),
    ("prompt_b_version", "changed-b"),
    ("rubric_version", "changed-rubric"),
    ("engine_version", "changed-engine"),
    ("sampling_policy_revision", 999),
    ("risk_review_version", "changed-risk"),
    ("created_at", datetime(2030, 1, 1, tzinfo=timezone.utc)),
]


@pytest.mark.parametrize(("field", "new_value"), _IMMUTABLE_FIELD_CHANGES)
def test_persisted_bundle_rejects_every_field_mutation(
    db: Session, field: str, new_value: object
) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    original_id = bundle.id

    setattr(bundle, field, new_value)
    with pytest.raises(StrategyBundleImmutableError, match="禁止原地更新"):
        db.commit()
    db.rollback()

    persisted = db.get(StrategyBundle, original_id)
    assert persisted is not None
    assert getattr(persisted, field) != new_value


def test_persisted_bundle_rejects_orm_delete(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    bundle_id = bundle.id

    db.delete(bundle)
    with pytest.raises(StrategyBundleImmutableError, match="禁止删除"):
        db.commit()
    db.rollback()

    assert db.get(StrategyBundle, bundle_id) is not None


def test_new_result_persists_complete_bundle_snapshot(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    snapshot_json = build_strategy_snapshot(bundle, prompt_a, prompt_b, policy)
    asset, job = _asset_and_job(db)

    result = _evaluation(
        asset=asset,
        job=job,
        bundle_id=bundle.id,
        snapshot_json=snapshot_json,
    )
    db.add(result)
    db.commit()

    snapshot = json.loads(result.strategy_snapshot_json or "")
    assert result.strategy_bundle_id == bundle.id
    assert snapshot["bundle_id"] == bundle.id
    assert snapshot["canonical_hash"] == bundle.canonical_hash
    assert snapshot["prompt_a"]["id"] == prompt_a.id
    assert snapshot["prompt_b"]["id"] == prompt_b.id
    assert snapshot["sampling_policy"]["revision"] == policy.revision


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "missing_bundle",
        "missing_snapshot",
        "empty_snapshot",
        "incomplete_snapshot",
        "mismatch",
        "hash_mismatch",
    ],
)
def test_new_result_rejects_missing_or_incomplete_strategy(
    db: Session, invalid_kind: str
) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    snapshot_json = build_strategy_snapshot(bundle, prompt_a, prompt_b, policy)
    asset, job = _asset_and_job(db)
    bundle_id: int | None = bundle.id

    if invalid_kind == "missing_bundle":
        bundle_id = None
    elif invalid_kind == "missing_snapshot":
        snapshot_json = None
    elif invalid_kind == "empty_snapshot":
        snapshot_json = " "
    elif invalid_kind == "incomplete_snapshot":
        snapshot_json = "{}"
    elif invalid_kind == "mismatch":
        snapshot = json.loads(snapshot_json)
        snapshot["bundle_id"] = bundle.id + 1
        snapshot_json = json.dumps(snapshot)
    else:
        snapshot = json.loads(snapshot_json)
        snapshot["canonical_hash"] = "f" * 64
        snapshot_json = json.dumps(snapshot)

    db.add(
        _evaluation(
            asset=asset,
            job=job,
            bundle_id=bundle_id,
            snapshot_json=snapshot_json,
        )
    )
    with pytest.raises(StrategySnapshotRequiredError):
        db.commit()
    db.rollback()


def test_database_trigger_rejects_unbound_raw_result_insert(db: Session) -> None:
    asset, job = _asset_and_job(db)

    with pytest.raises(IntegrityError, match="strategy binding is required"):
        db.connection().exec_driver_sql(
            """
            INSERT INTO evaluation_results (
                asset_id, job_id, precheck_json, scoring_json, raw_response_a,
                needs_review, model_id, prompt_a_version, rubric_version,
                engine_version, created_at, updated_at
            )
            VALUES (?, ?, '{}', '{}', '{}', 0, 'model', 'A1', 'R1', 'E1',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (asset.id, job.id),
        )
    db.rollback()


def test_database_trigger_rejects_incomplete_raw_snapshot(db: Session) -> None:
    model_config, prompt_a, prompt_b, policy = _seed_strategy_inputs(db)
    bundle = _bundle(db, model_config, prompt_a, prompt_b, policy)
    asset, job = _asset_and_job(db)

    with pytest.raises(IntegrityError, match="strategy binding is required"):
        db.connection().exec_driver_sql(
            """
            INSERT INTO evaluation_results (
                asset_id, job_id, strategy_bundle_id, strategy_snapshot_json,
                precheck_json, scoring_json, raw_response_a, needs_review,
                model_id, prompt_a_version, prompt_b_version,
                risk_review_version, rubric_version, engine_version,
                created_at, updated_at
            )
            VALUES (
                ?, ?, ?, ?, '{}', '{}', '{}', 0,
                'test-model-v1', 'A-v1', 'B-v1', 'risk-v1',
                'rubric-v1', 'engine-v2.5.0',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (
                asset.id,
                job.id,
                bundle.id,
                json.dumps({"bundle_id": bundle.id, "placeholder": True}),
            ),
        )
    db.rollback()
