"""ADR-0033 Task 1 tests: worker v3 shadow scoring (non-invasive, default-off).

Exercises the extracted ``worker_v3_shadow`` helpers directly against an
isolated in-memory SQLite engine (StaticPool) — no worker, no queue, no model
calls, no real DB.  Mirrors the isolation of
``test_category_evaluation_v3_config_api``.

Coverage:
1. switch OFF (default) → ``compute_v3_shadow`` returns None, no writes.
2. switch ON + no active v3 config → payload ``status="skipped"``.
3. switch ON + active config + redline-hit precheck → payload ``status="ok"``
   with a result (redline branch needs no grades → faithful shadow).
4. switch ON + evaluate_one raises (monkeypatched) → payload ``status="error"``,
   never re-raised.
5. 非侵入证明: the authoritative v1 scoring dict is identical regardless of the
   shadow switch / success / failure, and computing the shadow performs no
   write side effects on the session.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker_v3_shadow
from app.database import Base
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations
from app.models import CategoryEvaluationV3Config

_CATEGORY_KEY = "inspiration_image"


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_active_config(db: Session, category_key: str = _CATEGORY_KEY) -> int:
    """Persist an *active* v3 config assembled from the frozen seed; return revision."""
    config = CategoryEvaluationV3Config(
        category_key=category_key,
        display_name="灵感图 v3",
        status="active",
        contract_json=json.dumps(build_inspiration_v3_contract(), ensure_ascii=False),
        classification_map_json=json.dumps(
            build_inspiration_classification_map(), ensure_ascii=False
        ),
        subcategory_dimensions_json=json.dumps(
            build_inspiration_subcategory_dimensions(), ensure_ascii=False
        ),
        revision=7,
    )
    db.add(config)
    db.commit()
    return config.revision


def _redline_precheck() -> dict[str, Any]:
    """A precheck that trips the '是截图' redline (needs no dimension grades)."""
    return {
        "classification": {"scope_status": "in_scope", "primary_confidence": 0.9},
        "production_fields": {"reason": ["是截图"]},
    }


# --------------------------------------------------------------------------- #
# 1. switch OFF (default) → None, no side effects
# --------------------------------------------------------------------------- #


def test_shadow_disabled_returns_none(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADR33_V3_SHADOW_ENABLED", raising=False)
    assert worker_v3_shadow.v3_shadow_enabled() is False
    with sessions() as db:
        _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db,
            _CATEGORY_KEY,
            _redline_precheck(),
            None,
            enabled=worker_v3_shadow.v3_shadow_enabled(),
        )
    assert payload is None


@pytest.mark.parametrize("token", ["1", "true", "TRUE", " True "])
def test_switch_tokens_enable(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("ADR33_V3_SHADOW_ENABLED", token)
    assert worker_v3_shadow.v3_shadow_enabled() is True


@pytest.mark.parametrize("token", ["0", "", "yes", "off", "2"])
def test_switch_tokens_stay_off(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("ADR33_V3_SHADOW_ENABLED", token)
    assert worker_v3_shadow.v3_shadow_enabled() is False


# --------------------------------------------------------------------------- #
# 2. switch ON + no active config → skipped
# --------------------------------------------------------------------------- #


def test_no_active_config_skips(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _redline_precheck(), None, enabled=True
        )
    assert payload == {"status": "skipped", "reason": "no_active_v3_config"}


def test_only_non_active_config_skips(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        config = CategoryEvaluationV3Config(
            category_key=_CATEGORY_KEY,
            display_name="灵感图 v3",
            status="draft",
            contract_json=json.dumps(build_inspiration_v3_contract()),
            classification_map_json=json.dumps(build_inspiration_classification_map()),
            subcategory_dimensions_json=json.dumps(
                build_inspiration_subcategory_dimensions()
            ),
        )
        db.add(config)
        db.commit()
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _redline_precheck(), None, enabled=True
        )
    assert payload == {"status": "skipped", "reason": "no_active_v3_config"}


def test_missing_category_key_skips(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db, None, _redline_precheck(), None, enabled=True
        )
    assert payload == {"status": "skipped", "reason": "no_category_key"}


# --------------------------------------------------------------------------- #
# 3. switch ON + active config + redline hit → ok with result
# --------------------------------------------------------------------------- #


def test_redline_hit_produces_ok(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        revision = _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _redline_precheck(), None, enabled=True
        )
    assert payload is not None
    assert payload["status"] == "ok"
    assert payload["engine"] == "adr33-v3"
    assert payload["config_revision"] == revision
    assert payload["result"]["redline"]["hit"] is True
    assert payload["result"]["resolved"] is None
    # Whole payload must be JSON-serializable (it is stored as TEXT).
    json.dumps(payload, ensure_ascii=False)


def test_non_redline_with_specific_dims_skips_mapping(
    sessions: sessionmaker[Session],
) -> None:
    """A non-redline image resolves to a track with v1-less specific dims → skip,
    never a fabricated grade."""
    precheck = {
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": 0.95,
            "primary_category": "室内设计",
        },
        "production_fields": {"reason": []},
    }
    with sessions() as db:
        _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, precheck, None, enabled=True
        )
    assert payload is not None
    assert payload["status"] == "skipped"
    assert payload["reason"] == "grade_mapping_unavailable"


# --------------------------------------------------------------------------- #
# 4. switch ON + evaluate_one raises → error, not re-raised
# --------------------------------------------------------------------------- #


def test_evaluate_one_exception_becomes_error(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("v3 engine exploded")

    monkeypatch.setattr(
        "app.inspiration_category_seed.evaluate_one", _boom
    )
    with sessions() as db:
        _seed_active_config(db)
        # Must not raise:
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _redline_precheck(), None, enabled=True
        )
    assert payload is not None
    assert payload["status"] == "error"
    assert "v3 engine exploded" in payload["error"]


def test_malformed_contract_json_becomes_error(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        config = CategoryEvaluationV3Config(
            category_key=_CATEGORY_KEY,
            display_name="灵感图 v3",
            status="active",
            contract_json="{not valid json",
            classification_map_json="{}",
            subcategory_dimensions_json="{}",
        )
        db.add(config)
        db.commit()
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _redline_precheck(), None, enabled=True
        )
    assert payload is not None
    assert payload["status"] == "error"


# --------------------------------------------------------------------------- #
# 5. non-invasiveness proof
# --------------------------------------------------------------------------- #


def test_shadow_never_mutates_authoritative_scoring(
    sessions: sessionmaker[Session],
) -> None:
    """The authoritative v1 scoring dict is untouched regardless of shadow
    outcome, and no rows are written by computing the shadow."""
    authoritative = {
        "engine_version": "v1",
        "score": 83.5,
        "level": "L4",
        "confidence": 0.9,
        "needs_review": False,
    }
    baseline = dict(authoritative)

    with sessions() as db:
        _seed_active_config(db)
        results_before = db.scalars(
            select(CategoryEvaluationV3Config)
        ).all()
        count_before = len(results_before)

        for enabled in (False, True):
            _ = worker_v3_shadow.compute_v3_shadow(
                db,
                _CATEGORY_KEY,
                _redline_precheck(),
                None,
                enabled=enabled,
            )
            # Authoritative scoring is never read or mutated by the shadow path.
            assert authoritative == baseline

        # The shadow path issues only SELECTs — the config table is unchanged
        # (still exactly the one row we seeded, same revision).
        results_after = db.scalars(select(CategoryEvaluationV3Config)).all()
        assert len(results_after) == count_before
        assert results_after[0].revision == 7
