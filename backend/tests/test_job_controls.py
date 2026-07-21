from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, current_user
from app.models import Asset, EvaluationControl, EvaluationJob, PromptVersion, User


def test_jobs_pin_prompts_and_support_pause_resume_cancel() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(username="tester", password_hash="unused", display_name="测试员")
    asset = Asset(
        original_name="room.jpg",
        stored_name="room.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=1200,
        height=800,
        sha256="b" * 64,
    )
    prompt_a = PromptVersion(
        stage="A",
        name="分类",
        version="A-2.1",
        system_prompt="system prompt for classification",
        user_prompt="user prompt",
        status="published",
    )
    prompt_b = PromptVersion(
        stage="B",
        name="美感",
        version="B-2.3",
        system_prompt="system prompt for aesthetic scoring",
        user_prompt="user prompt",
        status="draft",
    )
    db.add_all([user, asset, prompt_a, prompt_b, EvaluationControl(id=1)])
    db.commit()

    def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            "/api/jobs/enqueue",
            json={
                "asset_ids": [asset.id],
                "prompt_a_id": prompt_a.id,
                "prompt_b_id": prompt_b.id,
            },
        )
        assert created.status_code == 200
        job_id = created.json()["job_ids"][0]

        listed = client.get("/api/jobs").json()["items"][0]
        assert listed["prompt_a_version"] == "A-2.1"
        assert listed["prompt_b_version"] == "B-2.3"
        assert listed["updated_at"]

        paused = client.post("/api/jobs/control/pause")
        assert paused.status_code == 200
        assert paused.json()["affected"] == 1
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "paused"
        assert db.get(EvaluationJob, job_id).updated_at is not None
        assert client.get("/api/jobs/control").json()["paused"] is True

        resumed = client.post("/api/jobs/control/resume")
        assert resumed.status_code == 200
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "queued"

        canceled = client.post("/api/jobs/control/cancel")
        assert canceled.status_code == 200
        db.expire_all()
        assert db.get(EvaluationJob, job_id).status == "canceled"
        assert db.get(Asset, asset.id).status == "uploaded"

        single = client.post(
            "/api/jobs/enqueue",
            json={"asset_ids": [asset.id], "prompt_id": prompt_a.id},
        )
        assert single.status_code == 200
        single_job = client.get("/api/jobs").json()["items"][0]
        assert single_job["prompt_version"] == "A-2.1"
        assert single_job["prompt_a_version"] is None
        assert single_job["prompt_b_version"] is None
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
