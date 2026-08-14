from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.category_evaluation_v3_config_api import (
    build_category_evaluation_v3_config_router,
)
from app.category_evaluation_contract import canonical_contract_hash
from app.category_evaluation_v3_revisions import (
    RevisionArtifacts,
    activate_candidate_revision,
    create_candidate_revision,
    ensure_projected_revision,
)
from app.database import Base, get_db
from app.dimension_schema_registry import canonical_json
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.migrations import run_migrations
from app.models import CategoryEvaluationV3Config, CategoryEvaluationV3Revision


_BASE = "/api/category-evaluation/v3-config"


def _valid_body(category_key: str = "inspiration_image") -> dict[str, Any]:
    contract = build_inspiration_v3_contract()
    contract["category_key"] = category_key
    return {
        "category_key": category_key,
        "display_name": "灵感图 v3",
        "contract": contract,
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
    }


class _Principal:
    username = "revision-tester"


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


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    app = FastAPI()

    def require_user() -> Any:
        return _Principal()

    def test_db() -> Iterator[Session]:
        with sessions() as db:
            yield db

    app.include_router(build_category_evaluation_v3_config_router(require_user))
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as test_client:
        yield test_client


def _create_runtime(client: TestClient, category_key: str = "inspiration_image") -> dict:
    response = client.post(f"{_BASE}/", json=_valid_body(category_key))
    assert response.status_code == 201, response.text
    return response.json()


def _candidate_payload(
    runtime: dict[str, Any],
    *,
    body: dict[str, Any] | None = None,
    parent_revision_id: int | None = None,
) -> dict[str, Any]:
    payload = deepcopy(body or _valid_body(runtime["category_key"]))
    payload.update(
        {
            "parent_revision_id": (
                runtime["projected_revision_id"]
                if parent_revision_id is None
                else parent_revision_id
            ),
            "expected_projected_revision": runtime["revision"],
            "expected_projected_contract_hash": runtime["contract_hash"],
        }
    )
    return payload


def _stale_projection_pair(
    *,
    category_key: str,
) -> tuple[CategoryEvaluationV3Config, CategoryEvaluationV3Revision]:
    body = _valid_body(category_key)
    contract_json = canonical_json(body["contract"])
    classification_map_json = canonical_json(body["classification_map"])
    subcategory_dimensions_json = canonical_json(body["subcategory_dimensions"])
    shared = {
        "category_key": category_key,
        "status": "active",
        "contract_json": contract_json,
        "classification_map_json": classification_map_json,
        "subcategory_dimensions_json": subcategory_dimensions_json,
        "dimension_deduction_rules_json": "{}",
        "media_penalty_enabled": True,
        "revision": 1,
        "contract_hash": canonical_contract_hash(body["contract"]),
        "created_by": "system:test",
    }
    projected = CategoryEvaluationV3Config(
        display_name="现役运行时合同",
        projected_revision_id=None,
        **shared,
    )
    stale = CategoryEvaluationV3Revision(
        display_name="陈旧不可变合同",
        parent_revision_id=None,
        **shared,
    )
    return projected, stale


def test_ensure_projected_revision_appends_when_same_revision_artifacts_are_stale(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        projected, stale = _stale_projection_pair(
            category_key="stale_same_revision",
        )
        db.add_all([projected, stale])
        db.flush()

        repaired = ensure_projected_revision(db, projected)
        db.commit()

        assert repaired.id != stale.id
        assert repaired.revision == 2
        assert repaired.parent_revision_id == stale.id
        assert repaired.display_name == projected.display_name
        assert repaired.contract_json == projected.contract_json
        assert repaired.classification_map_json == projected.classification_map_json
        assert repaired.subcategory_dimensions_json == projected.subcategory_dimensions_json
        assert repaired.dimension_deduction_rules_json == projected.dimension_deduction_rules_json
        assert repaired.media_penalty_enabled == projected.media_penalty_enabled
        assert repaired.contract_hash == projected.contract_hash
        assert repaired.created_by == projected.created_by
        assert stale.status == "retired"
        assert projected.revision == 2
        assert projected.projected_revision_id == repaired.id


def test_ensure_projected_revision_rejects_mismatched_nonempty_pointer(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        projected, stale = _stale_projection_pair(
            category_key="mismatched_pointer",
        )
        db.add_all([projected, stale])
        db.flush()
        projected.projected_revision_id = stale.id
        db.flush()

        with pytest.raises(RuntimeError, match="冻结产物不一致"):
            ensure_projected_revision(db, projected)

        assert projected.projected_revision_id == stale.id
        assert projected.revision == 1
        assert stale.status == "active"


def test_activate_candidate_copies_frozen_provenance_to_runtime_projection(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as db:
        projected, _stale = _stale_projection_pair(
            category_key="activation_provenance",
        )
        db.add(projected)
        db.flush()
        current = ensure_projected_revision(db, projected)
        body = _valid_body("activation_provenance")
        candidate, created = create_candidate_revision(
            db,
            projected,
            parent_revision_id=current.id,
            artifacts=RevisionArtifacts(
                display_name="候选合同",
                contract=body["contract"],
                classification_map=body["classification_map"],
                subcategory_dimensions=body["subcategory_dimensions"],
            ),
            expected_projected_revision=projected.revision,
            expected_projected_hash=projected.contract_hash,
            actor="candidate:author",
        )
        assert created is True

        activate_candidate_revision(
            db,
            projected,
            candidate,
            actor="release:approver",
        )

        assert projected.created_by == candidate.created_by
        assert ensure_projected_revision(db, projected).id == candidate.id


def test_create_candidate_is_append_only_and_runtime_projection_is_unchanged(
    client: TestClient,
    sessions: sessionmaker[Session],
) -> None:
    runtime = _create_runtime(client)
    body = _valid_body()
    body["display_name"] = "灵感图 v3 候选"
    body["contract"]["common_modifiers"]["media_type_penalty"]["penalties"][
        "ai_image"
    ] = -10

    created = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(runtime, body=body),
    )
    assert created.status_code == 201, created.text
    candidate = created.json()
    assert candidate["status"] == "candidate"
    assert candidate["revision"] == runtime["revision"] + 1
    assert candidate["parent_revision_id"] == runtime["projected_revision_id"]
    assert candidate["display_name"] == "灵感图 v3 候选"
    assert candidate["contract_hash"] != runtime["contract_hash"]
    assert candidate["created_by"] == "revision-tester"

    unchanged = client.get(f"{_BASE}/inspiration_image").json()
    assert unchanged["revision"] == runtime["revision"]
    assert unchanged["contract_hash"] == runtime["contract_hash"]
    assert unchanged["display_name"] == runtime["display_name"]
    assert unchanged["candidate_count"] == 1

    with sessions() as db:
        from app.category_evaluation_v3_revisions import revision_bundle
        from app.models import CategoryEvaluationV3Revision

        projected = db.scalar(
            select(CategoryEvaluationV3Config).where(
                CategoryEvaluationV3Config.category_key == "inspiration_image"
            )
        )
        stored = db.get(CategoryEvaluationV3Revision, candidate["id"])
        assert projected is not None and stored is not None
        assert projected.projected_revision_id == runtime["projected_revision_id"]
        assert revision_bundle(stored) == {
            "category_key": "inspiration_image",
            "revision": 2,
            "contract_hash": candidate["contract_hash"],
            "contract": body["contract"],
            "classification_map": body["classification_map"],
            "subcategory_dimensions": body["subcategory_dimensions"],
            "dimension_deduction_rules": candidate["dimension_deduction_rules"],
            "media_penalty_enabled": candidate["media_penalty_enabled"],
        }


def test_revision_history_detail_and_child_candidate_are_ordered(
    client: TestClient,
) -> None:
    runtime = _create_runtime(client)
    first_body = _valid_body()
    first_body["contract"]["common_modifiers"]["media_type_penalty"]["penalties"][
        "ai_image"
    ] = -10
    first = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(runtime, body=first_body),
    ).json()

    child_body = deepcopy(first_body)
    child_body["display_name"] = "灵感图 v3 候选 2"
    child_body["contract"]["common_modifiers"]["media_type_penalty"]["penalties"][
        "ai_image"
    ] = -8
    child_response = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(
            runtime,
            body=child_body,
            parent_revision_id=first["id"],
        ),
    )
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()
    assert child["parent_revision_id"] == first["id"]
    assert child["revision"] == 3

    history = client.get(f"{_BASE}/inspiration_image/revisions")
    assert history.status_code == 200, history.text
    assert [item["revision"] for item in history.json()["items"]] == [3, 2, 1]
    assert history.json()["projected_revision_id"] == runtime["projected_revision_id"]
    assert history.json()["candidate_count"] == 2

    selected = client.get(
        f"{_BASE}/inspiration_image/revisions/{child['revision']}"
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["id"] == child["id"]
    assert selected.json()["contract"] == child_body["contract"]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("expected_projected_revision", 999, "projected_revision_conflict"),
        (
            "expected_projected_contract_hash",
            "0" * 64,
            "projected_contract_hash_conflict",
        ),
    ],
)
def test_candidate_creation_rejects_stale_runtime_projection(
    client: TestClient,
    field: str,
    value: Any,
    code: str,
) -> None:
    runtime = _create_runtime(client)
    payload = _candidate_payload(runtime)
    payload[field] = value
    response = client.post(
        f"{_BASE}/inspiration_image/revisions", json=payload
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    assert client.get(f"{_BASE}/inspiration_image").json()["candidate_count"] == 0


def test_candidate_parent_must_belong_to_category_and_be_on_candidate_chain(
    client: TestClient,
) -> None:
    runtime = _create_runtime(client)
    foreign_runtime = _create_runtime(client, "space_image")

    foreign = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(
            runtime,
            parent_revision_id=foreign_runtime["projected_revision_id"],
        ),
    )
    assert foreign.status_code == 409, foreign.text
    assert foreign.json()["detail"]["code"] == "parent_revision_conflict"

    missing = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(runtime, parent_revision_id=999999),
    )
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"]["code"] == "parent_revision_conflict"


def test_duplicate_candidate_request_is_idempotent_but_conflicting_retry_fails(
    client: TestClient,
) -> None:
    runtime = _create_runtime(client)
    payload = _candidate_payload(runtime)
    first = client.post(
        f"{_BASE}/inspiration_image/revisions", json=payload
    )
    assert first.status_code == 201, first.text
    repeated = client.post(
        f"{_BASE}/inspiration_image/revisions", json=payload
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == first.json()["id"]

    conflict_payload = deepcopy(payload)
    conflict_payload["display_name"] = "相同合同但冲突的显示名"
    conflict = client.post(
        f"{_BASE}/inspiration_image/revisions", json=conflict_payload
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "candidate_revision_conflict"


def test_candidate_creation_rejects_cross_category_and_unknown_profile(
    client: TestClient,
) -> None:
    runtime = _create_runtime(client)
    cross_category = _candidate_payload(runtime)
    cross_category["category_key"] = "space_image"
    mismatch = client.post(
        f"{_BASE}/inspiration_image/revisions", json=cross_category
    )
    assert mismatch.status_code == 400, mismatch.text
    assert mismatch.json()["detail"]["code"] == "category_key_mismatch"

    unknown = _candidate_payload(runtime)
    unknown["contract"] = {
        "profile_type": "future-3d-v1",
        "category_key": "inspiration_image",
    }
    unknown["classification_map"] = {}
    unknown["subcategory_dimensions"] = {}
    unsupported = client.post(
        f"{_BASE}/inspiration_image/revisions", json=unknown
    )
    assert unsupported.status_code == 400, unsupported.text
    assert unsupported.json()["detail"]["code"] == "profile_type_unsupported"


def test_revision_detail_missing_returns_coded_404(client: TestClient) -> None:
    _create_runtime(client)
    response = client.get(f"{_BASE}/inspiration_image/revisions/999")
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "v3_revision_not_found"


def test_revision_json_is_canonical_and_not_double_encoded(client: TestClient) -> None:
    runtime = _create_runtime(client)
    response = client.post(
        f"{_BASE}/inspiration_image/revisions",
        json=_candidate_payload(runtime),
    )
    assert response.status_code == 201, response.text
    assert json.loads(json.dumps(response.json()["contract"])) == _valid_body()[
        "contract"
    ]
