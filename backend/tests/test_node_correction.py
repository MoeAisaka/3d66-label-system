from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dimension_deduction_bridge import empty_deduction_output
from app.evaluation_v3_pipeline import recompute_qualified_v3
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.models import EvaluationResult
from app.node_correction_api import build_node_correction_router
from app.worker_v3_authoritative import build_v3_authoritative_scoring


def _context() -> dict:
    return {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 3,
    }


def _precheck() -> dict:
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "建筑设计",
            "primary_confidence": 0.96,
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }


def test_correct_dimension_rule_appends_evidence_and_recomputes_downstream() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    context = _context()
    config = context["subcategory_dimensions"]["class_one"]
    dimension_output = empty_deduction_output(config)
    precheck = _precheck()
    aggregate = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=dimension_output,
    )
    scoring = build_v3_authoritative_scoring(aggregate, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = context

    with sessions() as db:
        row = EvaluationResult(
            asset_id=1,
            job_id=1,
            precheck_json=json.dumps(precheck, ensure_ascii=False),
            aesthetic_json=json.dumps(dimension_output, ensure_ascii=False),
            scoring_json=json.dumps(scoring, ensure_ascii=False),
            correction_history_json="[]",
            raw_response_a="{}",
            score=scoring["score"],
            level=scoring["level"],
            confidence=scoring["confidence"],
            needs_review=False,
            model_id="fake",
            prompt_a_version="a",
            rubric_version="r",
            engine_version="e",
        )
        db.add(row)
        db.commit()
        result_id = row.id
        original_score = row.score

    def db_dependency():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    def reviewer():
        return SimpleNamespace(username="reviewer", display_name="审核员")

    app = FastAPI()
    app.include_router(build_node_correction_router(reviewer))
    app.dependency_overrides[get_db] = db_dependency
    first_dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    first_rule = first_dimension["deduction_rules"][0]
    new_hit = {
        "rule_id": first_rule["rule_id"],
        "confidence": "high",
        "evidence": "主体明显偏移，左侧大面积空置",
    }
    payload = {
        "correction_key": "case-1-rule-1",
        "node_type": "dimension_rule",
        "node_path": f"dimension.{first_dimension['key']}.hit_rules.{first_rule['rule_id']}",
        "old_value": None,
        "new_value": new_hit,
        "evidence": [
            {
                "rule_id": first_rule["rule_id"],
                "old_confidence": None,
                "new_confidence": "high",
                "old_evidence": "",
                "new_evidence": new_hit["evidence"],
            }
        ],
        "reason": "人工复核确认规则命中",
    }
    client = TestClient(app)
    response = client.post(
        f"/api/evaluation-results/{result_id}/correct-node", json=payload
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] < original_score
    assert body["correction"]["downstream_recomputed"] is True
    assert body["correction"]["evidence"][0]["new_evidence"] == new_hit["evidence"]
    assert len(body["correction_history"]) == 1

    # Same correction key is idempotent and never appends twice.
    replay = client.post(
        f"/api/evaluation-results/{result_id}/correct-node", json=payload
    )
    assert replay.status_code == 200
    assert len(replay.json()["correction_history"]) == 1
    engine.dispose()


def test_correct_precheck_redline_track_and_final_level_replays_full_path() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    context = _context()
    dimension_output = empty_deduction_output(
        context["subcategory_dimensions"]["class_one"]
    )
    precheck = _precheck()
    aggregate = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=dimension_output,
    )
    scoring = build_v3_authoritative_scoring(aggregate, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = context

    with sessions() as db:
        row = EvaluationResult(
            asset_id=1,
            job_id=1,
            precheck_json=json.dumps(precheck, ensure_ascii=False),
            aesthetic_json=json.dumps(dimension_output, ensure_ascii=False),
            scoring_json=json.dumps(scoring, ensure_ascii=False),
            correction_history_json="[]",
            raw_response_a="{}",
            score=scoring["score"],
            level=scoring["level"],
            confidence=scoring["confidence"],
            needs_review=False,
            model_id="fake",
            prompt_a_version="a",
            rubric_version="r",
            engine_version="e",
        )
        db.add(row)
        db.commit()
        result_id = row.id
        original_score = row.score

    def db_dependency():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    def reviewer():
        return SimpleNamespace(username="reviewer", display_name="审核员")

    app = FastAPI()
    app.include_router(build_node_correction_router(reviewer))
    app.dependency_overrides[get_db] = db_dependency
    client = TestClient(app)

    def correct(
        key: str,
        node_type: str,
        node_path: str,
        old_value: object,
        new_value: object,
    ) -> dict:
        response = client.post(
            f"/api/evaluation-results/{result_id}/correct-node",
            json={
                "correction_key": key,
                "node_type": node_type,
                "node_path": node_path,
                "old_value": old_value,
                "new_value": new_value,
                "evidence": [],
                "reason": "前端节点纠偏集成测试",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    media = correct(
        "ui-media",
        "precheck_field",
        "precheck.production_fields.trait",
        "实景照片",
        "AI图",
    )
    assert media["score"] == original_score - 15
    assert media["scoring"]["media_key"] == "ai_image"

    redline = correct(
        "ui-redline-on",
        "redline",
        "redline.production_fields.reason",
        [],
        ["是截图"],
    )
    assert redline["score"] == 49
    assert redline["level"] == "L5"
    assert redline["scoring"]["hit_rules"] == ["screenshot"]

    restored = correct(
        "ui-redline-off",
        "redline",
        "redline.production_fields.reason",
        ["是截图"],
        [],
    )
    assert restored["score"] == media["score"]

    track = correct(
        "ui-track",
        "track",
        "track_key",
        "class_one",
        "class_three",
    )
    assert track["score"] == 55
    assert track["level"] == "L3"
    assert track["scoring"]["track_key"] == "class_three"
    assert set(track["aesthetic"]["dimensions"]) == {
        "subject_focus",
        "mood_atmosphere",
        "composition_lighting",
        "reference_value",
        "visual_impact",
    }

    final = correct(
        "ui-final-level",
        "final_level",
        "final_level",
        "L3",
        "L4",
    )
    assert final["score"] == track["score"]
    assert final["level"] == "L4"
    assert final["correction"]["downstream_recomputed"] is False
    assert final["correction"]["corrector"] == "审核员"
    assert len(final["correction_history"]) == 5
    engine.dispose()
