"""硬缺陷与调用B美感分必须是可纠偏节点。

这两项都是模型判断、都真实决定分数，但此前没有可提交的合同节点：
- 硬缺陷会压分或封顶（`hard_defects` -> `_apply_hard_defect_penalty`）；
- 美感分是等级撮合器的初始分（`aesthetic_score` -> `initial_score`）。

运营看到不同意的判断却改不了，只能绕道改别的字段，纠偏理由也就归错了因。
这批用例锁住三件事：节点存在且能过合同校验、写入后分数确定性重算、非法输入
fail-closed 被拒。

顺带钉住一个容易回退的实现细节：美感基座要求非空可见证据，人工纠偏时必须把
纠偏理由写成新分数的证据，否则重放会直接失败。
"""

from __future__ import annotations

import itertools
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.category_evaluation_aggregator import aggregate_category_evaluation
from app.correction_contract import (
    assert_correction_contract_complete,
    freeze_contract_from_execution_snapshot,
    validate_correction_contract,
    validate_node_value,
)
from app.database import Base
from app.dimension_deduction_bridge import (
    compose_rule_deductions,
    empty_deduction_output,
)
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.models import EvaluationResult
from app.node_correction_api import CorrectNodeRequest, apply_node_correction
from app.worker_v3_authoritative import build_v3_authoritative_scoring

MODEL_AESTHETIC_SCORE = 86
_job_ids = itertools.count(1)


def _v3_bundle() -> dict:
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
        "hard_defects": [],
    }


def _dimension_output(config: dict) -> dict:
    output = empty_deduction_output(config)
    output.update(
        {
            "aesthetic_score": MODEL_AESTHETIC_SCORE,
            "aesthetic_evidence": ["构图均衡，主体清晰"],
            "aesthetic_confidence": 0.9,
            "raw_payload": {"provider": "fake"},
        }
    )
    output.pop("warning", None)
    return output


def _contract() -> dict:
    v3 = _v3_bundle()
    return freeze_contract_from_execution_snapshot(
        category_key="inspiration_image",
        execution_snapshot={
            "rubric_version": "r1",
            "v3_authoritative_bundle": v3,
            "pipeline_config": {"production_fields": {}},
            "dimension_contract": {"definition": {}},
        },
    )


def _nodes_by_key(contract: dict) -> dict[str, dict]:
    return {str(node["node_key"]): node for node in contract["nodes"]}


@pytest.fixture()
def stored_result():
    """一条已落库的正常评测：86 分 / L2，带完整调用B输出。"""
    v3 = _v3_bundle()
    config = v3["subcategory_dimensions"]["class_one"]
    precheck = _precheck()
    dimension_output = _dimension_output(config)
    composed = compose_rule_deductions(
        config=config, dimension_output=dimension_output, require_foundation=True
    )
    aggregate = aggregate_category_evaluation(
        v3["contract"],
        precheck,
        composed,
        track_key="class_one",
        initial_score=composed.get("aesthetic_score"),
    )
    scoring = build_v3_authoritative_scoring(aggregate, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = v3

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        row = EvaluationResult(
            asset_id=1,
            job_id=next(_job_ids),
            precheck_json=json.dumps(precheck, ensure_ascii=False),
            aesthetic_json=json.dumps(dimension_output, ensure_ascii=False),
            scoring_json=json.dumps(scoring, ensure_ascii=False),
            correction_history_json="[]",
            raw_response_a="{}",
            score=scoring.get("score"),
            level=scoring.get("level"),
            confidence=scoring.get("confidence"),
            needs_review=False,
            model_id="fake",
            prompt_a_version="a",
            rubric_version="r",
            engine_version="e",
        )
        db.add(row)
        db.commit()
        yield db, row


def _current_aesthetic_score(row: EvaluationResult) -> int | None:
    return json.loads(row.aesthetic_json).get("aesthetic_score")


def test_contract_exposes_hard_defect_and_aesthetic_nodes() -> None:
    contract = _contract()
    nodes = _nodes_by_key(contract)

    assert not validate_correction_contract(contract)
    assert_correction_contract_complete(contract)

    hard_defects = nodes["call_a.hard_defects"]
    assert hard_defects["layer"] == "A", "硬缺陷是调用A的判断"
    assert hard_defects["path"] == "precheck.hard_defects"
    assert hard_defects["metadata"]["node_type"] == "precheck_field"

    aesthetic = nodes["call_b.aesthetic_score"]
    assert aesthetic["layer"] == "B", "美感分是调用B的判断"
    assert aesthetic["metadata"]["node_type"] == "aesthetic_score"
    assert (aesthetic["minimum"], aesthetic["maximum"]) == (0, 100)


def test_hard_defect_options_come_from_frozen_contract() -> None:
    """选项必须取自冻结合同，机制增删缺陷时前端无需改代码。"""
    contract = _contract()
    node = _nodes_by_key(contract)["call_a.hard_defects"]

    options = node["options"]
    labels = node["metadata"]["option_labels"]
    assert "blurry_grayish" in options
    assert labels["blurry_grayish"], "每个缺陷都要有中文说明供运营识别"
    assert "subject_obscuring_watermark" not in options, (
        "水印属于 image_defects，不应混进硬缺陷选项"
    )
    assert set(labels) == set(options)


@pytest.mark.parametrize(
    ("value", "accepted"),
    [(0, True), (60, True), (100, True), (200, False), (-1, False), ("60", False)],
)
def test_aesthetic_score_node_value_bounds(value: object, accepted: bool) -> None:
    node = _nodes_by_key(_contract())["call_b.aesthetic_score"]
    if accepted:
        validate_node_value(node, value)
        return
    with pytest.raises(Exception):
        validate_node_value(node, value)


def test_correcting_aesthetic_score_recomputes_downstream(stored_result) -> None:
    db, row = stored_result
    assert row.score == MODEL_AESTHETIC_SCORE

    apply_node_correction(
        db,
        result=row,
        payload=CorrectNodeRequest(
            correction_key="aesthetic-1",
            node_type="aesthetic_score",
            node_path="aesthetic.aesthetic_score",
            old_value=MODEL_AESTHETIC_SCORE,
            new_value=60,
            evidence=[],
            reason="人工判定构图松散，美感分应为60",
        ),
        corrector="operator",
    )
    db.commit()
    db.refresh(row)

    assert row.score == 60, "美感分是撮合器初始分，改了必须带动最终分数"
    assert row.level == "L3"

    stored = json.loads(row.aesthetic_json)
    assert stored["aesthetic_score"] == 60
    assert stored["manual_aesthetic_score"] is True
    assert stored["aesthetic_confidence"] == 1.0, "人工真值不应沿用模型置信度"
    assert stored["aesthetic_evidence"][0] == "人工判定构图松散，美感分应为60", (
        "美感基座要求非空证据，人工理由必须写成新分数的证据，否则重放会失败"
    )
    assert "构图均衡，主体清晰" in stored["aesthetic_evidence"], "模型原证据要保留供对照"


def test_correcting_hard_defects_caps_the_score(stored_result) -> None:
    db, row = stored_result
    before = row.score

    apply_node_correction(
        db,
        result=row,
        payload=CorrectNodeRequest(
            correction_key="defect-1",
            node_type="precheck_field",
            node_path="precheck.hard_defects",
            old_value=[],
            new_value=["blurry_grayish"],
            evidence=[],
            reason="画面整体发灰，暗部层次丢失",
        ),
        corrector="operator",
    )
    db.commit()
    db.refresh(row)

    assert row.score < before, "新增硬缺陷必须压分"
    assert json.loads(row.precheck_json)["hard_defects"] == ["blurry_grayish"]


def test_aesthetic_score_can_be_corrected_repeatedly_with_full_history(
    stored_result,
) -> None:
    """运营可以反复改同一项；每次都留痕，且需带上真实当前值。"""
    db, row = stored_result

    for value in (60, 75, 92):
        apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key=None,
                node_type="aesthetic_score",
                node_path="aesthetic.aesthetic_score",
                old_value=_current_aesthetic_score(row),
                new_value=value,
                evidence=[],
                reason=f"人工判定美感分为 {value}",
            ),
            corrector="operator",
        )
        db.commit()
        db.refresh(row)
        assert row.score == value

    history = json.loads(row.correction_history_json)
    assert len(history) == 3, "每次纠偏都必须追加可审计历史"
    assert row.level == "L1"


def test_stale_aesthetic_value_is_rejected(stored_result) -> None:
    """乐观并发校验：拿着过期的当前值提交必须被拒，避免覆盖他人纠偏。"""
    db, row = stored_result

    with pytest.raises(Exception) as excinfo:
        apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key=None,
                node_type="aesthetic_score",
                node_path="aesthetic.aesthetic_score",
                old_value=11,  # 与实际存储的 86 不一致
                new_value=60,
                evidence=[],
                reason="拿着过期值提交",
            ),
            corrector="operator",
        )
    assert "node_value_conflict" in str(getattr(excinfo.value, "detail", excinfo.value))


@pytest.mark.parametrize("bad_value", [200, -1, "60", True, 60.5])
def test_invalid_aesthetic_score_fails_closed(stored_result, bad_value) -> None:
    db, row = stored_result

    with pytest.raises(Exception) as excinfo:
        apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key=None,
                node_type="aesthetic_score",
                node_path="aesthetic.aesthetic_score",
                old_value=_current_aesthetic_score(row),
                new_value=bad_value,
                evidence=[],
                reason="非法输入",
            ),
            corrector="operator",
        )
    assert "aesthetic_score_invalid" in str(
        getattr(excinfo.value, "detail", excinfo.value)
    )


def test_wrong_aesthetic_node_path_is_rejected(stored_result) -> None:
    db, row = stored_result

    with pytest.raises(Exception) as excinfo:
        apply_node_correction(
            db,
            result=row,
            payload=CorrectNodeRequest(
                correction_key=None,
                node_type="aesthetic_score",
                node_path="aesthetic.wrong_field",
                old_value=_current_aesthetic_score(row),
                new_value=60,
                evidence=[],
                reason="错误路径",
            ),
            corrector="operator",
        )
    assert "node_path_invalid" in str(getattr(excinfo.value, "detail", excinfo.value))
