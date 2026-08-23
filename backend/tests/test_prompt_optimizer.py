from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import dimension_deduction_bridge, optimizer
from app.database import Base
from app.doubao import DoubaoClient, DoubaoHTTPError, DoubaoResponse
from app.main import _optimization_payload
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    OptimizerConfig,
    PromptOptimizationRun,
    PromptVersion,
    SampleSet,
    SampleSetItem,
)


AUDIT_FIELDS = {
    "status",
    "attempt_count",
    "upstream_status_code",
    "request_correlation_id",
    "elapsed_ms",
    "error_type",
    "error_message",
    "output_budget",
    "reasoning_effort",
}


def _client_without_secret_lookup(config: OptimizerConfig) -> DoubaoClient:
    client = object.__new__(DoubaoClient)
    client.config = config
    client.api_key = "offline-test-key"
    return client


def test_per_call_attempt_override_does_not_change_default_retry(
    monkeypatch,
) -> None:
    config = OptimizerConfig(
        provider="openai",
        model_id="offline-optimizer-model",
        encrypted_api_key="offline-placeholder",
        max_tokens=12000,
        max_retries=1,
        structured_output=True,
    )
    client = _client_without_secret_lookup(config)
    calls: list[dict] = []

    async def fail_with_502(payload):
        calls.append(payload)
        raise DoubaoHTTPError(
            502,
            {
                "x-request-id": "req-502",
                "set-cookie": "must-not-be-audit-data",
            },
        )

    monkeypatch.setattr(client, "_post", fail_with_502)

    with pytest.raises(DoubaoHTTPError) as overridden:
        asyncio.run(
            client.chat_json(
                "system",
                "user",
                max_attempts=1,
                output_budget=2048,
                reasoning_effort="high",
                structured_output=True,
            )
        )
    assert len(calls) == 1
    assert overridden.value.attempt_count == 1
    assert overridden.value.status_code == 502
    assert overridden.value.request_correlation_id == "req-502"
    assert calls[0]["max_completion_tokens"] == 2048
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["response_format"] == {"type": "json_object"}

    calls.clear()
    with pytest.raises(DoubaoHTTPError) as defaulted:
        asyncio.run(client.chat_json("system", "user"))
    assert len(calls) == 2
    assert defaulted.value.attempt_count == 2


def test_diagnostic_image_call_attempt_override_is_exactly_one(
    tmp_path,
    monkeypatch,
) -> None:
    config = OptimizerConfig(
        provider="openai",
        model_id="offline-optimizer-model",
        encrypted_api_key="offline-placeholder",
        max_tokens=12000,
        max_retries=1,
        structured_output=False,
    )
    client = _client_without_secret_lookup(config)
    image_path = tmp_path / "diagnostic.jpg"
    image_path.write_bytes(b"offline-image-placeholder")
    calls: list[dict] = []

    async def fail_with_502(payload):
        calls.append(payload)
        raise DoubaoHTTPError(502, {"x-request-id": "diagnostic-502"})

    monkeypatch.setattr(client, "_post", fail_with_502)
    with pytest.raises(DoubaoHTTPError) as failure:
        asyncio.run(
            client.chat_json_images(
                "system",
                [("sample", image_path, "image/jpeg")],
                max_attempts=1,
                output_budget=2048,
                reasoning_effort="high",
                structured_output=True,
                max_image_count=1,
                max_single_image_bytes=1024,
                max_total_image_bytes=1024,
            )
        )

    assert len(calls) == 1
    assert failure.value.attempt_count == 1
    assert calls[0]["max_completion_tokens"] == 2048
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_image_limit_is_enforced_at_encoding_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    config = OptimizerConfig(
        provider="openai",
        model_id="offline-optimizer-model",
        encrypted_api_key="offline-placeholder",
    )
    client = _client_without_secret_lookup(config)
    image_path = tmp_path / "mutable.jpg"
    image_path.write_bytes(b"123456")
    calls = 0

    async def must_not_post(_payload):
        nonlocal calls
        calls += 1
        raise AssertionError("oversized image reached HTTP")

    monkeypatch.setattr(client, "_post", must_not_post)
    with pytest.raises(ValueError, match="字节上限"):
        asyncio.run(
            client.chat_json_images(
                "system",
                [("sample", image_path, "image/jpeg")],
                max_attempts=1,
                max_image_count=1,
                max_single_image_bytes=5,
                max_total_image_bytes=5,
            )
        )
    assert calls == 0


def _bounded_item(tmp_path, index, decision, payload=b"image"):
    image_path = tmp_path / f"sample-{index}.jpg"
    image_path.write_bytes(payload)
    role = "target_error" if decision == "corrected" else "stable_control"
    return (
        SimpleNamespace(
            asset_id=index + 1,
            asset=SimpleNamespace(
                stored_name=image_path.name,
                sha256=f"{index:064x}",
            ),
        ),
        {
            "decision": decision,
            "sample_role": role,
            "dimension_schema": {
                "canonical_hash": "a" * 64,
            },
        },
    )


def test_diagnostic_records_are_bounded_and_role_stratified(tmp_path) -> None:
    selected = [
        _bounded_item(tmp_path, index, "corrected")
        for index in range(optimizer.MAX_DIAGNOSTIC_IMAGES + 3)
    ]
    selected.extend(
        _bounded_item(tmp_path, 100 + index, "approved")
        for index in range(2)
    )

    bounded, total_bytes, omitted_count = (
        optimizer._bounded_diagnostic_records(selected, tmp_path)
    )

    assert len(bounded) == optimizer.MAX_DIAGNOSTIC_IMAGES
    assert total_bytes == optimizer.MAX_DIAGNOSTIC_IMAGES * len(b"image")
    assert omitted_count == 5
    decisions = [record["decision"] for _, record, _ in bounded]
    assert "corrected" in decisions
    assert "approved" in decisions


def test_invalid_early_records_are_backfilled_from_full_role_pools(
    tmp_path,
    monkeypatch,
) -> None:
    corrected = [
        _bounded_item(tmp_path, index, "corrected") for index in range(40)
    ]
    controls = [
        _bounded_item(tmp_path, 100 + index, "approved") for index in range(10)
    ]
    records = {
        item.asset_id: record for item, record in corrected + controls
    }
    monkeypatch.setattr(
        optimizer,
        "_review_record",
        lambda item: dict(records[item.asset_id]),
    )

    selected, _holdout_ids, eligible_count = optimizer._select_records(
        [item for item, _ in corrected + controls]
    )
    assert eligible_count == 50
    assert len(selected) == 40
    selected_corrected = [
        pair for pair in selected if pair[1]["decision"] == "corrected"
    ]
    selected_controls = [
        pair for pair in selected if pair[1]["decision"] == "approved"
    ]
    assert len(selected_corrected) == 32
    assert len(selected_controls) == 8
    for item, _ in selected_corrected[:-1] + selected_controls[:-1]:
        (tmp_path / item.asset.stored_name).unlink()

    bounded, _total_bytes, omitted_count = (
        optimizer._bounded_diagnostic_records(selected, tmp_path)
    )

    assert omitted_count == 38
    assert sorted(record["decision"] for _, record, _ in bounded) == [
        "approved",
        "corrected",
    ]


def test_optimizer_rejects_mixed_dimension_semantics(
    tmp_path,
    monkeypatch,
) -> None:
    first = _bounded_item(tmp_path, 0, "corrected")
    second = _bounded_item(tmp_path, 1, "approved")
    records = {
        first[0].asset_id: first[1],
        second[0].asset_id: {
            **second[1],
            "dimension_schema": {"canonical_hash": "b" * 64},
        },
    }
    monkeypatch.setattr(
        optimizer,
        "_review_record",
        lambda item: dict(records[item.asset_id]),
    )

    with pytest.raises(
        ValueError,
        match="不能混用不同 DimensionSchema",
    ):
        optimizer._select_records([first[0], second[0]])


def test_diagnostic_records_respect_aggregate_byte_limit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(optimizer, "MAX_DIAGNOSTIC_IMAGE_BYTES", 10)
    selected = [
        _bounded_item(tmp_path, 0, "corrected", b"123456"),
        _bounded_item(tmp_path, 1, "corrected", b"123456"),
        _bounded_item(tmp_path, 2, "corrected", b"123456"),
    ]

    bounded, total_bytes, omitted_count = (
        optimizer._bounded_diagnostic_records(selected, tmp_path)
    )

    assert len(bounded) == 1
    assert total_bytes == 6
    assert omitted_count == 2


def _create_optimizer_run(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'optimizer.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )

    image_path = tmp_path / "optimizer-sample.jpg"
    image_path.write_bytes(b"offline-image-placeholder")

    with session_factory() as db:
        prompt = PromptVersion(
            stage="B",
            name="base prompt",
            version="B-offline-1",
            system_prompt="base system",
            user_prompt="base user",
            status="published",
        )
        sample_set = SampleSet(name="optimizer samples")
        asset = Asset(
            original_name="sample.jpg",
            stored_name=image_path.name,
            mime_type="image/jpeg",
            size_bytes=image_path.stat().st_size,
            sha256="a" * 64,
        )
        config = OptimizerConfig(
            encrypted_api_key="offline-placeholder",
            max_retries=1,
            structured_output=False,
        )
        db.add_all([prompt, sample_set, asset, config])
        db.flush()

        job = EvaluationJob(asset_id=asset.id, status="completed")
        db.add(job)
        db.flush()
        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            precheck_json="{}",
            aesthetic_json=json.dumps(
                {
                    "dimensions": {
                        "composition_viewpoint": {
                            "score": 3,
                            "evidence": "offline evidence",
                        }
                    }
                }
            ),
            scoring_json="{}",
            raw_response_a="{}",
            raw_response_b="{}",
            score=70,
            level="L3",
            confidence=0.8,
            needs_review=False,
            model_id="offline-evaluation-model",
            prompt_a_version="A-offline-1",
            prompt_b_version=prompt.version,
            rubric_version="rubric-offline-1",
            engine_version="engine-offline-1",
        )
        db.add(result)
        db.flush()
        review = HumanReview(
            evaluation_id=result.id,
            reviewer_name="offline-reviewer",
            decision="corrected",
            corrected_level="L2",
            note="composition was overrated",
            corrections_json=json.dumps(
                [
                    {
                        "target_type": "dimension",
                        "field_key": "composition_viewpoint",
                        "model_value": 3,
                        "human_value": 2,
                    }
                ]
            ),
        )
        item = SampleSetItem(
            sample_set_id=sample_set.id,
            asset_id=asset.id,
            source_result_id=result.id,
        )
        db.add_all([review, item])
        db.flush()
        run = PromptOptimizationRun(
            base_prompt_id=prompt.id,
            sample_set_id=sample_set.id,
            optimizer_model_id=config.model_id,
            created_by="offline-test",
        )
        db.add(run)
        db.commit()
        return engine, session_factory, run.id


def test_review_record_uses_result_bound_dimension_names(tmp_path) -> None:
    engine, session_factory, _run_id = _create_optimizer_run(tmp_path)
    definition = {
        "format_version": "dimension-schema-definition-v1",
        "dimensions": [
            {
                "key": "presentation_integrity",
                "label": "呈现质量",
            },
            {
                "key": "visual_hierarchy",
                "label": "视觉组织",
            },
            {
                "key": "inspiration_reference",
                "label": "参考价值",
            },
        ],
        "output_contract": {
            "dimension_output_keys": [
                "presentation_integrity",
                "visual_hierarchy",
                "inspiration_reference",
            ],
            "unknown_key_policy": "reject",
        },
    }
    try:
        with session_factory() as db:
            item = db.scalar(select(SampleSetItem))
            result = item.source_result
            result.strategy_snapshot_json = json.dumps(
                {
                    "schema_version": "strategy-bundle-v2",
                    "resolved_dimension_schema_id": 71,
                    "resolved_dimension_schema_key": "dimension.test",
                    "resolved_dimension_schema_version": "test-v1",
                    "resolved_dimension_schema_hash": (
                        optimizer.canonical_hash(definition)
                    ),
                    "resolved_dimensions_snapshot": definition,
                },
                ensure_ascii=False,
            )
            result.aesthetic_json = json.dumps(
                {
                    "dimensions": {
                        dimension["key"]: {"grade": 3}
                        for dimension in definition["dimensions"]
                    }
                },
                ensure_ascii=False,
            )
            record = optimizer._review_record(item)

        assert record is not None
        assert record["dimension_names"] == {
            "presentation_integrity": "呈现质量",
            "visual_hierarchy": "视觉组织",
            "inspiration_reference": "参考价值",
        }
        assert record["dimension_schema"] == {
            "schema_id": 71,
            "schema_key": "dimension.test",
            "version": "test-v1",
            "canonical_hash": optimizer.canonical_hash(definition),
        }
    finally:
        engine.dispose()


def _install_optimizer_dependencies(
    monkeypatch,
    tmp_path,
    session_factory,
    fake_client_type,
) -> None:
    monkeypatch.setattr(optimizer, "SessionLocal", session_factory)
    monkeypatch.setattr(
        optimizer,
        "get_settings",
        lambda: SimpleNamespace(upload_dir=tmp_path),
    )
    monkeypatch.setattr(optimizer, "DoubaoClient", fake_client_type)


def test_oversized_diagnostic_image_fails_before_client_call(
    tmp_path,
    monkeypatch,
) -> None:
    engine, session_factory, run_id = _create_optimizer_run(tmp_path)
    image_path = tmp_path / "optimizer-sample.jpg"
    image_path.write_bytes(
        b"x" * (optimizer.MAX_DIAGNOSTIC_IMAGE_BYTES + 1)
    )

    class ClientMustNotRun:
        calls = 0

        def __init__(self, _config):
            pass

        async def chat_json_images(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("oversized input reached the HTTP client")

    _install_optimizer_dependencies(
        monkeypatch,
        tmp_path,
        session_factory,
        ClientMustNotRun,
    )
    try:
        asyncio.run(optimizer.run_prompt_optimization(run_id))
        with session_factory() as db:
            run = db.get(PromptOptimizationRun, run_id)
            audit = json.loads(run.diagnostic_audit_json)
            assert run.status == "failed"
            assert run.candidate_system_prompt == ""
            assert run.candidate_user_prompt == ""
            assert audit["status"] == "failed"
            assert audit["attempt_count"] == 0
            assert audit["error_type"] == "invalid_stage_output"
        assert ClientMustNotRun.calls == 0
    finally:
        engine.dispose()


def test_encoding_boundary_rejection_persists_zero_attempts(
    tmp_path,
    monkeypatch,
) -> None:
    engine, session_factory, run_id = _create_optimizer_run(tmp_path)
    original_bounded = optimizer._bounded_diagnostic_records

    def mutate_after_metadata_check(selected, upload_dir):
        bounded = original_bounded(selected, upload_dir)
        image_path = bounded[0][0][2]
        image_path.write_bytes(
            b"x" * (optimizer.MAX_DIAGNOSTIC_SINGLE_IMAGE_BYTES + 1)
        )
        return bounded

    class EncodingBoundaryClient(DoubaoClient):
        post_calls = 0

        def __init__(self, config):
            self.config = config
            self.api_key = "offline-test-key"

        async def _post(self, _payload):
            type(self).post_calls += 1
            raise AssertionError("encoding-boundary rejection reached HTTP")

    monkeypatch.setattr(
        optimizer,
        "_bounded_diagnostic_records",
        mutate_after_metadata_check,
    )
    _install_optimizer_dependencies(
        monkeypatch,
        tmp_path,
        session_factory,
        EncodingBoundaryClient,
    )
    try:
        asyncio.run(optimizer.run_prompt_optimization(run_id))
        with session_factory() as db:
            run = db.get(PromptOptimizationRun, run_id)
            audit = json.loads(run.diagnostic_audit_json)
            assert run.status == "failed"
            assert audit["status"] == "failed"
            assert audit["attempt_count"] == 0
            assert audit["error_type"] == "invalid_stage_output"
            assert run.candidate_system_prompt == ""
            assert run.candidate_user_prompt == ""
        assert EncodingBoundaryClient.post_calls == 0
    finally:
        engine.dispose()


def test_diagnosis_and_audit_persist_when_synthesis_fails(
    tmp_path,
    monkeypatch,
) -> None:
    engine, session_factory, run_id = _create_optimizer_run(tmp_path)

    class FailingSynthesisClient:
        diagnostic_calls = 0
        synthesis_calls = 0

        def __init__(self, config):
            assert config.max_retries == 1

        async def chat_json_images(self, _system, samples, **options):
            type(self).diagnostic_calls += 1
            assert len(samples) == 1
            assert options == {
                "max_attempts": 1,
                "output_budget": 2048,
                "reasoning_effort": "high",
                "structured_output": True,
                "max_image_count": 8,
                "max_single_image_bytes": 16 * 1024 * 1024,
                "max_total_image_bytes": 32 * 1024 * 1024,
            }
            return DoubaoResponse(
                parsed={
                    "summary": "persisted diagnosis",
                    "cases": [],
                    "patterns": ["overrating"],
                    "prompt_risks": [],
                },
                raw_text='{"summary":"persisted diagnosis"}',
                raw_payload={
                    "body": "bulk upstream response must not enter audit",
                    "headers": {"set-cookie": "secret"},
                },
                upstream_status_code=200,
                request_correlation_id="diag-request-1",
                attempt_count=1,
                output_budget=2048,
                reasoning_effort="high",
            )

        async def chat_json(self, _system, _user, **options):
            type(self).synthesis_calls += 1
            assert options == {
                "max_attempts": 1,
                "output_budget": 4096,
                "reasoning_effort": "high",
                "structured_output": True,
            }
            with session_factory() as verification:
                persisted = verification.get(PromptOptimizationRun, run_id)
                diagnosis = json.loads(persisted.diagnosis_json)
                diagnostic_audit = json.loads(
                    persisted.diagnostic_audit_json
                )
                assert diagnosis["summary"] == "persisted diagnosis"
                assert diagnostic_audit["status"] == "succeeded"
                assert diagnostic_audit["attempt_count"] == 1
            error = DoubaoHTTPError(
                502,
                {
                    "x-request-id": "synthesis-request-502",
                    "x-debug-body": "must-not-be-stored",
                },
            )
            error.attempt_count = 1
            raise error

    _install_optimizer_dependencies(
        monkeypatch,
        tmp_path,
        session_factory,
        FailingSynthesisClient,
    )
    try:
        asyncio.run(optimizer.run_prompt_optimization(run_id))
        with session_factory() as db:
            run = db.get(PromptOptimizationRun, run_id)
            diagnosis = json.loads(run.diagnosis_json)
            diagnostic_audit = json.loads(run.diagnostic_audit_json)
            synthesis_audit = json.loads(run.synthesis_audit_json)
            assert run.status == "failed"
            assert diagnosis["summary"] == "persisted diagnosis"
            assert diagnosis["diagnostic"]["patterns"] == ["overrating"]
            assert run.candidate_system_prompt == ""
            assert run.candidate_user_prompt == ""
            assert diagnostic_audit["status"] == "succeeded"
            assert diagnostic_audit["output_budget"] == 2048
            assert synthesis_audit["status"] == "failed"
            assert synthesis_audit["attempt_count"] == 1
            assert synthesis_audit["upstream_status_code"] == 502
            assert (
                synthesis_audit["request_correlation_id"]
                == "synthesis-request-502"
            )
            assert synthesis_audit["error_type"] == "provider5xx"
            assert set(diagnostic_audit) == AUDIT_FIELDS
            assert set(synthesis_audit) == AUDIT_FIELDS
            assert "headers" not in run.diagnostic_audit_json
            assert "body" not in run.diagnostic_audit_json
            assert db.scalar(select(func.count(PromptVersion.id))) == 1
        assert FailingSynthesisClient.diagnostic_calls == 1
        assert FailingSynthesisClient.synthesis_calls == 1
    finally:
        engine.dispose()


def test_success_stores_candidate_without_creating_prompt_version(
    tmp_path,
    monkeypatch,
) -> None:
    engine, session_factory, run_id = _create_optimizer_run(tmp_path)

    class SuccessfulClient:
        diagnostic_calls = 0
        synthesis_calls = 0

        def __init__(self, _config):
            pass

        async def chat_json_images(self, _system, _samples, **options):
            type(self).diagnostic_calls += 1
            assert options["max_attempts"] == 1
            assert options["output_budget"] == 2048
            assert options["reasoning_effort"] == "high"
            assert options["structured_output"] is True
            assert options["max_image_count"] == 8
            assert options["max_single_image_bytes"] == 16 * 1024 * 1024
            assert options["max_total_image_bytes"] == 32 * 1024 * 1024
            return DoubaoResponse(
                parsed={
                    "summary": "diagnosis",
                    "cases": [],
                    "patterns": [],
                    "prompt_risks": [],
                },
                raw_text="{}",
                raw_payload={},
                upstream_status_code=200,
                request_correlation_id="diag-success",
                attempt_count=1,
                output_budget=2048,
                reasoning_effort="high",
            )

        async def chat_json(self, _system, _user, **options):
            type(self).synthesis_calls += 1
            assert options["max_attempts"] == 1
            assert options["output_budget"] == 4096
            assert options["reasoning_effort"] == "high"
            assert options["structured_output"] is True
            return DoubaoResponse(
                parsed={
                    "summary": "candidate ready",
                    "diagnosis": "minimal change",
                    "prompt_changes": ["tighten composition anchor"],
                    "candidate_system_prompt": "candidate system",
                    "candidate_user_prompt": "candidate user",
                    "change_note": "offline candidate only",
                    "validation_focus": ["composition"],
                },
                raw_text="{}",
                raw_payload={},
                upstream_status_code=200,
                request_correlation_id="synthesis-success",
                attempt_count=1,
                output_budget=4096,
                reasoning_effort="high",
            )

    _install_optimizer_dependencies(
        monkeypatch,
        tmp_path,
        session_factory,
        SuccessfulClient,
    )
    try:
        asyncio.run(optimizer.run_prompt_optimization(run_id))
        with session_factory() as db:
            run = db.get(PromptOptimizationRun, run_id)
            diagnosis = json.loads(run.diagnosis_json)
            diagnostic_audit = json.loads(run.diagnostic_audit_json)
            synthesis_audit = json.loads(run.synthesis_audit_json)
            assert run.status == "completed"
            assert run.candidate_system_prompt == "candidate system"
            assert run.candidate_user_prompt == "candidate user"
            assert run.change_note == "offline candidate only"
            assert diagnosis["diagnostic"]["summary"] == "diagnosis"
            assert diagnosis["prompt_changes"] == [
                "tighten composition anchor"
            ]
            assert diagnosis["sample_policy"]["sample_items"] == [
                {
                    "sample_item_id": run.sample_set.items[0].id,
                    "role": "target_error",
                }
            ]
            assert diagnostic_audit["output_budget"] == 2048
            assert synthesis_audit["output_budget"] == 4096
            assert diagnostic_audit["reasoning_effort"] == "high"
            assert synthesis_audit["reasoning_effort"] == "high"
            assert db.scalar(select(func.count(PromptVersion.id))) == 1
            prompt = db.scalar(select(PromptVersion))
            assert prompt.status == "published"
        assert SuccessfulClient.diagnostic_calls == 1
        assert SuccessfulClient.synthesis_calls == 1
    finally:
        engine.dispose()


def test_optimization_api_payload_allowlists_stage_audit(
    tmp_path,
) -> None:
    engine, session_factory, run_id = _create_optimizer_run(tmp_path)
    try:
        with session_factory() as db:
            run = db.get(PromptOptimizationRun, run_id)
            run.diagnostic_audit_json = json.dumps(
                {
                    "status": "succeeded",
                    "attempt_count": 1,
                    "upstream_status_code": 200,
                    "request_correlation_id": "safe-request-id",
                    "elapsed_ms": 12,
                    "error_type": None,
                    "error_message": None,
                    "output_budget": 2048,
                    "reasoning_effort": "high",
                    "headers": {"authorization": "secret"},
                    "raw_response": {"large": "body"},
                    "metadata": {"anything": "unsafe"},
                }
            )
            run.synthesis_audit_json = json.dumps(
                {
                    "status": "failed",
                    "attempt_count": 1,
                    "upstream_status_code": 502,
                    "request_correlation_id": "synthesis-request-id",
                    "elapsed_ms": 34,
                    "error_type": "provider5xx",
                    "error_message": "模型 API HTTP 502",
                    "output_budget": 4096,
                    "reasoning_effort": "high",
                    "response_body": "must not leave storage",
                }
            )
            payload = _optimization_payload(run)

        assert set(payload["stage_audit"]) == {
            "diagnostic",
            "synthesis",
        }
        for audit in payload["stage_audit"].values():
            assert set(audit) == AUDIT_FIELDS
            assert "headers" not in audit
            assert "raw_response" not in audit
            assert "response_body" not in audit
            assert "metadata" not in audit
    finally:
        engine.dispose()


def test_reject_unrunnable_candidate_prompt_allows_supported_placeholders() -> None:
    """平台支持的占位符必须放行，否则 AI 推荐会被自己的校验堵死。"""
    body = "\n".join(
        f"参考 {placeholder} 打分"
        for placeholder in dimension_deduction_bridge.SUPPORTED_PLACEHOLDERS
    )

    # 不抛异常即通过。
    optimizer.reject_unrunnable_candidate_prompt(body, source="优化候选")
    optimizer.reject_unrunnable_candidate_prompt("没有任何占位符", source="优化候选")
    optimizer.reject_unrunnable_candidate_prompt("", source="优化候选")


def test_reject_unrunnable_candidate_prompt_blocks_unknown_placeholder() -> None:
    """AI 写出引擎不认识的占位符时，必须在生成期就拦下。

    否则候选能建成、能被运营采纳成正式版本，直到回归时每条样本都失败——运营要
    看着 N 条相同的失败才知道是 AI 把提示词写坏了。
    """
    with pytest.raises(ValueError) as excinfo:
        optimizer.reject_unrunnable_candidate_prompt(
            "请结合 {{human_truth}} 与 {{another_bad}} 打分",
            source="优化候选",
        )

    detail = str(excinfo.value)
    # 点名全部不支持的占位符，运营才知道要删哪几个。
    assert "{{human_truth}}" in detail
    assert "{{another_bad}}" in detail
    # 列出可用清单，并说明为什么必须拦。
    for supported in dimension_deduction_bridge.SUPPORTED_PLACEHOLDERS:
        assert supported in detail
    assert "每条样本都会被拒" in detail
    assert "优化候选" in detail


def test_reject_unrunnable_candidate_prompt_labels_its_caller() -> None:
    """两条 AI 推荐路径共用该校验，报错要能分辨是哪一条。"""
    with pytest.raises(ValueError, match="诊断模型生成的候选 User Prompt"):
        optimizer.reject_unrunnable_candidate_prompt(
            "参考 {{nope}}", source="诊断模型生成的候选 User Prompt"
        )
