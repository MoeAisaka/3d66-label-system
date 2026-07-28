from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    CanaryRun,
    EvaluationResult,
    User,
)
from app.p0e_candidate_package import CANDIDATE_PACKAGE_VERSION
from app.p0e_image_freeze import MANIFEST_VERSION
from app.p0e_safe_import import PREFLIGHT_SCHEMA_VERSION


_BATCH_KEY = "p0e:" + "a" * 64


@dataclass
class ApiContext:
    client: TestClient
    sessions: sessionmaker[Session]
    user: User


@contextmanager
def _api_context(*, authenticated: bool = True) -> Iterator[ApiContext]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    with sessions() as db:
        user = User(
            username="canary-tester",
            password_hash="unused",
            display_name="金丝雀测试员",
        )
        db.add(user)
        db.commit()

    def test_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    if authenticated:
        app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        yield ApiContext(client=client, sessions=sessions, user=user)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create(
    client: TestClient,
    *,
    seed: str = "e3-seed",
    display_name: str | None = "P0-E 金丝雀",
) -> dict[str, Any]:
    response = client.post(
        "/api/canary-runs",
        json={
            "domain": "3D",
            "target_size": 40,
            "seed": seed,
            "display_name": display_name,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _preflight(
    batch_key: str = _BATCH_KEY,
) -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "batch_key": batch_key,
        "mode": "preflight_only",
        "writes_business_database": False,
    }


def _approval(
    *,
    approved_by: str = "operator-01",
) -> dict[str, Any]:
    return {
        "human_approved": True,
        "approved_by": approved_by,
        "batch_key": _BATCH_KEY,
        "applied_mappings": [],
    }


def _fetch_config() -> dict[str, Any]:
    return {
        "allowed_hosts": [
            "images.example.test",
            "cdn.example.test",
        ],
        "pinned_https_attested": True,
    }


def _manifest() -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "complete": True,
        "expected_source_count": 40,
        "frozen_source_count": 40,
        "errors": [],
        "assets": [],
    }


def _human_review_evidence() -> dict[str, Any]:
    return {
        "candidate_preview": {
            "schema_version": CANDIDATE_PACKAGE_VERSION,
            "complete_for_requested_preview": True,
            "selected_count": 40,
            "forms_gold": False,
            "downloads_performed": False,
            "model_runs_performed": False,
        },
        "human_review_handoff": {
            "all_items_require_review": True,
            "no_truth_or_gold_granted": True,
            "item_count": 40,
        },
    }


def _transition(
    client: TestClient,
    run: dict[str, Any],
    transition: str,
    evidence: dict[str, Any],
    *,
    expected_fingerprint: str | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    response = client.post(
        (
            f"/api/canary-runs/{run['run_id']}"
            f"/transitions/{transition}"
        ),
        json={
            "expected_snapshot_fingerprint": (
                expected_fingerprint
                or run["snapshot_fingerprint"]
            ),
            "evidence": evidence,
        },
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def _advance_to_human_review(
    client: TestClient,
    run: dict[str, Any],
) -> dict[str, Any]:
    for transition, evidence in (
        ("preflight_ready", _preflight()),
        ("approvals_ready", _approval()),
        ("freeze_ready", _fetch_config()),
        ("candidate_ready", _manifest()),
        ("human_review_ready", _human_review_evidence()),
    ):
        run = _transition(client, run, transition, evidence)
    return run


def _assert_response_has_no_unsafe_url(node: Any) -> None:
    if isinstance(node, str):
        parts = urlsplit(node)
        if parts.scheme.casefold() in {"http", "https"}:
            assert parts.username is None
            assert parts.password is None
            assert not parts.query
            assert not parts.fragment
        return
    if isinstance(node, dict):
        for value in node.values():
            _assert_response_has_no_unsafe_url(value)
    elif isinstance(node, list):
        for value in node:
            _assert_response_has_no_unsafe_url(value)


def test_migration_17_upgrades_an_old_database(tmp_path: Path) -> None:
    database_path = tmp_path / "v16.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for version in range(1, 17):
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, f"historical-{version}"),
            )
        run_migrations(connection)

        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(canary_runs)"
            )
        }
        assert columns == {
            "run_id",
            "display_name",
            "current_state",
            "plan_json",
            "evidence_json",
            "snapshot_json",
            "snapshot_fingerprint",
            "created_by",
            "created_at",
            "updated_at",
        }
        applied = connection.exec_driver_sql(
            "SELECT name FROM schema_migrations WHERE version = 17"
        ).scalar_one()
        assert applied == "add_canary_run_persistence"
    engine.dispose()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/canary-runs",
            {"domain": "3D", "target_size": 40, "seed": "s"},
        ),
        ("get", "/api/canary-runs", None),
        ("get", "/api/canary-runs/canary:missing", None),
        (
            "post",
            "/api/canary-runs/canary:missing/"
            "transitions/preflight_ready",
            {
                "expected_snapshot_fingerprint": "a" * 64,
                "evidence": _preflight(),
            },
        ),
        (
            "post",
            "/api/canary-runs/canary:missing/cancel",
            {
                "expected_snapshot_fingerprint": "a" * 64,
                "reason": "stop",
            },
        ),
        (
            "post",
            "/api/canary-runs/canary:missing/fail",
            {
                "expected_snapshot_fingerprint": "a" * 64,
                "reason": "stop",
            },
        ),
    ],
)
def test_all_canary_apis_require_authentication(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    with _api_context(authenticated=False) as context:
        response = context.client.request(
            method,
            path,
            json=payload,
        )
        assert response.status_code == 401


def test_create_is_idempotent_strict_and_detects_drift() -> None:
    with _api_context() as context:
        first = _create(context.client)
        second = _create(context.client)
        assert second["run_id"] == first["run_id"]
        assert second["snapshot_fingerprint"] == first[
            "snapshot_fingerprint"
        ]
        assert second["created_by"] == context.user.username

        drift = context.client.post(
            "/api/canary-runs",
            json={
                "domain": "3D",
                "target_size": 40,
                "seed": "e3-seed",
                "display_name": "另一个名称",
            },
        )
        assert drift.status_code == 409
        assert drift.json()["detail"]["code"] == (
            "CANARY_RUN_IDEMPOTENCY_DRIFT"
        )

        for invalid_domain in ("3d", "3D ", "2D", ""):
            response = context.client.post(
                "/api/canary-runs",
                json={
                    "domain": invalid_domain,
                    "target_size": 40,
                    "seed": "strict-domain",
                },
            )
            assert response.status_code == 422

        forbidden = context.client.post(
            "/api/canary-runs",
            json={
                "domain": "3D",
                "target_size": 40,
                "seed": "forbidden",
                "state": "human_review_ready",
                "forms_gold": True,
            },
        )
        assert forbidden.status_code == 422

        with context.sessions() as db:
            count = db.scalar(
                select(func.count()).select_from(CanaryRun)
            )
            assert count == 1


def test_complete_sequence_persists_canonical_snapshots_and_no_side_effects() -> None:
    with _api_context() as context:
        with context.sessions() as db:
            asset_count_before = db.scalar(
                select(func.count()).select_from(Asset)
            )
            result_count_before = db.scalar(
                select(func.count()).select_from(EvaluationResult)
            )

        run = _create(context.client)
        states = ["draft"]
        for transition, evidence in (
            ("preflight_ready", _preflight()),
            ("approvals_ready", _approval()),
            ("freeze_ready", _fetch_config()),
            ("candidate_ready", _manifest()),
            ("human_review_ready", _human_review_evidence()),
        ):
            run = _transition(
                context.client,
                run,
                transition,
                evidence,
            )
            states.append(run["state"])
            assert all(
                run[field] is False
                for field in (
                    "writes_business_database",
                    "downloads_performed",
                    "model_runs_performed",
                    "forms_gold",
                    "publishes_release",
                )
            )
            _assert_response_has_no_unsafe_url(run)

        assert states == [
            "draft",
            "preflight_ready",
            "approvals_ready",
            "freeze_ready",
            "candidate_ready",
            "human_review_ready",
        ]
        assert set(run["evidence"]) == {
            "xlsx_preflight",
            "approval",
            "fetch_config",
            "manifest",
            "candidate_preview",
            "human_review_handoff",
        }
        assert run["created_at"]
        assert run["updated_at"]

        detail = context.client.get(
            f"/api/canary-runs/{run['run_id']}"
        )
        assert detail.status_code == 200
        assert detail.json() == run
        listed = context.client.get("/api/canary-runs")
        assert listed.status_code == 200
        assert [item["run_id"] for item in listed.json()["items"]] == [
            run["run_id"]
        ]

        with context.sessions() as db:
            record = db.get(CanaryRun, run["run_id"])
            assert record is not None
            for value in (
                record.plan_json,
                record.evidence_json,
                record.snapshot_json,
            ):
                assert value == json.dumps(
                    json.loads(value),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            assert db.scalar(
                select(func.count()).select_from(Asset)
            ) == asset_count_before
            assert db.scalar(
                select(func.count()).select_from(EvaluationResult)
            ) == result_count_before


def test_skips_backtracks_and_terminal_transitions_are_fail_closed() -> None:
    with _api_context() as context:
        run = _create(context.client)
        skipped = _transition(
            context.client,
            run,
            "approvals_ready",
            _approval(),
            expected_status=422,
        )
        assert skipped["detail"]["code"] == "TRANSITION_GATE_SKIPPED"

        run = _transition(
            context.client,
            run,
            "preflight_ready",
            _preflight(),
        )
        run = _transition(
            context.client,
            run,
            "approvals_ready",
            _approval(),
        )
        backward = _transition(
            context.client,
            run,
            "preflight_ready",
            _preflight(),
            expected_status=422,
        )
        assert backward["detail"]["code"] in {
            "TRANSITION_BACKWARD_NOT_ALLOWED",
            "TRANSITION_GATE_SKIPPED",
        }

        terminal = _advance_to_human_review(
            context.client,
            _create(context.client, seed="terminal"),
        )
        rejected = _transition(
            context.client,
            terminal,
            "preflight_ready",
            _preflight(),
            expected_status=422,
        )
        assert rejected["detail"]["code"] == (
            "TRANSITION_FROM_TERMINAL_STATE"
        )


def test_missing_gate_evidence_preserves_machine_readable_issue() -> None:
    with _api_context() as context:
        run = _create(context.client)
        rejected = _transition(
            context.client,
            run,
            "preflight_ready",
            {
                "schema_version": PREFLIGHT_SCHEMA_VERSION,
            },
            expected_status=422,
        )
        issue = rejected["detail"]
        assert issue["code"] == "PREFLIGHT_BATCH_KEY_MISSING"
        assert set(issue) == {
            "code",
            "message",
            "current_state",
            "attempted_transition",
            "retryable",
        }

        invalid_shape = context.client.post(
            (
                f"/api/canary-runs/{run['run_id']}"
                "/transitions/preflight_ready"
            ),
            json={
                "expected_snapshot_fingerprint": run[
                    "snapshot_fingerprint"
                ],
                "evidence": _preflight(),
                "snapshot_fingerprint": "b" * 64,
                "publishes_release": True,
            },
        )
        assert invalid_shape.status_code == 422


@pytest.mark.parametrize(
    ("unsafe_url", "expected_code"),
    [
        (
            "https://images.example.test/a.png?token=secret",
            "EVIDENCE_URL_CONTAINS_QUERY",
        ),
        (
            "https://images.example.test/a.png#fragment",
            "EVIDENCE_URL_CONTAINS_FRAGMENT",
        ),
        (
            "https://user:pass@images.example.test/a.png",
            "EVIDENCE_URL_CONTAINS_USERINFO",
        ),
    ],
)
def test_unsafe_evidence_urls_are_rejected_and_never_returned(
    unsafe_url: str,
    expected_code: str,
) -> None:
    with _api_context() as context:
        run = _create(
            context.client,
            seed=f"url-{expected_code}",
        )
        rejected = _transition(
            context.client,
            run,
            "preflight_ready",
            {
                **_preflight(),
                "source_url": unsafe_url,
            },
            expected_status=422,
        )
        assert rejected["detail"]["code"] == expected_code
        detail = context.client.get(
            f"/api/canary-runs/{run['run_id']}"
        ).json()
        assert detail["state"] == "draft"
        assert unsafe_url not in json.dumps(
            detail,
            ensure_ascii=False,
        )
        _assert_response_has_no_unsafe_url(detail)


def test_optimistic_lock_and_transition_idempotency() -> None:
    with _api_context() as context:
        run = _create(context.client)
        original_fingerprint = run["snapshot_fingerprint"]
        advanced = _transition(
            context.client,
            run,
            "preflight_ready",
            _preflight(),
        )

        replay = _transition(
            context.client,
            advanced,
            "preflight_ready",
            _preflight(),
            expected_fingerprint=original_fingerprint,
        )
        assert replay["snapshot_fingerprint"] == advanced[
            "snapshot_fingerprint"
        ]
        assert replay["updated_at"] == advanced["updated_at"]

        conflict = _transition(
            context.client,
            advanced,
            "preflight_ready",
            _preflight("p0e:" + "b" * 64),
            expected_status=409,
        )
        assert conflict["detail"]["code"] == (
            "CANARY_RUN_EVIDENCE_CONFLICT"
        )

        stale = _transition(
            context.client,
            advanced,
            "approvals_ready",
            _approval(),
            expected_fingerprint=original_fingerprint,
            expected_status=409,
        )
        assert stale["detail"]["code"] == "CANARY_RUN_SNAPSHOT_STALE"
        detail = context.client.get(
            f"/api/canary-runs/{run['run_id']}"
        ).json()
        assert detail["state"] == "preflight_ready"


def test_cancel_and_fail_are_persisted_terminal_and_idempotent() -> None:
    with _api_context() as context:
        cancelled_source = _create(
            context.client,
            seed="cancel-seed",
        )
        cancel = context.client.post(
            (
                f"/api/canary-runs/{cancelled_source['run_id']}"
                "/cancel"
            ),
            json={
                "expected_snapshot_fingerprint": cancelled_source[
                    "snapshot_fingerprint"
                ],
                "reason": "人工取消",
            },
        )
        assert cancel.status_code == 200
        cancelled = cancel.json()
        assert cancelled["state"] == "cancelled"
        assert cancelled["evidence"]["cancellation"] == {
            "reason": "人工取消"
        }

        replay = context.client.post(
            (
                f"/api/canary-runs/{cancelled_source['run_id']}"
                "/cancel"
            ),
            json={
                "expected_snapshot_fingerprint": cancelled_source[
                    "snapshot_fingerprint"
                ],
                "reason": "人工取消",
            },
        )
        assert replay.status_code == 200
        assert replay.json()["snapshot_fingerprint"] == cancelled[
            "snapshot_fingerprint"
        ]

        different_reason = context.client.post(
            (
                f"/api/canary-runs/{cancelled_source['run_id']}"
                "/cancel"
            ),
            json={
                "expected_snapshot_fingerprint": cancelled[
                    "snapshot_fingerprint"
                ],
                "reason": "另一个原因",
            },
        )
        assert different_reason.status_code == 409

        from_terminal = _transition(
            context.client,
            cancelled,
            "preflight_ready",
            _preflight(),
            expected_status=422,
        )
        assert from_terminal["detail"]["code"] == (
            "TRANSITION_FROM_TERMINAL_STATE"
        )

        failed_source = _transition(
            context.client,
            _create(context.client, seed="fail-seed"),
            "preflight_ready",
            _preflight(),
        )
        failed_response = context.client.post(
            f"/api/canary-runs/{failed_source['run_id']}/fail",
            json={
                "expected_snapshot_fingerprint": failed_source[
                    "snapshot_fingerprint"
                ],
                "reason": "门禁失败",
            },
        )
        assert failed_response.status_code == 200
        failed = failed_response.json()
        assert failed["state"] == "failed"
        assert failed["evidence"]["failure"] == {
            "reason": "门禁失败"
        }
        for response in (cancelled, failed):
            assert response["writes_business_database"] is False
            assert response["downloads_performed"] is False
            assert response["model_runs_performed"] is False
            assert response["forms_gold"] is False
            assert response["publishes_release"] is False


def test_not_found_is_explicit() -> None:
    with _api_context() as context:
        response = context.client.get(
            "/api/canary-runs/canary:does-not-exist"
        )
        assert response.status_code == 404
        assert response.json()["detail"] == {
            "code": "CANARY_RUN_NOT_FOUND",
            "message": "CanaryRun 不存在。",
        }
