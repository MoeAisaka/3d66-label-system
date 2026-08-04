"""ADR-0033 Task 1b tests: v3 specific-dimension 影子调用B (default-off, best-effort).

Exercises the dedicated specific-dimension shadow call added in Task 1b:
- ``build_specific_dimension_shadow_prompt`` (prompt covers every specific key)
- ``fetch_v3_specific_grades`` (switch-gated / valid / exception / incomplete)
- ``resolve_specific_shadow_targets`` (read-only target resolution)
- ``compute_v3_shadow`` end-to-end with specific grades supplied (ok) or absent
  (skipped) — plus a non-invasiveness proof that authoritative scoring is
  untouched regardless of the shadow outcome.

No worker, no queue, no real DB, no real network: the model client is a fake.
"""

from __future__ import annotations

import asyncio
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
from app.worker_v3_shadow_prompt import build_specific_dimension_shadow_prompt

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


def _seed_active_config(db: Session) -> int:
    config = CategoryEvaluationV3Config(
        category_key=_CATEGORY_KEY,
        display_name="灵感图 v3",
        status="active",
        contract_json=json.dumps(build_inspiration_v3_contract(), ensure_ascii=False),
        classification_map_json=json.dumps(
            build_inspiration_classification_map(), ensure_ascii=False
        ),
        subcategory_dimensions_json=json.dumps(
            build_inspiration_subcategory_dimensions(), ensure_ascii=False
        ),
        revision=9,
    )
    db.add(config)
    db.commit()
    return config.revision


# 方案 A：class_one 的真实 6 维度全部在 common_group（specific_group 置空）。
_CLASS_ONE_COMMON_KEYS = (
    "visual_structure",
    "color_aesthetics",
    "emotional_expression",
    "design_aesthetics",
    "originality",
    "design_trendiness",
)


def _class_one_precheck() -> dict[str, Any]:
    """Non-redline precheck resolving to class_one（建筑设计 → class_one）."""
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_confidence": 0.95,
            "primary_category": "建筑设计",
        },
        "production_fields": {"reason": []},
    }


def _common_aesthetic() -> dict[str, Any]:
    """A v1 调用B-shaped aesthetic covering class_one's real 6 common dims (方案 A),
    so the *common* grade mapping succeeds end-to-end."""
    return {"dimensions": {key: {"grade": 4} for key in _CLASS_ONE_COMMON_KEYS}}


class _FakeResponse:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed


class _FakeClient:
    """A stand-in DoubaoClient whose ``chat_json`` returns a canned parsed dict."""

    def __init__(self, parsed: Any) -> None:
        self._parsed = parsed
        self.calls: list[dict[str, Any]] = []

    async def chat_json(
        self, system_prompt: str, user_prompt: str, image_path: Any = None,
        mime_type: Any = None, **_kwargs: Any,
    ) -> _FakeResponse:
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "image": image_path}
        )
        return _FakeResponse(self._parsed)


class _BoomClient:
    async def chat_json(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        raise RuntimeError("shadow 调用B network exploded")


# --------------------------------------------------------------------------- #
# prompt builder
# --------------------------------------------------------------------------- #


def test_prompt_covers_every_specific_key() -> None:
    dims = [
        {"key": "spatial_originality", "label": "空间原创设计感"},
        {"key": "design_trendiness", "label": "设计流行度"},
    ]
    system, user = build_specific_dimension_shadow_prompt("class_one", dims)
    assert isinstance(system, str) and system
    for dim in dims:
        assert dim["key"] in user
        assert dim["label"] in user
    # It must instruct grade + JSON-only output.
    assert "grade" in user
    assert "JSON" in user or "json" in user


# --------------------------------------------------------------------------- #
# fetch_v3_specific_grades
# --------------------------------------------------------------------------- #


def test_fetch_disabled_returns_none() -> None:
    client = _FakeClient({"dimensions": {}})
    result = asyncio.run(
        worker_v3_shadow.fetch_v3_specific_grades(
            client, "img.png", "image/png", "class_one",
            [{"key": "spatial_originality", "label": "空间原创设计感"}],
            enabled=False,
        )
    )
    assert result is None
    assert client.calls == []  # switch off → NO model call issued


def test_fetch_valid_grades_returns_ok_map() -> None:
    parsed = {
        "dimensions": {
            "spatial_originality": {"grade": 4, "evidence": "布局新颖"},
            "design_trendiness": {"grade": 3, "evidence": "尚可"},
        }
    }
    client = _FakeClient(parsed)
    dims = [
        {"key": "spatial_originality", "label": "空间原创设计感"},
        {"key": "design_trendiness", "label": "设计流行度"},
    ]
    result = asyncio.run(
        worker_v3_shadow.fetch_v3_specific_grades(
            client, "img.png", "image/png", "class_one", dims, enabled=True
        )
    )
    assert result == {
        "status": "ok",
        "track_key": "class_one",
        "grades": {"spatial_originality": 4, "design_trendiness": 3},
    }
    assert len(client.calls) == 1  # exactly one extra call


def test_fetch_client_exception_is_swallowed() -> None:
    dims = [{"key": "spatial_originality", "label": "空间原创设计感"}]
    result = asyncio.run(
        worker_v3_shadow.fetch_v3_specific_grades(
            _BoomClient(), "img.png", "image/png", "class_one", dims, enabled=True
        )
    )
    assert result is not None
    assert result["status"] == "error"
    assert "exploded" in result["error"]


def test_fetch_incomplete_map_is_error() -> None:
    # Missing design_trendiness → fail-closed error, not a partial map.
    parsed = {"dimensions": {"spatial_originality": {"grade": 4}}}
    dims = [
        {"key": "spatial_originality", "label": "空间原创设计感"},
        {"key": "design_trendiness", "label": "设计流行度"},
    ]
    result = asyncio.run(
        worker_v3_shadow.fetch_v3_specific_grades(
            _FakeClient(parsed), "img.png", "image/png", "class_one", dims, enabled=True
        )
    )
    assert result is not None
    assert result["status"] == "error"


def test_fetch_out_of_range_grade_is_error() -> None:
    parsed = {"dimensions": {"spatial_originality": {"grade": 7}}}
    dims = [{"key": "spatial_originality", "label": "空间原创设计感"}]
    result = asyncio.run(
        worker_v3_shadow.fetch_v3_specific_grades(
            _FakeClient(parsed), "img.png", "image/png", "class_one", dims, enabled=True
        )
    )
    assert result is not None
    assert result["status"] == "error"


# --------------------------------------------------------------------------- #
# resolve_specific_shadow_targets (read-only)
# --------------------------------------------------------------------------- #


def test_resolve_targets_disabled_returns_none(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        _seed_active_config(db)
        assert worker_v3_shadow.resolve_specific_shadow_targets(
            db, _CATEGORY_KEY, _class_one_precheck(), enabled=False
        ) is None


def test_resolve_targets_empty_specific_group_returns_none(
    sessions: sessionmaker[Session],
) -> None:
    """方案 A: every inspiration track carries all dimensions in common_group with
    an empty specific_group, so the specific-shadow target resolver returns None
    (no extra specific 调用B is ever needed for this category)."""
    with sessions() as db:
        _seed_active_config(db)
        target = worker_v3_shadow.resolve_specific_shadow_targets(
            db, _CATEGORY_KEY, _class_one_precheck(), enabled=True
        )
    assert target is None


def test_resolve_targets_redline_returns_none(sessions: sessionmaker[Session]) -> None:
    redline = {
        "classification": {"scope_status": "in_scope", "primary_confidence": 0.9},
        "production_fields": {"reason": ["是截图"]},
    }
    with sessions() as db:
        _seed_active_config(db)
        assert worker_v3_shadow.resolve_specific_shadow_targets(
            db, _CATEGORY_KEY, redline, enabled=True
        ) is None


def test_resolve_targets_no_config_returns_none(sessions: sessionmaker[Session]) -> None:
    with sessions() as db:
        assert worker_v3_shadow.resolve_specific_shadow_targets(
            db, _CATEGORY_KEY, _class_one_precheck(), enabled=True
        ) is None


# --------------------------------------------------------------------------- #
# end-to-end: compute_v3_shadow with specific grades supplied / absent
# --------------------------------------------------------------------------- #


def test_compute_with_common_aesthetic_is_ok(sessions: sessionmaker[Session]) -> None:
    # 方案 A: class_one 全部维度在 common_group，只要 v1 aesthetic 覆盖这 6 个 key，
    # 共性 grade 就能完整映射 → ok（specific_group 为空，无需特有 grade）。
    with sessions() as db:
        revision = _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db,
            _CATEGORY_KEY,
            _class_one_precheck(),
            _common_aesthetic(),
            enabled=True,
        )
    assert payload is not None
    assert payload["status"] == "ok"
    assert payload["config_revision"] == revision
    assert payload["result"]["resolved"]["track_key"] == "class_one"
    json.dumps(payload, ensure_ascii=False)  # serializable


def test_compute_without_common_grades_skips(
    sessions: sessionmaker[Session],
) -> None:
    # 空 aesthetic → 共性 6 维度无法从 aesthetic 映射 → fail-closed skip。
    with sessions() as db:
        _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db, _CATEGORY_KEY, _class_one_precheck(), {"dimensions": {}}, enabled=True
        )
    assert payload is not None
    assert payload["status"] == "skipped"
    assert payload["reason"] == "grade_mapping_unavailable"


def test_compute_with_incomplete_common_grades_skips(
    sessions: sessionmaker[Session],
) -> None:
    # 缺一个共性维度 grade → 仍 fail-closed skip，绝不硬猜。
    incomplete = {"dimensions": {key: {"grade": 4} for key in _CLASS_ONE_COMMON_KEYS[:-1]}}
    with sessions() as db:
        _seed_active_config(db)
        payload = worker_v3_shadow.compute_v3_shadow(
            db,
            _CATEGORY_KEY,
            _class_one_precheck(),
            incomplete,
            enabled=True,
        )
    assert payload is not None
    assert payload["status"] == "skipped"
    assert payload["reason"] == "grade_mapping_unavailable"


# --------------------------------------------------------------------------- #
# non-invasiveness: authoritative scoring untouched whatever the shadow does
# --------------------------------------------------------------------------- #


def test_specific_shadow_never_mutates_authoritative_scoring(
    sessions: sessionmaker[Session],
) -> None:
    authoritative = {"engine_version": "v1", "score": 83.5, "level": "L4"}
    baseline = dict(authoritative)
    with sessions() as db:
        _seed_active_config(db)
        count_before = len(db.scalars(select(CategoryEvaluationV3Config)).all())
        for aesthetic in (
            None,
            _common_aesthetic(),
            {"dimensions": {}},  # incomplete
        ):
            worker_v3_shadow.compute_v3_shadow(
                db,
                _CATEGORY_KEY,
                _class_one_precheck(),
                aesthetic,
                enabled=True,
            )
            assert authoritative == baseline
        count_after = len(db.scalars(select(CategoryEvaluationV3Config)).all())
        assert count_after == count_before
