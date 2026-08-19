from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.correction_contract import correction_contract_hash
from app.correction_view import (
    CorrectionViewError,
    build_correction_nodes,
    build_correction_view,
    submit_correction_nodes,
)
from app.database import Base, get_db
from app.dimension_deduction_bridge import empty_deduction_output
from app.evaluation_v3_pipeline import recompute_qualified_v3
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)
from app.main import app, current_user
from app.models import (
    BaselineRegressionItem,
    BaselineRegressionRun,
    EvaluationResult,
    SampleSet,
    SampleSetItem,
)
from app.worker_v3_authoritative import build_v3_authoritative_scoring


def _node(
    node_key: str,
    *,
    layer: str = "A",
    path: str | None = None,
    node_type: str = "text",
    semantic_version: str = "1",
    compatibility_key: str | None = None,
    required_evidence: bool = False,
) -> dict:
    node = {
        "node_key": node_key,
        "layer": layer,
        "path": path or node_key,
        "order": 1,
        "label": f"节点{node_key}",
        "description": f"用于纠正节点{node_key}的人工判断",
        "type": node_type,
        "semantic_version": semantic_version,
        "compatibility_key": compatibility_key or node_key,
        "required": True,
        "evidence": {
            "description": f"请提供节点{node_key}的图片证据",
            "required": required_evidence,
        },
    }
    if node_type == "enum":
        node["options"] = ["L1", "L2", "L3", "L4", "L5"]
    if layer == "V3":
        node["recompute_ref"] = "evaluation_v3_pipeline.recompute_qualified_v3"
        node["steps"] = ["读取冻结规则", "服务端重新计算", "返回最终等级"]
    return node


def _contract(*nodes: dict, version: str = "2") -> dict:
    contract = {
        "contract_version": version,
        "category_key": "inspiration_image",
        "nodes": list(nodes),
    }
    contract["contract_hash"] = correction_contract_hash(contract)
    return contract


def test_build_nodes_inherits_only_compatible_values_and_omits_deleted_nodes() -> None:
    stable = _node("call_a.title", path="call_a.title")
    changed = _node(
        "call_b.composition",
        layer="B",
        path="dimension.composition.hit_rules",
        node_type="list",
        semantic_version="2",
    )
    added = _node("call_a.style", path="call_a.style")
    final = _node(
        "v3.final_level",
        layer="V3",
        path="final_level",
        node_type="enum",
    )
    final["metadata"] = {
        "expression": "score >= hidden_threshold",
        "ui": {"hint": "只读说明", "code": "return hidden_rule"},
    }
    previous_values = {
        "call_a.title": {
            **stable,
            "human_value": "现代住宅",
            "reason": "主体明确",
            "evidence": [{"text": "画面主体为住宅"}],
        },
        "call_b.composition": {
            **changed,
            "semantic_version": "1",
            "human_value": ["rule-old"],
        },
        "deleted.node": {
            **_node("deleted.node"),
            "human_value": "旧值",
        },
    }

    nodes = build_correction_nodes(
        _contract(stable, changed, added, final),
        model_values={
            "call_a.title": "模型标题",
            "call_a.style": "模型风格",
            "v3.final_level": "L2",
        },
        human_values={},
        previous_values=previous_values,
    )

    by_key = {node["node_key"]: node for node in nodes}
    assert set(by_key) == {
        "call_a.title",
        "call_b.composition",
        "call_a.style",
        "v3.final_level",
    }
    assert by_key["call_a.title"]["human_value"] == "现代住宅"
    assert by_key["call_a.title"]["inheritance"]["status"] == "inherited"
    assert by_key["call_b.composition"]["inheritance"]["status"] == "changed"
    assert "human_value" not in by_key["call_b.composition"]
    assert by_key["call_a.style"]["inheritance"]["status"] == "new"
    assert by_key["v3.final_level"]["steps"] == [
        "读取冻结规则",
        "服务端重新计算",
        "返回最终等级",
    ]
    assert "rule" not in by_key["v3.final_level"]
    assert "expression" not in by_key["v3.final_level"]["metadata"]
    assert by_key["v3.final_level"]["metadata"]["ui"] == {
        "hint": "只读说明"
    }


def test_read_only_nodes_use_frozen_value_and_cannot_be_submitted() -> None:
    threshold_node = _node(
        "v3.level_thresholds",
        layer="V3",
        path="scoring.level_thresholds",
        node_type="list",
    )
    threshold_node["metadata"] = {
        "editable": False,
        "frozen_value": [
            {"min_score": 90, "level": "L1"},
            {"min_score": 75, "level": "L2"},
        ],
    }
    contract = _contract(threshold_node)
    nodes = build_correction_nodes(
        contract,
        model_values={},
        current_values={},
        human_values={},
        previous_values={},
    )

    assert nodes[0]["model_value"] == threshold_node["metadata"]["frozen_value"]
    assert nodes[0]["current_value"] == threshold_node["metadata"]["frozen_value"]
    assert nodes[0]["editable"] is False

    db, run, item, _ = _submission_fixture()
    run.correction_contract_json = json.dumps(contract, ensure_ascii=False)
    run.correction_contract_hash = contract["contract_hash"]
    with pytest.raises(CorrectionViewError) as exc_info:
        submit_correction_nodes(
            db,
            run=run,
            item=item,
            contract_hash=contract["contract_hash"],
            nodes=[
                {
                    "node_key": "v3.level_thresholds",
                    "human_value": threshold_node["metadata"]["frozen_value"],
                    "reason": "阈值需要修改",
                    "evidence": [{"text": "人工判断"}],
                }
            ],
            review_revision=3,
            idempotency_key="read-only-threshold-0001",
            actor="运营乙",
        )
    assert exc_info.value.code == "CORRECTION_NODE_READ_ONLY"
    db.close()


def test_build_view_reads_only_the_run_frozen_contract() -> None:
    old_node = _node("call_a.title", path="call_a.title")
    old_contract = _contract(old_node, version="1")
    run = SimpleNamespace(
        id=17,
        category_key="inspiration_image",
        correction_contract_json=json.dumps(old_contract, ensure_ascii=False),
        correction_contract_hash=old_contract["contract_hash"],
        execution_snapshot_json=json.dumps(
            {
                "correction_contract": old_contract,
                "active_contract_that_must_not_be_read": _contract(
                    _node("new.active.node")
                ),
            },
            ensure_ascii=False,
        ),
    )
    item = SimpleNamespace(
        id=23,
        run_id=17,
        asset_id=101,
        evaluation_id=None,
        evaluation=None,
        result_snapshot_json=json.dumps(
            {"stage_a": {"production_fields": {"title": "冻结标题"}}},
            ensure_ascii=False,
        ),
    )

    view = build_correction_view(None, run=run, item=item)

    assert view["contract"]["contract_hash"] == old_contract["contract_hash"]
    assert view["snapshot_status"] == "frozen"
    assert [node["node_key"] for node in view["nodes"]] == ["call_a.title"]
    assert view["nodes"][0]["model_value"] == "冻结标题"


def test_build_view_reads_call_b_model_value_from_frozen_result_snapshot() -> None:
    rule_node = _node(
        "call_b.composition.rule_1",
        layer="B",
        path="dimension.composition.hit_rules.rule_1",
        node_type="rule_hit",
    )
    contract = _contract(rule_node)
    model_hit = {
        "rule_id": "rule_1",
        "confidence": "medium",
        "evidence": "模型识别到主体偏移",
    }
    run = SimpleNamespace(
        id=17,
        category_key="inspiration_image",
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        execution_snapshot_json="{}",
    )
    item = SimpleNamespace(
        id=23,
        run_id=17,
        asset_id=101,
        evaluation_id=None,
        evaluation=None,
        result_snapshot_json=json.dumps(
            {
                "stage_b": {
                    "dimensions": {
                        "composition": {"hit_rules": [model_hit]}
                    }
                },
                "scoring": {"level": "L2", "score": 70},
            },
            ensure_ascii=False,
        ),
    )

    view = build_correction_view(None, run=run, item=item)

    assert view["nodes"][0]["model_value"] == model_hit


def _submission_fixture() -> tuple[Session, SimpleNamespace, SimpleNamespace, dict]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    result = EvaluationResult(
        asset_id=101,
        job_id=201,
        precheck_json=json.dumps(
            {"production_fields": {"title": "模型标题"}}, ensure_ascii=False
        ),
        aesthetic_json="{}",
        scoring_json=json.dumps({"score": 70, "level": "L2"}),
        correction_history_json=json.dumps(
            [
                {
                    "correction_key": "older-event",
                    "node_type": "call_a_field",
                    "node_path": "call_a.title",
                    "old_value": "更早标题",
                    "new_value": "模型标题",
                    "evidence": [],
                    "reason": "既有历史",
                    "corrector": "运营甲",
                    "corrected_at": "2026-08-18T00:00:00+00:00",
                    "downstream_recomputed": False,
                }
            ],
            ensure_ascii=False,
        ),
        raw_response_a='{"immutable":"provider response"}',
        raw_response_b=None,
        score=70,
        level="L2",
        confidence=0.8,
        needs_review=False,
        review_revision=3,
        model_id="fake-model",
        prompt_a_version="a-v1",
        prompt_b_version="b-v1",
        rubric_version="r-v1",
        engine_version="e-v1",
    )
    db.add(result)
    db.flush()
    contract_node = _node(
        "call_a.title",
        path="call_a.title",
        required_evidence=True,
    )
    contract_node["metadata"] = {"node_type": "call_a_field"}
    contract = _contract(contract_node)
    run = SimpleNamespace(
        id=17,
        category_key="inspiration_image",
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        execution_snapshot_json="{}",
    )
    item = SimpleNamespace(
        id=23,
        run_id=17,
        asset_id=101,
        evaluation_id=result.id,
        evaluation=result,
        result_snapshot_json=json.dumps(
            {"stage_a": {"production_fields": {"title": "模型标题"}}},
            ensure_ascii=False,
        ),
    )
    return db, run, item, contract


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"contract_hash": "0" * 64}, "CORRECTION_CONTRACT_STALE"),
        (
            {
                "nodes": [
                    {
                        "node_key": "outside.contract",
                        "human_value": "值",
                        "reason": "理由",
                        "evidence": [{"text": "证据"}],
                    }
                ]
            },
            "CORRECTION_NODE_UNKNOWN",
        ),
        ({"review_revision": 2}, "CORRECTION_REVIEW_STALE"),
        (
            {
                "nodes": [
                    {
                        "node_key": "call_a.title",
                        "human_value": "新标题",
                        "reason": "理由",
                        "evidence": [],
                    }
                ]
            },
            "CORRECTION_EVIDENCE_REQUIRED",
        ),
    ],
)
def test_submit_nodes_fails_closed(override: dict, code: str) -> None:
    db, run, item, contract = _submission_fixture()
    request = {
        "contract_hash": contract["contract_hash"],
        "nodes": [
            {
                "node_key": "call_a.title",
                "human_value": "新标题",
                "reason": "主体识别错误",
                "evidence": [{"text": "主体是现代住宅"}],
            }
        ],
        "review_revision": 3,
        "idempotency_key": "submission-0001",
        **override,
    }

    with pytest.raises(CorrectionViewError) as exc_info:
        submit_correction_nodes(db, run=run, item=item, actor="运营乙", **request)

    assert exc_info.value.code == code
    assert item.evaluation.review_revision == 3
    assert len(json.loads(item.evaluation.correction_history_json)) == 1
    db.close()


def test_submit_rejects_invalid_enum_value() -> None:
    db, run, item, _contract_value = _submission_fixture()
    enum_node = _node(
        "call_a.grade",
        path="call_a.grade",
        node_type="enum",
        required_evidence=True,
    )
    enum_node["metadata"] = {"node_type": "call_a_field"}
    contract = _contract(enum_node)
    run.correction_contract_json = json.dumps(contract, ensure_ascii=False)
    run.correction_contract_hash = contract["contract_hash"]

    with pytest.raises(CorrectionViewError) as exc_info:
        submit_correction_nodes(
            db,
            run=run,
            item=item,
            contract_hash=contract["contract_hash"],
            nodes=[
                {
                    "node_key": "call_a.grade",
                    "human_value": "L9",
                    "reason": "等级需要修正",
                    "evidence": [{"text": "不符合当前等级"}],
                }
            ],
            review_revision=3,
            idempotency_key="submission-enum",
            actor="运营乙",
        )

    assert exc_info.value.code == "CORRECTION_NODE_VALUE_INVALID"
    db.close()


def test_valid_submission_appends_history_preserves_raw_response_and_is_idempotent() -> None:
    db, run, item, contract = _submission_fixture()
    raw_before = item.evaluation.raw_response_a
    old_history = deepcopy(json.loads(item.evaluation.correction_history_json))
    request = {
        "contract_hash": contract["contract_hash"],
        "nodes": [
            {
                "node_key": "call_a.title",
                "human_value": "现代住宅",
                "reason": "主体是住宅而不是泛空间",
                "evidence": [{"text": "画面中央为现代住宅立面"}],
            }
        ],
        "review_revision": 3,
        "idempotency_key": "submission-valid-0001",
        "actor": "运营乙",
    }

    view = submit_correction_nodes(db, run=run, item=item, **request)

    history = json.loads(item.evaluation.correction_history_json)
    assert history[:1] == old_history
    assert len(history) == 2
    assert history[-1]["node_key"] == "call_a.title"
    assert history[-1]["contract_hash"] == contract["contract_hash"]
    assert history[-1]["idempotency_key"] == "submission-valid-0001"
    assert item.evaluation.raw_response_a == raw_before
    assert item.evaluation.review_revision == 4
    assert view["review_revision"] == 4
    assert view["nodes"][0]["human_value"] == "现代住宅"

    replay = submit_correction_nodes(
        db,
        run=run,
        item=item,
        review_revision=3,
        **{key: value for key, value in request.items() if key != "review_revision"},
    )

    assert replay["idempotent_replay"] is True
    assert len(json.loads(item.evaluation.correction_history_json)) == 2
    db.close()


def test_baseline_final_level_correction_becomes_next_run_human_truth() -> None:
    db, run, item, _contract_value = _submission_fixture()
    final_node = _node(
        "v3.final_level",
        layer="V3",
        path="scoring.level",
        node_type="enum",
        required_evidence=True,
    )
    final_node["metadata"] = {"node_type": "final_level"}
    contract = _contract(final_node)
    run.baseline_set_id = 88
    run.correction_contract_json = json.dumps(contract, ensure_ascii=False)
    run.correction_contract_hash = contract["contract_hash"]
    item.evaluation.scoring_json = json.dumps(
        {"score": 70, "level": "L2", "v3_context": {}},
        ensure_ascii=False,
    )

    submit_correction_nodes(
        db,
        run=run,
        item=item,
        contract_hash=contract["contract_hash"],
        nodes=[
            {
                "node_key": "v3.final_level",
                "human_value": "L1",
                "reason": "人工确认应升档",
                "evidence": [{"text": "整体完成度达到推荐档"}],
            }
        ],
        review_revision=3,
        idempotency_key="baseline-final-level-0001",
        actor="运营乙",
    )

    golden = db.scalar(
        select(SampleSet).where(
            SampleSet.name == "系统黄金集·inspiration_image"
        )
    )
    assert golden is not None
    truth = db.scalar(
        select(SampleSetItem).where(
            SampleSetItem.sample_set_id == golden.id,
            SampleSetItem.asset_id == item.asset_id,
        )
    )
    assert truth is not None
    assert truth.expected_level == "L1"
    assert json.loads(truth.truth_json)["corrected_level"] == "L1"
    db.close()


def test_reused_idempotency_key_with_different_payload_is_rejected() -> None:
    db, run, item, contract = _submission_fixture()
    original = {
        "contract_hash": contract["contract_hash"],
        "nodes": [
            {
                "node_key": "call_a.title",
                "human_value": "现代住宅",
                "reason": "主体是住宅",
                "evidence": [{"text": "住宅立面"}],
            }
        ],
        "review_revision": 3,
        "idempotency_key": "submission-conflict",
        "actor": "运营乙",
    }
    submit_correction_nodes(db, run=run, item=item, **original)
    changed = deepcopy(original)
    changed["nodes"][0]["human_value"] = "办公空间"

    with pytest.raises(CorrectionViewError) as exc_info:
        submit_correction_nodes(db, run=run, item=item, **changed)

    assert exc_info.value.code == "CORRECTION_IDEMPOTENCY_CONFLICT"
    db.close()


def test_baseline_correction_view_routes_return_frozen_view_and_stable_errors() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    result = EvaluationResult(
        asset_id=101,
        job_id=201,
        precheck_json=json.dumps(
            {"production_fields": {"title": "模型标题"}}, ensure_ascii=False
        ),
        aesthetic_json="{}",
        scoring_json=json.dumps({"score": 70, "level": "L2"}),
        correction_history_json="[]",
        raw_response_a='{"immutable":true}',
        score=70,
        level="L2",
        confidence=0.8,
        needs_review=False,
        review_revision=0,
        model_id="fake-model",
        prompt_a_version="a-v1",
        prompt_b_version="b-v1",
        rubric_version="r-v1",
        engine_version="e-v1",
    )
    db.add(result)
    db.flush()
    node = _node("call_a.title", path="call_a.title", required_evidence=True)
    node["metadata"] = {"node_type": "call_a_field"}
    contract = _contract(node)
    run = BaselineRegressionRun(
        baseline_set_id=501,
        sequence_no=1,
        strategy_bundle_id=601,
        category_key="inspiration_image",
        strategy_snapshot_json="{}",
        execution_snapshot_json=json.dumps(
            {"correction_contract": contract}, ensure_ascii=False
        ),
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        baseline_set_fingerprint="f" * 64,
        status="completed",
        total=1,
        completed=1,
        valid_predictions=1,
        failed=0,
        metrics_json="{}",
        created_by="test",
    )
    db.add(run)
    db.flush()
    item = BaselineRegressionItem(
        run_id=run.id,
        baseline_set_item_id=701,
        asset_id=101,
        expected_level="L1",
        evaluation_id=result.id,
        status="completed",
        result_snapshot_json=json.dumps(
            {
                "predicted_level": "L2",
                "stage_a": {"production_fields": {"title": "模型标题"}},
            },
            ensure_ascii=False,
        ),
    )
    db.add(item)
    db.commit()
    user = SimpleNamespace(username="运营乙", is_admin=True, role="admin")
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[current_user] = lambda: user
    client = TestClient(app)
    try:
        view_response = client.get(
            f"/api/baseline-regressions/{run.id}/items/{item.id}/correction-view"
        )
        assert view_response.status_code == 200
        assert view_response.json()["contract"]["contract_hash"] == contract[
            "contract_hash"
        ]

        stale = client.post(
            f"/api/baseline-regressions/{run.id}/items/{item.id}/corrections",
            json={
                "contract_hash": "0" * 64,
                "review_revision": 0,
                "idempotency_key": "api-stale-0001",
                "nodes": [
                    {
                        "node_key": "call_a.title",
                        "human_value": "现代住宅",
                        "reason": "主体识别错误",
                        "evidence": [{"text": "主体为住宅"}],
                    }
                ],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "CORRECTION_CONTRACT_STALE"

        saved = client.post(
            f"/api/baseline-regressions/{run.id}/items/{item.id}/corrections",
            json={
                "contract_hash": contract["contract_hash"],
                "review_revision": 0,
                "idempotency_key": "api-valid-0001",
                "nodes": [
                    {
                        "node_key": "call_a.title",
                        "human_value": "现代住宅",
                        "reason": "主体识别错误",
                        "evidence": [{"text": "主体为住宅"}],
                    }
                ],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["review_revision"] == 1
        assert saved.json()["nodes"][0]["human_value"] == "现代住宅"
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_v3_rule_submission_uses_authoritative_recompute_and_preserves_raw_payload() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    context = {
        "contract": build_inspiration_v3_contract(),
        "classification_map": build_inspiration_classification_map(),
        "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        "config_revision": 3,
    }
    precheck = {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": "建筑设计",
            "primary_confidence": 0.96,
        },
        "production_fields": {"reason": [], "trait": "实景照片"},
    }
    config = context["subcategory_dimensions"]["class_one"]
    dimension_output = empty_deduction_output(config)
    aggregate = recompute_qualified_v3(
        v3_context=context,
        precheck=precheck,
        dimension_output=dimension_output,
    )
    scoring = build_v3_authoritative_scoring(aggregate, precheck=precheck)
    scoring.pop("_dimension_deduction_raw_payload", None)
    scoring["v3_context"] = context
    first_dimension = config["common_group"]["schema_definition"]["dimensions"][0]
    first_rule = first_dimension["deduction_rules"][0]
    node_key = f"v3.{first_dimension['key']}.{first_rule['rule_id']}"
    path = (
        f"dimension.{first_dimension['key']}.hit_rules.{first_rule['rule_id']}"
    )
    contract_node = _node(
        node_key,
        layer="V3",
        path=path,
        node_type="rule_hit",
        required_evidence=True,
    )
    contract_node["metadata"] = {"node_type": "dimension_rule"}
    contract = _contract(contract_node)
    result = EvaluationResult(
        asset_id=101,
        job_id=201,
        precheck_json=json.dumps(precheck, ensure_ascii=False),
        aesthetic_json=json.dumps(dimension_output, ensure_ascii=False),
        scoring_json=json.dumps(scoring, ensure_ascii=False),
        correction_history_json="[]",
        raw_response_a='{"immutable":"provider payload"}',
        raw_response_b='{"immutable":"dimension payload"}',
        score=scoring["score"],
        level=scoring["level"],
        confidence=scoring["confidence"],
        needs_review=False,
        review_revision=0,
        model_id="fake-model",
        prompt_a_version="a-v1",
        prompt_b_version="b-v1",
        rubric_version="r-v1",
        engine_version="e-v1",
    )
    db.add(result)
    db.flush()
    run = SimpleNamespace(
        id=17,
        category_key="inspiration_image",
        correction_contract_json=json.dumps(contract, ensure_ascii=False),
        correction_contract_hash=contract["contract_hash"],
        execution_snapshot_json="{}",
    )
    item = SimpleNamespace(
        id=23,
        run_id=17,
        asset_id=101,
        evaluation_id=result.id,
        evaluation=result,
        result_snapshot_json=json.dumps(
            {
                "predicted_level": result.level,
                "authoritative_score": result.score,
                "stage_a": precheck,
            },
            ensure_ascii=False,
        ),
    )
    new_hit = {
        "rule_id": first_rule["rule_id"],
        "confidence": "medium",
        "evidence": "主体明显偏移，左侧大面积空置",
    }
    expected_dimension_output = deepcopy(dimension_output)
    expected_dimension_output["dimensions"][first_dimension["key"]][
        "hit_rules"
    ] = [new_hit]
    expected_aggregate = recompute_qualified_v3(
        v3_context=context,
        precheck=deepcopy(precheck),
        dimension_output=expected_dimension_output,
    )
    expected_scoring = build_v3_authoritative_scoring(
        expected_aggregate, precheck=precheck
    )
    raw_a_before = result.raw_response_a
    raw_b_before = result.raw_response_b

    view = submit_correction_nodes(
        db,
        run=run,
        item=item,
        contract_hash=contract["contract_hash"],
        nodes=[
            {
                "node_key": node_key,
                "human_value": new_hit,
                "reason": "人工确认该扣分规则命中",
                "evidence": [
                    {
                        "rule_id": first_rule["rule_id"],
                        "new_confidence": "medium",
                        "new_evidence": new_hit["evidence"],
                    }
                ],
            }
        ],
        review_revision=0,
        idempotency_key="v3-recompute-0001",
        actor="运营乙",
    )

    assert result.score == expected_scoring["score"]
    assert result.level == expected_scoring["level"]
    assert result.raw_response_a == raw_a_before
    assert result.raw_response_b == raw_b_before
    assert view["nodes"][0]["current_value"] == new_hit
    assert view["nodes"][0]["human_value"] == new_hit
    db.close()
    engine.dispose()
