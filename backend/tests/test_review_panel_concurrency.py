from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import app.main as main_module
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.main import app, current_user
from app.migrations import run_migrations
from app.models import (
    Asset,
    EvaluationJob,
    EvaluationResult,
    HumanReview,
    ModelConfig,
    OptimizationCaseQueue,
    PromptVersion,
    ReviewPanel,
    ReviewWorkflowPolicy,
    SamplingPolicy,
    User,
)
from app.strategy_bundle import build_strategy_snapshot, get_or_create_bundle


def _seed_concurrent_review_database(engine: Engine) -> tuple[User, int]:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        run_migrations(connection)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            username="concurrent-review-owner",
            password_hash="unused",
            display_name="并发审核管理员",
        )
        asset = Asset(
            original_name="concurrent-review.jpg",
            stored_name="concurrent-review.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            sha256="c" * 64,
        )
        policy = db.get(ReviewWorkflowPolicy, 1)
        assert policy is not None
        policy.initial_reviewers = 3
        prompt = PromptVersion(
            stage="A",
            name="并发初审测试提示词",
            version="concurrent-review-A1",
            system_prompt="system prompt for concurrent review",
            user_prompt="user prompt",
            rubric_version="R1",
            status="published",
        )
        model = ModelConfig(
            name="concurrent review model",
            model_id="concurrent-review-model",
            base_url="https://example.test/v1",
            api_path="/chat/completions",
        )
        sampling_policy = SamplingPolicy(id=1, revision=1)
        db.add_all([user, asset, prompt, model, sampling_policy])
        db.flush()
        bundle = get_or_create_bundle(
            db=db,
            model_config=model,
            prompt_a=prompt,
            prompt_b=None,
            rubric_version="R1",
            engine_version="E1",
            risk_review_version=None,
            sampling_policy=sampling_policy,
        )
        job = EvaluationJob(
            asset_id=asset.id,
            prompt_a_id=prompt.id,
            strategy_bundle_id=bundle.id,
            status="completed",
            stage="done",
            progress=100,
        )
        db.add(job)
        db.flush()
        result = EvaluationResult(
            asset_id=asset.id,
            job_id=job.id,
            strategy_bundle_id=bundle.id,
            strategy_snapshot_json=build_strategy_snapshot(
                bundle, prompt, None, sampling_policy
            ),
            precheck_json=json.dumps(
                {
                    "classification": {
                        "scope_status": "in_scope",
                        "primary_category": "住宅设计",
                        "primary_confidence": 0.95,
                    },
                    "image_quality": {"quality_severity": "normal"},
                },
                ensure_ascii=False,
            ),
            aesthetic_json=json.dumps(
                {
                    "dimensions": {
                        key: {"grade": 4}
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
                    }
                },
                ensure_ascii=False,
            ),
            scoring_json=json.dumps({"formal": True, "level": "L3"}),
            raw_response_a="{}",
            score=75,
            level="L3",
            confidence=0.5,
            needs_review=True,
            model_id=model.model_id,
            prompt_a_version=prompt.version,
            rubric_version="R1",
            engine_version="E1",
        )
        db.add(result)
        db.commit()
        return user, result.id


def _concurrent_clients(
    tmp_path: Path,
) -> tuple[Engine, tuple[TestClient, TestClient], int]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review-panel-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    user, evaluation_id = _seed_concurrent_review_database(engine)

    def override_db():
        db = Session(engine, expire_on_commit=False)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = lambda: user
    clients = (
        TestClient(app, raise_server_exceptions=False),
        TestClient(app, raise_server_exceptions=False),
    )
    return engine, clients, evaluation_id


def _close_concurrent_clients(
    engine: Engine, clients: tuple[TestClient, TestClient]
) -> None:
    for client in clients:
        client.close()
    app.dependency_overrides.clear()
    engine.dispose()


def _synchronize_revision_claims(monkeypatch) -> Any:
    original_claim = main_module.claim_review_panel_revision
    barrier = threading.Barrier(2)

    def synchronized_claim(*args: Any, **kwargs: Any) -> int:
        barrier.wait(timeout=5)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        main_module, "claim_review_panel_revision", synchronized_claim
    )
    return original_claim


def _parallel_post(
    clients: tuple[TestClient, TestClient],
    url: str,
    payloads: tuple[dict[str, Any], dict[str, Any]],
):
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(client.post, url, json=payload)
            for client, payload in zip(clients, payloads, strict=True)
        ]
        return [future.result(timeout=15) for future in futures]


def test_concurrent_panel_votes_have_one_cas_winner_and_retry_without_loss(
    tmp_path: Path, monkeypatch
) -> None:
    engine, clients, evaluation_id = _concurrent_clients(tmp_path)
    try:
        opened = clients[0].post(
            f"/api/evaluations/{evaluation_id}/review-panel/open",
            json={},
        )
        assert opened.status_code == 200
        assert opened.json()["revision"] == 0

        original_claim = _synchronize_revision_claims(monkeypatch)
        payloads = tuple(
            {
                "reviewer_name": reviewer,
                "decision": "approved",
                "expected_panel_revision": 0,
                "note": "并发盲审",
                "corrections": [],
            }
            for reviewer in ("并发审核员甲", "并发审核员乙")
        )
        responses = _parallel_post(
            clients,
            f"/api/evaluations/{evaluation_id}/review-panel/votes",
            payloads,
        )
        assert sorted(response.status_code for response in responses) == [
            200,
            409,
        ]
        conflict = next(
            response for response in responses if response.status_code == 409
        )
        assert conflict.json()["detail"] == {
            "code": "STALE_REVIEW_PANEL",
            "message": "初审组修订号已变化，请刷新后重试",
            "revision": 1,
        }

        with Session(engine) as db:
            panel = db.scalar(
                select(ReviewPanel).where(
                    ReviewPanel.evaluation_id == evaluation_id
                )
            )
            assert panel is not None
            assert panel.revision == 1
            assert panel.status == "collecting"
            assert db.scalar(
                select(func.count(HumanReview.id)).where(
                    HumanReview.panel_id == panel.id
                )
            ) == 1
            assert db.get(EvaluationResult, evaluation_id).review_revision == 1

        monkeypatch.setattr(
            main_module, "claim_review_panel_revision", original_claim
        )
        losing_index = next(
            index
            for index, response in enumerate(responses)
            if response.status_code == 409
        )
        retried_payload = {**payloads[losing_index], "expected_panel_revision": 1}
        retried = clients[losing_index].post(
            f"/api/evaluations/{evaluation_id}/review-panel/votes",
            json=retried_payload,
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["revision"] == 2
        assert retried.json()["status"] == "collecting"
        assert retried.json()["submitted_count"] == 2

        with Session(engine) as db:
            panel = db.scalar(
                select(ReviewPanel).where(
                    ReviewPanel.evaluation_id == evaluation_id
                )
            )
            assert panel is not None
            assert panel.revision == 2
            assert db.scalars(
                select(HumanReview.reviewer_name)
                .where(HumanReview.panel_id == panel.id)
                .order_by(HumanReview.reviewer_name)
            ).all() == ["并发审核员乙", "并发审核员甲"]
            assert db.get(EvaluationResult, evaluation_id).review_revision == 2
    finally:
        _close_concurrent_clients(engine, clients)


def test_concurrent_lead_adjudications_finalize_and_enqueue_exactly_once(
    tmp_path: Path, monkeypatch
) -> None:
    engine, clients, evaluation_id = _concurrent_clients(tmp_path)
    try:
        assert clients[0].post(
            f"/api/evaluations/{evaluation_id}/review-panel/open",
            json={},
        ).status_code == 200
        decisions = ("approved", "rejected", "corrected")
        for index, decision in enumerate(decisions):
            corrections = (
                [
                    {
                        "target_type": "dimension",
                        "field_key": "color_material",
                        "model_value": 4,
                        "human_value": 3,
                        "reason_codes": ["concurrent-adjudication"],
                        "note": "并发裁决测试",
                    }
                ]
                if decision == "corrected"
                else []
            )
            voted = clients[0].post(
                f"/api/evaluations/{evaluation_id}/review-panel/votes",
                json={
                    "reviewer_name": f"盲审员{index}",
                    "decision": decision,
                    "expected_panel_revision": index,
                    "note": "形成主审裁决状态",
                    "corrections": corrections,
                },
            )
            assert voted.status_code == 200, voted.text
        assert voted.json()["status"] == "lead_adjudication"
        assert voted.json()["revision"] == 3

        _synchronize_revision_claims(monkeypatch)
        correction = {
            "target_type": "dimension",
            "field_key": "color_material",
            "model_value": 4,
            "human_value": 3,
            "reason_codes": ["concurrent-adjudication"],
            "note": "主审纠偏",
        }
        payloads = tuple(
            {
                "lead_reviewer_name": reviewer,
                "decision": "corrected",
                "expected_panel_revision": 3,
                "note": "并发主审裁决",
                "corrections": [correction],
            }
            for reviewer in ("并发主审甲", "并发主审乙")
        )
        responses = _parallel_post(
            clients,
            (
                f"/api/evaluations/{evaluation_id}"
                "/review-panel/lead-adjudication"
            ),
            payloads,
        )
        assert sorted(response.status_code for response in responses) == [
            200,
            409,
        ]
        winner = next(
            response for response in responses if response.status_code == 200
        )
        assert winner.json()["status"] == "completed"
        assert winner.json()["revision"] == 4
        conflict = next(
            response for response in responses if response.status_code == 409
        )
        assert conflict.json()["detail"]["code"] == "STALE_REVIEW_PANEL"
        assert conflict.json()["detail"]["revision"] == 4

        with Session(engine) as db:
            panel = db.scalar(
                select(ReviewPanel).where(
                    ReviewPanel.evaluation_id == evaluation_id
                )
            )
            assert panel is not None
            assert panel.status == "completed"
            assert panel.revision == 4
            assert panel.final_review_id is not None
            assert db.scalar(
                select(func.count(HumanReview.id)).where(
                    HumanReview.evaluation_id == evaluation_id
                )
            ) == 4
            assert db.scalar(
                select(func.count(OptimizationCaseQueue.id)).where(
                    OptimizationCaseQueue.evaluation_id == evaluation_id
                )
            ) == 1
            evaluation = db.get(EvaluationResult, evaluation_id)
            assert evaluation is not None
            assert evaluation.review_revision == 4
            assert evaluation.review_stage == "completed"
    finally:
        _close_concurrent_clients(engine, clients)
