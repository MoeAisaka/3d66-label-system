import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import _add_to_category_golden_set, app, current_user
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    SampleSet,
    SampleSetItem,
    SampleTruthRevision,
    User,
)


def test_system_golden_set_revises_truth_for_repeated_asset_correction() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    try:
        asset = Asset(
            original_name="repeat.jpg",
            stored_name="repeat.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            sha256="f" * 64,
            category_key="space_image",
        )
        db.add(asset)
        db.flush()
        results = []
        for index in (1, 2):
            job = EvaluationJob(
                asset_id=asset.id,
                category_key="space_image",
                status="completed",
                stage="done",
                progress=100,
            )
            db.add(job)
            db.flush()
            result = EvaluationResult(
                asset_id=asset.id,
                job_id=job.id,
                precheck_json='{"classification":{"primary_category":"住宅设计"}}',
                aesthetic_json=None,
                scoring_json="{}",
                raw_response_a="{}",
                score=50 + index,
                level="L3",
                confidence=0.9,
                needs_review=False,
                model_id="repeat-model",
                prompt_a_version="repeat-a",
                prompt_b_version="repeat-b",
                rubric_version="repeat-rubric",
                engine_version="repeat-engine",
            )
            db.add(result)
            db.flush()
            results.append(result)

        first_truth = {"corrected_level": "L3", "marker": "first"}
        second_truth = {"corrected_level": "L2", "marker": "second"}
        _add_to_category_golden_set(
            db, evaluation=results[0], truth=first_truth, actor="reviewer-a"
        )
        db.flush()
        _add_to_category_golden_set(
            db, evaluation=results[0], truth=first_truth, actor="reviewer-a"
        )
        db.flush()
        _add_to_category_golden_set(
            db, evaluation=results[1], truth=second_truth, actor="reviewer-b"
        )
        db.flush()

        sample_set = db.scalar(
            select(SampleSet).where(SampleSet.name == "系统黄金集·space_image")
        )
        item = db.scalar(
            select(SampleSetItem).where(SampleSetItem.sample_set_id == sample_set.id)
        )
        assert item.source_result_id == results[1].id
        assert item.truth_revision == 2
        assert json.loads(item.truth_json)["marker"] == "second"
        assert db.scalar(
            select(func.count()).select_from(SampleSetItem).where(
                SampleSetItem.sample_set_id == sample_set.id
            )
        ) == 1
        revisions = db.scalars(
            select(SampleTruthRevision)
            .where(SampleTruthRevision.sample_item_id == item.id)
            .order_by(SampleTruthRevision.revision)
        ).all()
        assert [revision.revision for revision in revisions] == [1, 2]
        assert [json.loads(revision.truth_json)["marker"] for revision in revisions] == [
            "first",
            "second",
        ]
    finally:
        db.close()
        engine.dispose()


def test_sample_set_captures_human_final_level() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    asset = Asset(
        original_name="sample.jpg",
        stored_name="sample.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=1200,
        height=800,
        sha256="a" * 64,
        status="evaluated",
    )
    db.add_all([user, asset])
    db.flush()
    job = EvaluationJob(asset_id=asset.id, status="completed", stage="done", progress=100)
    db.add(job)
    db.flush()
    result = EvaluationResult(
        asset_id=asset.id,
        job_id=job.id,
        precheck_json=json.dumps(
            {
                "classification": {
                    "primary_category": "住宅设计",
                    "scope_status": "in_scope",
                    "primary_confidence": 0.95,
                },
                "image_quality": {"quality_severity": "good", "confidence": 0.95},
                "media_form": {},
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps(
            {
                "dimensions": {
                    key: {"grade": 5 if key == "color_material" else 4}
                    for key in (
                        "composition_viewpoint",
                        "lighting_atmosphere",
                        "color_material",
                        "spatial_design_furnishing",
                        "visual_hierarchy",
                        "detail_completion",
                        "inspiration_reference",
                        "presentation_integrity",
                    )
                },
                "assessment_confidence": 0.9,
            },
            ensure_ascii=False,
        ),
        scoring_json="{}",
        raw_response_a="{}",
        raw_response_b=None,
        score=65,
        level="L3",
        confidence=0.9,
        needs_review=False,
        model_id="doubao-1.8",
        prompt_a_version="A1",
        prompt_b_version=None,
        rubric_version="R1",
        engine_version="E1",
    )
    db.add(result)
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        opened = client.post(
            f"/api/evaluations/{result.id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200
        reviewed = client.post(
            f"/api/evaluations/{result.id}/review-panel/votes",
            json={
                "reviewer_name": "审核员",
                "decision": "corrected",
                "expected_panel_revision": opened.json()["revision"],
                "note": "色彩与材质评分偏高",
                "corrections": [
                    {
                        "target_type": "dimension",
                        "field_key": "color_material",
                        "model_value": 5,
                        "human_value": 3,
                        "reason_codes": ["photography_as_design"],
                        "note": "统一色调主要来自摄影调色",
                    }
                ],
            },
        )
        assert reviewed.status_code == 200
        asset_detail = client.get(f"/api/assets/{asset.id}").json()
        assert "evaluation" not in asset_detail
        assert "status" not in asset_detail
        evaluation_detail = client.get(f"/api/evaluations/{result.id}").json()
        assert evaluation_detail["evaluation"]["updated_at"]
        assert evaluation_detail["evaluation"]["human_review"]["corrected_score"] is not None
        correction = evaluation_detail["evaluation"]["human_review"]["corrections"][0]
        assert correction["field_key"] == "color_material"
        assert correction["human_value"] == 3

        created = client.post(
            "/api/sample-sets",
            json={"name": "黄金样本", "description": "迁移回归"},
        )
        assert created.status_code == 200
        sample_set_id = created.json()["id"]

        added = client.post(
            f"/api/sample-sets/{sample_set_id}/items",
            json={"asset_ids": [asset.id]},
        )
        assert added.status_code == 200
        assert added.json()["added"] == 1

        detail = client.get(f"/api/sample-sets/{sample_set_id}").json()
        assert detail["summary"]["item_count"] == 1
        assert detail["items"][0]["expected_level"] == "L4"
        assert detail["items"][0]["expected_category"] == "住宅设计"

        overridden = client.post(
            "/api/sample-sets",
            json={"name": "L2 专项样本", "description": "批量等级覆盖"},
        )
        overridden_id = overridden.json()["id"]
        client.post(
            f"/api/sample-sets/{overridden_id}/items",
            json={"asset_ids": [asset.id], "expected_level": "L2"},
        )
        overridden_detail = client.get(f"/api/sample-sets/{overridden_id}").json()
        assert overridden_detail["items"][0]["expected_level"] == "L2"

        second_job = EvaluationJob(asset_id=asset.id, status="completed", stage="done", progress=100)
        db.add(second_job)
        db.flush()
        second_result = EvaluationResult(
            asset_id=asset.id,
            job_id=second_job.id,
            precheck_json=json.dumps(
                {"classification": {"primary_category": "商业空间"}}, ensure_ascii=False
            ),
            aesthetic_json=None,
            scoring_json="{}",
            raw_response_a="{}",
            raw_response_b=None,
            score=52,
            level="L2",
            confidence=0.8,
            needs_review=True,
            model_id="doubao-2.0",
            prompt_a_version="A2",
            prompt_b_version="B2",
            rubric_version="R1",
            engine_version="E1",
        )
        db.add(second_result)
        db.commit()

        evaluation_rows = client.get("/api/evaluations?limit=100").json()["items"]
        matching = [item for item in evaluation_rows if item["id"] == asset.id]
        assert len(matching) == 2
        assert {item["evaluation"]["versions"]["model"] for item in matching} == {
            "doubao-1.8",
            "doubao-2.0",
        }
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_create_sample_set_copies_locked_golden_set_into_editable_draft() -> None:
    """已锁定黄金集的 409 建议"复制形成新草稿版本后再调整"，这条路径必须真实存在。

    在此之前 SampleSetCreateRequest 只有 name/description/kind/category_key，
    没有任何从现有集复制的能力，运营撞上 409 后只能直连 SQL 绕过守卫。
    """
    from datetime import datetime, timezone

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="tester", password_hash="unused", display_name="测试员")
        asset = Asset(
            original_name="好-inspiration.jpg",
            stored_name="stored-good.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            width=1200,
            height=800,
            sha256="b" * 64,
            status="evaluated",
        )
        db.add_all([user, asset])
        db.flush()
        job = EvaluationJob(
            asset_id=asset.id,
            category_key="inspiration_image",
            status="completed",
            stage="done",
            progress=100,
        )
        db.add(job)
        db.flush()
        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            precheck_json="{}",
            aesthetic_json=None,
            scoring_json="{}",
            raw_response_a="{}",
            score=75,
            level="L2",
            confidence=0.9,
            needs_review=False,
            model_id="copy-model",
            prompt_a_version="copy-a",
            prompt_b_version="copy-b",
            rubric_version="copy-rubric",
            engine_version="copy-engine",
        )
        db.add(result)
        db.flush()
        locked = SampleSet(
            name="灵感图黄金集 v1",
            description="已锁定",
            kind="golden",
            category_key="inspiration_image",
            status="locked",
            created_by="tester",
        )
        db.add(locked)
        db.flush()
        db.add(
            SampleSetItem(
                sample_set_id=locked.id,
                asset_id=asset.id,
                source_result_id=result.id,
                expected_level="L2",
                expected_category="inspiration_image",
                truth_json=json.dumps({"level": "L2", "source": "human_correction"}),
                truth_revision=3,
                truth_updated_by="运营小张",
                truth_updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                note="人工纠偏过",
                added_by="tester",
            )
        )
        db.commit()
        locked_id = locked.id

    def _override_db():
        with Session(engine) as session:
            yield session

    holder = Session(engine)
    acting_user = holder.scalar(select(User).where(User.username == "tester"))
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = lambda: acting_user
    try:
        client = TestClient(app)
        # 锁定集自身不可改（守卫仍在），且提示里给出可用的复制路径
        blocked = client.patch(
            f"/api/sample-sets/{locked_id}/status", json={"status": "draft"}
        )
        assert blocked.status_code == 409
        assert "source_sample_set_id" in blocked.json()["detail"]

        created = client.post(
            "/api/sample-sets",
            json={"name": "灵感图黄金集 v2 草稿", "source_sample_set_id": locked_id},
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["copied_items"] == 1
        assert body["source_sample_set_id"] == locked_id
        new_id = body["id"]
    finally:
        app.dependency_overrides.clear()

    with Session(engine) as db:
        copy = db.get(SampleSet, new_id)
        assert copy is not None
        # 复制体必须是可编辑的 draft，否则又撞 409，等于没给出路
        assert copy.status == "draft"
        # kind 与 category_key 跟随源集，否则复制出来的集跑不了同一套回归
        assert copy.kind == "golden"
        assert copy.category_key == "inspiration_image"
        items = db.scalars(
            select(SampleSetItem).where(SampleSetItem.sample_set_id == new_id)
        ).all()
        assert len(items) == 1
        item = items[0]
        assert item.expected_level == "L2"
        # 人工纠偏真值连同修订号与署名一起带过去 —— 复制黄金集的意义就在这份结论
        assert json.loads(item.truth_json or "{}")["source"] == "human_correction"
        assert item.truth_revision == 3
        assert item.truth_updated_by == "运营小张"
    engine.dispose()


def test_create_sample_set_rejects_unknown_source() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(username="tester", password_hash="unused", display_name="测试员"))
        db.commit()

    def _override_db():
        with Session(engine) as session:
            yield session

    holder = Session(engine)
    acting_user = holder.scalar(select(User).where(User.username == "tester"))
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = lambda: acting_user
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/sample-sets", json={"name": "复制不存在的集", "source_sample_set_id": 9999}
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
        holder.close()
    engine.dispose()
