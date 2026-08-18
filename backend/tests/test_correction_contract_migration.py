from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect

from app.correction_contract import (
    correction_contract_from_run_snapshot,
    freeze_correction_contract,
)
from app.migrations.runner import MIGRATIONS


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
