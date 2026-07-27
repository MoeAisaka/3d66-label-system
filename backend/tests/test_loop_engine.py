from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base, get_db
from app.doubao import DoubaoResponse
from app.loop_engine import (
    LoopContractError,
    assert_safe_normalized_payload,
    contains_credential_auth_scheme,
    decide_next_step,
    normalize_targeted_model_result,
    validate_result_scope,
    validate_submission_scope,
)
from app.main import app, current_user
from app.models import (
    Asset,
    EvaluationJob,
    LoopAttempt,
    LoopRun,
    ModelConfig,
    PromptVersion,
    StrategyBundle,
    User,
)
from app.scoring import ENGINE_VERSION
from app.strategy_bundle import build_strategy_snapshot, get_or_create_bundle


def _attempt(
    business_round: int,
    result: dict,
    *,
    target_dimensions: list[str] | None = None,
) -> dict:
    kinds = {1: "base", 2: "targeted_recheck", 3: "arbitration"}
    return {
        "round": business_round,
        "kind": kinds[business_round],
        "target_dimensions": target_dimensions or [],
        "normalized_result": result,
    }


def _valid_result(
    value: int,
    evidence: str,
    *,
    field: str = "lighting",
) -> dict:
    return {
        "dimension_values": {field: value},
        "evidence": {field: evidence},
        "schema_valid": True,
        "enum_valid": True,
        "cross_field_valid": True,
        "confidence_threshold_met": True,
        "version_threshold_met": True,
        "freeze_threshold_met": True,
    }


def test_empty_and_single_stable_result_fail_closed() -> None:
    empty = decide_next_step([_attempt(1, {})])
    assert empty.machine_converged is False
    assert empty.needs_human is True
    assert empty.reason_codes == ("MISSING_EVIDENCE",)

    stable_only = decide_next_step(
        [_attempt(1, {"stable": True, "evidence": {"scope": "in_scope"}})]
    )
    assert stable_only.machine_converged is False
    assert stable_only.needs_human is True

    valid_single = decide_next_step(
        [_attempt(1, _valid_result(3, "first observation"))]
    )
    assert valid_single.machine_converged is False
    assert valid_single.status == "needs_human"
    assert valid_single.reason_codes == (
        "UNLOCATABLE_UNSTABLE_RESULT",
    )
    assert valid_single.next_round is None


def test_round1_problem_schedules_only_problem_dimensions() -> None:
    decision = decide_next_step(
        [
            _attempt(
                1,
                {
                    "low_confidence_dimensions": ["lighting"],
                    "ab_conflict_dimensions": ["composition"],
                    "schema_error_dimensions": ["material"],
                    "new_evidence": True,
                    "dimension_values": {
                        "lighting": 2,
                        "composition": 3,
                        "material": 2,
                    },
                    "evidence": {
                        "lighting": "dark",
                        "composition": "cropped",
                        "material": "ambiguous",
                    },
                },
            )
        ]
    )
    assert decision.status == "waiting_result"
    assert decision.next_round == 2
    assert decision.next_kind == "targeted_recheck"
    assert decision.target_dimensions == (
        "composition",
        "lighting",
        "material",
    )


def test_round2_resolution_stops_and_conflict_alone_reaches_round3() -> None:
    round1 = _attempt(
        1,
        {
            **_valid_result(3, "first observation"),
            "problem_dimensions": ["lighting"],
        },
    )
    resolved = decide_next_step(
        [
            round1,
            _attempt(
                2,
                _valid_result(3, "independent second observation"),
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert resolved.machine_converged is True
    assert resolved.reason_codes == ("ROUND2_RESOLVED",)
    assert resolved.evidence["rounds"][-1]["server_consistency"] is True
    assert resolved.evidence["rounds"][-1]["conflict_dimensions"] == []

    conflict = decide_next_step(
        [
            round1,
            _attempt(
                2,
                {
                    **_valid_result(4, "independent conflicting observation"),
                },
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert conflict.next_round == 3
    assert conflict.next_kind == "arbitration"
    assert conflict.target_dimensions == ("lighting",)
    assert conflict.evidence["rounds"][-1]["server_consistency"] is False
    assert conflict.evidence["rounds"][-1]["conflict_dimensions"] == [
        "lighting"
    ]


def test_round3_forces_human_when_arbitration_does_not_converge() -> None:
    attempts = [
        _attempt(
            1,
            {
                **_valid_result(2, "first observation"),
                "problem_dimensions": ["lighting"],
            },
        ),
        _attempt(
            2,
            _valid_result(4, "second conflicting observation"),
            target_dimensions=["lighting"],
        ),
        _attempt(
            3,
            {
                "suggested_values": {"lighting": 3},
                "arbitration_evidence": {
                    "lighting": ["A=2", "B=4", "arbiter=3"]
                },
                **{
                    key: True
                    for key in (
                        "schema_valid",
                        "enum_valid",
                        "cross_field_valid",
                        "confidence_threshold_met",
                        "version_threshold_met",
                        "freeze_threshold_met",
                    )
                },
            },
            target_dimensions=["lighting"],
        ),
    ]
    decision = decide_next_step(attempts)
    assert decision.needs_human is True
    assert decision.reason_codes == ("ROUND3_FORCED_HUMAN",)
    assert decision.next_round is None
    assert decision.evidence["rounds"][-1]["suggested_values"] == {
        "lighting": 3
    }


def test_round3_can_converge_only_with_suggestion_and_conflict_evidence() -> None:
    first_two = [
        _attempt(
            1,
            {
                **_valid_result(2, "first observation"),
                "problem_dimensions": ["lighting"],
            },
        ),
        _attempt(
            2,
            _valid_result(4, "second conflicting observation"),
            target_dimensions=["lighting"],
        ),
    ]
    converged = decide_next_step(
        [
            *first_two,
            _attempt(
                3,
                {
                    "suggested_values": {"lighting": 4},
                    "arbitration_evidence": {
                        "lighting": ["A=2", "B=4", "new crop confirms B"]
                    },
                    **{
                        key: True
                        for key in (
                            "schema_valid",
                            "enum_valid",
                            "cross_field_valid",
                            "confidence_threshold_met",
                            "version_threshold_met",
                            "freeze_threshold_met",
                        )
                    },
                },
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert converged.machine_converged is True
    assert converged.reason_codes == ("ROUND3_RESOLVED",)

    missing_evidence = decide_next_step(
        [
            *first_two,
            _attempt(
                3,
                {
                    "suggested_values": {"lighting": 4},
                    "arbitration_evidence": {},
                },
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert missing_evidence.needs_human is True
    assert missing_evidence.reason_codes == ("MISSING_EVIDENCE",)


def test_no_new_evidence_stops_without_repeated_call() -> None:
    decision = decide_next_step(
        [
            _attempt(
                1,
                {
                    "problem_dimensions": ["lighting"],
                    "new_evidence": False,
                    "stable": True,
                    "dimension_values": {"lighting": 3},
                    "evidence": {"lighting": "existing"},
                },
            )
        ]
    )
    assert decision.needs_human is True
    assert decision.reason_codes == ("NO_NEW_EVIDENCE",)

    normalized = normalize_targeted_model_result(
        {
            "dimension_values": {"lighting": 3},
            "evidence": {"lighting": "provider says no new evidence"},
            "confidence_by_dimension": {"lighting": 0.95},
            "new_evidence": False,
        },
        business_round=2,
        target_dimensions=["lighting"],
    )
    assert normalized["new_evidence"] is False
    targeted_decision = decide_next_step(
        [
            _attempt(
                1,
                {
                    **_valid_result(3, "first observation"),
                    "problem_dimensions": ["lighting"],
                },
            ),
            _attempt(
                2,
                normalized,
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert targeted_decision.needs_human is True
    assert targeted_decision.reason_codes == ("NO_NEW_EVIDENCE",)


def test_human_flags_and_identical_evidence_block_machine_progress() -> None:
    needs_human = decide_next_step(
        [
            _attempt(
                1,
                {
                    **_valid_result(3, "first"),
                    "needs_human": True,
                },
            )
        ]
    )
    assert needs_human.needs_human is True
    assert needs_human.reason_codes == ("FORCE_HUMAN",)

    legacy_needs_review = decide_next_step(
        [
            _attempt(
                1,
                {
                    **_valid_result(3, "first"),
                    "needs_review": True,
                },
            )
        ]
    )
    assert legacy_needs_review.needs_human is True
    assert legacy_needs_review.reason_codes == ("FORCE_HUMAN",)

    duplicate = decide_next_step(
        [
            _attempt(1, _valid_result(3, "same")),
            _attempt(
                2,
                _valid_result(3, "same"),
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert duplicate.needs_human is True
    assert duplicate.reason_codes == ("DUPLICATE_RESULT",)

    duplicate_target_evidence = decide_next_step(
        [
            _attempt(
                1,
                {
                    **_valid_result(3, "same target evidence"),
                    "problem_dimensions": ["lighting"],
                    "evidence": {
                        "lighting": "same target evidence",
                        "composition": "unrelated first-round evidence",
                    },
                },
            ),
            _attempt(
                2,
                _valid_result(4, "same target evidence"),
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert duplicate_target_evidence.needs_human is True
    assert duplicate_target_evidence.reason_codes == (
        "DUPLICATE_EVIDENCE",
    )


def test_round3_evidence_must_be_new_across_the_entire_loop_history() -> None:
    decision = decide_next_step(
        [
            _attempt(
                1,
                {
                    **_valid_result(2, "E1"),
                    "problem_dimensions": ["lighting"],
                },
            ),
            _attempt(
                2,
                _valid_result(4, "E2"),
                target_dimensions=["lighting"],
            ),
            _attempt(
                3,
                {
                    "suggested_values": {"lighting": 2},
                    "arbitration_evidence": {"lighting": "E1"},
                    **{
                        flag: True
                        for flag in (
                            "schema_valid",
                            "enum_valid",
                            "cross_field_valid",
                            "confidence_threshold_met",
                            "version_threshold_met",
                            "freeze_threshold_met",
                        )
                    },
                },
                target_dimensions=["lighting"],
            ),
        ]
    )
    assert decision.machine_converged is False
    assert decision.needs_human is True
    assert decision.reason_codes == ("DUPLICATE_RESULT",)
    assert (
        decision.evidence["rounds"][-1]["server_new_evidence"]
        is False
    )


def test_targeted_result_rejects_full_or_non_target_payloads() -> None:
    base = {
        **_valid_result(3, "new"),
        "confidence_by_dimension": {"lighting": 0.9},
    }
    for field in ("dimensions", "classification", "full_output"):
        with pytest.raises(LoopContractError, match="未允许字段"):
            validate_result_scope(
                business_round=2,
                target_dimensions=["lighting"],
                normalized_result={**base, field: {}},
            )
    with pytest.raises(LoopContractError, match="未冻结维度"):
        validate_result_scope(
            business_round=2,
            target_dimensions=["lighting"],
            normalized_result={
                **base,
                "dimension_values": {
                    "lighting": 3,
                    "composition": 4,
                },
            },
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "Authorization: Bearer top-secret-value"},
        {"note": "authorization_header=top-secret-value"},
        {"note": "AUTHORIZATION-HEADER:top-secret-value"},
        {"note": "client_secret=top-secret-value"},
        {"note": "CLIENT-SECRET:top-secret-value"},
        {"note": "x_auth_token=top-secret-value"},
        {"note": "X-AUTH-TOKEN:top-secret-value"},
        {"note": "api_key=top-secret-value"},
        {"note": "API-KEY:top-secret-value"},
        {"note": "access_token=top-secret-value"},
        {"note": "refresh-token:top-secret-value"},
        {"note": "cookie=top-secret-value"},
        {"note": "session:top-secret-value"},
        {"note": "password=top-secret-value"},
        {"note": "passwd:top-secret-value"},
        {"note": '{"client_secret":"top-secret-value"}'},
        {"note": "authorization_header/top-secret-value"},
        {"note": "AUTHORIZATION-HEADER/top-secret-value"},
        {"note": "client_secret/top-secret-value"},
        {"note": "CLIENT-SECRET/top-secret-value"},
        {"note": "x_auth_token/top-secret-value"},
        {"note": "X-AUTH-TOKEN/top-secret-value"},
        {"note": "api_key/top-secret-value"},
        {"note": "API-KEY/top-secret-value"},
        {"note": "cookie/top-secret-value"},
        {"note": "Authorization: Basic dXNlcjpwYXNz"},
        {
            "note": (
                "Authorization: Bearer "
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature123"
            )
        },
        {"note": "Bearer sk-example-secret"},
        {"note": "Basic dXNlcjpwYXNz"},
        {"auth_token": "top-secret-value"},
        {"x-api-key": "top-secret-value"},
        {"authorization_header": "top-secret-value"},
        {"client_secret_value": "top-secret-value"},
        {"x_auth_token_value": "top-secret-value"},
        {"provider_payload_backup": "top-secret-value"},
        {"authorizationHeaderBackup": "top-secret-value"},
        {"backupProviderPayloadValue": "top-secret-value"},
        {"url": "https://user:pass@example.test/path"},
        {"url": "https://example.test/path?api-key=top-secret-value"},
    ],
)
def test_sensitive_payload_detection_rejects_keys_and_string_values(
    payload: dict,
) -> None:
    with pytest.raises(LoopContractError):
        assert_safe_normalized_payload(payload)


def test_sensitive_payload_detection_allows_business_key_words() -> None:
    assert_safe_normalized_payload(
        {
            "key": "lighting",
            "business_key": "catalog-entry",
            "business_path": "business_key/foo",
            "keyword": "tokenization quality",
            "keyword_path": "keyword/path",
            "note": (
                "业务自然语言可讨论 business_key 和 keyword，"
                "也可以讨论 API key 的字段设计。"
                "该供应商使用 Basic authentication 模式，无任何真实凭据。"
                "该供应商使用 Bearer token 机制，无任何真实凭据。"
                "Bearer token mechanism and Bearer authentication mode "
                "are documentation terms."
            ),
            "url": "https://example.test/path?monkey=business",
            "ordinary_url": "https://example.test/v1/catalog/items/foo",
        }
    )


@pytest.mark.parametrize(
    "credential_text",
    [
        "Bearer abcdefghijkl",
        "Bearer abcdefghijklmnopq",
    ],
)
def test_lowercase_opaque_bearer_is_rejected_without_reflection(
    credential_text: str,
) -> None:
    assert contains_credential_auth_scheme(credential_text) is True
    with pytest.raises(LoopContractError) as caught:
        assert_safe_normalized_payload({"note": credential_text})
    assert credential_text not in str(caught.value)
    with pytest.raises(LoopContractError) as key_error:
        assert_safe_normalized_payload({credential_text: "safe value"})
    assert credential_text not in str(key_error.value)


def test_auth_scheme_business_language_and_basic_detection_do_not_regress() -> None:
    for business_text in (
        "Bearer token mechanism",
        "Bearer authentication mode",
        "该供应商使用 Bearer token 机制，无任何真实凭据。",
    ):
        assert contains_credential_auth_scheme(business_text) is False
        assert_safe_normalized_payload({"note": business_text})

    assert contains_credential_auth_scheme("Basic abcdefghijkl") is False
    assert contains_credential_auth_scheme("Basic dXNlcjpwYXNz") is True


@pytest.mark.parametrize(
    "malicious_key",
    [
        "authorization_header",
        "client_secret_value",
        "x_auth_token_value",
        "provider_payload_backup",
    ],
)
def test_sensitive_key_name_is_not_reflected_in_contract_error(
    malicious_key: str,
) -> None:
    sentinel = "credential-value-must-not-echo"
    with pytest.raises(LoopContractError) as caught:
        assert_safe_normalized_payload({malicious_key: sentinel})
    message = str(caught.value)
    assert malicious_key not in message
    assert sentinel not in message
    assert "$.*" in message


def test_scope_contract_rejects_full_round2_and_fourth_round() -> None:
    try:
        validate_submission_scope(
            business_round=2,
            expected_kind="targeted_recheck",
            expected_dimensions=["lighting"],
            submitted_kind="targeted_recheck",
            submitted_dimensions=["all"],
        )
    except LoopContractError as exc:
        assert "问题维度" in str(exc)
    else:
        raise AssertionError("round2 全量重跑必须被拒绝")

    try:
        validate_submission_scope(
            business_round=4,
            expected_kind="arbitration",
            expected_dimensions=["lighting"],
            submitted_kind="arbitration",
            submitted_dimensions=["lighting"],
        )
    except LoopContractError as exc:
        assert "第四轮" in str(exc)
    else:
        raise AssertionError("第四轮必须被拒绝")

    try:
        validate_result_scope(
            business_round=2,
            target_dimensions=["lighting"],
            normalized_result={
                "dimension_values": {
                    "lighting": 3,
                    "composition": 4,
                }
            },
        )
    except LoopContractError as exc:
        assert "未冻结维度" in str(exc)
    else:
        raise AssertionError("round2 结果不能夹带非目标维度")


def test_loop_openapi_documents_idempotency_without_runtime_validation() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]
    for model_name in ("LoopCreateRequest", "LoopResultRequest"):
        model_schema = components[model_name]
        field_schema = model_schema["properties"]["idempotency_key"]
        assert "idempotency_key" in model_schema["required"]
        assert field_schema["type"] == "string"
        assert field_schema["minLength"] == 8
        assert field_schema["maxLength"] == 160

    create_ref = schema["paths"]["/api/loops"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    attach_ref = schema["paths"][
        "/api/loops/{loop_id}/attempts/{business_round}/result"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert create_ref.endswith("/LoopCreateRequest")
    assert attach_ref.endswith("/LoopResultRequest")

    create_properties = components["LoopCreateRequest"]["properties"]
    assert create_properties["source"]["anyOf"][0] == {
        "enum": ["interactive", "validation"],
        "type": "string",
    }
    assert create_properties["model_id"]["anyOf"][0]["maxLength"] == 200
    assert (
        create_properties["prompt_a_version"]["anyOf"][0]["maxLength"]
        == 40
    )
    assert (
        create_properties["prompt_b_version"]["anyOf"][0]["maxLength"]
        == 40
    )
    assert create_properties["asset_id"]["minimum"] == 1
    assert create_properties["input_evidence"]["type"] == "object"

    attach_properties = components["LoopResultRequest"]["properties"]
    assert attach_properties["kind"] == {
        "enum": ["base", "targeted_recheck", "arbitration"],
        "title": "Kind",
        "type": "string",
    }
    assert attach_properties["target_dimensions"]["maxItems"] == 100
    assert attach_properties["target_dimensions"]["items"] == {
        "type": "string"
    }
    assert attach_properties["normalized_result"]["type"] == "object"
    assert attach_properties["model_id"]["anyOf"][0]["maxLength"] == 200
    assert (
        attach_properties["prompt_a_version"]["anyOf"][0]["maxLength"]
        == 40
    )
    assert (
        attach_properties["prompt_b_version"]["anyOf"][0]["maxLength"]
        == 40
    )


def test_loop_api_is_idempotent_and_rejects_drift_and_out_of_order(
    tmp_path,
) -> None:
    database_path = tmp_path / "loop-api.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(
        username="loop-tester",
        password_hash="unused",
        display_name="Loop Tester",
    )
    asset = Asset(
        original_name="loop.jpg",
        stored_name="loop.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="d" * 64,
    )
    bundle = StrategyBundle(
        canonical_hash="e" * 64,
        model_id="model-v1",
        model_config_snapshot="{}",
        prompt_a_version="A-v1",
        prompt_b_version="B-v1",
        rubric_version="R-v1",
        engine_version="E-v1",
    )
    prompt_a = PromptVersion(
        stage="A",
        name="A",
        version="A-v1",
        system_prompt="A system",
        user_prompt="A user",
        rubric_version="R-v1",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="B",
        version="B-v1",
        system_prompt="B system",
        user_prompt="B user",
        rubric_version="R-v1",
        status="published",
    )
    db.add_all([user, asset, prompt_a, prompt_b, bundle])
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        create_payload = {
            "asset_id": asset.id,
            "strategy_bundle_id": bundle.id,
            "idempotency_key": "create-loop-0001",
            "input_evidence": {"asset_sha256": asset.sha256},
        }
        created = client.post("/api/loops", json=create_payload)
        replayed = client.post("/api/loops", json=create_payload)
        assert created.status_code == 200
        assert replayed.status_code == 200
        assert created.json()["id"] == replayed.json()["id"]
        loop_id = created.json()["id"]
        initial_jobs = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        assert len(initial_jobs) == 1
        assert initial_jobs[0].queue_class == "interactive"
        assert initial_jobs[0].strategy_bundle_id == bundle.id
        assert initial_jobs[0].loop_attempt_id is not None

        out_of_order = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": "round2-too-early",
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": {"stable": True},
            },
        )
        assert out_of_order.status_code == 409

        drift = client.post(
            f"/api/loops/{loop_id}/attempts/1/result",
            json={
                "idempotency_key": "round1-drift",
                "strategy_bundle_id": bundle.id,
                "model_id": "other-model",
                "kind": "base",
                "target_dimensions": [],
                "normalized_result": {"stable": True},
            },
        )
        assert drift.status_code == 409

        round1_payload = {
            "idempotency_key": "round1-result-0001",
            "strategy_bundle_id": bundle.id,
            "kind": "base",
            "target_dimensions": [],
            "normalized_result": {
                "problem_dimensions": ["lighting"],
                "new_evidence": True,
                "dimension_values": {"lighting": 2},
                "evidence": {"lighting": "dim"},
            },
            "latency_ms": 20,
            "cost": 0.01,
        }
        round1 = client.post(
            f"/api/loops/{loop_id}/attempts/1/result",
            json=round1_payload,
        )
        repeated = client.post(
            f"/api/loops/{loop_id}/attempts/1/result",
            json=round1_payload,
        )
        assert round1.status_code == 200
        assert repeated.status_code == 200
        assert round1.json()["current_round"] == 2
        assert round1.json()["attempts"][1]["target_dimensions"] == [
            "lighting"
        ]
        jobs_after_round1 = db.scalars(
            select(EvaluationJob).order_by(EvaluationJob.id)
        ).all()
        assert [job.status for job in jobs_after_round1] == [
            "canceled",
            "queued",
        ]
        assert jobs_after_round1[1].loop_attempt_id == (
            round1.json()["attempts"][1]["id"]
        )

        validation_loop = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": "validation-loop-0001",
                "source": "validation",
            },
        )
        assert validation_loop.status_code == 200
        validation_attempt_id = validation_loop.json()["attempts"][0]["id"]
        validation_job = db.scalar(
            select(EvaluationJob).where(
                EvaluationJob.loop_attempt_id == validation_attempt_id
            )
        )
        assert validation_job is not None
        assert validation_job.queue_class == "validation"
        assert validation_job.origin_queue_class == "validation"

        full_round2 = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": "round2-full-0001",
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["all"],
                "normalized_result": {"stable": True},
            },
        )
        assert full_round2.status_code == 409
        assert (
            client.post(
                f"/api/loops/{loop_id}/attempts/4/result",
                json={
                    "idempotency_key": "round4-invalid",
                    "strategy_bundle_id": bundle.id,
                    "kind": "arbitration",
                    "target_dimensions": ["lighting"],
                    "normalized_result": {"stable": True},
                },
            ).status_code
            == 409
        )

        unsafe = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": "unsafe-loop-0001",
                "input_evidence": {"raw_payload": {"secret": "redacted"}},
            },
        )
        assert unsafe.status_code == 400
        assert "redacted" not in unsafe.text
        before_credential_rejections = len(
            db.scalars(select(LoopRun)).all()
        )
        idempotency_secret = "Bearer loop-idempotency-secret"
        unsafe_idempotency = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": idempotency_secret,
            },
        )
        assert unsafe_idempotency.status_code == 400
        assert idempotency_secret not in unsafe_idempotency.text

        positive_business_language = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": "business-language-0001",
                "input_evidence": {
                    "business_key": "catalog-entry",
                    "keyword": "tokenization quality",
                    "business_path": "business_key/foo",
                    "keyword_path": "keyword/path",
                    "ordinary_url": (
                        "https://example.test/v1/catalog/items/foo"
                    ),
                    "note": (
                        "业务自然语言可讨论 business_key 和 keyword，"
                        "也可以讨论 API key 的字段设计。"
                        "该供应商使用 Basic authentication 模式，"
                        "无任何真实凭据；Bearer token 机制也仅为业务描述。"
                    ),
                },
            },
        )
        assert positive_business_language.status_code == 200
        assert (
            positive_business_language.json()["attempts"][0][
                "input_evidence"
            ]["note"]
            == (
                "业务自然语言可讨论 business_key 和 keyword，"
                "也可以讨论 API key 的字段设计。"
                "该供应商使用 Basic authentication 模式，"
                "无任何真实凭据；Bearer token 机制也仅为业务描述。"
            )
        )

        for malicious_key in (
            "authorization_header",
            "client_secret_value",
            "x_auth_token_value",
            "provider_payload_backup",
        ):
            secret_value = f"{malicious_key}-secret-value"
            rejected = client.post(
                "/api/loops",
                json={
                    **create_payload,
                    "idempotency_key": (
                        f"unsafe-key-{malicious_key}"
                    ),
                    "input_evidence": {
                        malicious_key: secret_value
                    },
                },
            )
            assert rejected.status_code == 400
            assert malicious_key not in rejected.text
            assert secret_value not in rejected.text

        def loop_database_text() -> str:
            rows = [
                *db.scalars(select(LoopRun)).all(),
                *db.scalars(select(LoopAttempt)).all(),
            ]
            return json.dumps(
                [
                    {
                        key: value
                        for key, value in vars(row).items()
                        if not key.startswith("_")
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                default=str,
            )

        alias_attacks = (
            ("authorization_header=", "loop-auth-header-sentinel"),
            ("client_secret=", "loop-client-secret-sentinel"),
            ("client-secret:", "loop-client-dash-sentinel"),
            (
                "AUTHORIZATION_HEADER/",
                "loop-slash-auth-underscore-sentinel",
            ),
            (
                "Authorization-Header/",
                "loop-slash-auth-dash-sentinel",
            ),
            ("CLIENT_SECRET/", "loop-slash-client-underscore-sentinel"),
            ("Client-Secret/", "loop-slash-client-dash-sentinel"),
            ("X_AUTH_TOKEN/", "loop-slash-xauth-underscore-sentinel"),
            ("X-Auth-Token/", "loop-slash-xauth-dash-sentinel"),
            ("API_KEY/", "loop-slash-api-underscore-sentinel"),
            ("Api-Key/", "loop-slash-api-dash-sentinel"),
            ("COOKIE/", "loop-slash-cookie-sentinel"),
        )
        for alias, sentinel in alias_attacks:
            malicious_value = f"{alias}{sentinel}"
            create_rejected = client.post(
                "/api/loops",
                json={
                    **create_payload,
                    "idempotency_key": malicious_value,
                },
            )
            evidence_rejected = client.post(
                "/api/loops",
                json={
                    **create_payload,
                    "idempotency_key": (
                        f"unsafe-note-{sentinel}"
                    ),
                    "input_evidence": {
                        "note": malicious_value,
                    },
                },
            )
            strategy_reference_rejected = client.post(
                "/api/loops",
                json={
                    **create_payload,
                    "idempotency_key": (
                        f"unsafe-strategy-{sentinel}"
                    ),
                    "model_id": malicious_value,
                },
            )
            attach_rejected = client.post(
                f"/api/loops/{loop_id}/attempts/2/result",
                json={
                    "idempotency_key": malicious_value,
                    "strategy_bundle_id": bundle.id,
                    "kind": "targeted_recheck",
                    "target_dimensions": ["lighting"],
                    "normalized_result": _valid_result(
                        3, "safe evidence"
                    ),
                },
            )
            attach_field_rejected = client.post(
                f"/api/loops/{loop_id}/attempts/2/result",
                json={
                    "idempotency_key": (
                        f"unsafe-attach-field-{sentinel}"
                    ),
                    "strategy_bundle_id": bundle.id,
                    "kind": "targeted_recheck",
                    "target_dimensions": ["lighting"],
                    "normalized_result": _valid_result(
                        3, malicious_value
                    ),
                },
            )
            for response in (
                create_rejected,
                evidence_rejected,
                strategy_reference_rejected,
                attach_rejected,
                attach_field_rejected,
            ):
                assert response.status_code == 400
                assert sentinel not in response.text
                assert malicious_value not in response.text
            assert sentinel not in loop_database_text()

        long_create_sentinel = "long-create-loop-sentinel"
        long_create_key = (
            "authorization_header="
            + ("x" * 180)
            + long_create_sentinel
        )
        long_attach_sentinel = "long-attach-loop-sentinel"
        long_attach_key = (
            "client_secret="
            + ("y" * 180)
            + long_attach_sentinel
        )
        long_create = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": long_create_key,
            },
        )
        long_attach = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": long_attach_key,
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": _valid_result(
                    3, "safe evidence"
                ),
            },
        )
        ordinary_long_key = "ordinary-" + ("z" * 200)
        ordinary_long = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": ordinary_long_key,
            },
        )
        ordinary_long_attach = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": ordinary_long_key,
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": _valid_result(
                    3, "safe evidence"
                ),
            },
        )
        for response, forbidden in (
            (long_create, long_create_sentinel),
            (long_attach, long_attach_sentinel),
            (ordinary_long, ordinary_long_key),
            (ordinary_long_attach, ordinary_long_key),
        ):
            assert response.status_code == 400
            assert forbidden not in response.text
        database_text = loop_database_text()
        assert long_create_sentinel not in database_text
        assert long_attach_sentinel not in database_text
        assert ordinary_long_key not in database_text

        illegal_type_sentinel = "illegal-key-type-sentinel"
        illegal_type_key = {
            "authorization_header": illegal_type_sentinel
        }
        illegal_create = client.post(
            "/api/loops",
            json={
                **create_payload,
                "idempotency_key": illegal_type_key,
            },
        )
        illegal_attach = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": illegal_type_key,
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": _valid_result(
                    3, "safe evidence"
                ),
            },
        )
        for response in (illegal_create, illegal_attach):
            assert response.status_code == 400
            assert illegal_type_sentinel not in response.text
        assert illegal_type_sentinel not in loop_database_text()

        boundary_attacks = [
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "unsafe-source-credential",
                        "source": "Bearer abcdefghijklmnopq",
                    },
                ),
                "abcdefghijklmnopq",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "long-model-id-attack",
                        "model_id": (
                            ("m" * 201) + "longmodelidsentinel"
                        ),
                    },
                ),
                "longmodelidsentinel",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "long-prompt-a-attack",
                        "prompt_a_version": (
                            ("a" * 41) + "longpromptasentinel"
                        ),
                    },
                ),
                "longpromptasentinel",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "long-prompt-b-attack",
                        "prompt_b_version": (
                            ("b" * 41) + "longpromptbsentinel"
                        ),
                    },
                ),
                "longpromptbsentinel",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "object-source-attack",
                        "source": {
                            "note": "objectsourcesentinel",
                        },
                    },
                ),
                "objectsourcesentinel",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "array-model-attack",
                        "model_id": ["arraymodelsentinel"],
                    },
                ),
                "arraymodelsentinel",
            ),
            (
                client.post(
                    "/api/loops",
                    json={
                        **create_payload,
                        "idempotency_key": "object-asset-attack",
                        "asset_id": {
                            "note": "objectassetsentinel",
                        },
                    },
                ),
                "objectassetsentinel",
            ),
            (
                client.post(
                    f"/api/loops/{loop_id}/attempts/2/result",
                    json={
                        "idempotency_key": "unsafe-kind-credential",
                        "strategy_bundle_id": bundle.id,
                        "kind": "Bearer qrstuvwxyzabcdef",
                        "target_dimensions": ["lighting"],
                        "normalized_result": _valid_result(
                            3, "safe evidence"
                        ),
                    },
                ),
                "qrstuvwxyzabcdef",
            ),
            (
                client.post(
                    f"/api/loops/{loop_id}/attempts/2/result",
                    json={
                        "idempotency_key": "object-kind-attack",
                        "strategy_bundle_id": bundle.id,
                        "kind": {
                            "note": "objectkindsentinel",
                        },
                        "target_dimensions": ["lighting"],
                        "normalized_result": _valid_result(
                            3, "safe evidence"
                        ),
                    },
                ),
                "objectkindsentinel",
            ),
            (
                client.post(
                    f"/api/loops/{loop_id}/attempts/2/result",
                    json={
                        "idempotency_key": "array-strategy-attack",
                        "strategy_bundle_id": [
                            "arraystrategysentinel"
                        ],
                        "kind": "targeted_recheck",
                        "target_dimensions": ["lighting"],
                        "normalized_result": _valid_result(
                            3, "safe evidence"
                        ),
                    },
                ),
                "arraystrategysentinel",
            ),
        ]
        for response, sentinel in boundary_attacks:
            assert response.status_code == 400
            assert sentinel not in response.text
            assert sentinel not in loop_database_text()

        assert len(db.scalars(select(LoopRun)).all()) == (
            before_credential_rejections
            + 1
        )
        unsafe_result = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": "unsafe-result-0001",
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": {
                    "raw_response": {"token": "provider-secret-value"}
                },
            },
        )
        assert unsafe_result.status_code == 400
        assert "provider-secret-value" not in unsafe_result.text
        attach_idempotency_secret = (
            "Bearer attach-idempotency-secret"
        )
        unsafe_attach_idempotency = client.post(
            f"/api/loops/{loop_id}/attempts/2/result",
            json={
                "idempotency_key": attach_idempotency_secret,
                "strategy_bundle_id": bundle.id,
                "kind": "targeted_recheck",
                "target_dimensions": ["lighting"],
                "normalized_result": _valid_result(
                    3, "safe evidence"
                ),
            },
        )
        assert unsafe_attach_idempotency.status_code == 400
        assert (
            attach_idempotency_secret
            not in unsafe_attach_idempotency.text
        )
        assert "redacted" not in json.dumps(
            client.get(f"/api/loops/{loop_id}").json()
        )
        db.commit()
        raw_database = database_path.read_bytes()
        for _, sentinel in boundary_attacks:
            assert sentinel.encode() not in raw_database
        for alias, sentinel in alias_attacks:
            if "/" in alias:
                assert sentinel.encode() not in raw_database
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_targeted_worker_completion_auto_advances_same_business_round(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="worker-loop.jpg",
        stored_name="worker-loop.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="a" * 64,
    )
    business_auth_description = (
        "该供应商使用 Basic authentication 模式，无任何真实凭据；"
        "Bearer token 机制也仅为业务描述。"
        "Bearer token mechanism and Bearer authentication mode "
        "are documentation terms."
    )
    model_config = ModelConfig(
        name="Worker Model",
        provider="doubao",
        base_url="https://example.test/v1",
        api_path="/chat/completions",
        model_id="frozen-model",
        encrypted_api_key="credential-not-read",
    )
    prompt_a = PromptVersion(
        stage="A",
        name="A",
        version="worker-A-v1",
        system_prompt=business_auth_description,
        user_prompt="keyword/path and business_key/foo",
        rubric_version="R-v1",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="B",
        version="worker-B-v1",
        system_prompt="frozen B",
        user_prompt="unused",
        rubric_version="R-v1",
    )
    db.add_all([asset, model_config, prompt_a, prompt_b])
    db.flush()
    bundle = get_or_create_bundle(
        db,
        model_config=model_config,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version="R-v1",
        engine_version="E-v1",
        risk_review_version=None,
        sampling_policy=None,
    )
    snapshot = json.loads(
        build_strategy_snapshot(
            bundle,
            prompt_a,
            prompt_b,
            sampling_policy=None,
        )
    )
    assert snapshot["prompt_a"]["system_prompt"] == business_auth_description
    assert snapshot["prompt_a"]["user_prompt"] == prompt_a.user_prompt
    db.flush()
    loop_run = LoopRun(
        idempotency_key="worker-loop-idempotency",
        request_fingerprint="c" * 64,
        asset_id=asset.id,
        strategy_bundle_id=bundle.id,
        status="waiting_result",
        current_round=2,
        decision_json="{}",
    )
    first = LoopAttempt(
        business_round=1,
        kind="base",
        target_dimensions_json="[]",
        input_evidence_json="{}",
        normalized_result_json=json.dumps(
            {
                **_valid_result(3, "first observation"),
                "problem_dimensions": ["lighting"],
            }
        ),
        status="completed",
        result_idempotency_key="worker-round1",
        result_fingerprint="d" * 64,
    )
    second = LoopAttempt(
        business_round=2,
        kind="targeted_recheck",
        target_dimensions_json='["lighting"]',
        input_evidence_json="{}",
        status="waiting_result",
    )
    loop_run.attempts.extend([first, second])
    db.add(loop_run)
    db.flush()
    job = EvaluationJob(
        asset_id=asset.id,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        strategy_bundle_id=bundle.id,
        loop_attempt_id=second.id,
        queue_class="interactive",
        origin_queue_class="interactive",
        technical_attempt=0,
        status="processing",
    )
    db.add(job)
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    captured: dict[str, str] = {}

    class FakeClient:
        async def chat_json(
            self,
            system_prompt,
            user_prompt,
            **_kwargs,
        ):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return DoubaoResponse(
                parsed={
                    "dimension_values": {"lighting": 3},
                    "evidence": {
                        "lighting": "independent second observation"
                    },
                    "confidence_by_dimension": {"lighting": 0.95},
                },
                raw_text="{}",
                raw_payload={},
            )

    image_path = tmp_path / "worker-loop.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        asyncio.run(
            worker._evaluate_targeted_loop_job(
                job_id=job.id,
                job=job,
                attempt=second,
                client=FakeClient(),
                prompt_a=prompt_a,
                image_path=image_path,
                asset=asset,
                metadata={"mime_type": "image/jpeg"},
            )
        )
        db.expire_all()
        completed_job = db.get(EvaluationJob, job.id)
        completed_attempt = db.get(LoopAttempt, second.id)
        completed_loop = db.get(LoopRun, loop_run.id)
        assert completed_job.status == "completed"
        assert completed_attempt.status == "completed"
        assert completed_attempt.business_round == 2
        assert completed_attempt.technical_attempt == 0
        assert json.loads(completed_attempt.conflict_json) == []
        assert completed_loop.status == "machine_converged"
        assert completed_loop.current_round == 2
        assert captured["system"] == business_auth_description
        assert captured["system"] == snapshot["prompt_a"]["system_prompt"]
        assert '"lighting"' in captured["user"]
    finally:
        db.close()
        engine.dispose()


def test_worker_rejects_frozen_bundle_prompt_drift(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="drift.jpg",
        stored_name="drift.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="9" * 64,
    )
    model = ModelConfig(
        model_id="frozen-model",
        encrypted_api_key="credential-not-read",
    )
    prompt_a = PromptVersion(
        stage="A",
        name="A",
        version="frozen-A-v1",
        system_prompt="original A",
        user_prompt="original A user",
        rubric_version="R-v1",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="B",
        version="frozen-B-v1",
        system_prompt="original B",
        user_prompt="original B user",
        rubric_version="R-v1",
    )
    db.add_all([asset, model, prompt_a, prompt_b])
    db.flush()
    bundle = get_or_create_bundle(
        db,
        model_config=model,
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        rubric_version="R-v1",
        engine_version=ENGINE_VERSION,
        risk_review_version=None,
        sampling_policy=None,
    )
    job = EvaluationJob(
        asset_id=asset.id,
        prompt_a_id=prompt_a.id,
        prompt_b_id=prompt_b.id,
        strategy_bundle_id=bundle.id,
        status="processing",
    )
    db.add(job)
    db.commit()
    prompt_a.system_prompt = "mutated A"
    db.commit()

    @contextmanager
    def test_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(worker, "session_scope", test_scope)
    try:
        with pytest.raises(ValueError, match="StrategyBundle"):
            asyncio.run(worker.evaluate_job(job.id))
    finally:
        db.close()
        engine.dispose()
