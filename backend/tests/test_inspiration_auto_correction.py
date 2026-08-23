from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.database import get_db
from app.inspiration_auto_correction import (
    AutoCorrectionPolicy,
    BALANCED_GOLDEN_SET_NAME,
    balanced_rebuild_set_name,
    build_drift_report,
    decide_level_correction,
    ensure_inspiration_golden_set,
    ensure_inspiration_balanced_golden_set,
    fit_level_calibration,
    rating_from_original_name,
    rebuild_inspiration_balanced_golden_set,
    survey_inspiration_balanced_candidates,
)
from app.main import app, current_user
from app.models import Asset, BaselineSet, BaselineSetItem, User


def _sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_rating_regex_is_path_aware_and_does_not_guess() -> None:
    assert rating_from_original_name("好图补充/好_15015638.jpeg") == "好"
    assert rating_from_original_name(r"豆包美感\中差_14304072.jpeg") is None
    assert rating_from_original_name("批次_过滤_100.png") == "过滤"
    assert rating_from_original_name("好东西_100.png") is None
    assert rating_from_original_name("中等偏上_100.png") is None


def test_golden_set_references_cross_category_assets_without_mutation() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        db.add_all(
            [
                Asset(
                    original_name="好图补充/好_1.jpeg",
                    stored_name="a.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256="a" * 64,
                    category_key="space_image",
                    status="deleted",
                ),
                Asset(
                    original_name="豆包美感/过滤_2.jpeg",
                    stored_name="b.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=2,
                    sha256="b" * 64,
                    category_key="space_image",
                ),
                Asset(
                    original_name="后续候选/好_3.jpeg",
                    stored_name="late.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256="d" * 64,
                    category_key="space_image",
                ),
                Asset(
                    original_name="说明.pdf",
                    stored_name="c.pdf",
                    mime_type="application/pdf",
                    size_bytes=3,
                    sha256="c" * 64,
                    category_key="pdf_text",
                ),
                Asset(
                    original_name="好图补充/好_1.jpeg",
                    stored_name="a-copy.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256="a" * 64,
                    category_key="space_image",
                ),
            ]
        )
        db.commit()
        expected_distribution = {
            "好": 2,
            "中等": 0,
            "中差": 0,
            "极差": 0,
            "过滤": 1,
        }
        golden, report = ensure_inspiration_golden_set(
            db, expected_distribution=expected_distribution
        )
        assert golden.category_key == "inspiration_image"
        assert report["item_count"] == 3
        assert report["distribution"]["好"] == 2
        assert report["distribution"]["过滤"] == 1
        assert len(report["excluded_candidate_ids"]) == 1
        assert db.scalars(select(Asset).order_by(Asset.id)).all()[0].category_key == "space_image"
        snapshots = [
            json.loads(item.asset_snapshot_json)
            for item in db.scalars(select(BaselineSetItem).order_by(BaselineSetItem.id))
        ]
        assert [item["truth_updated_by"] for item in snapshots] == [
            "灵感图人工评级前缀",
            "灵感图人工评级前缀",
            "灵感图人工评级前缀",
        ]
        assert [item["asset_source_category_key"] for item in snapshots] == [
            "space_image",
            "space_image",
            "space_image",
        ]

        same, replay = ensure_inspiration_golden_set(
            db, expected_distribution=expected_distribution
        )
        assert same.id == golden.id
        assert replay["idempotent"] is True
        assert len(db.scalars(select(BaselineSetItem)).all()) == 3
    engine.dispose()


def test_balanced_golden_set_is_immutable() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        for rating in ("好", "中等", "中差", "极差", "过滤"):
            for index in range(20):
                db.add(
                    Asset(
                        original_name=f"batch/{rating}_{rating}-{index}.jpeg",
                        stored_name=f"{rating}-{index}.jpeg",
                        mime_type="image/jpeg",
                        size_bytes=1,
                        sha256=f"{rating}-{index}".encode().hex().ljust(64, "0"),
                        category_key="space_image",
                    )
                )
        db.commit()
        golden, report = ensure_inspiration_balanced_golden_set(db)
        assert report["item_count"] == 100
        assert report["distribution"] == {
            "好": 20, "中等": 20, "中差": 20, "极差": 20, "过滤": 20
        }
        same, replay = ensure_inspiration_balanced_golden_set(db)
        assert same.id == golden.id
        assert replay["idempotent"] is True
    engine.dispose()


def test_balanced_golden_set_can_be_frozen_from_baseline_api() -> None:
    engine, sessions = _sessions()
    db = sessions()
    user = User(username="baseline-owner", password_hash="unused", display_name="基准负责人")
    db.add(user)
    for rating in ("好", "中等", "中差", "极差", "过滤"):
        for index in range(20):
            db.add(
                Asset(
                    original_name=f"batch/{rating}_{rating}-{index}.jpeg",
                    stored_name=f"{rating}-{index}.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256=f"{rating}-{index}".encode().hex().ljust(64, "0"),
                    category_key="space_image",
                )
            )
    db.commit()
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        response = client.post("/api/baseline-sets/inspiration-balanced-100")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["category_key"] == "inspiration_image"
        assert payload["summary"]["item_count"] == 100
        assert payload["distribution"] == {
            "好": 20, "中等": 20, "中差": 20, "极差": 20, "过滤": 20
        }
        frozen = db.get(BaselineSet, payload["summary"]["id"])
        assert frozen is not None
        assert frozen.created_by == user.username

        replay = client.post("/api/baseline-sets/inspiration-balanced-100")
        assert replay.status_code == 200
        assert replay.json()["summary"]["id"] == payload["summary"]["id"]
        assert replay.json()["idempotent"] is True
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_balanced_golden_set_rejects_duplicate_sha256() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        for rating in ("好", "中等", "中差", "极差", "过滤"):
            for index in range(20):
                sha = (
                    "duplicate".encode().hex().ljust(64, "0")
                    if rating == "好" and index < 2
                    else f"{rating}-{index}".encode().hex().ljust(64, "0")
                )
                db.add(
                    Asset(
                        original_name=f"batch/{rating}_{rating}-{index}.jpeg",
                        stored_name=f"{rating}-{index}.jpeg",
                        mime_type="image/jpeg",
                        size_bytes=1,
                        sha256=sha,
                        category_key="space_image",
                    )
                )
        db.commit()
        with pytest.raises(ValueError, match="重复 SHA-256"):
            ensure_inspiration_balanced_golden_set(db)
    engine.dispose()


def _seed_rated_assets(db, *, per_rating: int = 40) -> None:
    for rating in ("好", "中等", "中差", "极差", "过滤"):
        for index in range(per_rating):
            db.add(
                Asset(
                    original_name=f"batch/{rating}_{rating}-{index}.jpeg",
                    stored_name=f"{rating}-{index}.jpeg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256=f"{rating}-{index}".encode().hex().ljust(64, "0"),
                    category_key="space_image",
                )
            )
    db.commit()


def _asset_ids_of(db, baseline_set_id: int) -> set[int]:
    return set(
        db.scalars(
            select(BaselineSetItem.asset_id).where(
                BaselineSetItem.baseline_set_id == baseline_set_id
            )
        ).all()
    )


def test_balanced_rebuild_lands_in_new_set_and_never_rewrites_the_frozen_one() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db)
        original, _ = ensure_inspiration_balanced_golden_set(db)
        original_ids = _asset_ids_of(db, original.id)

        rebuilt, report = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops"
        )
        assert rebuilt.id != original.id
        assert report["created"] is True
        assert report["idempotent"] is False
        assert report["item_count"] == 100
        assert report["distribution"] == {
            "好": 20, "中等": 20, "中差": 20, "极差": 20, "过滤": 20
        }
        # The frozen sample keeps both its identity and its exact membership:
        # runs already reference it, so a rebuild must not touch it.
        assert db.get(BaselineSet, original.id).name == BALANCED_GOLDEN_SET_NAME
        assert _asset_ids_of(db, original.id) == original_ids

        again, replay = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops"
        )
        assert again.id == rebuilt.id
        assert replay["idempotent"] is True
        assert replay["fingerprint"] == report["fingerprint"]
    engine.dispose()


def test_balanced_rebuild_can_reach_material_the_frozen_sample_missed() -> None:
    """The reason this capability exists: newly rated assets stay unreachable.

    The original sample draws ascending by asset id, so once the corpus grows
    past the quota every later upload is permanently excluded from it.
    """

    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db)
        original, _ = ensure_inspiration_balanced_golden_set(db)
        original_ids = _asset_ids_of(db, original.id)

        rebuilt, report = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", strategy="newest"
        )
        rebuilt_ids = _asset_ids_of(db, rebuilt.id)
        assert rebuilt_ids.isdisjoint(original_ids)
        assert report["coverage"]["new_asset_count"] == 100
        assert report["coverage"]["reused_asset_count"] == 0
        assert report["coverage"]["previous_balanced_set_id"] == original.id
    engine.dispose()


def test_rebuild_seed_reshuffles_stable_hash_but_is_inert_for_recency() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db)
        first, _ = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", seed=1
        )
        second, second_report = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", seed=2
        )
        # A different seed is a genuinely different draw, so it must become its
        # own frozen set rather than rewriting the first.
        assert second.id != first.id
        assert second_report["created"] is True
        assert _asset_ids_of(db, second.id) != _asset_ids_of(db, first.id)

        # For recency strategies the seed cannot change anything, so bumping it
        # replays the same set instead of freezing a byte-identical copy.
        newest, _ = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", strategy="newest", seed=1
        )
        newest_again, replay = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", strategy="newest", seed=999
        )
        assert newest_again.id == newest.id
        assert replay["idempotent"] is True
    engine.dispose()


def test_rebuild_name_only_carries_parameters_that_change_the_draw() -> None:
    assert balanced_rebuild_set_name(
        per_level=20, strategy="stable_hash", seed=7
    ).endswith("stable_hash-seed7")
    for strategy in ("newest", "oldest"):
        assert balanced_rebuild_set_name(
            per_level=20, strategy=strategy, seed=7
        ) == balanced_rebuild_set_name(
            per_level=20, strategy=strategy, seed=8
        )


def test_rebuild_excludes_deleted_and_deduplicates_by_sha256() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db, per_rating=22)
        # Hidden from the operator's asset list, so a fresh draw must skip it.
        hidden = db.scalars(select(Asset).order_by(Asset.id.asc())).first()
        hidden.status = "deleted"
        # Same binary re-uploaded under another rated name.
        duplicate_of = db.scalars(
            select(Asset).order_by(Asset.id.asc())
        ).all()[1]
        db.add(
            Asset(
                original_name="batch/好_reupload.jpeg",
                stored_name="好_reupload.jpeg",
                mime_type="image/jpeg",
                size_bytes=1,
                sha256=duplicate_of.sha256,
                category_key="space_image",
            )
        )
        db.commit()

        rebuilt, report = rebuild_inspiration_balanced_golden_set(
            db, created_by="ops", strategy="oldest"
        )
        assert report["deleted_excluded"] == 1
        assert report["duplicate_sha256_skipped"] == 1
        selected = _asset_ids_of(db, rebuilt.id)
        assert hidden.id not in selected
        assert report["item_count"] == 100
    engine.dispose()


def test_rebuild_refuses_when_a_level_cannot_fill_the_quota() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db, per_rating=20)
        with pytest.raises(ValueError, match="无法满足"):
            rebuild_inspiration_balanced_golden_set(
                db, created_by="ops", per_level=25
            )
        with pytest.raises(ValueError, match="不支持的抽样方式"):
            rebuild_inspiration_balanced_golden_set(
                db, created_by="ops", strategy="random"
            )
    engine.dispose()


def test_rebuild_survey_reports_reach_without_writing() -> None:
    engine, sessions = _sessions()
    with sessions() as db:
        _seed_rated_assets(db)
        original, _ = ensure_inspiration_balanced_golden_set(db)
        before = len(db.scalars(select(BaselineSet)).all())

        survey = survey_inspiration_balanced_candidates(db)
        assert survey["candidate_total"] == 200
        assert survey["max_per_level"] == 40
        assert survey["current_balanced_set"]["baseline_set_id"] == original.id
        assert survey["current_balanced_set"]["item_count"] == 100
        assert "stable_hash" in survey["strategies"]
        assert len(db.scalars(select(BaselineSet)).all()) == before
    engine.dispose()


def test_rebuild_endpoint_freezes_a_new_set_alongside_the_original() -> None:
    engine, sessions = _sessions()
    db = sessions()
    user = User(
        username="baseline-owner", password_hash="unused", display_name="基准负责人"
    )
    db.add(user)
    _seed_rated_assets(db)
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        frozen = client.post("/api/baseline-sets/inspiration-balanced-100")
        assert frozen.status_code == 200
        original_id = frozen.json()["summary"]["id"]

        survey = client.get(
            "/api/baseline-sets/inspiration-balanced-sample/rebuild-survey"
        )
        assert survey.status_code == 200
        assert survey.json()["max_per_level"] == 40

        response = client.post(
            "/api/baseline-sets/inspiration-balanced-sample/rebuild",
            json={"per_level": 20, "strategy": "newest", "seed": 1},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["id"] != original_id
        assert payload["summary"]["category_key"] == "inspiration_image"
        assert payload["summary"]["item_count"] == 100
        assert payload["created"] is True
        assert db.get(BaselineSet, original_id) is not None

        # Within the schema bound but beyond what the corpus can fill: the
        # quota check must refuse it rather than freeze a short sample.
        conflict = client.post(
            "/api/baseline-sets/inspiration-balanced-sample/rebuild",
            json={"per_level": 200, "strategy": "newest", "seed": 1},
        )
        assert conflict.status_code == 409
        assert "无法满足" in conflict.json()["detail"]

        # Out-of-range parameters are rejected by the schema before any work.
        assert client.post(
            "/api/baseline-sets/inspiration-balanced-sample/rebuild",
            json={"per_level": 0},
        ).status_code == 422
        assert client.post(
            "/api/baseline-sets/inspiration-balanced-sample/rebuild",
            json={"strategy": "random"},
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_calibration_decision_uses_only_frozen_mapping_and_confidence_gate() -> None:
    policy = AutoCorrectionPolicy(
        confidence_threshold=0.0,
        minimum_support=1,
        coverage_rate=1.0,
        calibration_fraction=1.0,
        maximum_level_shift=1,
    )
    rows = [
        {"asset_id": index, "expected_level": "L2", "predicted_level": "L3"}
        for index in range(1, 41)
    ] + [
        {"asset_id": index, "expected_level": "L3", "predicted_level": "L3"}
        for index in range(41, 46)
    ]
    calibration = fit_level_calibration(rows, policy=policy)
    decision = decide_level_correction(
        asset_id=999,
        predicted_level="L3",
        calibration=calibration,
        policy=policy,
    )
    assert decision["action"] == "auto_apply"
    assert decision["target_level"] == "L2"
    assert decision["support"] == 45

    conservative = AutoCorrectionPolicy(
        confidence_threshold=0.99,
        minimum_support=100,
        coverage_rate=1.0,
        calibration_fraction=1.0,
    )
    manual = decide_level_correction(
        asset_id=999,
        predicted_level="L3",
        calibration=calibration,
        policy=conservative,
    )
    assert manual["action"] == "manual_review"


def test_calibration_prefers_observable_score_track_cell_over_ambiguous_level() -> None:
    policy = AutoCorrectionPolicy(
        confidence_threshold=0.85,
        minimum_support=30,
        coverage_rate=1.0,
        calibration_fraction=1.0,
        maximum_level_shift=1,
    )
    rows = [
        {
            "asset_id": index,
            "expected_level": "L1" if index <= 95 else "L2",
            "predicted_level": "L2",
            "authoritative_score": 70,
            "track_key": "class_one",
        }
        for index in range(1, 101)
    ] + [
        {
            "asset_id": index,
            "expected_level": "L2",
            "predicted_level": "L2",
            "authoritative_score": 65,
            "track_key": "class_one",
        }
        for index in range(101, 201)
    ]
    calibration = fit_level_calibration(rows, policy=policy)
    assert calibration["mappings"]["L2"]["target_level"] == "L2"
    decision = decide_level_correction(
        asset_id=999,
        predicted_level="L2",
        authoritative_score=70,
        track_key="class_one",
        calibration=calibration,
        policy=policy,
    )
    assert decision["action"] == "auto_apply"
    assert decision["target_level"] == "L1"
    assert decision["calibration_key"] == "score-track:L2:class_one:70"


def test_drift_report_counts_fixes_and_new_errors_on_independent_holdout() -> None:
    items = [
        SimpleNamespace(
            asset_id=1,
            expected_level="L2",
            status="completed",
            result_snapshot_json=json.dumps({"predicted_level": "L3"}),
            evaluation=SimpleNamespace(level="L2"),
        ),
        SimpleNamespace(
            asset_id=2,
            expected_level="L1",
            status="completed",
            result_snapshot_json=json.dumps({"predicted_level": "L1"}),
            evaluation=SimpleNamespace(level="L2"),
        ),
    ]
    run = SimpleNamespace(id=7, items=items)
    report = build_drift_report(
        run,
        policy=AutoCorrectionPolicy(calibration_fraction=0.0),
    )
    assert report["fixed_errors"] == 1
    assert report["introduced_errors"] == 1
    assert report["before"]["exact_accuracy"] == 0.5
    assert report["after"]["exact_accuracy"] == 0.5
    assert report["holdout_after"]["denominator"] == 2
