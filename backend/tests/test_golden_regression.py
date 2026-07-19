import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    PromptRegressionRun,
    PromptVersion,
    User,
)


DIMENSIONS = {
    "composition_viewpoint": {"grade": 3},
    "lighting_atmosphere": {"grade": 4},
    "color_material": {"grade": 3},
    "spatial_design_furnishing": {"grade": 4},
    "visual_hierarchy": {"grade": 3},
    "detail_completion": {"grade": 4},
    "inspiration_reference": {"grade": 3},
    "presentation_integrity": {"grade": 4},
}


def test_golden_set_locks_runs_and_preserves_history() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    asset = Asset(
        original_name="golden.jpg",
        stored_name="golden.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=1200,
        height=800,
        sha256="g" * 64,
        status="evaluated",
    )
    prompt_a = PromptVersion(
        stage="A", name="预检", version="A1", system_prompt="s", user_prompt="u", status="published"
    )
    prompt_b = PromptVersion(
        stage="B", name="美感", version="B1", system_prompt="s", user_prompt="u", status="published"
    )
    db.add_all([user, asset, prompt_a, prompt_b])
    db.flush()
    job = EvaluationJob(asset_id=asset.id, prompt_a_id=prompt_a.id, prompt_b_id=prompt_b.id, status="completed")
    db.add(job)
    db.flush()
    result = EvaluationResult(
        asset_id=asset.id,
        job_id=job.id,
        precheck_json=json.dumps(
            {
                "classification": {"primary_category": "住宅设计"},
                "image_quality": {"quality_severity": "moderate"},
                "media_form": {
                    "real_photo": {"status": "yes"},
                    "professional_photography": {"status": "no"},
                },
            },
            ensure_ascii=False,
        ),
        aesthetic_json=json.dumps({"dimensions": DIMENSIONS}, ensure_ascii=False),
        scoring_json="{}",
        raw_response_a="{}",
        raw_response_b="{}",
        score=72,
        level="L3",
        confidence=0.9,
        needs_review=False,
        model_id="doubao-2.0",
        prompt_a_version="A1",
        prompt_b_version="B1",
        rubric_version="R1",
        engine_version="E1",
    )
    db.add(result)
    db.flush()
    db.add(
        HumanReview(
            evaluation_id=result.id,
            reviewer_name="审核员",
            decision="corrected",
            corrected_level="L2",
            note="画质问题导致等级偏高",
            corrections_json=json.dumps(
                [
                    {
                        "target_type": "dimension",
                        "field_key": "lighting_atmosphere",
                        "model_value": 4,
                        "human_value": 2,
                        "reason_codes": ["quality_issue"],
                        "note": "过曝影响氛围",
                    }
                ],
                ensure_ascii=False,
            ),
        )
    )
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/sample-sets",
            json={"name": "黄金回归集", "description": "发布门槛", "kind": "golden"},
        )
        assert created.status_code == 200
        sample_set_id = created.json()["id"]
        added = client.post(
            f"/api/sample-sets/{sample_set_id}/items", json={"asset_ids": [asset.id]}
        )
        assert added.status_code == 200

        detail = client.get(f"/api/sample-sets/{sample_set_id}").json()
        item = detail["items"][0]
        assert detail["summary"]["kind"] == "golden"
        assert item["truth"]["level"] == "L2"
        assert item["truth"]["quality_severity"] == "moderate"
        assert item["truth"]["dimensions"]["lighting_atmosphere"] == 2
        assert len(item["truth"]["dimensions"]) == 8

        locked = client.patch(
            f"/api/sample-sets/{sample_set_id}/status", json={"status": "locked"}
        )
        assert locked.status_code == 200
        run = client.post(
            "/api/prompt-regressions", json={"sample_set_id": sample_set_id}
        )
        assert run.status_code == 200
        run_id = run.json()["ids"][0]
        regression = db.get(PromptRegressionRun, run_id)
        assert regression is not None
        assert regression.total == 1
        assert regression.items[0].job_id is not None

        history = client.get(
            f"/api/sample-sets/{sample_set_id}/items/{item['id']}/history"
        ).json()
        assert len(history["evaluations"]) == 1
        assert history["evaluations"][0]["reviews"][0]["note"] == "画质问题导致等级偏高"
        assert history["truth_revisions"][0]["revision"] == 1
        assert history["regressions"][0]["run_id"] == run_id

        prompt_b2 = PromptVersion(
            stage="B", name="美感", version="B2", system_prompt="s2", user_prompt="u2", status="draft"
        )
        db.add(prompt_b2)
        db.commit()
        published = client.post(f"/api/prompts/{prompt_b2.id}/publish")
        assert published.status_code == 200
        assert len(published.json()["regression_run_ids"]) == 1
        auto_run = db.scalar(
            select(PromptRegressionRun).where(
                PromptRegressionRun.id == published.json()["regression_run_ids"][0]
            )
        )
        assert auto_run is not None
        assert auto_run.prompt_b_id == prompt_b2.id
        assert auto_run.total == 1
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
