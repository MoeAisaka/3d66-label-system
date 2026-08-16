from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.field_demand_contracts import create_field_demand_contract
from app.main import app, current_user
from app.models import (
    LabelRelease,
    PublishedLabel,
    ShadowProjectionLease,
    ShadowProjectionRun,
    User,
)
from app.projection_contracts import create_contract_version
from app.shadow_projection import (
    FixtureShadowProjectionAdapter,
    ShadowProjectionError,
    ShadowSafetyEvidence,
    SqlShadowProjectionAdapter,
    build_shadow_manifest,
    create_shadow_projection_target,
    enqueue_shadow_projection_run,
    retry_shadow_projection_run,
    rollback_shadow_projection_run,
    shadow_projection_worker_tick,
)


def _context() -> tuple[object, Session, object, object, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    field_contract = create_field_demand_contract(
        db,
        contract_key="3d-search",
        category_key="model_3d_su",
        consumer_key="search",
        owner="tpeng-3d",
        fields=[
            {"field_key": "style", "source_path": "semantic.style", "required": True, "data_type": "string"},
            {"field_key": "quality", "source_path": "quality.score", "required": True, "data_type": "number"},
        ],
        thresholds={"accuracy": 0.9, "recall": 0.9},
        status="active",
        created_by="admin",
    )
    target = create_shadow_projection_target(
        db,
        target_key="3d-shadow-unified",
        adapter_key="fixture-shadow",
        connection_locator="target-registry:3d-shadow",
        secret_reference="secret-ref:3d-shadow-writer",
        schema_name="labellab_shadow",
        table_name="asset_dimension_shadow",
        environment="shadow",
        shadow_only=True,
        owner="tpeng-3d",
        schema_fingerprint="e" * 64,
        status="active",
        created_by="admin",
    )
    projection_contract = create_contract_version(
        db,
        contract_key="3d-shadow-unified",
        target_role="unified_dimension",
        table_name="asset_dimension_shadow",
        environment="shadow",
        primary_key=["content_key"],
        field_mappings={
            "content_key": "content_key",
            "category_key": "category_key",
            "style": "semantic.style",
            "quality_score": "quality.score",
            "label_version": "$label.version",
            "asset_version": "provenance.asset_sha256",
            "mechanism_version": "provenance.strategy_bundle_id",
            "model_version": "provenance.model_id",
        },
        input_versions={"label_schema_version": "published-label-v1"},
        mode="snapshot",
        idempotency_key_template="{table_name}:{content_key}:{label_version}",
        checkpoint={"kind": "published_label_id"},
        reconciliation={"checks": ["row_count", "payload_hash", "version"]},
        rollback={"strategy": "batch_delete"},
        owner="tpeng-3d",
        status="active",
        created_by="admin",
        adapter_key="fixture-shadow",
        target_key=target.target_key,
        write_policy="shadow_only",
        category_key="model_3d_su",
        field_contract_id=field_contract.id,
        max_batch_size=500,
    )
    _published(db, content_key="3d:1001", category_key="model_3d_su", style="modern")
    _published(db, content_key="space:2001", category_key="space_image", style="other")
    db.commit()
    return engine, db, field_contract, target, projection_contract


def _published(db: Session, *, content_key: str, category_key: str, style: str) -> None:
    release = LabelRelease(
        release_key=f"release:{content_key}",
        content_key=content_key,
        category_key=category_key,
        label_schema_version="published-label-v1",
        label_payload_json="{}",
        payload_hash="a" * 64,
        status="published",
        requested_by="admin",
        approved_by="admin",
        approved_at=datetime.now(timezone.utc),
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    payload = {
        "content_key": content_key,
        "category_key": category_key,
        "semantic": {"style": style},
        "quality": {"score": 90},
        "provenance": {
            "asset_sha256": "b" * 64,
            "strategy_bundle_id": 7,
            "model_id": "3d-quality-model-v1",
        },
        "candidate_mechanism": {"must_not_project": True},
        "raw_response": "must-not-project",
    }
    db.add(
        PublishedLabel(
            release_id=release.id,
            content_key=content_key,
            category_key=category_key,
            version=1,
            label_schema_version="published-label-v1",
            label_payload_json=json.dumps(payload),
            payload_hash="c" * 64,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
    )


def test_shadow_run_rejects_more_than_500_rows() -> None:
    engine, db, field_contract, target, contract = _context()
    try:
        run = enqueue_shadow_projection_run(
            db,
            projection_contract=contract,
            field_contract=field_contract,
            target=target,
            max_rows=501,
            actor="admin",
        )
        assert run.status == "blocked"
        assert run.error_code == "CANARY_LIMIT_EXCEEDED"
    finally:
        db.close()
        engine.dispose()


def test_shadow_manifest_contains_only_published_matching_category() -> None:
    engine, db, field_contract, target, contract = _context()
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        manifest = build_shadow_manifest(db, run=run)
        assert manifest.row_count == 1
        assert manifest.rows[0]["category_key"] == "model_3d_su"
        serialized = json.dumps(manifest.rows)
        assert "space:2001" not in serialized
        assert "candidate_mechanism" not in serialized
        assert "raw_response" not in serialized
    finally:
        db.close()
        engine.dispose()


def test_shadow_manifest_rejects_non_positive_mechanism_version() -> None:
    engine, db, field_contract, target, contract = _context()
    try:
        label = db.scalar(
            select(PublishedLabel).where(PublishedLabel.category_key == "model_3d_su")
        )
        payload = json.loads(label.label_payload_json)
        payload["provenance"]["strategy_bundle_id"] = 0
        label.label_payload_json = json.dumps(payload)
        run = enqueue_shadow_projection_run(
            db,
            projection_contract=contract,
            field_contract=field_contract,
            target=target,
            max_rows=10,
            actor="admin",
        )

        with pytest.raises(ShadowProjectionError, match="机制"):
            build_shadow_manifest(db, run=run)
    finally:
        db.close()
        engine.dispose()


def test_shadow_target_must_be_shadow_only_and_least_privileged() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(
        shadow_only=True,
        least_privileged=False,
        schema_fingerprint=target.schema_fingerprint,
    )
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter)
        assert run.status == "blocked"
        assert run.error_code == "SHADOW_PERMISSION_OVERBROAD"
        assert adapter.rows == []
    finally:
        db.close()
        engine.dispose()


def test_crash_after_apply_retries_without_duplicate_rows() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(
        shadow_only=True,
        least_privileged=True,
        schema_fingerprint=target.schema_fingerprint,
        transient_after_apply=1,
    )
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter)
        assert run.status == "queued"
        assert adapter.rows and len(adapter.rows) == 1
        run.retry_after = None
        shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter)
        assert run.status == "succeeded"
        assert len(adapter.rows) == 1
        assert json.loads(run.checkpoint_json)["reconciled"] is True
    finally:
        db.close()
        engine.dispose()


def test_unexpected_adapter_failure_rolls_back_worker_state_and_recovers() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(
        shadow_only=True,
        least_privileged=True,
        schema_fingerprint=target.schema_fingerprint,
    )

    class UnexpectedOnceAdapter:
        def __init__(self) -> None:
            self.should_fail = True

        def verify_shadow_target(self) -> ShadowSafetyEvidence:
            return adapter.verify_shadow_target()

        def apply_batch(
            self, *, batch_id: str, rows: list[dict[str, object]]
        ) -> None:
            adapter.apply_batch(batch_id=batch_id, rows=rows)
            if self.should_fail:
                self.should_fail = False
                raise RuntimeError("unexpected adapter failure after apply")

        def read_back(self, *, batch_id: str) -> list[dict[str, object]]:
            return adapter.read_back(batch_id=batch_id)

        def rollback_batch(self, *, batch_id: str) -> int:
            return adapter.rollback_batch(batch_id=batch_id)

    unexpected_adapter = UnexpectedOnceAdapter()
    try:
        run = enqueue_shadow_projection_run(
            db,
            projection_contract=contract,
            field_contract=field_contract,
            target=target,
            max_rows=10,
            actor="admin",
        )
        db.commit()
        run_id = run.id
        db.close()

        with Session(engine, expire_on_commit=False) as failing_db:
            with pytest.raises(RuntimeError, match="unexpected adapter failure"):
                shadow_projection_worker_tick(
                    failing_db,
                    "worker-a",
                    adapter_resolver=lambda _target: unexpected_adapter,
                )
            failing_db.rollback()

        assert len(adapter.rows) == 1
        with Session(engine, expire_on_commit=False) as check_db:
            stored = check_db.get(ShadowProjectionRun, run_id)
            assert stored.status == "queued"
            assert check_db.scalar(select(func.count(ShadowProjectionLease.id))) == 0

        with Session(engine, expire_on_commit=False) as retry_db:
            recovered = shadow_projection_worker_tick(
                retry_db,
                "worker-a",
                adapter_resolver=lambda _target: unexpected_adapter,
            )
            retry_db.commit()
            assert recovered.status == "succeeded"
        assert len(adapter.rows) == 1
    finally:
        if db.is_active:
            db.close()
        engine.dispose()


def test_target_lease_allows_only_one_writer() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(shadow_only=True, least_privileged=True, schema_fingerprint=target.schema_fingerprint)
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        db.add(ShadowProjectionLease(target_id=target.id, worker_id="worker-other", expires_at=datetime.now(timezone.utc) + timedelta(minutes=1)))
        db.flush()
        assert shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter) is None
        assert run.status == "queued"
        assert adapter.rows == []
    finally:
        db.close()
        engine.dispose()


def test_three_transient_failures_open_target_circuit() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(
        shadow_only=True,
        least_privileged=True,
        schema_fingerprint=target.schema_fingerprint,
        transient_before_apply=3,
    )
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        for _ in range(3):
            run.retry_after = None
            shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter)
        assert run.status == "blocked"
        assert run.error_code == "PROJECTION_CIRCUIT_OPEN"
        assert target.circuit_opened_at is not None
    finally:
        db.close()
        engine.dispose()


def test_hash_drift_blocks_checkpoint_and_batch_can_be_rolled_back() -> None:
    engine, db, field_contract, target, contract = _context()
    adapter = FixtureShadowProjectionAdapter(
        shadow_only=True,
        least_privileged=True,
        schema_fingerprint=target.schema_fingerprint,
        corrupt_readback=True,
    )
    try:
        run = enqueue_shadow_projection_run(db, projection_contract=contract, field_contract=field_contract, target=target, max_rows=10, actor="admin")
        shadow_projection_worker_tick(db, "worker-a", adapter_resolver=lambda _target: adapter)
        assert run.status == "blocked"
        assert run.error_code == "PROJECTION_HASH_DRIFT"
        assert json.loads(run.checkpoint_json).get("reconciled") is not True
        rollback_shadow_projection_run(db, run=run, adapter=adapter, actor="admin")
        assert run.status == "rolled_back"
        assert adapter.rows == []
        assert db.scalar(select(func.count(ShadowProjectionRun.id))) == 1
    finally:
        db.close()
        engine.dispose()


def test_sql_shadow_adapter_upserts_idempotently_and_rolls_back_batch() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE asset_dimension_shadow (
                batch_id TEXT NOT NULL,
                content_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY (batch_id, content_key)
            )
        """)
    adapter = SqlShadowProjectionAdapter(
        connection_factory=engine.begin,
        table_name="asset_dimension_shadow",
        evidence=ShadowSafetyEvidence(
            shadow_only=True,
            least_privileged=True,
            schema_fingerprint="e" * 64,
        ),
    )
    try:
        adapter.apply_batch(
            batch_id="shadow-batch-1",
            rows=[{"content_key": "3d:1001", "quality_score": 90}],
        )
        adapter.apply_batch(
            batch_id="shadow-batch-1",
            rows=[{"content_key": "3d:1001", "quality_score": 91}],
        )

        assert adapter.read_back(batch_id="shadow-batch-1") == [
            {"content_key": "3d:1001", "quality_score": 91}
        ]
        assert adapter.rollback_batch(batch_id="shadow-batch-1") == 1
        assert adapter.read_back(batch_id="shadow-batch-1") == []
    finally:
        engine.dispose()


@contextmanager
def _api_context() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    user = User(
        username="shadow-admin",
        password_hash="unused",
        display_name="影子投影管理员",
        role="admin",
        is_admin=True,
    )

    def override_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _target_payload() -> dict[str, object]:
    return {
        "target_key": "3d-shadow-unified",
        "adapter_key": "sql-shadow",
        "connection_locator": "target-registry:3d-shadow",
        "secret_reference": "secret-ref:3d-shadow-writer",
        "schema_name": "labellab_shadow",
        "table_name": "asset_dimension_shadow",
        "environment": "shadow",
        "shadow_only": True,
        "owner": "tpeng-3d",
        "schema_fingerprint": "e" * 64,
        "status": "active",
    }


def _shadow_contract_payload(*, field_contract_id: int) -> dict[str, object]:
    return {
        "contract_key": "3d-shadow-unified",
        "target_role": "unified_dimension",
        "table_name": "asset_dimension_shadow",
        "environment": "shadow",
        "adapter_key": "sql-shadow",
        "target_key": "3d-shadow-unified",
        "write_policy": "shadow_only",
        "category_key": "model_3d_su",
        "field_contract_id": field_contract_id,
        "max_batch_size": 500,
        "primary_key": ["content_key"],
        "field_mappings": {
            "content_key": "content_key",
            "category_key": "category_key",
            "style": "semantic.style",
            "quality_score": "quality.score",
            "label_version": "$label.version",
            "asset_version": "provenance.asset_sha256",
            "mechanism_version": "provenance.strategy_bundle_id",
            "model_version": "provenance.model_id",
        },
        "input_versions": {"label_schema_version": "published-label-v1"},
        "mode": "snapshot",
        "idempotency_key_template": "{table_name}:{content_key}:{label_version}",
        "checkpoint": {"kind": "published_label_id"},
        "reconciliation": {"checks": ["row_count", "payload_hash", "version"]},
        "rollback": {"strategy": "batch_delete"},
        "owner": "tpeng-3d",
        "status": "active",
    }


def test_shadow_projection_admin_apis_redact_secret_and_enqueue_run() -> None:
    with _api_context() as (client, sessions):
        with sessions() as db:
            field_contract = create_field_demand_contract(
                db,
                contract_key="3d-search",
                category_key="model_3d_su",
                consumer_key="search",
                owner="tpeng-3d",
                fields=[
                    {"field_key": "style", "source_path": "semantic.style", "required": True, "data_type": "string"},
                    {"field_key": "quality", "source_path": "quality.score", "required": True, "data_type": "number"},
                ],
                thresholds={"accuracy": 0.9, "recall": 0.9},
                status="active",
                created_by="admin",
            )
            db.commit()
            field_contract_id = field_contract.id

        target_response = client.post(
            "/api/shadow-projection-targets", json=_target_payload()
        )
        assert target_response.status_code == 200, target_response.text
        target = target_response.json()
        assert target["connection_locator"] == "target-registry:3d-shadow"
        assert target["secret_status"] == "unresolved"
        assert "secret_reference" not in target
        assert "3d-shadow-writer" not in target_response.text

        contract_response = client.post(
            "/api/projection-contracts",
            json=_shadow_contract_payload(field_contract_id=field_contract_id),
        )
        assert contract_response.status_code == 200, contract_response.text
        contract = contract_response.json()
        assert contract["environment"] == "shadow"
        assert contract["write_policy"] == "shadow_only"

        run_response = client.post(
            "/api/shadow-projection-runs",
            json={
                "projection_contract_id": contract["id"],
                "field_contract_id": field_contract_id,
                "target_id": target["id"],
                "max_rows": 500,
            },
        )
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()
        assert run["status"] == "queued"
        assert run["max_rows"] == 500
        assert run["target"]["secret_status"] == "unresolved"
        assert "secret_reference" not in run_response.text

        listed = client.get("/api/shadow-projection-runs")
        detail = client.get(f"/api/shadow-projection-runs/{run['id']}")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [run["id"]]
        assert detail.status_code == 200
        assert detail.json()["batch_id"] == run["batch_id"]

        with sessions() as db:
            stored_run = db.get(ShadowProjectionRun, run["id"])
            stored_target = stored_run.target
            stored_run.status = "blocked"
            stored_run.error_code = "PROJECTION_CIRCUIT_OPEN"
            stored_target.consecutive_failures = 3
            stored_target.circuit_opened_at = datetime.now(timezone.utc)
            db.commit()

        retried = client.post(
            f"/api/shadow-projection-runs/{run['id']}/retry"
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "queued"
        assert retried.json()["error_code"] == ""
        assert retried.json()["checkpoint"]["resume"]["circuit_reviewed"] is True


def test_shadow_projection_retry_rejects_non_transient_blocker() -> None:
    engine, db, field_contract, target, contract = _context()
    try:
        run = enqueue_shadow_projection_run(
            db,
            projection_contract=contract,
            field_contract=field_contract,
            target=target,
            max_rows=10,
            actor="admin",
        )
        run.status = "blocked"
        run.error_code = "PROJECTION_HASH_DRIFT"
        with pytest.raises(ShadowProjectionError, match="不是可重试"):
            retry_shadow_projection_run(db, run=run, actor="admin")
        assert run.status == "blocked"
        assert run.error_code == "PROJECTION_HASH_DRIFT"
    finally:
        db.close()
        engine.dispose()


def test_worker_loop_isolates_shadow_projection_tick_failure(monkeypatch) -> None:
    from app import worker

    calls = {"shadow": 0, "continue": 0}

    @contextmanager
    def fake_session_scope() -> Iterator[object]:
        yield object()

    async def fake_process_one() -> bool:
        return False

    def fail_shadow_tick(*_args: object, **_kwargs: object) -> None:
        calls["shadow"] += 1
        raise RuntimeError("shadow tick failed")

    def should_continue() -> bool:
        calls["continue"] += 1
        return calls["continue"] == 1

    monkeypatch.setattr(worker, "init_database", lambda: None)
    monkeypatch.setattr(worker, "session_scope", fake_session_scope)
    monkeypatch.setattr(worker, "seed_defaults", lambda _db: None)
    monkeypatch.setattr(worker, "touch_automation_worker_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "process_one", fake_process_one)
    monkeypatch.setattr(worker, "optimization_worker_tick", lambda _worker_id: {"status": "idle"})
    monkeypatch.setattr(worker, "shadow_projection_worker_tick", fail_shadow_tick, raising=False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    worker.run_forever(poll_seconds=0, should_continue=should_continue)

    assert calls["shadow"] == 1
