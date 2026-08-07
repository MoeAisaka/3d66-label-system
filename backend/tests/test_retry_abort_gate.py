from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base
from app.models import Asset, EvaluationControl, EvaluationJob


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, Session(engine, expire_on_commit=False)


def test_abort_notice_blocks_recovery_child_before_creation(tmp_path, monkeypatch) -> None:
    engine, db = _db()
    try:
        asset = Asset(
            original_name="retry-abort.jpg",
            stored_name="retry-abort.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            sha256="a" * 64,
        )
        parent = EvaluationJob(
            asset=asset,
            status="processing",
            queue_class="canary",
            origin_queue_class="canary",
            batch_key="label148",
        )
        db.add_all([parent, EvaluationControl(id=1, paused=False)])
        db.commit()
        notice = tmp_path / "ABORT-NOTICE.txt"
        notice.write_text("stop", encoding="utf-8")
        monkeypatch.setenv("ABORT_NOTICE_PATH", str(notice))

        @contextmanager
        def scope():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

        monkeypatch.setattr(worker, "session_scope", scope)
        assert worker._handle_technical_failure(parent.id, TimeoutError("timeout")) is False
        db.expire_all()
        persisted = db.get(EvaluationJob, parent.id)
        assert persisted is not None
        assert persisted.status == "failed"
        assert persisted.stage == "retry_aborted"
        assert persisted.error_message == "technical:retry_aborted"
        assert db.scalars(select(EvaluationJob)).all() == [persisted]
    finally:
        db.close()
        engine.dispose()


def test_paused_control_blocks_recovery_child_before_creation(tmp_path, monkeypatch) -> None:
    engine, db = _db()
    try:
        asset = Asset(
            original_name="retry-paused.jpg",
            stored_name="retry-paused.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            sha256="b" * 64,
        )
        parent = EvaluationJob(
            asset=asset,
            status="processing",
            queue_class="canary",
            origin_queue_class="canary",
            batch_key="label148",
        )
        db.add_all([parent, EvaluationControl(id=1, paused=True)])
        db.commit()
        monkeypatch.setenv("ABORT_NOTICE_PATH", str(tmp_path / "missing"))

        @contextmanager
        def scope():
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

        monkeypatch.setattr(worker, "session_scope", scope)
        assert worker._handle_technical_failure(parent.id, TimeoutError("timeout")) is False
        db.expire_all()
        persisted = db.get(EvaluationJob, parent.id)
        assert persisted is not None
        assert persisted.status == "failed"
        assert persisted.stage == "retry_aborted"
        assert db.scalars(select(EvaluationJob)).all() == [persisted]
    finally:
        db.close()
        engine.dispose()
