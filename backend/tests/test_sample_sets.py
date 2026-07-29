import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import Asset, EvaluationJob, EvaluationResult, User


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
