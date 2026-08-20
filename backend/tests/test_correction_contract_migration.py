from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect

from app.correction_contract import (
    assert_correction_contract_complete,
    correction_contract_from_run_snapshot,
    freeze_contract_from_execution_snapshot,
    freeze_correction_contract,
)
from app.migrations.runner import MIGRATIONS
from app.inspiration_category_seed import (
    build_inspiration_subcategory_dimensions,
    build_inspiration_v3_contract,
)


def test_migration_75_adds_nullable_contract_columns_idempotently(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'correction-contract-v75.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE baseline_regression_runs (id INTEGER PRIMARY KEY, execution_snapshot_json TEXT NOT NULL DEFAULT '{}')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE evaluation_production_runs (id INTEGER PRIMARY KEY, category_profile_snapshot_json TEXT NOT NULL DEFAULT '{}')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE prompt_regression_runs (id INTEGER PRIMARY KEY, baseline_strategy_snapshot_json TEXT NOT NULL DEFAULT '{}', candidate_strategy_snapshot_json TEXT NOT NULL DEFAULT '{}')"
            )
            connection.exec_driver_sql(
                "INSERT INTO baseline_regression_runs (id, execution_snapshot_json) VALUES (1, '{\"legacy\":true}')"
            )
            migration = next(item for item in MIGRATIONS if item.version == 75)
            migration.up(connection)
            migration.up(connection)

            for table in (
                "baseline_regression_runs",
                "evaluation_production_runs",
                "prompt_regression_runs",
            ):
                columns = {column["name"] for column in inspect(connection).get_columns(table)}
                assert {"correction_contract_json", "correction_contract_hash"} <= columns
            assert connection.exec_driver_sql(
                "SELECT execution_snapshot_json FROM baseline_regression_runs WHERE id=1"
            ).scalar_one() == '{"legacy":true}'
    finally:
        engine.dispose()


def test_freeze_contract_is_canonical_and_snapshot_lookup_is_immutable() -> None:
    contract = freeze_correction_contract(
        category_key="inspiration_image",
        prompt_snapshot={"a": "提示词A"},
        dimension_snapshot={"dimensions": [{"key": "composition"}]},
        production_field_snapshot={"fields": [{"key": "title"}]},
        v3_snapshot={
            "nodes": [
                {
                    "node_key": "v3.final",
                    "layer": "V3",
                    "path": "final_level",
                    "order": 1,
                    "label": "最终等级",
                    "description": "根据冻结规则确定最终等级",
                    "type": "enum",
                    "options": ["L1", "L2", "L3", "L4", "L5"],
                    "semantic_version": "1",
                    "compatibility_key": "final-level",
                    "required": True,
                    "evidence": {"description": "需要等级判断证据"},
                    "recompute_ref": "scoring.final_level",
                }
            ]
        },
    )

    assert contract["contract_hash"]
    assert correction_contract_from_run_snapshot(contract) == contract
    snapshot = json.loads(json.dumps(contract, ensure_ascii=False))
    snapshot["nodes"].append({"node_key": "new"})
    assert correction_contract_from_run_snapshot(contract)["nodes"] != snapshot["nodes"]


def test_execution_snapshot_derives_complete_a_b_and_v3_correction_nodes() -> None:
    snapshot = {
        "pipeline_config": {
            "production_fields": {
                "fields": [
                    {
                        "key": "title",
                        "label": "素材标题",
                        "description": "用于检索与推荐的素材标题",
                        "type": "text",
                        "required": True,
                    },
                    {
                        "key": "tags",
                        "label": "素材标签",
                        "description": "用于检索与推荐的素材标签",
                        "type": "list",
                        "required": True,
                    },
                ]
            }
        },
        "dimension_contract": {
            "definition": {
                "dimensions": [
                    {
                        "key": "composition",
                        "label": "构图秩序",
                        "description": "判断主体、留白与层级关系",
                        "deduction_rules": [
                            {
                                "rule_id": "subject_offset",
                                "description": "主体明显偏移且留白失衡",
                            }
                        ],
                    }
                ]
            }
        },
        "v3_authoritative_bundle": {
            "contract": {
                "spec_version": "inspiration-v-test",
                "track_classification": {
                    "tracks": [
                        {"key": "architecture", "label": "建筑空间"},
                        {"key": "other", "label": "其它素材"},
                    ]
                },
                "level_thresholds": [
                    {"min_score": 90, "level": "L1"},
                    {"min_score": 75, "level": "L2"},
                    {"min_score": 60, "level": "L3"},
                    {"min_score": 0, "level": "L4"},
                ],
            },
            "subcategory_dimensions": {},
        },
    }

    contract = freeze_contract_from_execution_snapshot(
        category_key="inspiration_image",
        execution_snapshot=snapshot,
    )

    assert_correction_contract_complete(contract)
    by_key = {node["node_key"]: node for node in contract["nodes"]}
    assert {
        "call_a.title",
        "call_a.tags",
        "call_b.composition.subject_offset",
        "v3.track_key",
        "v3.level_thresholds",
        "v3.final_level",
    } <= set(by_key)
    assert by_key["call_b.composition.subject_offset"]["type"] == "rule_hit"
    assert by_key["v3.level_thresholds"]["metadata"]["editable"] is False
    assert by_key["v3.level_thresholds"]["metadata"]["frozen_value"] == [
        {"min_score": 90, "level": "L1"},
        {"min_score": 75, "level": "L2"},
        {"min_score": 60, "level": "L3"},
        {"min_score": 0, "level": "L4"},
    ]


def test_execution_snapshot_uses_compatibility_fields_and_v3_rule_nodes() -> None:
    v3_contract = build_inspiration_v3_contract()
    snapshot = {
        "pipeline_config": {},
        "dimension_contract": {"definition": {"dimensions": []}},
        "v3_authoritative_bundle": {
            "contract": v3_contract,
            "classification_map": {},
            "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        },
    }

    contract = freeze_contract_from_execution_snapshot(
        category_key="inspiration_image",
        execution_snapshot=snapshot,
    )

    assert_correction_contract_complete(contract)
    by_key = {node["node_key"]: node for node in contract["nodes"]}
    assert {
        "call_a.title",
        "call_a.tags",
        "call_a.reason",
        "call_a.trait",
        "v3.track_key",
        "v3.level_thresholds",
        "v3.final_level",
    } <= set(by_key)
    assert any(node["layer"] == "B" for node in contract["nodes"])
    assert all(
        node["node_key"].startswith("call_b.")
        and node["type"] == "rule_hit"
        for node in contract["nodes"]
        if node["layer"] == "B"
    )


def test_reason_correction_options_come_from_frozen_redline_contract() -> None:
    v3_contract = build_inspiration_v3_contract()
    v3_contract["redline_policy"]["rules"] = [
        {
            "key": "transparent_checkerboard",
            "signal": "production_fields.reason",
            "match_any": ["透明棋盘格", "透明棋盘格"],
            "exemptions": [],
            "enabled": True,
        },
        {
            "key": "hand_drawn_draft",
            "signal": "production_fields.reason",
            "match_any": ["手绘草稿"],
            "exemptions": [],
            "enabled": False,
        },
    ]
    snapshot = {
        "pipeline_config": {},
        "dimension_contract": {"definition": {"dimensions": []}},
        "v3_authoritative_bundle": {
            "contract": v3_contract,
            "classification_map": {},
            "subcategory_dimensions": build_inspiration_subcategory_dimensions(),
        },
    }

    contract = freeze_contract_from_execution_snapshot(
        category_key="inspiration_image",
        execution_snapshot=snapshot,
    )
    by_key = {node["node_key"]: node for node in contract["nodes"]}

    assert by_key["call_a.reason"]["options"] == ["透明棋盘格", "手绘草稿"]
