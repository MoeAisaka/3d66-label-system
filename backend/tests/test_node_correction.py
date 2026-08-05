from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
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
from app.node_correction_api import (
    CorrectNodeRequest,
    apply_node_correction,
    build_node_correction_router,
)
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


def _full_call_a_precheck() -> dict:
    precheck = _precheck()
    precheck["production_fields"] = {
        "title": "现代住宅",
        "seotitle": "现代住宅空间设计参考",
        "category": "居住空间,大平层",
        "style": "现代简约",
        "tags": ["住宅", "客厅", "木饰面", "自然光"],
        "cons": "局部层次略显单薄",
        "design": "以自然光和材质层次组织空间",
        "score": 70,
        "reason": [],
        "image_defects": "",
        "trait": "实景照片",
    }
    return precheck


def _result_for_call_a(sessions: sessionmaker) -> int:
    context = _context()
    precheck = _full_call_a_precheck()
    dimension_output = empty_deduction_output(
        context["subcategory_dimensions"]["class_one"]
    )
    aggregate = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=dimension_output,
    )
    scoring = build_v3_authoritative_scoring(aggregate, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = context
    scoring["score"] = 70
    scoring["level"] = "L2"
    with sessions() as db:
        row = EvaluationResult(
            asset_id=1,
            job_id=1,
            precheck_json=json.dumps(precheck, ensure_ascii=False),
            aesthetic_json=json.dumps(dimension_output, ensure_ascii=False),
            scoring_json=json.dumps(scoring, ensure_ascii=False),
            correction_history_json="[]",
            raw_response_a='{"immutable":"provider payload"}',
            score=70,
            level="L2",
            confidence=scoring["confidence"],
            needs_review=False,
            model_id="fake",
            prompt_a_version="a",
            rubric_version="r",
            engine_version="e",
        )
        db.add(row)
        db.commit()
        return int(row.id)


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
        "confidence": "medium",
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
                "new_confidence": "medium",
                "old_evidence": "",
                "new_evidence": new_hit["evidence"],
            }
        ],
        "reason": "人工复核确认规则命中",
    }
    client = TestClient(app)
    invalid_payload = {
        **payload,
        "correction_key": "case-invalid-chinese-confidence",
        "new_value": {**new_hit, "confidence": "中"},
    }
    invalid = client.post(
        f"/api/evaluation-results/{result_id}/correct-node", json=invalid_payload
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "dimension_rule_invalid"

    response = client.post(
        f"/api/evaluation-results/{result_id}/correct-node", json=payload
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] < original_score
    assert body["correction"]["downstream_recomputed"] is True
    assert body["correction"]["evidence"][0]["new_evidence"] == new_hit["evidence"]
    assert body["correction"]["new_value"]["confidence"] == "medium"
    assert body["correction_history"][0]["new_value"]["confidence"] == "medium"
    assert len(body["correction_history"]) == 1

    with sessions() as db:
        stored = db.get(EvaluationResult, result_id)
        dimensions = json.loads(stored.aesthetic_json)["dimensions"]
        assert dimensions[first_dimension["key"]]["hit_rules"][0]["confidence"] == "medium"
        history = json.loads(stored.correction_history_json)
        assert history[0]["new_value"]["confidence"] == "medium"

    # Same correction key is idempotent and never appends twice.
    replay = client.post(
        f"/api/evaluation-results/{result_id}/correct-node", json=payload
    )
    assert replay.status_code == 200
    assert len(replay.json()["correction_history"]) == 1

    with sessions() as db:
        stored = db.get(EvaluationResult, result_id)
        assert stored is not None
        old_level = stored.level
        new_level = "L5" if old_level != "L5" else "L4"
        apply_node_correction(
            db,
            result=stored,
            payload=CorrectNodeRequest(
                correction_key="auto-corrector-test",
                node_type="final_level",
                node_path="final_level",
                old_value=old_level,
                new_value=new_level,
                evidence=[],
                reason="黄金集高置信度校准",
            ),
            corrector="auto-corrector-v1",
            corrector_confidence=0.91,
            corrector_policy="level-confusion-calibration-v1",
        )
        db.commit()
        history = json.loads(stored.correction_history_json)
        assert history[-1]["corrector"] == "auto-corrector-v1"
        assert history[-1]["corrector_confidence"] == 0.91
        assert history[-1]["corrector_policy"] == "level-confusion-calibration-v1"
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
    assert media["score"] == original_score
    assert media["scoring"]["media_key"] is None
    assert media["scoring"]["media_penalty_enabled"] is False

    redline = correct(
        "ui-redline-on",
        "redline",
        "redline.production_fields.reason",
        [],
        ["是截图"],
    )
    assert redline["score"] == 20
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
    assert track["score"] == 70
    assert track["level"] == "L2"
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
        "L2",
        "L4",
    )
    assert final["score"] == track["score"]
    assert final["level"] == "L4"
    assert final["correction"]["downstream_recomputed"] is False
    assert final["correction"]["corrector"] == "审核员"
    assert len(final["correction_history"]) == 5
    engine.dispose()


def test_call_a_business_fields_persist_without_changing_score() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    result_id = _result_for_call_a(sessions)

    corrections = [
        ("title", "现代住宅", "光影住宅"),
        ("seotitle", "现代住宅空间设计参考", "光影住宅室内设计灵感"),
        ("style", "现代简约", "侘寂风"),
        ("cons", "局部层次略显单薄", "入口转折略显生硬"),
        ("design", "以自然光和材质层次组织空间", "以连续拱券组织空间序列"),
        ("category", "居住空间,大平层", "酒店民宿,度假酒店"),
        ("tags", ["住宅", "客厅", "木饰面", "自然光"], ["酒店", "大堂", "石材", "灯光"]),
        ("trait", "实景照片", "3D数字效果图"),
        ("reason", [], ["是截图"]),
        ("image_defects", "", "有水印"),
    ]
    with sessions() as db:
        row = db.get(EvaluationResult, result_id)
        assert row is not None
        raw_before = row.raw_response_a
        for index, (field, old_value, new_value) in enumerate(corrections, 1):
            response = apply_node_correction(
                db,
                result=row,
                payload=CorrectNodeRequest(
                    correction_key=f"call-a-field-{index}",
                    node_type="call_a_field",
                    node_path=f"call_a.{field}",
                    old_value=old_value,
                    new_value=new_value,
                    evidence=[],
                    reason=f"人工核对{field}",
                ),
                corrector="审核员",
            )
            assert response["score"] == 70
            assert response["level"] == "L2"

        db.commit()
        production = json.loads(row.precheck_json)["production_fields"]
        assert production["title"] == "光影住宅"
        assert production["seotitle"] == "光影住宅室内设计灵感"
        assert production["style"] == "侘寂风"
        assert production["cons"] == "入口转折略显生硬"
        assert production["design"] == "以连续拱券组织空间序列"
        assert production["category"] == "酒店民宿,度假酒店"
        assert production["tags"] == ["酒店", "大堂", "石材", "灯光"]
        assert production["trait"] == "3D数字效果图"
        assert production["reason"] == ["是截图"]
        assert production["image_defects"] == "有水印"
        assert row.raw_response_a == raw_before
        history = json.loads(row.correction_history_json)
        assert [item["node_path"] for item in history] == [
            "call_a.title",
            "call_a.seotitle",
            "call_a.style",
            "call_a.cons",
            "call_a.design",
            "call_a.category",
            "call_a.tags",
            "call_a.trait",
            "call_a.reason",
            "call_a.image_defects",
        ]
        assert history[-1]["node_type"] == "call_a_field"
        assert history[-1]["old_value"] == ""
        assert history[-1]["new_value"] == "有水印"
        assert history[-1]["corrector"] == "审核员"
        assert history[-1]["reason"] == "人工核对image_defects"
        assert history[-1]["corrected_at"].endswith("Z")
        assert history[-1]["downstream_recomputed"] is False
    engine.dispose()


@pytest.mark.parametrize(
    ("score", "expected_grade"),
    [(100, "L1"), (81, "L1"), (80, "L2"), (61, "L2"), (60, "L3"),
     (41, "L3"), (40, "L4"), (21, "L4"), (20, "L5"), (0, "L5")],
)
def test_call_a_score_recomputes_grade(score: int, expected_grade: str) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    result_id = _result_for_call_a(sessions)
    with sessions() as db:
        row = db.get(EvaluationResult, result_id)
        assert row is not None
        response = apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key=f"call-a-score-{score}",
                node_type="call_a_field",
                node_path="call_a.score",
                old_value=70,
                new_value=score,
                evidence=[],
                reason="人工校准综合评分",
            ),
            corrector="审核员",
        )
        db.commit()
        assert response["score"] == score
        assert response["level"] == expected_grade
        assert row.score == score
        assert row.level == expected_grade
        assert json.loads(row.precheck_json)["production_fields"]["score"] == score
        scoring = json.loads(row.scoring_json)
        assert scoring["score"] == score
        assert scoring["level"] == expected_grade
        assert scoring["manual_call_a_score"] == score
        history = json.loads(row.correction_history_json)
        assert history[-1]["downstream_recomputed"] is True
    engine.dispose()


def test_call_a_grade_is_manual_override_and_keeps_score() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    result_id = _result_for_call_a(sessions)
    with sessions() as db:
        row = db.get(EvaluationResult, result_id)
        assert row is not None
        response = apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key="call-a-grade-manual",
                node_type="call_a_field",
                node_path="call_a.grade",
                old_value="L2",
                new_value="L4",
                evidence=[],
                reason="人工等级结论优先",
            ),
            corrector="审核员",
        )
        db.commit()
        assert response["score"] == 70
        assert response["level"] == "L4"
        assert row.score == 70
        assert row.level == "L4"
        scoring = json.loads(row.scoring_json)
        assert scoring["manual_call_a_grade"] == "L4"
        history = json.loads(row.correction_history_json)
        assert history[-1]["downstream_recomputed"] is False
    engine.dispose()


def test_call_a_manual_score_and_grade_survive_later_node_replay() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    result_id = _result_for_call_a(sessions)
    with sessions() as db:
        row = db.get(EvaluationResult, result_id)
        assert row is not None

        def correct(key: str, node_type: str, node_path: str, old: object, new: object):
            return apply_node_correction(
                db,
                result=row,
                payload=CorrectNodeRequest(
                    correction_key=key,
                    node_type=node_type,
                    node_path=node_path,
                    old_value=old,
                    new_value=new,
                    evidence=[],
                    reason="连续纠偏一致性测试",
                ),
                corrector="审核员",
            )

        correct("manual-score", "call_a_field", "call_a.score", 70, 82)
        replay_after_score = correct(
            "replay-after-score",
            "precheck_field",
            "precheck.production_fields.trait",
            "实景照片",
            "AI图",
        )
        assert replay_after_score["score"] == 82
        assert replay_after_score["level"] == "L1"

        correct("manual-grade", "call_a_field", "call_a.grade", "L1", "L4")
        replay_after_grade = correct(
            "replay-after-grade",
            "precheck_field",
            "precheck.production_fields.trait",
            "AI图",
            "其它",
        )
        assert replay_after_grade["score"] == 82
        assert replay_after_grade["level"] == "L4"
        scoring = json.loads(row.scoring_json)
        assert scoring["manual_call_a_score"] == 82
        assert scoring["manual_call_a_grade"] == "L4"
    engine.dispose()


def test_call_a_missing_legacy_field_fails_safely() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    result_id = _result_for_call_a(sessions)
    with sessions() as db:
        row = db.get(EvaluationResult, result_id)
        assert row is not None
        precheck = json.loads(row.precheck_json)
        del precheck["production_fields"]["design"]
        row.precheck_json = json.dumps(precheck, ensure_ascii=False)
        with pytest.raises(HTTPException) as caught:
            apply_node_correction(
                db,
                result=row,
                payload=CorrectNodeRequest(
                    node_type="call_a_field",
                    node_path="call_a.design",
                    old_value=None,
                    new_value="补录设计理念",
                    evidence=[],
                    reason="旧记录补录尝试",
                ),
                corrector="审核员",
            )
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "call_a_field_missing"
    engine.dispose()
