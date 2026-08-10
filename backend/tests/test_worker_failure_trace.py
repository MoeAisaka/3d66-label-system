from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base
from app.doubao import DoubaoResponse
from app.inspiration_aesthetic_foundation import AestheticFoundationError
from app.migrations.runner import MIGRATIONS, run_migrations
from app.models import Asset, EvaluationJob


def test_migration_59_adds_immutable_failure_trace_contract(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'trace-m59.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE evaluation_jobs (id INTEGER PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE schema_migrations ("
                "version INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, "
                "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            for migration in MIGRATIONS:
                if migration.version <= 58:
                    connection.exec_driver_sql(
                        "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                        (migration.version, migration.name),
                    )
            run_migrations(connection)
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(evaluation_jobs)"
                )
            }
            assert {
                "trace_response_a_json",
                "trace_usage_a_json",
                "trace_response_b_json",
                "trace_usage_b_json",
                "failure_stage",
                "failure_code",
            } <= columns
            assert connection.exec_driver_sql(
                "SELECT name FROM schema_migrations WHERE version=59"
            ).scalar_one() == "persist_evaluation_job_failure_traces"
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            assert "trg_job_provider_trace_no_update" in triggers
            connection.exec_driver_sql(
                "INSERT INTO evaluation_jobs(id, trace_response_a_json) "
                "VALUES (1, '{\"id\":\"first\"}')"
            )
            with pytest.raises(DatabaseError, match="provider trace is immutable"):
                connection.exec_driver_sql(
                    "UPDATE evaluation_jobs SET trace_response_a_json="
                    "'{\"id\":\"second\"}' WHERE id=1"
                )
    finally:
        engine.dispose()


def test_failure_path_persists_sanitized_a_b_usage_stage_and_code(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    asset = Asset(
        original_name="trace.jpg",
        stored_name="trace.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        sha256="8" * 64,
    )
    job = EvaluationJob(asset=asset, status="processing", stage="aesthetic")
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

    monkeypatch.setattr(worker, "session_scope", test_scope)
    response_a = DoubaoResponse(
        parsed={"classification": {"scope_status": "in_scope"}},
        raw_text="raw-a",
        raw_payload={
            "id": "resp-a",
            "authorization": "Bearer must-not-persist",
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
        upstream_status_code=200,
        request_correlation_id="request-a",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )
    response_b = DoubaoResponse(
        parsed={"dimensions": {}},
        raw_text="raw-b-invalid",
        raw_payload={
            "id": "resp-b",
            "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
        },
        upstream_status_code=200,
        request_correlation_id="request-b",
        thinking_mode="disabled",
        input_tokens=13,
        output_tokens=5,
        total_tokens=18,
    )
    try:
        worker._persist_provider_trace(job.id, "A", response_a)
        worker._persist_provider_trace(job.id, "B", response_b)
        assert worker._handle_technical_failure(
            job.id,
            AestheticFoundationError(
                "dimension_evidence_invalid", "逐维证据不可为空"
            ),
        ) is False
        db.expire_all()
        failed = db.scalar(select(EvaluationJob).where(EvaluationJob.id == job.id))
        assert failed is not None
        trace_a = json.loads(failed.trace_response_a_json)
        trace_b = json.loads(failed.trace_response_b_json)
        usage_a = json.loads(failed.trace_usage_a_json)
        usage_b = json.loads(failed.trace_usage_b_json)
        assert trace_a["provider_payload"]["id"] == "resp-a"
        assert trace_a["provider_payload"]["authorization"] == "[REDACTED]"
        assert "must-not-persist" not in failed.trace_response_a_json
        assert trace_b["raw_text"] == "raw-b-invalid"
        assert trace_b["thinking_mode"] == "disabled"
        assert usage_a == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
        assert usage_b == {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}
        assert failed.failure_stage == "aesthetic"
        assert failed.failure_code == "dimension_evidence_invalid"
        assert failed.error_message == "technical:non_retryable"
    finally:
        db.close()
        engine.dispose()
