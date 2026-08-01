from __future__ import annotations

import json
import re
from typing import Callable

from sqlalchemy import Connection

from ..category_pipeline import legacy_preprocess_to_pipeline, pipeline_json


class Migration:
    def __init__(self, version: int, name: str, up: Callable[[Connection], None]) -> None:
        self.version = version
        self.name = name
        self.up = up


def _ensure_schema_migrations_table(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _probe_sqlite_json_functions(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    try:
        row = connection.exec_driver_sql(
            "SELECT json_valid('{}'), json_type('{}', '$'), json('{}')"
        ).first()
    except Exception as exc:
        raise RuntimeError(
            "SQLite JSON 函数不可用，无法安全执行版本化数据合同"
        ) from exc
    if row is None or row[0] != 1 or row[1] != "object":
        raise RuntimeError("SQLite JSON 函数探测失败")


def _migration_001_add_sample_expected_level(connection: Connection) -> None:
    migration_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(migration_items)")
    }
    if "sample_expected_level" not in migration_columns:
        connection.exec_driver_sql(
            "ALTER TABLE migration_items ADD COLUMN sample_expected_level VARCHAR(10)"
        )


def _migration_002_add_review_corrections(connection: Connection) -> None:
    review_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(human_reviews)")
    }
    if "corrections_json" not in review_columns:
        connection.exec_driver_sql(
            "ALTER TABLE human_reviews ADD COLUMN corrections_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "corrected_score" not in review_columns:
        connection.exec_driver_sql(
            "ALTER TABLE human_reviews ADD COLUMN corrected_score FLOAT"
        )


def _migration_003_add_evaluation_job_refs(connection: Connection) -> None:
    job_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")
    }
    if "prompt_a_id" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN prompt_a_id INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL"
        )
    if "prompt_b_id" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN prompt_b_id INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL"
        )
    if "regression_item_id" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN regression_item_id INTEGER REFERENCES prompt_regression_items(id) ON DELETE SET NULL"
        )


def _migration_004_add_evaluation_job_updated_at(connection: Connection) -> None:
    job_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")
    }
    if "updated_at" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN updated_at DATETIME"
        )
        connection.exec_driver_sql(
            "UPDATE evaluation_jobs SET updated_at = created_at WHERE updated_at IS NULL"
        )


def _migration_005_add_prompt_version_updated_at(connection: Connection) -> None:
    prompt_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(prompt_versions)")
    }
    if "updated_at" not in prompt_columns:
        connection.exec_driver_sql(
            "ALTER TABLE prompt_versions ADD COLUMN updated_at DATETIME"
        )
        connection.exec_driver_sql(
            "UPDATE prompt_versions SET updated_at = created_at WHERE updated_at IS NULL"
        )


def _migration_006_add_sample_set_kind_status(connection: Connection) -> None:
    sample_set_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(sample_sets)")
    }
    if "kind" not in sample_set_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sample_sets ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'test'"
        )
    if "status" not in sample_set_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sample_sets ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft'"
        )


def _migration_007_add_sample_item_truth_fields(connection: Connection) -> None:
    sample_item_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(sample_set_items)")
    }
    for column_name, definition in (
        ("truth_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("truth_revision", "INTEGER NOT NULL DEFAULT 1"),
        ("truth_updated_by", "VARCHAR(80)"),
        ("truth_updated_at", "DATETIME"),
    ):
        if column_name not in sample_item_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE sample_set_items ADD COLUMN {column_name} {definition}"
            )


def _migration_008_add_model_high_risk_review(connection: Connection) -> None:
    model_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(model_configs)")
    }
    if "high_risk_review_enabled" not in model_columns:
        connection.exec_driver_sql(
            "ALTER TABLE model_configs ADD COLUMN high_risk_review_enabled BOOLEAN NOT NULL DEFAULT 1"
        )


def _migration_009_add_result_risk_review_fields(connection: Connection) -> None:
    result_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_results)")
    }
    for column_name, definition in (
        ("raw_response_risk_review", "TEXT"),
        ("risk_review_json", "TEXT"),
        ("risk_review_version", "VARCHAR(40)"),
    ):
        if column_name not in result_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE evaluation_results ADD COLUMN {column_name} {definition}"
            )


def _migration_010_add_result_updated_at(connection: Connection) -> None:
    result_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_results)")
    }
    if "updated_at" not in result_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_results ADD COLUMN updated_at DATETIME"
        )
        connection.exec_driver_sql(
            "UPDATE evaluation_results SET updated_at = created_at WHERE updated_at IS NULL"
        )


def _migration_011_add_strategy_bundles(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "strategy_bundles" not in tables:
        connection.exec_driver_sql("""
            CREATE TABLE strategy_bundles (
                id INTEGER PRIMARY KEY,
                canonical_hash VARCHAR(64) NOT NULL UNIQUE,
                model_id VARCHAR(200) NOT NULL,
                model_config_snapshot TEXT NOT NULL,
                prompt_a_version VARCHAR(40) NOT NULL,
                prompt_b_version VARCHAR(40),
                rubric_version VARCHAR(40) NOT NULL,
                engine_version VARCHAR(40) NOT NULL,
                sampling_policy_revision INTEGER,
                risk_review_version VARCHAR(40),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_strategy_bundles_canonical_hash "
        "ON strategy_bundles(canonical_hash)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_model_id "
        "ON strategy_bundles(model_id)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_prompt_a_version "
        "ON strategy_bundles(prompt_a_version)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_prompt_b_version "
        "ON strategy_bundles(prompt_b_version)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_rubric_version "
        "ON strategy_bundles(rubric_version)",
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_engine_version "
        "ON strategy_bundles(engine_version)",
    ):
        connection.exec_driver_sql(statement)

    # SQLite cannot add a NOT NULL column to a populated table without a
    # backfill. Keep historical rows nullable, then make all post-v11 writes
    # strict with triggers. New ORM writes also perform structural validation.
    result_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_results)")
    }
    if "strategy_bundle_id" not in result_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_results ADD COLUMN strategy_bundle_id "
            "INTEGER REFERENCES strategy_bundles(id) ON DELETE RESTRICT"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_evaluation_results_strategy_bundle_id "
            "ON evaluation_results(strategy_bundle_id)"
        )
    if "strategy_snapshot_json" not in result_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_results ADD COLUMN strategy_snapshot_json TEXT"
        )

    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_strategy_bundles_no_update
        BEFORE UPDATE ON strategy_bundles
        BEGIN
            SELECT RAISE(ABORT, 'StrategyBundle is immutable');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_strategy_bundles_no_delete
        BEFORE DELETE ON strategy_bundles
        BEGIN
            SELECT RAISE(ABORT, 'StrategyBundle cannot be deleted');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_results_require_strategy_insert
        BEFORE INSERT ON evaluation_results
        WHEN NEW.strategy_bundle_id IS NULL
          OR NEW.strategy_snapshot_json IS NULL
          OR length(trim(NEW.strategy_snapshot_json)) < 3
          OR CASE
              WHEN json_valid(NEW.strategy_snapshot_json) = 0 THEN 1
              ELSE
                  json_type(NEW.strategy_snapshot_json, '$') <> 'object'
                  OR json_extract(NEW.strategy_snapshot_json, '$.bundle_id') IS NULL
                  OR length(
                      json_extract(NEW.strategy_snapshot_json, '$.canonical_hash')
                  ) <> 64
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.schema_version'
                  ) <> 'strategy-bundle-v1'
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.model_config'
                  ) <> 'object'
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.prompt_a'
                  ) <> 'object'
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.prompt_a.id'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.prompt_a.version'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.prompt_b'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.rubric_version'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.engine_version'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.sampling_policy'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.risk_review_version'
                  ) IS NULL
            END
          OR NOT EXISTS (
              SELECT 1
              FROM strategy_bundles AS bundle
              WHERE bundle.id = NEW.strategy_bundle_id
                AND bundle.id = json_extract(
                    NEW.strategy_snapshot_json, '$.bundle_id'
                )
                AND bundle.canonical_hash = json_extract(
                    NEW.strategy_snapshot_json, '$.canonical_hash'
                )
                AND bundle.model_id = json_extract(
                    NEW.strategy_snapshot_json, '$.model_id'
                )
                AND bundle.prompt_a_version = json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_a.version'
                )
                AND bundle.prompt_b_version IS json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_b.version'
                )
                AND bundle.rubric_version = json_extract(
                    NEW.strategy_snapshot_json, '$.rubric_version'
                )
                AND bundle.engine_version = json_extract(
                    NEW.strategy_snapshot_json, '$.engine_version'
                )
                AND bundle.sampling_policy_revision IS json_extract(
                    NEW.strategy_snapshot_json, '$.sampling_policy.revision'
                )
                AND bundle.risk_review_version IS json_extract(
                    NEW.strategy_snapshot_json, '$.risk_review_version'
                )
          )
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationResult strategy binding is required');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_results_require_strategy_update
        BEFORE UPDATE OF strategy_bundle_id, strategy_snapshot_json
        ON evaluation_results
        WHEN NEW.strategy_bundle_id IS NULL
          OR NEW.strategy_snapshot_json IS NULL
          OR length(trim(NEW.strategy_snapshot_json)) < 3
          OR CASE
              WHEN json_valid(NEW.strategy_snapshot_json) = 0 THEN 1
              ELSE
                  json_type(NEW.strategy_snapshot_json, '$') <> 'object'
                  OR json_extract(NEW.strategy_snapshot_json, '$.bundle_id') IS NULL
                  OR length(
                      json_extract(NEW.strategy_snapshot_json, '$.canonical_hash')
                  ) <> 64
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.schema_version'
                  ) <> 'strategy-bundle-v1'
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.model_config'
                  ) <> 'object'
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.prompt_a'
                  ) <> 'object'
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.prompt_a.id'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.prompt_a.version'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.prompt_b'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.rubric_version'
                  ) IS NULL
                  OR json_extract(
                      NEW.strategy_snapshot_json, '$.engine_version'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.sampling_policy'
                  ) IS NULL
                  OR json_type(
                      NEW.strategy_snapshot_json, '$.risk_review_version'
                  ) IS NULL
            END
          OR NOT EXISTS (
              SELECT 1
              FROM strategy_bundles AS bundle
              WHERE bundle.id = NEW.strategy_bundle_id
                AND bundle.id = json_extract(
                    NEW.strategy_snapshot_json, '$.bundle_id'
                )
                AND bundle.canonical_hash = json_extract(
                    NEW.strategy_snapshot_json, '$.canonical_hash'
                )
                AND bundle.model_id = json_extract(
                    NEW.strategy_snapshot_json, '$.model_id'
                )
                AND bundle.prompt_a_version = json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_a.version'
                )
                AND bundle.prompt_b_version IS json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_b.version'
                )
                AND bundle.rubric_version = json_extract(
                    NEW.strategy_snapshot_json, '$.rubric_version'
                )
                AND bundle.engine_version = json_extract(
                    NEW.strategy_snapshot_json, '$.engine_version'
                )
                AND bundle.sampling_policy_revision IS json_extract(
                    NEW.strategy_snapshot_json, '$.sampling_policy.revision'
                )
                AND bundle.risk_review_version IS json_extract(
                    NEW.strategy_snapshot_json, '$.risk_review_version'
                )
          )
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationResult strategy binding is required');
        END
    """)


def _migration_012_add_paired_strategy_regression(connection: Connection) -> None:
    run_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(prompt_regression_runs)"
        )
    }
    run_definitions = (
        ("regression_mode", "VARCHAR(30) NOT NULL DEFAULT 'single'"),
        ("baseline_strategy_bundle_id", "INTEGER"),
        ("candidate_strategy_bundle_id", "INTEGER"),
        ("baseline_strategy_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("candidate_strategy_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("sample_set_version", "VARCHAR(64)"),
        ("sample_manifest_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("metric_rules_version", "VARCHAR(80)"),
        ("metric_rules_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("summary_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("recommendation", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("approval_status", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("approved_by", "VARCHAR(80)"),
        ("approval_note", "TEXT NOT NULL DEFAULT ''"),
        ("approved_at", "DATETIME"),
    )
    for column_name, definition in run_definitions:
        if column_name not in run_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE prompt_regression_runs "
                f"ADD COLUMN {column_name} {definition}"
            )

    item_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(prompt_regression_items)"
        )
    }
    item_definitions = (
        ("sample_role", "VARCHAR(30)"),
        ("source_evaluation_id", "INTEGER"),
        ("source_review_id", "INTEGER"),
        ("truth_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("baseline_evaluation_id", "INTEGER"),
        ("candidate_evaluation_id", "INTEGER"),
        ("baseline_result_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("candidate_result_json", "TEXT NOT NULL DEFAULT '{}'"),
    )
    for column_name, definition in item_definitions:
        if column_name not in item_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE prompt_regression_items "
                f"ADD COLUMN {column_name} {definition}"
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_regression_mode "
        "ON prompt_regression_runs(regression_mode)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_baseline_strategy_bundle_id "
        "ON prompt_regression_runs(baseline_strategy_bundle_id)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_candidate_strategy_bundle_id "
        "ON prompt_regression_runs(candidate_strategy_bundle_id)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_sample_set_version "
        "ON prompt_regression_runs(sample_set_version)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_metric_rules_version "
        "ON prompt_regression_runs(metric_rules_version)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_recommendation "
        "ON prompt_regression_runs(recommendation)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_runs_approval_status "
        "ON prompt_regression_runs(approval_status)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_items_sample_role "
        "ON prompt_regression_items(sample_role)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_items_source_evaluation_id "
        "ON prompt_regression_items(source_evaluation_id)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_items_source_review_id "
        "ON prompt_regression_items(source_review_id)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_items_baseline_evaluation_id "
        "ON prompt_regression_items(baseline_evaluation_id)",
        "CREATE INDEX IF NOT EXISTS ix_prompt_regression_items_candidate_evaluation_id "
        "ON prompt_regression_items(candidate_evaluation_id)",
    ):
        connection.exec_driver_sql(statement)

    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_run_contract_insert
        BEFORE INSERT ON prompt_regression_runs
        WHEN NEW.regression_mode = 'paired'
          AND (
              NEW.baseline_strategy_bundle_id IS NULL
              OR NEW.candidate_strategy_bundle_id IS NULL
              OR NEW.baseline_strategy_bundle_id = NEW.candidate_strategy_bundle_id
              OR NEW.sample_set_version IS NULL
              OR length(NEW.sample_set_version) <> 64
              OR NEW.metric_rules_version IS NULL
              OR length(trim(NEW.metric_rules_version)) = 0
              OR json_valid(NEW.baseline_strategy_snapshot_json) = 0
              OR json_valid(NEW.candidate_strategy_snapshot_json) = 0
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.model_config'
              ) IS NOT 'object'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.prompt_a'
              ) IS NOT 'object'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.prompt_a.id'
              ) IS NOT 'integer'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.prompt_a.system_prompt'
              ) IS NOT 'text'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.prompt_a.user_prompt'
              ) IS NOT 'text'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.prompt_b'
              ) IS NOT 'object'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.rubric_version'
              ) IS NOT 'text'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.engine_version'
              ) IS NOT 'text'
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.sampling_policy'
              ) IS NULL
              OR json_type(
                  NEW.baseline_strategy_snapshot_json, '$.risk_review_version'
              ) IS NULL
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.model_config'
              ) IS NOT 'object'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.prompt_a'
              ) IS NOT 'object'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.prompt_a.id'
              ) IS NOT 'integer'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.prompt_a.system_prompt'
              ) IS NOT 'text'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.prompt_a.user_prompt'
              ) IS NOT 'text'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.prompt_b'
              ) IS NOT 'object'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.rubric_version'
              ) IS NOT 'text'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.engine_version'
              ) IS NOT 'text'
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.sampling_policy'
              ) IS NULL
              OR json_type(
                  NEW.candidate_strategy_snapshot_json, '$.risk_review_version'
              ) IS NULL
              OR json_valid(NEW.sample_manifest_json) = 0
              OR json_valid(NEW.metric_rules_json) = 0
              OR NOT EXISTS (
                  SELECT 1 FROM strategy_bundles
                  WHERE id = NEW.baseline_strategy_bundle_id
                    AND id = json_extract(
                        NEW.baseline_strategy_snapshot_json, '$.bundle_id'
                    )
                    AND canonical_hash = json_extract(
                        NEW.baseline_strategy_snapshot_json, '$.canonical_hash'
                    )
              )
              OR NOT EXISTS (
                  SELECT 1 FROM strategy_bundles
                  WHERE id = NEW.candidate_strategy_bundle_id
                    AND id = json_extract(
                        NEW.candidate_strategy_snapshot_json, '$.bundle_id'
                    )
                    AND canonical_hash = json_extract(
                        NEW.candidate_strategy_snapshot_json, '$.canonical_hash'
                    )
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression contract is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_run_frozen
        BEFORE UPDATE OF
            regression_mode,
            baseline_strategy_bundle_id,
            candidate_strategy_bundle_id,
            baseline_strategy_snapshot_json,
            candidate_strategy_snapshot_json,
            sample_set_version,
            sample_manifest_json,
            metric_rules_version,
            metric_rules_json
        ON prompt_regression_runs
        WHEN OLD.regression_mode = 'paired'
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression definition is immutable');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_approval_contract
        BEFORE UPDATE OF approval_status ON prompt_regression_runs
        WHEN OLD.regression_mode = 'paired'
          AND (
              OLD.approval_status <> 'pending'
              OR NEW.approval_status NOT IN ('approved', 'rejected')
              OR (
                  NEW.approval_status = 'approved'
                  AND OLD.recommendation <> 'pass'
              )
              OR NEW.approved_by IS NULL
              OR length(trim(NEW.approved_by)) = 0
              OR length(trim(NEW.approval_note)) = 0
              OR NEW.approved_at IS NULL
          )
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression approval is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_item_contract_insert
        BEFORE INSERT ON prompt_regression_items
        WHEN EXISTS (
            SELECT 1
            FROM prompt_regression_runs
            WHERE id = NEW.run_id AND regression_mode = 'paired'
        )
          AND (
              NEW.sample_role NOT IN (
                  'target_error', 'stable_control', 'blind_holdout'
              )
              OR NEW.source_evaluation_id IS NULL
              OR NEW.source_review_id IS NULL
              OR json_valid(NEW.truth_snapshot_json) = 0
              OR json_extract(
                  NEW.truth_snapshot_json, '$.source.evaluation_id'
              ) IS NOT NEW.source_evaluation_id
              OR json_extract(
                  NEW.truth_snapshot_json, '$.source.review_id'
              ) IS NOT NEW.source_review_id
              OR NOT EXISTS (
                  SELECT 1
                  FROM sample_set_items AS sample
                  JOIN evaluation_results AS source
                    ON source.id = NEW.source_evaluation_id
                   AND source.asset_id = sample.asset_id
                  JOIN human_reviews AS review
                    ON review.id = NEW.source_review_id
                   AND review.evaluation_id = source.id
                  WHERE sample.id = NEW.sample_item_id
                    AND review.decision IN ('approved', 'corrected')
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression truth evidence is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_item_complete
        BEFORE UPDATE ON prompt_regression_items
        WHEN NEW.status = 'completed'
          AND OLD.status <> 'completed'
          AND EXISTS (
              SELECT 1
              FROM prompt_regression_runs
              WHERE id = NEW.run_id AND regression_mode = 'paired'
          )
          AND (
              NEW.baseline_evaluation_id IS NULL
              OR NEW.candidate_evaluation_id IS NULL
              OR NEW.evaluation_id IS NOT NEW.candidate_evaluation_id
              OR json_valid(NEW.baseline_result_json) = 0
              OR json_valid(NEW.candidate_result_json) = 0
              OR json_valid(NEW.comparison_json) = 0
              OR NOT EXISTS (
                  SELECT 1
                  FROM prompt_regression_runs AS run
                  JOIN sample_set_items AS sample
                    ON sample.id = NEW.sample_item_id
                  JOIN evaluation_results AS baseline
                    ON baseline.id = NEW.baseline_evaluation_id
                   AND baseline.asset_id = sample.asset_id
                   AND baseline.strategy_bundle_id =
                       run.baseline_strategy_bundle_id
                  JOIN evaluation_results AS candidate
                    ON candidate.id = NEW.candidate_evaluation_id
                   AND candidate.asset_id = sample.asset_id
                   AND candidate.strategy_bundle_id =
                       run.candidate_strategy_bundle_id
                  WHERE run.id = NEW.run_id
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression result binding is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_item_truth_frozen
        BEFORE UPDATE OF
            run_id,
            sample_item_id,
            sample_role,
            source_evaluation_id,
            source_review_id,
            truth_snapshot_json
        ON prompt_regression_items
        WHEN EXISTS (
            SELECT 1
            FROM prompt_regression_runs
            WHERE id = OLD.run_id AND regression_mode = 'paired'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Paired regression truth is immutable');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_paired_regression_item_frozen
        BEFORE UPDATE OF
            baseline_evaluation_id,
            candidate_evaluation_id,
            baseline_result_json,
            candidate_result_json,
            comparison_json
        ON prompt_regression_items
        WHEN OLD.status = 'completed'
          AND EXISTS (
              SELECT 1
              FROM prompt_regression_runs
              WHERE id = OLD.run_id AND regression_mode = 'paired'
          )
        BEGIN
            SELECT RAISE(ABORT, 'Completed paired regression item is immutable');
        END
    """)


def _migration_013_freeze_paired_strategy_snapshots(
    connection: Connection,
) -> None:
    run_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(prompt_regression_runs)"
        )
    }
    for column_name in (
        "baseline_strategy_snapshot_json",
        "candidate_strategy_snapshot_json",
    ):
        if column_name not in run_columns:
            connection.exec_driver_sql(
                "ALTER TABLE prompt_regression_runs "
                f"ADD COLUMN {column_name} TEXT NOT NULL DEFAULT '{{}}'"
            )

    snapshot_invalid = """
        json_valid({snapshot}) = 0
        OR json_type({snapshot}, '$') IS NOT 'object'
        OR json_extract({snapshot}, '$.schema_version')
            <> 'strategy-bundle-v1'
        OR json_type({snapshot}, '$.model_config') IS NOT 'object'
        OR json_type({snapshot}, '$.model_config.name') IS NULL
        OR json_type({snapshot}, '$.model_config.provider') IS NULL
        OR json_type({snapshot}, '$.model_config.base_url') IS NULL
        OR json_type({snapshot}, '$.model_config.api_path') IS NULL
        OR json_type({snapshot}, '$.model_config.model_id') IS NULL
        OR json_type({snapshot}, '$.model_config.temperature') IS NULL
        OR json_type({snapshot}, '$.model_config.max_tokens') IS NULL
        OR json_type(
            {snapshot}, '$.model_config.timeout_seconds'
        ) IS NULL
        OR json_type({snapshot}, '$.model_config.max_retries') IS NULL
        OR json_type(
            {snapshot}, '$.model_config.max_concurrency'
        ) IS NULL
        OR json_type(
            {snapshot}, '$.model_config.structured_output'
        ) IS NULL
        OR json_type(
            {snapshot}, '$.model_config.high_risk_review_enabled'
        ) IS NULL
        OR json_type({snapshot}, '$.prompt_a') IS NOT 'object'
        OR json_type({snapshot}, '$.prompt_b') IS NOT 'object'
        OR json_extract({snapshot}, '$.prompt_a.stage') <> 'A'
        OR json_extract({snapshot}, '$.prompt_b.stage') <> 'B'
        OR json_type({snapshot}, '$.prompt_a.id') IS NULL
        OR json_type({snapshot}, '$.prompt_b.id') IS NULL
        OR json_type({snapshot}, '$.prompt_a.name') IS NULL
        OR json_type({snapshot}, '$.prompt_b.name') IS NULL
        OR json_type({snapshot}, '$.prompt_a.rubric_version') IS NULL
        OR json_type({snapshot}, '$.prompt_b.rubric_version') IS NULL
        OR json_type({snapshot}, '$.prompt_a.system_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_b.system_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_a.user_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_b.user_prompt') IS NULL
        OR json_type({snapshot}, '$.sampling_policy') IS NULL
        OR json_type({snapshot}, '$.sampling_policy')
            NOT IN ('object', 'null')
        OR json_type({snapshot}, '$.risk_review_version') IS NULL
        OR NOT EXISTS (
            SELECT 1
            FROM strategy_bundles AS bundle
            WHERE bundle.id = {bundle_id}
              AND bundle.id = json_extract({snapshot}, '$.bundle_id')
              AND bundle.canonical_hash =
                  json_extract({snapshot}, '$.canonical_hash')
              AND bundle.model_id =
                  json_extract({snapshot}, '$.model_id')
              AND json(bundle.model_config_snapshot) =
                  json(json_extract({snapshot}, '$.model_config'))
              AND bundle.prompt_a_version =
                  json_extract({snapshot}, '$.prompt_a.version')
              AND bundle.prompt_b_version =
                  json_extract({snapshot}, '$.prompt_b.version')
              AND bundle.rubric_version =
                  json_extract({snapshot}, '$.rubric_version')
              AND bundle.engine_version =
                  json_extract({snapshot}, '$.engine_version')
              AND bundle.sampling_policy_revision IS
                  json_extract(
                      {snapshot}, '$.sampling_policy.revision'
                  )
              AND bundle.risk_review_version IS
                  json_extract({snapshot}, '$.risk_review_version')
        )
    """
    baseline_invalid = snapshot_invalid.format(
        snapshot="NEW.baseline_strategy_snapshot_json",
        bundle_id="NEW.baseline_strategy_bundle_id",
    )
    candidate_invalid = snapshot_invalid.format(
        snapshot="NEW.candidate_strategy_snapshot_json",
        bundle_id="NEW.candidate_strategy_bundle_id",
    )
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS
            trg_paired_regression_strategy_snapshot_insert
        BEFORE INSERT ON prompt_regression_runs
        WHEN NEW.regression_mode = 'paired'
          AND ({baseline_invalid} OR {candidate_invalid})
        BEGIN
            SELECT RAISE(
                ABORT, 'Paired regression strategy snapshot is invalid'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS
            trg_paired_regression_strategy_snapshot_frozen
        BEFORE UPDATE OF
            baseline_strategy_snapshot_json,
            candidate_strategy_snapshot_json
        ON prompt_regression_runs
        WHEN OLD.regression_mode = 'paired'
        BEGIN
            SELECT RAISE(
                ABORT, 'Paired regression strategy snapshot is immutable'
            );
        END
    """)


def _migration_014_add_loop_queue_and_breakers(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS loop_runs (
            id INTEGER PRIMARY KEY,
            idempotency_key VARCHAR(160) NOT NULL UNIQUE,
            request_fingerprint VARCHAR(64) NOT NULL,
            asset_id INTEGER NOT NULL
                REFERENCES assets(id) ON DELETE CASCADE,
            strategy_bundle_id INTEGER NOT NULL
                REFERENCES strategy_bundles(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL DEFAULT 'waiting_result'
                CHECK (
                    status IN (
                        'waiting_result',
                        'machine_converged',
                        'needs_human'
                    )
                ),
            current_round INTEGER NOT NULL DEFAULT 1
                CHECK (current_round BETWEEN 1 AND 3),
            decision_json TEXT NOT NULL DEFAULT '{}',
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS loop_attempts (
            id INTEGER PRIMARY KEY,
            loop_run_id INTEGER NOT NULL
                REFERENCES loop_runs(id) ON DELETE CASCADE,
            business_round INTEGER NOT NULL
                CHECK (business_round BETWEEN 1 AND 3),
            kind VARCHAR(30) NOT NULL
                CHECK (
                    kind IN ('base', 'targeted_recheck', 'arbitration')
                ),
            target_dimensions_json TEXT NOT NULL DEFAULT '[]',
            input_evidence_json TEXT NOT NULL DEFAULT '{}',
            normalized_result_json TEXT,
            conflict_json TEXT NOT NULL DEFAULT '[]',
            status VARCHAR(30) NOT NULL DEFAULT 'waiting_result'
                CHECK (status IN ('waiting_result', 'completed')),
            technical_attempt INTEGER NOT NULL DEFAULT 0
                CHECK (technical_attempt BETWEEN 0 AND 2),
            cost FLOAT,
            latency_ms INTEGER,
            result_idempotency_key VARCHAR(160),
            result_fingerprint VARCHAR(64),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            CONSTRAINT uq_loop_attempt_business_round
                UNIQUE (loop_run_id, business_round),
            CONSTRAINT uq_loop_attempt_result_idempotency
                UNIQUE (loop_run_id, result_idempotency_key),
            CHECK (
                (business_round = 1 AND kind = 'base')
                OR (
                    business_round = 2
                    AND kind = 'targeted_recheck'
                )
                OR (
                    business_round = 3
                    AND kind = 'arbitration'
                )
            )
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS circuit_breakers (
            id INTEGER PRIMARY KEY,
            scope_type VARCHAR(20) NOT NULL
                CHECK (scope_type IN ('strategy', 'batch')),
            scope_key VARCHAR(160) NOT NULL,
            state VARCHAR(20) NOT NULL DEFAULT 'closed'
                CHECK (state IN ('closed', 'open')),
            failure_count INTEGER NOT NULL DEFAULT 0
                CHECK (failure_count >= 0),
            window_started_at DATETIME,
            last_failure_at DATETIME,
            opened_at DATETIME,
            cooldown_until DATETIME,
            reason VARCHAR(80),
            reset_by VARCHAR(80),
            reset_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_circuit_breaker_scope
                UNIQUE (scope_type, scope_key)
        )
    """)

    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_loop_runs_idempotency_key "
        "ON loop_runs(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS ix_loop_runs_asset_id "
        "ON loop_runs(asset_id)",
        "CREATE INDEX IF NOT EXISTS ix_loop_runs_strategy_bundle_id "
        "ON loop_runs(strategy_bundle_id)",
        "CREATE INDEX IF NOT EXISTS ix_loop_runs_status "
        "ON loop_runs(status)",
        "CREATE INDEX IF NOT EXISTS ix_loop_attempts_loop_run_id "
        "ON loop_attempts(loop_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_loop_attempts_business_round "
        "ON loop_attempts(business_round)",
        "CREATE INDEX IF NOT EXISTS ix_loop_attempts_status "
        "ON loop_attempts(status)",
        "CREATE INDEX IF NOT EXISTS ix_circuit_breakers_scope_type "
        "ON circuit_breakers(scope_type)",
        "CREATE INDEX IF NOT EXISTS ix_circuit_breakers_scope_key "
        "ON circuit_breakers(scope_key)",
        "CREATE INDEX IF NOT EXISTS ix_circuit_breakers_state "
        "ON circuit_breakers(state)",
    ):
        connection.exec_driver_sql(statement)

    job_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_jobs)"
        )
    }
    job_definitions = (
        (
            "strategy_bundle_id",
            "INTEGER REFERENCES strategy_bundles(id) ON DELETE RESTRICT",
        ),
        (
            "loop_attempt_id",
            "INTEGER REFERENCES loop_attempts(id) ON DELETE SET NULL",
        ),
        (
            "parent_job_id",
            "INTEGER REFERENCES evaluation_jobs(id) ON DELETE SET NULL",
        ),
        (
            "queue_class",
            "VARCHAR(30) NOT NULL DEFAULT 'production_batch'",
        ),
        (
            "origin_queue_class",
            "VARCHAR(30) NOT NULL DEFAULT 'production_batch'",
        ),
        ("technical_attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("technical_error_type", "VARCHAR(40)"),
        ("retry_after_at", "DATETIME"),
        ("batch_key", "VARCHAR(120)"),
    )
    for column_name, definition in job_definitions:
        if column_name not in job_columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_jobs "
                f"ADD COLUMN {column_name} {definition}"
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_strategy_bundle_id "
        "ON evaluation_jobs(strategy_bundle_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_loop_attempt_id "
        "ON evaluation_jobs(loop_attempt_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_parent_job_id "
        "ON evaluation_jobs(parent_job_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_queue_class "
        "ON evaluation_jobs(queue_class)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_origin_queue_class "
        "ON evaluation_jobs(origin_queue_class)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_technical_error_type "
        "ON evaluation_jobs(technical_error_type)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_retry_after_at "
        "ON evaluation_jobs(retry_after_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_batch_key "
        "ON evaluation_jobs(batch_key)",
    ):
        connection.exec_driver_sql(statement)

    queue_validation = """
        NEW.queue_class NOT IN (
            'validation',
            'interactive',
            'production_batch',
            'canary',
            'recovery'
        )
        OR NEW.origin_queue_class NOT IN (
            'validation',
            'interactive',
            'production_batch',
            'canary',
            'recovery'
        )
        OR NEW.technical_attempt NOT BETWEEN 0 AND 2
        OR (
            NEW.queue_class = 'recovery'
            AND (
                NEW.parent_job_id IS NULL
                OR NEW.technical_attempt = 0
            )
        )
    """
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS
            trg_evaluation_jobs_queue_contract_insert
        BEFORE INSERT ON evaluation_jobs
        WHEN {queue_validation}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob queue contract is invalid');
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS
            trg_evaluation_jobs_queue_contract_update
        BEFORE UPDATE OF
            queue_class,
            origin_queue_class,
            parent_job_id,
            technical_attempt
        ON evaluation_jobs
        WHEN {queue_validation}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob queue contract is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_loop_runs_strategy_frozen
        BEFORE UPDATE OF strategy_bundle_id ON loop_runs
        BEGIN
            SELECT RAISE(ABORT, 'LoopRun strategy is immutable');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_target_contract_insert
        BEFORE INSERT ON loop_attempts
        WHEN (
            NEW.business_round = 1
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_array_length(NEW.target_dimensions_json) <> 0
            )
        )
        OR (
            NEW.business_round IN (2, 3)
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_type(NEW.target_dimensions_json, '$') <> 'array'
                OR json_array_length(NEW.target_dimensions_json) = 0
                OR EXISTS (
                    SELECT 1
                    FROM json_each(NEW.target_dimensions_json)
                    WHERE lower(value) IN ('*', 'all', '__all__', 'full')
                )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'LoopAttempt target contract is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_completed_immutable
        BEFORE UPDATE ON loop_attempts
        WHEN OLD.status = 'completed'
        BEGIN
            SELECT RAISE(ABORT, 'Completed LoopAttempt is immutable');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_completed_no_delete
        BEFORE DELETE ON loop_attempts
        WHEN OLD.status = 'completed'
        BEGIN
            SELECT RAISE(ABORT, 'Completed LoopAttempt cannot be deleted');
        END
    """)


def _migration_015_harden_loop_retry_and_scheduler(connection: Connection) -> None:
    job_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_jobs)"
        )
    }
    if "root_job_id" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN root_job_id INTEGER "
            "REFERENCES evaluation_jobs(id) ON DELETE SET NULL"
        )

    connection.exec_driver_sql("""
        UPDATE evaluation_jobs
        SET root_job_id = id
        WHERE root_job_id IS NULL
    """)
    connection.exec_driver_sql("""
        WITH RECURSIVE lineage(id, root_id, depth) AS (
            SELECT id, id, 0
            FROM evaluation_jobs
            WHERE parent_job_id IS NULL
            UNION ALL
            SELECT child.id, lineage.root_id, lineage.depth + 1
            FROM evaluation_jobs AS child
            JOIN lineage ON child.parent_job_id = lineage.id
            WHERE lineage.depth < 2
        )
        UPDATE evaluation_jobs
        SET root_job_id = (
            SELECT lineage.root_id
            FROM lineage
            WHERE lineage.id = evaluation_jobs.id
        )
        WHERE EXISTS (
            SELECT 1 FROM lineage
            WHERE lineage.id = evaluation_jobs.id
        )
    """)
    invalid_retry = connection.exec_driver_sql("""
        SELECT child.id
        FROM evaluation_jobs AS child
        LEFT JOIN evaluation_jobs AS parent
          ON parent.id = child.parent_job_id
        WHERE child.technical_attempt NOT BETWEEN 0 AND 2
           OR (
                child.technical_attempt = 0
                AND (
                    child.parent_job_id IS NOT NULL
                    OR child.queue_class = 'recovery'
                )
           )
           OR (
                child.technical_attempt > 0
                AND (
                    parent.id IS NULL
                    OR child.root_job_id IS NULL
                    OR child.queue_class <> 'recovery'
                    OR parent.technical_attempt + 1
                        <> child.technical_attempt
                    OR parent.root_job_id <> child.root_job_id
                    OR parent.origin_queue_class
                        <> child.origin_queue_class
                    OR parent.loop_attempt_id
                        IS NOT child.loop_attempt_id
                    OR parent.strategy_bundle_id
                        IS NOT child.strategy_bundle_id
                    OR parent.batch_key IS NOT child.batch_key
                )
           )
        LIMIT 1
    """).first()
    if invalid_retry is not None:
        raise RuntimeError(
            "历史 EvaluationJob retry 链违反递增或继承合同"
        )
    fork = connection.exec_driver_sql("""
        SELECT root_job_id, technical_attempt, COUNT(*)
        FROM evaluation_jobs
        WHERE root_job_id IS NOT NULL
        GROUP BY root_job_id, technical_attempt
        HAVING COUNT(*) > 1
        LIMIT 1
    """).first()
    if fork is not None:
        raise RuntimeError(
            "历史 EvaluationJob 已存在分叉 retry 链，拒绝静默迁移"
        )

    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_root_job_id "
        "ON evaluation_jobs(root_job_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_evaluation_jobs_root_attempt "
        "ON evaluation_jobs(root_job_id, technical_attempt) "
        "WHERE root_job_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_evaluation_jobs_loop_attempt_technical "
        "ON evaluation_jobs(loop_attempt_id, technical_attempt) "
        "WHERE loop_attempt_id IS NOT NULL",
    ]
    if {"regression_item_id", "strategy_bundle_id"}.issubset(job_columns):
        index_statements.append(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_evaluation_jobs_regression_strategy "
            "ON evaluation_jobs(regression_item_id, strategy_bundle_id) "
            "WHERE regression_item_id IS NOT NULL "
            "AND strategy_bundle_id IS NOT NULL"
        )
    for statement in index_statements:
        connection.exec_driver_sql(statement)

    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS queue_scheduler_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            policy_version VARCHAR(80) NOT NULL,
            global_limit INTEGER NOT NULL CHECK (global_limit >= 1),
            validation_deficit INTEGER NOT NULL DEFAULT 0,
            interactive_deficit INTEGER NOT NULL DEFAULT 0,
            production_batch_deficit INTEGER NOT NULL DEFAULT 0,
            canary_deficit INTEGER NOT NULL DEFAULT 0,
            recovery_deficit INTEGER NOT NULL DEFAULT 0,
            dispatch_count INTEGER NOT NULL DEFAULT 0
                CHECK (dispatch_count >= 0),
            last_recovery_dispatch INTEGER,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql("""
        INSERT OR IGNORE INTO queue_scheduler_state (
            id, policy_version, global_limit
        ) VALUES (1, 'queue-policy-v1', 1)
    """)

    target_invalid = """
        (
            NEW.business_round = 1
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_type(NEW.target_dimensions_json, '$') <> 'array'
                OR json_array_length(NEW.target_dimensions_json) <> 0
            )
        )
        OR (
            NEW.business_round IN (2, 3)
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_type(NEW.target_dimensions_json, '$') <> 'array'
                OR json_array_length(NEW.target_dimensions_json) = 0
                OR EXISTS (
                    SELECT 1
                    FROM json_each(NEW.target_dimensions_json)
                    WHERE type <> 'text'
                       OR trim(value) = ''
                       OR lower(value) IN ('*', 'all', '__all__', 'full')
                )
            )
        )
    """
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_target_contract_update
        BEFORE UPDATE OF
            business_round, kind, target_dimensions_json
        ON loop_attempts
        WHEN {target_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'LoopAttempt target contract is invalid');
        END
    """)

    completed_invalid = """
        NEW.status = 'completed'
        AND (
            NEW.normalized_result_json IS NULL
            OR trim(NEW.normalized_result_json) = ''
            OR json_valid(NEW.normalized_result_json) = 0
            OR CASE
                WHEN json_valid(NEW.normalized_result_json) = 1
                THEN (
                    json_type(NEW.normalized_result_json, '$') <> 'object'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM json_each(NEW.normalized_result_json)
                    )
                )
                ELSE 1
            END
            OR NEW.result_idempotency_key IS NULL
            OR trim(NEW.result_idempotency_key) = ''
            OR NEW.result_fingerprint IS NULL
            OR length(NEW.result_fingerprint) <> 64
            OR lower(NEW.result_fingerprint) GLOB '*[^0-9a-f]*'
            OR NEW.completed_at IS NULL
        )
    """
    invalid_completed = connection.exec_driver_sql(f"""
        SELECT id
        FROM loop_attempts AS NEW
        WHERE {completed_invalid}
        LIMIT 1
    """).first()
    if invalid_completed is not None:
        raise RuntimeError(
            "历史 completed LoopAttempt 缺少结果、幂等键、指纹或完成时间"
        )
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_completed_contract_insert
        BEFORE INSERT ON loop_attempts
        WHEN {completed_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'Completed LoopAttempt contract is invalid');
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS trg_loop_attempts_completed_contract_update
        BEFORE UPDATE ON loop_attempts
        WHEN {completed_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'Completed LoopAttempt contract is invalid');
        END
    """)

    retry_invalid = """
        NEW.technical_attempt NOT BETWEEN 0 AND 2
        OR (
            NEW.technical_attempt = 0
            AND (
                NEW.parent_job_id IS NOT NULL
                OR NEW.queue_class = 'recovery'
            )
        )
        OR (
            NEW.technical_attempt > 0
            AND (
                NEW.parent_job_id IS NULL
                OR NEW.root_job_id IS NULL
                OR NEW.queue_class <> 'recovery'
                OR NOT EXISTS (
                    SELECT 1
                    FROM evaluation_jobs AS parent
                    WHERE parent.id = NEW.parent_job_id
                      AND parent.technical_attempt + 1
                          = NEW.technical_attempt
                      AND parent.root_job_id = NEW.root_job_id
                      AND parent.origin_queue_class
                          = NEW.origin_queue_class
                      AND parent.loop_attempt_id IS NEW.loop_attempt_id
                      AND parent.strategy_bundle_id
                          IS NEW.strategy_bundle_id
                      AND parent.batch_key IS NEW.batch_key
                )
            )
        )
    """
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_jobs_retry_chain_insert
        BEFORE INSERT ON evaluation_jobs
        WHEN {retry_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob retry chain is invalid');
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_jobs_retry_chain_update
        BEFORE UPDATE OF
            parent_job_id, root_job_id, queue_class, origin_queue_class,
            technical_attempt, loop_attempt_id, strategy_bundle_id, batch_key
        ON evaluation_jobs
        WHEN {retry_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob retry chain is invalid');
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_evaluation_jobs_set_root
        AFTER INSERT ON evaluation_jobs
        WHEN NEW.technical_attempt = 0 AND NEW.root_job_id IS NULL
        BEGIN
            UPDATE evaluation_jobs
            SET root_job_id = NEW.id
            WHERE id = NEW.id;
        END
    """)


def _rebuild_evaluation_jobs_without_legacy_pair_constraint(
    connection: Connection,
) -> None:
    """Remove a legacy table-level pair UNIQUE that SQLite cannot drop."""
    legacy_constraint = False
    for index_row in connection.exec_driver_sql(
        "PRAGMA index_list(evaluation_jobs)"
    ):
        index_name = index_row[1]
        is_unique = index_row[2] == 1
        origin = index_row[3]
        if not is_unique or origin != "u":
            continue
        columns = tuple(
            row[2]
            for row in connection.exec_driver_sql(
                f'PRAGMA index_info("{index_name}")'
            )
        )
        if columns == ("regression_item_id", "strategy_bundle_id"):
            legacy_constraint = True
            break
    if not legacy_constraint:
        return

    table_sql = connection.exec_driver_sql("""
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'evaluation_jobs'
    """).scalar_one()
    constraint_pattern = re.compile(
        r""",\s*
        (?:
            CONSTRAINT\s+
            ["']?uq_evaluation_jobs_regression_strategy["']?\s+
        )?
        UNIQUE\s*\(\s*
            ["']?regression_item_id["']?\s*,\s*
            ["']?strategy_bundle_id["']?\s*
        \)
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    rebuilt_sql, removed = constraint_pattern.subn("", table_sql, count=1)
    if removed != 1:
        raise RuntimeError(
            "无法安全定位旧配对回归表级唯一约束"
        )
    rebuilt_sql, renamed = re.subn(
        r"^\s*CREATE\s+TABLE\s+[\"']?evaluation_jobs[\"']?",
        "CREATE TABLE evaluation_jobs_v16_rebuild",
        rebuilt_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if renamed != 1:
        raise RuntimeError("无法安全重建 evaluation_jobs 表")

    schema_objects = connection.exec_driver_sql("""
        SELECT type, name, sql
        FROM sqlite_master
        WHERE tbl_name = 'evaluation_jobs'
          AND type IN ('index', 'trigger')
          AND sql IS NOT NULL
        ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name
    """).all()
    recreate_sql = [
        sql
        for object_type, name, sql in schema_objects
        if not (
            object_type == "index"
            and name == "uq_evaluation_jobs_regression_strategy"
        )
    ]
    columns = [
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_jobs)"
        )
    ]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)

    # A raw transaction is required because SQLite ignores foreign_keys=OFF
    # once a transaction has started. This path runs only for the legacy
    # auto-index and restores FK enforcement before returning.
    raw = connection.connection.driver_connection
    foreign_keys_enabled = bool(
        raw.execute("PRAGMA foreign_keys").fetchone()[0]
    )
    legacy_alter_table = int(
        raw.execute("PRAGMA legacy_alter_table").fetchone()[0]
    )
    raw.commit()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("PRAGMA legacy_alter_table=ON")
        raw.execute("BEGIN IMMEDIATE")
        raw.execute(
            "DROP TABLE IF EXISTS evaluation_jobs_v16_rebuild"
        )
        raw.execute(rebuilt_sql)
        raw.execute(
            "INSERT INTO evaluation_jobs_v16_rebuild "
            f"({quoted_columns}) "
            f"SELECT {quoted_columns} FROM evaluation_jobs"
        )
        raw.execute("DROP TABLE evaluation_jobs")
        raw.execute(
            "ALTER TABLE evaluation_jobs_v16_rebuild "
            "RENAME TO evaluation_jobs"
        )
        for statement in recreate_sql:
            raw.execute(statement)
        violation = raw.execute("PRAGMA foreign_key_check").fetchone()
        if violation is not None:
            raise RuntimeError(
                "evaluation_jobs 重建后外键检查失败"
            )
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.execute(
            f"PRAGMA legacy_alter_table={legacy_alter_table}"
        )
        raw.execute(
            f"PRAGMA foreign_keys={1 if foreign_keys_enabled else 0}"
        )


def _migration_016_finalize_retry_and_loop_guards(
    connection: Connection,
) -> None:
    job_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_jobs)"
        )
    }
    if {
        "regression_item_id",
        "strategy_bundle_id",
        "technical_attempt",
    }.issubset(job_columns):
        duplicate_root = connection.exec_driver_sql("""
            SELECT regression_item_id, strategy_bundle_id
            FROM evaluation_jobs
            WHERE regression_item_id IS NOT NULL
              AND strategy_bundle_id IS NOT NULL
              AND technical_attempt = 0
            GROUP BY regression_item_id, strategy_bundle_id
            HAVING COUNT(*) > 1
            LIMIT 1
        """).first()
        if duplicate_root is not None:
            raise RuntimeError(
                "历史配对回归存在重复初始任务，拒绝静默替换唯一索引"
            )
        _rebuild_evaluation_jobs_without_legacy_pair_constraint(
            connection
        )
        connection.exec_driver_sql(
            "DROP INDEX IF EXISTS "
            "uq_evaluation_jobs_regression_strategy"
        )
        connection.exec_driver_sql("""
            CREATE UNIQUE INDEX
                uq_evaluation_jobs_regression_strategy
            ON evaluation_jobs(
                regression_item_id,
                strategy_bundle_id
            )
            WHERE regression_item_id IS NOT NULL
              AND strategy_bundle_id IS NOT NULL
              AND technical_attempt = 0
        """)

    target_invalid = """
        (
            NEW.business_round = 1
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_type(NEW.target_dimensions_json, '$') <> 'array'
                OR json_array_length(NEW.target_dimensions_json) <> 0
            )
        )
        OR (
            NEW.business_round IN (2, 3)
            AND (
                json_valid(NEW.target_dimensions_json) = 0
                OR json_type(NEW.target_dimensions_json, '$') <> 'array'
                OR json_array_length(NEW.target_dimensions_json) = 0
                OR EXISTS (
                    SELECT 1
                    FROM json_each(NEW.target_dimensions_json)
                    WHERE type <> 'text'
                       OR trim(value) = ''
                       OR lower(value) IN ('*', 'all', '__all__', 'full')
                )
            )
        )
    """
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_loop_attempts_target_contract_insert"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_loop_attempts_target_contract_update"
    )
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_loop_attempts_target_contract_insert
        BEFORE INSERT ON loop_attempts
        WHEN {target_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'LoopAttempt target contract is invalid');
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_loop_attempts_target_contract_update
        BEFORE UPDATE OF
            business_round, kind, target_dimensions_json
        ON loop_attempts
        WHEN {target_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'LoopAttempt target contract is invalid');
        END
    """)

    inherited_field_checks = [
        "parent.asset_id = NEW.asset_id",
        "parent.origin_queue_class = NEW.origin_queue_class",
        "parent.loop_attempt_id IS NEW.loop_attempt_id",
        "parent.strategy_bundle_id IS NEW.strategy_bundle_id",
        "parent.batch_key IS NEW.batch_key",
    ]
    for nullable_field in (
        "prompt_a_id",
        "prompt_b_id",
        "regression_item_id",
    ):
        if nullable_field in job_columns:
            inherited_field_checks.append(
                f"parent.{nullable_field} IS NEW.{nullable_field}"
            )
    inherited_fields_sql = "\n                      AND ".join(
        inherited_field_checks
    )
    retry_invalid = f"""
        NEW.technical_attempt NOT BETWEEN 0 AND 2
        OR (
            NEW.technical_attempt = 0
            AND (
                NEW.parent_job_id IS NOT NULL
                OR NEW.queue_class = 'recovery'
            )
        )
        OR (
            NEW.technical_attempt > 0
            AND (
                NEW.parent_job_id IS NULL
                OR NEW.root_job_id IS NULL
                OR NEW.queue_class <> 'recovery'
                OR NOT EXISTS (
                    SELECT 1
                    FROM evaluation_jobs AS parent
                    WHERE parent.id = NEW.parent_job_id
                      AND parent.technical_attempt + 1
                          = NEW.technical_attempt
                      AND parent.root_job_id = NEW.root_job_id
                      AND {inherited_fields_sql}
                )
            )
        )
    """
    invalid_retry = connection.exec_driver_sql(f"""
        SELECT NEW.id
        FROM evaluation_jobs AS NEW
        WHERE {retry_invalid}
        LIMIT 1
    """).first()
    if invalid_retry is not None:
        raise RuntimeError(
            "历史 EvaluationJob retry 链违反业务字段继承合同"
        )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_evaluation_jobs_retry_chain_insert"
    )
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_evaluation_jobs_retry_chain_update"
    )
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_evaluation_jobs_retry_chain_insert
        BEFORE INSERT ON evaluation_jobs
        WHEN {retry_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob retry chain is invalid');
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_evaluation_jobs_retry_chain_update
        BEFORE UPDATE OF
            {", ".join(
                field
                for field in (
                    "asset_id",
                    "prompt_a_id",
                    "prompt_b_id",
                    "regression_item_id",
                )
                if field in job_columns
            )},
            parent_job_id, root_job_id, queue_class, origin_queue_class,
            technical_attempt, loop_attempt_id, strategy_bundle_id, batch_key
        ON evaluation_jobs
        WHEN {retry_invalid}
        BEGIN
            SELECT RAISE(ABORT, 'EvaluationJob retry chain is invalid');
        END
    """)


def _migration_017_add_canary_run_persistence(
    connection: Connection,
) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS canary_runs (
            run_id VARCHAR(80) PRIMARY KEY,
            display_name VARCHAR(160),
            current_state VARCHAR(30) NOT NULL DEFAULT 'draft',
            plan_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            snapshot_json TEXT NOT NULL,
            snapshot_fingerprint VARCHAR(64) NOT NULL,
            created_by VARCHAR(80) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_canary_runs_current_state CHECK (
                current_state IN (
                    'draft',
                    'preflight_ready',
                    'approvals_ready',
                    'freeze_ready',
                    'candidate_ready',
                    'human_review_ready',
                    'failed',
                    'cancelled'
                )
            ),
            CONSTRAINT ck_canary_runs_plan_json CHECK (
                json_valid(plan_json) = 1
                AND json_type(plan_json, '$') = 'object'
            ),
            CONSTRAINT ck_canary_runs_evidence_json CHECK (
                json_valid(evidence_json) = 1
                AND json_type(evidence_json, '$') = 'object'
            ),
            CONSTRAINT ck_canary_runs_snapshot_json CHECK (
                json_valid(snapshot_json) = 1
                AND json_type(snapshot_json, '$') = 'object'
            ),
            CONSTRAINT ck_canary_runs_snapshot_fingerprint CHECK (
                length(snapshot_fingerprint) = 64
                AND lower(snapshot_fingerprint)
                    NOT GLOB '*[^0-9a-f]*'
            )
        )
    """)
    connection.exec_driver_sql("""
        CREATE INDEX IF NOT EXISTS ix_canary_runs_current_state
        ON canary_runs(current_state)
    """)
    connection.exec_driver_sql("""
        CREATE INDEX IF NOT EXISTS ix_canary_runs_snapshot_fingerprint
        ON canary_runs(snapshot_fingerprint)
    """)
    connection.exec_driver_sql("""
        CREATE INDEX IF NOT EXISTS ix_canary_runs_updated_at
        ON canary_runs(updated_at)
    """)


def _migration_018_add_prompt_optimizer_stage_audit(
    connection: Connection,
) -> None:
    table_exists = connection.exec_driver_sql("""
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'prompt_optimization_runs'
    """).first()
    if table_exists is None:
        # 正式启动会先由 Base.metadata.create_all 创建完整表；旧版部分模式
        # 测试和恢复探测可能没有该业务表，此时没有可回填的数据。
        return
    legacy_audit = (
        '{"status":"not_recorded","attempt_count":0,'
        '"upstream_status_code":null,"request_correlation_id":null,'
        '"elapsed_ms":null,"error_type":null,"error_message":null,'
        '"output_budget":null,"reasoning_effort":null}'
    )
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(prompt_optimization_runs)"
        )
    }
    for column_name in (
        "diagnostic_audit_json",
        "synthesis_audit_json",
    ):
        if column_name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE prompt_optimization_runs "
                f"ADD COLUMN {column_name} TEXT NOT NULL "
                f"DEFAULT '{legacy_audit}'"
            )
        connection.exec_driver_sql(
            f"UPDATE prompt_optimization_runs "
            f"SET {column_name} = ? "
            f"WHERE {column_name} IS NULL "
            f"OR trim({column_name}) = '' "
            f"OR json_valid({column_name}) <> 1 "
            f"OR CASE WHEN json_valid({column_name}) = 1 THEN ("
            f"json_type({column_name}, '$') <> 'object' "
            f"OR json_type({column_name}, '$.status') IS NULL "
            f"OR json_extract({column_name}, '$.status') NOT IN ("
            f"'not_recorded','pending','running','succeeded','failed'"
            f") "
            f"OR EXISTS ("
            f"SELECT 1 FROM json_each({column_name}) AS audit_field "
            f"WHERE audit_field.key NOT IN ("
            f"'status','attempt_count','upstream_status_code',"
            f"'request_correlation_id','elapsed_ms','error_type',"
            f"'error_message','output_budget','reasoning_effort'"
            f"))) ELSE 0 END",
            (legacy_audit,),
        )


def _migration_019_add_staged_human_review_and_candidate_gate(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "evaluation_results" in tables:
        result_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(evaluation_results)"
            )
        }
        if "review_stage" not in result_columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_results "
                "ADD COLUMN review_stage VARCHAR(20) NOT NULL DEFAULT 'initial'"
            )
        if "review_revision" not in result_columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_results "
                "ADD COLUMN review_revision INTEGER NOT NULL DEFAULT 0"
            )
        if "human_reviews" in tables:
            connection.exec_driver_sql("""
                UPDATE evaluation_results
                SET review_revision = (
                    SELECT COUNT(*)
                    FROM human_reviews
                    WHERE human_reviews.evaluation_id = evaluation_results.id
                )
            """)
            connection.exec_driver_sql("""
                UPDATE evaluation_results
                SET review_stage = CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM human_reviews
                        WHERE human_reviews.evaluation_id = evaluation_results.id
                          AND human_reviews.decision IN ('approved', 'corrected')
                    ) THEN 'completed'
                    ELSE 'initial'
                END
            """)
        connection.exec_driver_sql("""
            CREATE INDEX IF NOT EXISTS ix_evaluation_results_review_stage
            ON evaluation_results(review_stage)
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_evaluation_review_contract_insert
            BEFORE INSERT ON evaluation_results
            WHEN NEW.review_stage NOT IN (
                'initial','secondary','arbitration','completed'
            ) OR NEW.review_revision < 0
            BEGIN
                SELECT RAISE(ABORT, 'Invalid evaluation review contract');
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_evaluation_review_contract_update
            BEFORE UPDATE OF review_stage, review_revision ON evaluation_results
            WHEN NEW.review_stage NOT IN (
                'initial','secondary','arbitration','completed'
            ) OR NEW.review_revision < OLD.review_revision
            BEGIN
                SELECT RAISE(ABORT, 'Invalid evaluation review transition');
            END
        """)

    if "human_reviews" in tables:
        review_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(human_reviews)"
            )
        }
        if "stage" not in review_columns:
            connection.exec_driver_sql(
                "ALTER TABLE human_reviews "
                "ADD COLUMN stage VARCHAR(20) NOT NULL DEFAULT 'initial'"
            )
        connection.exec_driver_sql("""
            CREATE INDEX IF NOT EXISTS ix_human_reviews_stage
            ON human_reviews(stage)
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_human_review_stage_insert
            BEFORE INSERT ON human_reviews
            WHEN NEW.stage NOT IN ('initial','secondary','arbitration')
            BEGIN
                SELECT RAISE(ABORT, 'Invalid human review stage');
            END
        """)
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS trg_human_review_immutable
            BEFORE UPDATE ON human_reviews
            BEGIN
                SELECT RAISE(ABORT, 'Human review history is append-only');
            END
        """)

    if "prompt_versions" in tables:
        prompt_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(prompt_versions)"
            )
        }
        if "source_optimization_run_id" not in prompt_columns:
            connection.exec_driver_sql(
                "ALTER TABLE prompt_versions "
                "ADD COLUMN source_optimization_run_id INTEGER"
            )
        connection.exec_driver_sql("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_prompt_versions_source_optimization_run
            ON prompt_versions(source_optimization_run_id)
            WHERE source_optimization_run_id IS NOT NULL
        """)


def _migration_020_add_material_packages_and_review_panels(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "assets" in tables:
        asset_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(assets)"
            )
        }
        asset_created_at = (
            "created_at"
            if "created_at" in asset_columns
            else "CURRENT_TIMESTAMP"
        )
        asset_order = (
            "created_at, id"
            if "created_at" in asset_columns
            else "id"
        )
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS material_packages (
                id INTEGER PRIMARY KEY,
                package_key VARCHAR(80) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                source VARCHAR(30) NOT NULL DEFAULT 'manual_upload'
                    CHECK(source IN (
                        'manual_upload','production_import','legacy_backfill'
                    )),
                created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS material_package_items (
                id INTEGER PRIMARY KEY,
                package_id INTEGER NOT NULL
                    REFERENCES material_packages(id) ON DELETE CASCADE,
                asset_id INTEGER NOT NULL
                    REFERENCES assets(id) ON DELETE RESTRICT,
                original_name VARCHAR(500) NOT NULL,
                duplicate BOOLEAN NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_material_package_item_position
                    UNIQUE(package_id, position)
            )
        """)
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_material_packages_created_at "
            "ON material_packages(created_at)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_material_package_items_package_id "
            "ON material_package_items(package_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_material_package_items_asset_id "
            "ON material_package_items(asset_id)"
        )
        if connection.exec_driver_sql(
            "SELECT 1 FROM assets LIMIT 1"
        ).first() is not None and connection.exec_driver_sql(
            "SELECT 1 FROM material_packages LIMIT 1"
        ).first() is None:
            connection.exec_driver_sql(f"""
                INSERT INTO material_packages(
                    package_key, name, source, created_by, created_at
                )
                SELECT
                    'legacy-backfill-v20',
                    '历史素材',
                    'legacy_backfill',
                    'migration-v20',
                    COALESCE(MIN({asset_created_at}), CURRENT_TIMESTAMP)
                FROM assets
            """)
            connection.exec_driver_sql(f"""
                INSERT INTO material_package_items(
                    package_id, asset_id, original_name, duplicate,
                    position, created_at
                )
                SELECT
                    (SELECT id FROM material_packages
                     WHERE package_key = 'legacy-backfill-v20'),
                    id,
                    original_name,
                    0,
                    ROW_NUMBER() OVER (ORDER BY {asset_order}),
                    {asset_created_at}
                FROM assets
            """)

    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS agent_plan_versions (
            id INTEGER PRIMARY KEY,
            name VARCHAR(160) NOT NULL,
            version VARCHAR(80) NOT NULL UNIQUE,
            plan_json TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','published','archived')),
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_agent_plan_versions_status "
        "ON agent_plan_versions(status)"
    )
    connection.exec_driver_sql(
        """
        INSERT OR IGNORE INTO agent_plan_versions(
            name, version, plan_json, status, created_by
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "A预检—B美感—高风险保守复核",
            "controlled-agent-plan-v1",
            '{"roles":["precheck","aesthetic","risk_review"],'
            '"routing":"controlled","max_rounds":3}',
            "published",
            "migration-v20",
        ),
    )
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS review_workflow_policies (
            id INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 1,
            initial_reviewers INTEGER NOT NULL DEFAULT 1
                CHECK(
                    initial_reviewers >= 1
                    AND initial_reviewers <= 9
                    AND initial_reviewers % 2 = 1
                ),
            updated_by VARCHAR(80) NOT NULL DEFAULT 'system',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql("""
        INSERT OR IGNORE INTO review_workflow_policies(
            id, revision, initial_reviewers, updated_by, updated_at
        ) VALUES (1, 1, 1, 'migration-v20', CURRENT_TIMESTAMP)
    """)
    if "strategy_bundles" in tables:
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(strategy_bundles)"
            )
        }
        if "agent_plan_version" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE strategy_bundles ADD COLUMN "
                "agent_plan_version VARCHAR(80) NOT NULL "
                "DEFAULT 'controlled-agent-plan-v1'"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_agent_plan_version "
            "ON strategy_bundles(agent_plan_version)"
        )

    if "evaluation_results" in tables and "human_reviews" in tables:
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS review_panels (
                id INTEGER PRIMARY KEY,
                evaluation_id INTEGER NOT NULL UNIQUE
                    REFERENCES evaluation_results(id) ON DELETE CASCADE,
                required_reviewers INTEGER NOT NULL DEFAULT 1
                    CHECK(
                        required_reviewers >= 1
                        AND required_reviewers <= 9
                        AND required_reviewers % 2 = 1
                    ),
                status VARCHAR(30) NOT NULL DEFAULT 'collecting'
                    CHECK(status IN (
                        'collecting','lead_adjudication','completed'
                    )),
                revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
                final_review_id INTEGER
                    REFERENCES human_reviews(id) ON DELETE RESTRICT,
                final_truth_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        """)
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_review_panels_status "
            "ON review_panels(status)"
        )
        review_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(human_reviews)"
            )
        }
        if "panel_id" not in review_columns:
            connection.exec_driver_sql(
                "ALTER TABLE human_reviews ADD COLUMN panel_id INTEGER "
                "REFERENCES review_panels(id) ON DELETE RESTRICT"
            )
        if "panel_revision" not in review_columns:
            connection.exec_driver_sql(
                "ALTER TABLE human_reviews ADD COLUMN panel_revision INTEGER"
            )
        if "reviewer_name" in review_columns:
            connection.exec_driver_sql("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    uq_human_review_panel_reviewer
                ON human_reviews(panel_id, reviewer_name)
                WHERE panel_id IS NOT NULL
            """)
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS optimization_case_queue (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                evaluation_id INTEGER NOT NULL
                    REFERENCES evaluation_results(id) ON DELETE RESTRICT,
                final_review_id INTEGER NOT NULL
                    REFERENCES human_reviews(id) ON DELETE RESTRICT,
                prompt_version VARCHAR(40) NOT NULL,
                severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                    CHECK(severity IN ('P0','P1','P2','P3')),
                case_json TEXT NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending','batched','processing','completed','failed'
                    )),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_status "
            "ON optimization_case_queue(status)"
        )

    if "prompt_versions" in tables:
        prompt_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(prompt_versions)"
            )
        }
        if "rollback_prompt_id" not in prompt_columns:
            connection.exec_driver_sql(
                "ALTER TABLE prompt_versions ADD COLUMN rollback_prompt_id "
                "INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL"
            )
        if "canary_status" not in prompt_columns:
            connection.exec_driver_sql(
                "ALTER TABLE prompt_versions ADD COLUMN canary_status "
                "VARCHAR(20) NOT NULL DEFAULT 'not_started'"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_prompt_versions_canary_status "
            "ON prompt_versions(canary_status)"
        )


def _migration_021_add_prompt_metric_snapshots(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "prompt_versions" not in tables:
        return
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS prompt_metric_snapshots (
            id INTEGER PRIMARY KEY,
            prompt_id INTEGER NOT NULL
                REFERENCES prompt_versions(id) ON DELETE CASCADE,
            task_set_key VARCHAR(160) NOT NULL,
            task_set_hash VARCHAR(64) NOT NULL,
            evaluation_ids_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            total_count INTEGER NOT NULL,
            reviewed_count INTEGER NOT NULL,
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_prompt_metric_snapshot_task_set
                UNIQUE(prompt_id, task_set_hash),
            CONSTRAINT ck_prompt_metric_snapshot_counts
                CHECK(
                    total_count >= 1
                    AND reviewed_count >= 0
                    AND reviewed_count <= total_count
                )
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_prompt_metric_snapshots_prompt_id "
        "ON prompt_metric_snapshots(prompt_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_prompt_metric_snapshots_task_set_key "
        "ON prompt_metric_snapshots(task_set_key)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_prompt_metric_snapshots_task_set_hash "
        "ON prompt_metric_snapshots(task_set_hash)"
    )


def _migration_022_add_phase_b_automation_feedback_benchmarks(
    connection: Connection,
) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS automation_policies (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            enabled BOOLEAN NOT NULL DEFAULT 0,
            dry_run BOOLEAN NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 1,
            case_threshold INTEGER NOT NULL DEFAULT 10
                CHECK(case_threshold BETWEEN 1 AND 1000),
            immediate_severities_json TEXT NOT NULL DEFAULT '["P0","P1"]',
            daily_budget_micros INTEGER NOT NULL DEFAULT 0
                CHECK(daily_budget_micros >= 0),
            cooldown_seconds INTEGER NOT NULL DEFAULT 21600
                CHECK(cooldown_seconds >= 0),
            max_candidates INTEGER NOT NULL DEFAULT 1
                CHECK(max_candidates BETWEEN 1 AND 5),
            lease_seconds INTEGER NOT NULL DEFAULT 300
                CHECK(lease_seconds BETWEEN 30 AND 3600),
            max_attempts INTEGER NOT NULL DEFAULT 3
                CHECK(max_attempts BETWEEN 1 AND 10),
            base_retry_seconds INTEGER NOT NULL DEFAULT 60
                CHECK(base_retry_seconds BETWEEN 1 AND 86400),
            updated_by VARCHAR(80) NOT NULL DEFAULT 'system',
            last_triggered_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql("""
        INSERT OR IGNORE INTO automation_policies (
            id, enabled, dry_run, revision
        ) VALUES (1, 0, 1, 1)
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS automation_optimization_runs (
            id INTEGER PRIMARY KEY,
            run_key VARCHAR(160) NOT NULL UNIQUE,
            base_prompt_version VARCHAR(40) NOT NULL,
            policy_revision INTEGER NOT NULL,
            status VARCHAR(40) NOT NULL DEFAULT 'planned'
                CHECK(status IN (
                    'planned','awaiting_executor','running',
                    'awaiting_release_review','failed','cancelled'
                )),
            dry_run BOOLEAN NOT NULL DEFAULT 1,
            trigger_reason VARCHAR(80) NOT NULL,
            case_ids_json TEXT NOT NULL,
            frozen_input_json TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            candidate_count INTEGER NOT NULL DEFAULT 0,
            estimated_cost_micros INTEGER NOT NULL DEFAULT 0,
            actual_cost_micros INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(80) NOT NULL DEFAULT 'automation',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            CHECK(
                estimated_cost_micros >= 0
                AND actual_cost_micros >= 0
            )
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_runs_status "
        "ON automation_optimization_runs(status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_runs_created_at "
        "ON automation_optimization_runs(created_at)"
    )

    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS production_feedback_events (
            id INTEGER PRIMARY KEY,
            event_id VARCHAR(160) NOT NULL UNIQUE,
            schema_version VARCHAR(40) NOT NULL,
            event_type VARCHAR(80) NOT NULL,
            source_system VARCHAR(120) NOT NULL,
            occurred_at DATETIME NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            payload_json TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'accepted'
                CHECK(status IN ('accepted','mapped','rejected')),
            received_by VARCHAR(80) NOT NULL DEFAULT 'system',
            received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_production_feedback_received_at "
        "ON production_feedback_events(received_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_production_feedback_source "
        "ON production_feedback_events(source_system)"
    )

    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "optimization_case_queue" in tables:
        columns = {
            row[1]: row
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(optimization_case_queue)"
            )
        }
        needs_rebuild = (
            "source_type" not in columns
            or bool(columns["evaluation_id"][3])
            or bool(columns["final_review_id"][3])
        )
        if needs_rebuild:
            connection.exec_driver_sql(
                "ALTER TABLE optimization_case_queue "
                "RENAME TO optimization_case_queue_v21"
            )
            connection.exec_driver_sql("""
                CREATE TABLE optimization_case_queue (
                    id INTEGER PRIMARY KEY,
                    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                    evaluation_id INTEGER
                        REFERENCES evaluation_results(id) ON DELETE RESTRICT,
                    final_review_id INTEGER
                        REFERENCES human_reviews(id) ON DELETE RESTRICT,
                    source_type VARCHAR(30) NOT NULL DEFAULT 'human_review',
                    source_event_id INTEGER UNIQUE
                        REFERENCES production_feedback_events(id)
                        ON DELETE RESTRICT,
                    prompt_version VARCHAR(40) NOT NULL,
                    severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                        CHECK(severity IN ('P0','P1','P2','P3')),
                    case_json TEXT NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'pending'
                        CHECK(status IN (
                            'pending','batched','processing',
                            'completed','failed'
                        )),
                    lease_owner VARCHAR(120),
                    lease_token VARCHAR(80),
                    lease_expires_at DATETIME,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at DATETIME,
                    last_error TEXT NOT NULL DEFAULT '',
                    automation_run_id INTEGER
                        REFERENCES automation_optimization_runs(id)
                        ON DELETE SET NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK(
                        (source_type = 'human_review'
                         AND evaluation_id IS NOT NULL
                         AND final_review_id IS NOT NULL
                         AND source_event_id IS NULL)
                        OR
                        (source_type = 'production_feedback'
                         AND evaluation_id IS NULL
                         AND final_review_id IS NULL
                         AND source_event_id IS NOT NULL)
                    )
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO optimization_case_queue (
                    id, idempotency_key, evaluation_id, final_review_id,
                    source_type, source_event_id, prompt_version, severity,
                    case_json, status, lease_owner, lease_token,
                    lease_expires_at, attempt_count, next_attempt_at,
                    last_error, automation_run_id, created_at, updated_at
                )
                SELECT
                    id, idempotency_key, evaluation_id, final_review_id,
                    'human_review', NULL, prompt_version, severity,
                    case_json, status, NULL, NULL, NULL, 0, NULL, '', NULL,
                    created_at, updated_at
                FROM optimization_case_queue_v21
            """)
            connection.exec_driver_sql(
                "DROP TABLE optimization_case_queue_v21"
            )
    else:
        connection.exec_driver_sql("""
            CREATE TABLE optimization_case_queue (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                evaluation_id INTEGER
                    REFERENCES evaluation_results(id) ON DELETE RESTRICT,
                final_review_id INTEGER
                    REFERENCES human_reviews(id) ON DELETE RESTRICT,
                source_type VARCHAR(30) NOT NULL DEFAULT 'human_review',
                source_event_id INTEGER UNIQUE
                    REFERENCES production_feedback_events(id)
                    ON DELETE RESTRICT,
                prompt_version VARCHAR(40) NOT NULL,
                severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                    CHECK(severity IN ('P0','P1','P2','P3')),
                case_json TEXT NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending','batched','processing',
                        'completed','failed'
                    )),
                lease_owner VARCHAR(120),
                lease_token VARCHAR(80),
                lease_expires_at DATETIME,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at DATETIME,
                last_error TEXT NOT NULL DEFAULT '',
                automation_run_id INTEGER
                    REFERENCES automation_optimization_runs(id)
                    ON DELETE SET NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (source_type = 'human_review'
                     AND evaluation_id IS NOT NULL
                     AND final_review_id IS NOT NULL
                     AND source_event_id IS NULL)
                    OR
                    (source_type = 'production_feedback'
                     AND evaluation_id IS NULL
                     AND final_review_id IS NULL
                     AND source_event_id IS NOT NULL)
                )
            )
        """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_status "
        "ON optimization_case_queue(status)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_source_type "
        "ON optimization_case_queue(source_type)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_prompt "
        "ON optimization_case_queue(prompt_version)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_lease "
        "ON optimization_case_queue(lease_expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_lease_token "
        "ON optimization_case_queue(lease_token)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_next_attempt "
        "ON optimization_case_queue(next_attempt_at)",
        "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_run "
        "ON optimization_case_queue(automation_run_id)",
    ):
        connection.exec_driver_sql(statement)

    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY,
            event_key VARCHAR(200) NOT NULL UNIQUE,
            category VARCHAR(80) NOT NULL,
            action VARCHAR(120) NOT NULL,
            subject_type VARCHAR(80) NOT NULL,
            subject_id VARCHAR(160) NOT NULL,
            actor VARCHAR(80) NOT NULL DEFAULT 'system',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_audit_events_created_at "
        "ON audit_events(created_at)"
    )
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS model_benchmark_experiments (
            id INTEGER PRIMARY KEY,
            experiment_key VARCHAR(160) NOT NULL UNIQUE,
            name VARCHAR(200) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK(status IN (
                    'draft','running','completed','failed','cancelled'
                )),
            execution_mode VARCHAR(20) NOT NULL DEFAULT 'disabled'
                CHECK(execution_mode IN ('disabled','test')),
            cohort_hash VARCHAR(64) NOT NULL,
            snapshot_hash VARCHAR(64) NOT NULL,
            frozen_snapshot_json TEXT NOT NULL,
            quality_gate_json TEXT NOT NULL,
            decision_json TEXT NOT NULL DEFAULT '{}',
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            finished_at DATETIME
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS model_benchmark_variants (
            id INTEGER PRIMARY KEY,
            experiment_id INTEGER NOT NULL
                REFERENCES model_benchmark_experiments(id)
                ON DELETE CASCADE,
            model_key VARCHAR(80) NOT NULL,
            provider VARCHAR(80) NOT NULL,
            model_id VARCHAR(200) NOT NULL,
            pricing_json TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','running','completed','failed')),
            metrics_json TEXT NOT NULL DEFAULT '{}',
            observations_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT NOT NULL DEFAULT '',
            started_at DATETIME,
            finished_at DATETIME,
            UNIQUE(experiment_id, model_key)
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_benchmark_experiments_status "
        "ON model_benchmark_experiments(status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_benchmark_variants_experiment "
        "ON model_benchmark_variants(experiment_id)"
    )

    for table in ("production_feedback_events", "audit_events"):
        connection.exec_driver_sql(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is immutable');
            END
        """)
        connection.exec_driver_sql(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} cannot be deleted');
            END
        """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_benchmark_snapshot_no_update
        BEFORE UPDATE OF
            experiment_key, cohort_hash, snapshot_hash,
            frozen_snapshot_json, quality_gate_json
        ON model_benchmark_experiments
        BEGIN
            SELECT RAISE(ABORT, 'benchmark snapshot is immutable');
        END
    """)


def _migration_023_enforce_material_package_immutability(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    immutable_tables = {
        "material_packages": "MaterialPackage",
        "material_package_items": "MaterialPackageItem",
    }
    for table, entity in immutable_tables.items():
        if table not in tables:
            continue
        connection.exec_driver_sql(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{entity} is immutable');
            END
        """)
        connection.exec_driver_sql(f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{entity} cannot be deleted');
            END
        """)


def _migration_024_add_real_executor_safety(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    def add_columns(table: str, columns: tuple[tuple[str, str], ...]) -> None:
        if table not in tables:
            return
        existing = {
            row[1]
            for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
        }
        for name, definition in columns:
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )

    add_columns("users", (("is_admin", "BOOLEAN NOT NULL DEFAULT 1"),))
    pricing_columns = (
        ("input_micros_per_million_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("output_micros_per_million_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("max_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    )
    add_columns(
        "model_configs",
        pricing_columns + (("benchmark_enabled", "BOOLEAN NOT NULL DEFAULT 0"),),
    )
    add_columns("optimizer_configs", pricing_columns)
    add_columns(
        "prompt_versions",
        ((
            "source_automation_run_id",
            "INTEGER REFERENCES automation_optimization_runs(id) ON DELETE SET NULL",
        ),),
    )
    if "prompt_versions" in tables:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_prompt_versions_source_automation_run "
            "ON prompt_versions(source_automation_run_id)"
        )
    add_columns(
        "automation_optimization_runs",
        (
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("total_tokens", "INTEGER"),
            ("retryable", "BOOLEAN NOT NULL DEFAULT 0"),
        ),
    )
    add_columns(
        "optimizer_configs",
        (
            ("protocol", "VARCHAR(40) NOT NULL DEFAULT 'openai_chat'"),
            ("capabilities_json", "TEXT NOT NULL DEFAULT '[\"text\",\"structured_output\"]'"),
        ),
    )
    add_columns(
        "model_benchmark_experiments",
        (
            ("max_round_cost_micros", "INTEGER NOT NULL DEFAULT 0"),
            ("actual_cost_micros", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    add_columns(
        "model_benchmark_variants",
        (
            (
                "model_config_id",
                (
                    "INTEGER REFERENCES model_configs(id) ON DELETE RESTRICT"
                    if "model_configs" in tables
                    else "INTEGER"
                ),
            ),
            ("input_tokens", "INTEGER"),
            ("output_tokens", "INTEGER"),
            ("total_tokens", "INTEGER"),
            ("actual_cost_micros", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_benchmark_variants_model_config "
        "ON model_benchmark_variants(model_config_id)"
    )
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS automation_budget_days (
            budget_date VARCHAR(10) PRIMARY KEY,
            reserved_micros INTEGER NOT NULL DEFAULT 0,
            spent_micros INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_automation_budget_day_costs
                CHECK(reserved_micros >= 0 AND spent_micros >= 0)
        )
    """)

    automation_sql = str(
        connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='automation_optimization_runs'"
        ).scalar_one_or_none()
        or ""
    )
    if automation_sql and (
        "'processing'" not in automation_sql or "'succeeded'" not in automation_sql
    ):
        connection.exec_driver_sql(
            "ALTER TABLE optimization_case_queue "
            "RENAME TO optimization_case_queue_v24"
        )
        connection.exec_driver_sql(
            "ALTER TABLE automation_optimization_runs "
            "RENAME TO automation_optimization_runs_v24"
        )
        connection.exec_driver_sql("""
            CREATE TABLE automation_optimization_runs (
                id INTEGER PRIMARY KEY,
                run_key VARCHAR(160) NOT NULL UNIQUE,
                base_prompt_version VARCHAR(40) NOT NULL,
                policy_revision INTEGER NOT NULL,
                status VARCHAR(40) NOT NULL DEFAULT 'planned'
                    CHECK(status IN (
                        'planned','awaiting_executor','processing','succeeded',
                        'running','awaiting_release_review','failed','cancelled'
                    )),
                dry_run BOOLEAN NOT NULL DEFAULT 1,
                trigger_reason VARCHAR(80) NOT NULL,
                case_ids_json TEXT NOT NULL,
                frozen_input_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                candidate_count INTEGER NOT NULL DEFAULT 0,
                estimated_cost_micros INTEGER NOT NULL DEFAULT 0,
                actual_cost_micros INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                retryable BOOLEAN NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                created_by VARCHAR(80) NOT NULL DEFAULT 'automation',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                CHECK(estimated_cost_micros >= 0 AND actual_cost_micros >= 0)
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO automation_optimization_runs (
                id, run_key, base_prompt_version, policy_revision, status,
                dry_run, trigger_reason, case_ids_json, frozen_input_json,
                result_json, candidate_count, estimated_cost_micros,
                actual_cost_micros, input_tokens, output_tokens, total_tokens,
                retryable, error_message, created_by, created_at, finished_at
            )
            SELECT
                id, run_key, base_prompt_version, policy_revision, status,
                dry_run, trigger_reason, case_ids_json, frozen_input_json,
                result_json, candidate_count, estimated_cost_micros,
                actual_cost_micros, input_tokens, output_tokens, total_tokens,
                retryable, error_message, created_by, created_at, finished_at
            FROM automation_optimization_runs_v24
        """)
        connection.exec_driver_sql("""
            CREATE TABLE optimization_case_queue (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                evaluation_id INTEGER REFERENCES evaluation_results(id)
                    ON DELETE RESTRICT,
                final_review_id INTEGER REFERENCES human_reviews(id)
                    ON DELETE RESTRICT,
                source_type VARCHAR(30) NOT NULL DEFAULT 'human_review',
                source_event_id INTEGER UNIQUE
                    REFERENCES production_feedback_events(id) ON DELETE RESTRICT,
                prompt_version VARCHAR(40) NOT NULL,
                severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                    CHECK(severity IN ('P0','P1','P2','P3')),
                case_json TEXT NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending','batched','processing','completed','failed'
                    )),
                lease_owner VARCHAR(120),
                lease_token VARCHAR(80),
                lease_expires_at DATETIME,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at DATETIME,
                last_error TEXT NOT NULL DEFAULT '',
                automation_run_id INTEGER
                    REFERENCES automation_optimization_runs(id) ON DELETE SET NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (source_type = 'human_review' AND evaluation_id IS NOT NULL
                     AND final_review_id IS NOT NULL AND source_event_id IS NULL)
                    OR
                    (source_type = 'production_feedback' AND evaluation_id IS NULL
                     AND final_review_id IS NULL AND source_event_id IS NOT NULL)
                )
            )
        """)
        if int(
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM optimization_case_queue_v24"
            ).scalar_one()
        ):
            connection.exec_driver_sql("""
                INSERT INTO optimization_case_queue (
                    id, idempotency_key, evaluation_id, final_review_id,
                    source_type, source_event_id, prompt_version, severity,
                    case_json, status, lease_owner, lease_token, lease_expires_at,
                    attempt_count, next_attempt_at, last_error, automation_run_id,
                    created_at, updated_at
                )
                SELECT
                    id, idempotency_key, evaluation_id, final_review_id,
                    source_type, source_event_id, prompt_version, severity,
                    case_json, status, lease_owner, lease_token, lease_expires_at,
                    attempt_count, next_attempt_at, last_error, automation_run_id,
                    created_at, updated_at
                FROM optimization_case_queue_v24
            """)
        connection.exec_driver_sql("DROP TABLE optimization_case_queue_v24")
        connection.exec_driver_sql(
            "DROP TABLE automation_optimization_runs_v24"
        )
        for statement in (
            "CREATE INDEX ix_automation_runs_status "
            "ON automation_optimization_runs(status)",
            "CREATE INDEX ix_automation_runs_created_at "
            "ON automation_optimization_runs(created_at)",
            "CREATE INDEX ix_optimization_case_queue_status "
            "ON optimization_case_queue(status)",
            "CREATE INDEX ix_optimization_case_queue_source_type "
            "ON optimization_case_queue(source_type)",
            "CREATE INDEX ix_optimization_case_queue_prompt "
            "ON optimization_case_queue(prompt_version)",
            "CREATE INDEX ix_optimization_case_queue_lease "
            "ON optimization_case_queue(lease_expires_at)",
            "CREATE INDEX ix_optimization_case_queue_lease_token "
            "ON optimization_case_queue(lease_token)",
            "CREATE INDEX ix_optimization_case_queue_next_attempt "
            "ON optimization_case_queue(next_attempt_at)",
            "CREATE INDEX ix_optimization_case_queue_run "
            "ON optimization_case_queue(automation_run_id)",
        ):
            connection.exec_driver_sql(statement)

    benchmark_sql = str(
        connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='model_benchmark_experiments'"
        ).scalar_one_or_none()
        or ""
    )
    if benchmark_sql and "'real'" not in benchmark_sql:
        model_config_reference = (
            "REFERENCES model_configs(id) ON DELETE RESTRICT"
            if "model_configs" in tables
            else ""
        )
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_benchmark_snapshot_no_update")
        connection.exec_driver_sql(
            "ALTER TABLE model_benchmark_variants "
            "RENAME TO model_benchmark_variants_v24"
        )
        connection.exec_driver_sql(
            "ALTER TABLE model_benchmark_experiments "
            "RENAME TO model_benchmark_experiments_v24"
        )
        connection.exec_driver_sql("""
            CREATE TABLE model_benchmark_experiments (
                id INTEGER PRIMARY KEY,
                experiment_key VARCHAR(160) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK(status IN (
                        'draft','running','completed','failed','cancelled'
                    )),
                execution_mode VARCHAR(20) NOT NULL DEFAULT 'test'
                    CHECK(execution_mode IN ('disabled','test','real')),
                cohort_hash VARCHAR(64) NOT NULL,
                snapshot_hash VARCHAR(64) NOT NULL,
                frozen_snapshot_json TEXT NOT NULL,
                quality_gate_json TEXT NOT NULL,
                max_round_cost_micros INTEGER NOT NULL DEFAULT 0,
                actual_cost_micros INTEGER NOT NULL DEFAULT 0,
                decision_json TEXT NOT NULL DEFAULT '{}',
                created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO model_benchmark_experiments (
                id, experiment_key, name, status, execution_mode,
                cohort_hash, snapshot_hash, frozen_snapshot_json,
                quality_gate_json, max_round_cost_micros,
                actual_cost_micros, decision_json, created_by, created_at,
                started_at, finished_at
            )
            SELECT
                id, experiment_key, name, status, execution_mode,
                cohort_hash, snapshot_hash, frozen_snapshot_json,
                quality_gate_json, max_round_cost_micros,
                actual_cost_micros, decision_json, created_by, created_at,
                started_at, finished_at
            FROM model_benchmark_experiments_v24
        """)
        connection.exec_driver_sql("""
            CREATE TABLE model_benchmark_variants (
                id INTEGER PRIMARY KEY,
                experiment_id INTEGER NOT NULL
                    REFERENCES model_benchmark_experiments(id) ON DELETE CASCADE,
                model_key VARCHAR(80) NOT NULL,
                provider VARCHAR(80) NOT NULL,
                model_id VARCHAR(200) NOT NULL,
                model_config_id INTEGER
                    {model_config_reference},
                pricing_json TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','completed','failed')),
                metrics_json TEXT NOT NULL DEFAULT '{}',
                observations_json TEXT NOT NULL DEFAULT '[]',
                error_message TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                actual_cost_micros INTEGER NOT NULL DEFAULT 0,
                started_at DATETIME,
                finished_at DATETIME,
                UNIQUE(experiment_id, model_key)
            )
        """.replace("{model_config_reference}", model_config_reference))
        connection.exec_driver_sql("""
            INSERT INTO model_benchmark_variants (
                id, experiment_id, model_key, provider, model_id,
                model_config_id, pricing_json, status, metrics_json,
                observations_json, error_message, input_tokens, output_tokens,
                total_tokens, actual_cost_micros, started_at, finished_at
            )
            SELECT
                id, experiment_id, model_key, provider, model_id,
                model_config_id, pricing_json, status, metrics_json,
                observations_json, error_message, input_tokens, output_tokens,
                total_tokens, actual_cost_micros, started_at, finished_at
            FROM model_benchmark_variants_v24
        """)
        connection.exec_driver_sql("DROP TABLE model_benchmark_variants_v24")
        connection.exec_driver_sql("DROP TABLE model_benchmark_experiments_v24")
        connection.exec_driver_sql(
            "CREATE INDEX ix_model_benchmark_experiments_status "
            "ON model_benchmark_experiments(status)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_model_benchmark_variants_experiment "
            "ON model_benchmark_variants(experiment_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_model_benchmark_variants_model_config "
            "ON model_benchmark_variants(model_config_id)"
        )
    if "model_benchmark_experiments" in tables:
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_benchmark_snapshot_no_update"
        )
        connection.exec_driver_sql("""
            CREATE TRIGGER trg_benchmark_snapshot_no_update
            BEFORE UPDATE OF
                experiment_key, execution_mode, cohort_hash, snapshot_hash,
                frozen_snapshot_json, quality_gate_json,
                max_round_cost_micros
            ON model_benchmark_experiments
            BEGIN
                SELECT RAISE(ABORT, 'benchmark snapshot is immutable');
            END
        """)


def _migration_025_add_baseline_regression_and_repair_prompt_fk(
    connection: Connection,
) -> None:
    """Migration 26: add baseline regression in a legal SQLite rebuild window.

    The repository's initial ORM schema is unnumbered, so product-facing
    Migration 26 is stored as schema_migrations version 25.
    """
    raw = connection.connection.driver_connection
    raw.commit()
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("PRAGMA legacy_alter_table=ON")
    try:
        raw.execute("BEGIN IMMEDIATE")
        tables = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        if "prompt_versions" in tables:
            prompt_columns = {
                row[1] for row in raw.execute("PRAGMA table_info(prompt_versions)")
            }
            if "source_automation_run_id" not in prompt_columns:
                raw.execute(
                    "ALTER TABLE prompt_versions ADD COLUMN "
                    "source_automation_run_id INTEGER REFERENCES "
                    "automation_optimization_runs(id) ON DELETE SET NULL"
                )
                prompt_columns.add("source_automation_run_id")
            automation_fk = next(
                (
                    row
                    for row in raw.execute(
                        "PRAGMA foreign_key_list(prompt_versions)"
                    )
                    if row[3] == "source_automation_run_id"
                ),
                None,
            )
            prompt_sql = str(
                raw.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='prompt_versions'"
                ).fetchone()[0]
                or ""
            )
            prompt_fk_polluted = (
                automation_fk is None
                or automation_fk[2] != "automation_optimization_runs"
                or "automation_optimization_runs_v24" in prompt_sql
            )
            supported_prompt_columns = {
                "id",
                "stage",
                "name",
                "version",
                "system_prompt",
                "user_prompt",
                "rubric_version",
                "status",
                "source",
                "source_optimization_run_id",
                "source_automation_run_id",
                "rollback_prompt_id",
                "canary_status",
                "change_note",
                "created_by",
                "created_at",
                "updated_at",
            }
            if not supported_prompt_columns.issubset(prompt_columns):
                prompt_fk_polluted = False
            if prompt_fk_polluted:
                before_count = raw.execute(
                    "SELECT COUNT(*) FROM prompt_versions"
                ).fetchone()[0]
                raw.execute(
                    "ALTER TABLE prompt_versions "
                    "RENAME TO prompt_versions_m26"
                )
                raw.execute("""
                    CREATE TABLE prompt_versions (
                        id INTEGER PRIMARY KEY,
                        stage VARCHAR(10) NOT NULL,
                        name VARCHAR(120) NOT NULL,
                        version VARCHAR(40) NOT NULL,
                        system_prompt TEXT NOT NULL,
                        user_prompt TEXT NOT NULL,
                        rubric_version VARCHAR(40) NOT NULL
                            DEFAULT 'rubric-v2.1',
                        status VARCHAR(20) NOT NULL DEFAULT 'draft',
                        source VARCHAR(20) NOT NULL DEFAULT 'manual',
                        source_optimization_run_id INTEGER,
                        source_automation_run_id INTEGER
                            REFERENCES automation_optimization_runs(id)
                            ON DELETE SET NULL,
                        rollback_prompt_id INTEGER
                            REFERENCES prompt_versions(id) ON DELETE SET NULL,
                        canary_status VARCHAR(20) NOT NULL
                            DEFAULT 'not_started',
                        change_note TEXT NOT NULL DEFAULT '',
                        created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_prompt_versions_source_optimization_run
                            UNIQUE(source_optimization_run_id)
                    )
                """)
                prompt_copy_columns = (
                    "id, stage, name, version, system_prompt, user_prompt, "
                    "rubric_version, status, source, source_optimization_run_id, "
                    "source_automation_run_id, rollback_prompt_id, canary_status, "
                    "change_note, created_by, created_at, updated_at"
                )
                raw.execute(
                    f"INSERT INTO prompt_versions ({prompt_copy_columns}) "
                    f"SELECT {prompt_copy_columns} FROM prompt_versions_m26"
                )
                after_count = raw.execute(
                    "SELECT COUNT(*) FROM prompt_versions"
                ).fetchone()[0]
                if before_count != after_count:
                    raise RuntimeError("prompt_versions 重建行数校验失败")
                raw.execute("DROP TABLE prompt_versions_m26")

            if supported_prompt_columns.issubset(prompt_columns):
                for statement in (
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_stage "
                    "ON prompt_versions(stage)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_version "
                    "ON prompt_versions(version)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_status "
                    "ON prompt_versions(status)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_source_optimization_run_id "
                    "ON prompt_versions(source_optimization_run_id)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_source_automation_run_id "
                    "ON prompt_versions(source_automation_run_id)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_rollback_prompt_id "
                    "ON prompt_versions(rollback_prompt_id)",
                    "CREATE INDEX IF NOT EXISTS ix_prompt_versions_canary_status "
                    "ON prompt_versions(canary_status)",
                ):
                    raw.execute(statement)

        required_business_tables = {
            "assets",
            "material_packages",
            "evaluation_jobs",
            "evaluation_results",
            "strategy_bundles",
            "optimization_case_queue",
            "human_reviews",
            "production_feedback_events",
            "automation_optimization_runs",
        }
        if not required_business_tables.issubset(tables):
            raw.commit()
            return

        raw.execute("""
            CREATE TABLE IF NOT EXISTS baseline_sets (
                id INTEGER PRIMARY KEY,
                name VARCHAR(160) NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                default_expected_level VARCHAR(10) NOT NULL
                    CHECK(default_expected_level IN ('L1','L2','L3','L4','L5')),
                fingerprint VARCHAR(64) NOT NULL,
                created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        raw.execute("""
            CREATE TABLE IF NOT EXISTS baseline_set_items (
                id INTEGER PRIMARY KEY,
                baseline_set_id INTEGER NOT NULL
                    REFERENCES baseline_sets(id) ON DELETE RESTRICT,
                asset_id INTEGER NOT NULL
                    REFERENCES assets(id) ON DELETE RESTRICT,
                source_package_id INTEGER
                    REFERENCES material_packages(id) ON DELETE RESTRICT,
                expected_level VARCHAR(10) NOT NULL
                    CHECK(expected_level IN ('L1','L2','L3','L4','L5')),
                asset_snapshot_json TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_baseline_set_asset
                    UNIQUE(baseline_set_id, asset_id)
            )
        """)
        raw.execute("""
            CREATE TABLE IF NOT EXISTS baseline_regression_runs (
                id INTEGER PRIMARY KEY,
                baseline_set_id INTEGER NOT NULL
                    REFERENCES baseline_sets(id) ON DELETE RESTRICT,
                sequence_no INTEGER NOT NULL CHECK(sequence_no >= 1),
                previous_run_id INTEGER
                    REFERENCES baseline_regression_runs(id) ON DELETE RESTRICT,
                strategy_bundle_id INTEGER NOT NULL
                    REFERENCES strategy_bundles(id) ON DELETE RESTRICT,
                strategy_snapshot_json TEXT NOT NULL,
                baseline_set_fingerprint VARCHAR(64) NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'running'
                    CHECK(status IN (
                        'running','completed','partial_failed','failed'
                    )),
                total INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                valid_predictions INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                CONSTRAINT uq_baseline_run_sequence
                    UNIQUE(baseline_set_id, sequence_no)
            )
        """)
        raw.execute("""
            CREATE TABLE IF NOT EXISTS baseline_regression_items (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL
                    REFERENCES baseline_regression_runs(id) ON DELETE CASCADE,
                baseline_set_item_id INTEGER NOT NULL
                    REFERENCES baseline_set_items(id) ON DELETE RESTRICT,
                asset_id INTEGER NOT NULL
                    REFERENCES assets(id) ON DELETE RESTRICT,
                expected_level VARCHAR(10) NOT NULL
                    CHECK(expected_level IN ('L1','L2','L3','L4','L5')),
                job_id INTEGER
                    REFERENCES evaluation_jobs(id) ON DELETE SET NULL,
                evaluation_id INTEGER UNIQUE
                    REFERENCES evaluation_results(id) ON DELETE SET NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','completed','failed')),
                result_snapshot_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                CONSTRAINT uq_baseline_run_set_item
                    UNIQUE(run_id, baseline_set_item_id)
            )
        """)

        evaluation_job_columns = {
            row[1] for row in raw.execute("PRAGMA table_info(evaluation_jobs)")
        }
        if "baseline_regression_item_id" not in evaluation_job_columns:
            raw.execute(
                "ALTER TABLE evaluation_jobs ADD COLUMN "
                "baseline_regression_item_id INTEGER REFERENCES "
                "baseline_regression_items(id) ON DELETE RESTRICT"
            )

        if "optimization_case_queue" in tables:
            queue_columns = {
                row[1]
                for row in raw.execute(
                    "PRAGMA table_info(optimization_case_queue)"
                )
            }
            queue_sql = str(
                raw.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='optimization_case_queue'"
                ).fetchone()[0]
                or ""
            )
            if (
                "baseline_regression_item_id" not in queue_columns
                or "baseline_regression" not in queue_sql
            ):
                before_count = raw.execute(
                    "SELECT COUNT(*) FROM optimization_case_queue"
                ).fetchone()[0]
                raw.execute(
                    "ALTER TABLE optimization_case_queue "
                    "RENAME TO optimization_case_queue_m26"
                )
                raw.execute("""
                    CREATE TABLE optimization_case_queue (
                        id INTEGER PRIMARY KEY,
                        idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                        evaluation_id INTEGER
                            REFERENCES evaluation_results(id) ON DELETE RESTRICT,
                        final_review_id INTEGER
                            REFERENCES human_reviews(id) ON DELETE RESTRICT,
                        source_type VARCHAR(30) NOT NULL DEFAULT 'human_review'
                            CHECK(source_type IN (
                                'human_review','production_feedback',
                                'baseline_regression'
                            )),
                        source_event_id INTEGER UNIQUE
                            REFERENCES production_feedback_events(id)
                            ON DELETE RESTRICT,
                        baseline_regression_item_id INTEGER UNIQUE
                            REFERENCES baseline_regression_items(id)
                            ON DELETE RESTRICT,
                        prompt_version VARCHAR(40) NOT NULL,
                        severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                            CHECK(severity IN ('P0','P1','P2','P3')),
                        case_json TEXT NOT NULL,
                        status VARCHAR(30) NOT NULL DEFAULT 'pending'
                            CHECK(status IN (
                                'pending','batched','processing',
                                'completed','failed'
                            )),
                        lease_owner VARCHAR(120),
                        lease_token VARCHAR(80),
                        lease_expires_at DATETIME,
                        attempt_count INTEGER NOT NULL DEFAULT 0
                            CHECK(attempt_count >= 0),
                        next_attempt_at DATETIME,
                        last_error TEXT NOT NULL DEFAULT '',
                        automation_run_id INTEGER
                            REFERENCES automation_optimization_runs(id)
                            ON DELETE SET NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CHECK(
                            (source_type = 'human_review'
                             AND evaluation_id IS NOT NULL
                             AND final_review_id IS NOT NULL
                             AND source_event_id IS NULL
                             AND baseline_regression_item_id IS NULL)
                            OR
                            (source_type = 'production_feedback'
                             AND evaluation_id IS NULL
                             AND final_review_id IS NULL
                             AND source_event_id IS NOT NULL
                             AND baseline_regression_item_id IS NULL)
                            OR
                            (source_type = 'baseline_regression'
                             AND evaluation_id IS NOT NULL
                             AND final_review_id IS NULL
                             AND source_event_id IS NULL
                             AND baseline_regression_item_id IS NOT NULL)
                        )
                    )
                """)
                queue_copy_columns = (
                    "id, idempotency_key, evaluation_id, final_review_id, "
                    "source_type, source_event_id, prompt_version, severity, "
                    "case_json, status, lease_owner, lease_token, "
                    "lease_expires_at, attempt_count, next_attempt_at, "
                    "last_error, automation_run_id, created_at, updated_at"
                )
                raw.execute(
                    "INSERT INTO optimization_case_queue ("
                    f"{queue_copy_columns}, baseline_regression_item_id) "
                    f"SELECT {queue_copy_columns}, NULL "
                    "FROM optimization_case_queue_m26"
                )
                after_count = raw.execute(
                    "SELECT COUNT(*) FROM optimization_case_queue"
                ).fetchone()[0]
                if before_count != after_count:
                    raise RuntimeError(
                        "optimization_case_queue 重建行数校验失败"
                    )
                raw.execute("DROP TABLE optimization_case_queue_m26")

        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_baseline_sets_name ON baseline_sets(name)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_sets_fingerprint ON baseline_sets(fingerprint)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_set_items_set ON baseline_set_items(baseline_set_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_set_items_asset ON baseline_set_items(asset_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_set_items_package ON baseline_set_items(source_package_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_runs_set ON baseline_regression_runs(baseline_set_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_runs_strategy ON baseline_regression_runs(strategy_bundle_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_runs_status ON baseline_regression_runs(status)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_items_run ON baseline_regression_items(run_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_items_set_item ON baseline_regression_items(baseline_set_item_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_items_asset ON baseline_regression_items(asset_id)",
            "CREATE INDEX IF NOT EXISTS ix_baseline_items_status ON baseline_regression_items(status)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_jobs_baseline_regression_item_id ON evaluation_jobs(baseline_regression_item_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_evaluation_jobs_baseline_regression_item ON evaluation_jobs(baseline_regression_item_id) WHERE baseline_regression_item_id IS NOT NULL AND technical_attempt = 0",
            "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_status ON optimization_case_queue(status)",
            "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_source_type ON optimization_case_queue(source_type)",
            "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_prompt ON optimization_case_queue(prompt_version)",
            "CREATE INDEX IF NOT EXISTS ix_optimization_case_queue_baseline_item ON optimization_case_queue(baseline_regression_item_id)",
        ):
            raw.execute(statement)

        for statement in (
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_sets_no_update BEFORE UPDATE ON baseline_sets BEGIN SELECT RAISE(ABORT, 'BaselineSet is immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_sets_no_delete BEFORE DELETE ON baseline_sets BEGIN SELECT RAISE(ABORT, 'BaselineSet cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_set_items_no_update BEFORE UPDATE ON baseline_set_items BEGIN SELECT RAISE(ABORT, 'BaselineSetItem is immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_set_items_no_delete BEFORE DELETE ON baseline_set_items BEGIN SELECT RAISE(ABORT, 'BaselineSetItem cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_runs_frozen BEFORE UPDATE OF baseline_set_id, sequence_no, previous_run_id, strategy_bundle_id, strategy_snapshot_json, baseline_set_fingerprint, total, created_by, created_at ON baseline_regression_runs BEGIN SELECT RAISE(ABORT, 'BaselineRegressionRun snapshot is immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_runs_no_delete BEFORE DELETE ON baseline_regression_runs BEGIN SELECT RAISE(ABORT, 'BaselineRegressionRun cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_items_frozen BEFORE UPDATE OF run_id, baseline_set_item_id, asset_id, expected_level, created_at ON baseline_regression_items BEGIN SELECT RAISE(ABORT, 'BaselineRegressionItem truth is immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS trg_baseline_items_no_delete BEFORE DELETE ON baseline_regression_items BEGIN SELECT RAISE(ABORT, 'BaselineRegressionItem cannot be deleted'); END",
        ):
            raw.execute(statement)

        violations = raw.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"Migration 26 foreign_key_check 失败：{violations[:3]}"
            )
        raw.commit()
    except Exception:
        if raw.in_transaction:
            raw.rollback()
        raise
    finally:
        raw.execute("PRAGMA legacy_alter_table=OFF")
        raw.execute("PRAGMA foreign_keys=ON")

    if raw.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("Migration 26 未能恢复 PRAGMA foreign_keys=ON")
    violations = raw.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"Migration 26 收尾 foreign_key_check 失败：{violations[:3]}"
        )


def _migration_026_add_dimension_schemas(connection: Connection) -> None:
    from ..dimension_schema_registry import materialized_space_schema_rows

    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    source_optimization_column = (
        "source_optimization_run_id INTEGER "
        "REFERENCES prompt_optimization_runs(id) ON DELETE RESTRICT"
        if "prompt_optimization_runs" in tables
        else "source_optimization_run_id INTEGER"
    )
    connection.exec_driver_sql(f"""
        CREATE TABLE IF NOT EXISTS dimension_schemas (
            id INTEGER PRIMARY KEY,
            schema_key VARCHAR(80) NOT NULL,
            version VARCHAR(64) NOT NULL,
            schema_type VARCHAR(20) NOT NULL,
            family_key VARCHAR(20) NOT NULL,
            display_name VARCHAR(160) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            parent_schema_id INTEGER
                REFERENCES dimension_schemas(id) ON DELETE RESTRICT,
            core_schema_id INTEGER
                REFERENCES dimension_schemas(id) ON DELETE RESTRICT,
            definition_json TEXT NOT NULL,
            canonical_hash VARCHAR(64) NOT NULL,
            {source_optimization_column},
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_by VARCHAR(80),
            published_at DATETIME,
            retired_at DATETIME,
            CONSTRAINT ck_dimension_schemas_schema_key
                CHECK(length(trim(schema_key)) > 0),
            CONSTRAINT ck_dimension_schemas_version
                CHECK(length(trim(version)) > 0),
            CONSTRAINT ck_dimension_schemas_schema_type
                CHECK(schema_type IN ('core','family_pack','extension')),
            CONSTRAINT ck_dimension_schemas_family_key
                CHECK(family_key IN (
                    'space','product','graphic','intent','common'
                )),
            CONSTRAINT ck_dimension_schemas_status
                CHECK(status IN (
                    'draft','candidate','published','retired'
                )),
            CONSTRAINT ck_dimension_schemas_definition_json
                CHECK(
                    json_valid(definition_json)
                    AND json_type(definition_json, '$') = 'object'
                ),
            CONSTRAINT ck_dimension_schemas_canonical_hash
                CHECK(
                    length(canonical_hash) = 64
                    AND canonical_hash = lower(canonical_hash)
                    AND canonical_hash NOT GLOB '*[^0-9a-f]*'
                ),
            CONSTRAINT ck_dimension_schemas_parent_not_self
                CHECK(parent_schema_id IS NULL OR parent_schema_id <> id),
            CONSTRAINT ck_dimension_schemas_core_not_self
                CHECK(core_schema_id IS NULL OR core_schema_id <> id),
            CONSTRAINT ck_dimension_schemas_publish_audit
                CHECK(
                    (
                        status IN ('published','retired')
                        AND published_by IS NOT NULL
                        AND published_at IS NOT NULL
                    )
                    OR
                    (
                        status IN ('draft','candidate')
                        AND published_by IS NULL
                        AND published_at IS NULL
                    )
                ),
            CONSTRAINT ck_dimension_schemas_retired_at
                CHECK(
                    (status = 'retired' AND retired_at IS NOT NULL)
                    OR (status <> 'retired' AND retired_at IS NULL)
                ),
            CONSTRAINT uq_dimension_schemas_key_version
                UNIQUE(schema_key, version),
            CONSTRAINT uq_dimension_schemas_canonical_hash
                UNIQUE(canonical_hash)
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_schema_key "
        "ON dimension_schemas(schema_key)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_version "
        "ON dimension_schemas(version)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_schema_type "
        "ON dimension_schemas(schema_type)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_family_key "
        "ON dimension_schemas(family_key)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_status "
        "ON dimension_schemas(status)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_parent_schema_id "
        "ON dimension_schemas(parent_schema_id)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_core_schema_id "
        "ON dimension_schemas(core_schema_id)",
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_schemas_source_optimization_run_id "
        "ON dimension_schemas(source_optimization_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_canonical_hash "
        "ON dimension_schemas(canonical_hash)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_schemas_registry "
        "ON dimension_schemas(schema_type, family_key, status)",
    ):
        connection.exec_driver_sql(statement)

    for row in materialized_space_schema_rows():
        existing = connection.exec_driver_sql(
            """
            SELECT schema_type, family_key, display_name, status,
                   definition_json, canonical_hash
            FROM dimension_schemas
            WHERE schema_key = ? AND version = ?
            """,
            (row["schema_key"], row["version"]),
        ).mappings().first()
        expected = {
            key: row[key]
            for key in (
                "schema_type",
                "family_key",
                "display_name",
                "status",
                "definition_json",
                "canonical_hash",
            )
        }
        if existing is not None:
            if dict(existing) != expected:
                raise RuntimeError(
                    "已存在的 DimensionSchema 兼容修订与迁移定义不一致"
                )
            continue
        connection.exec_driver_sql(
            """
            INSERT INTO dimension_schemas (
                schema_key, version, schema_type, family_key,
                display_name, status, definition_json, canonical_hash,
                created_by, published_by, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                row["schema_key"],
                row["version"],
                row["schema_type"],
                row["family_key"],
                row["display_name"],
                row["status"],
                row["definition_json"],
                row["canonical_hash"],
                "system:dimension-schema-bootstrap",
                "system:dimension-schema-bootstrap",
            ),
        )

    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_dimension_schemas_published_no_update
        BEFORE UPDATE ON dimension_schemas
        WHEN OLD.status IN ('published','retired')
        BEGIN
            SELECT RAISE(
                ABORT,
                'Published DimensionSchema is immutable; create a new version'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS trg_dimension_schemas_published_no_delete
        BEFORE DELETE ON dimension_schemas
        WHEN OLD.status IN ('published','retired')
        BEGIN
            SELECT RAISE(
                ABORT,
                'Published DimensionSchema cannot be deleted'
            );
        END
    """)

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError(
            f"DimensionSchema 迁移 foreign_key_check 失败：{violations[:3]}"
        )


def _install_v2_strategy_result_triggers(
    connection: Connection,
    *,
    install_result_triggers: bool,
) -> None:
    trigger_names = ["trg_strategy_bundles_contract_insert"]
    if install_result_triggers:
        trigger_names.extend(
            (
                "trg_evaluation_results_require_strategy_insert",
                "trg_evaluation_results_require_strategy_update",
            )
        )
    for trigger_name in trigger_names:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {trigger_name}"
        )

    connection.exec_driver_sql("""
        CREATE TRIGGER trg_strategy_bundles_contract_insert
        BEFORE INSERT ON strategy_bundles
        WHEN NEW.strategy_schema_version NOT IN (
                'strategy-bundle-v1','strategy-bundle-v2'
             )
          OR (
              NEW.strategy_schema_version = 'strategy-bundle-v1'
              AND (
                  NEW.dimension_route_policy_id IS NOT NULL
                  OR NEW.dimension_schema_set_snapshot IS NOT NULL
                  OR NEW.label_field_set_snapshot IS NOT NULL
                  OR NEW.resolved_schema_contract_version IS NOT NULL
              )
          )
          OR (
              NEW.strategy_schema_version = 'strategy-bundle-v2'
              AND (
                  NEW.dimension_route_policy_id IS NULL
                  OR length(trim(NEW.dimension_route_policy_id)) = 0
                  OR json_valid(NEW.dimension_schema_set_snapshot) = 0
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$'
                  ) <> 'object'
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) <> 'array'
                  OR json_array_length(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) < 1
                  OR json_valid(NEW.label_field_set_snapshot) = 0
                  OR json_type(
                      NEW.label_field_set_snapshot, '$'
                  ) <> 'object'
                  OR NEW.resolved_schema_contract_version IS NULL
                  OR length(
                      trim(NEW.resolved_schema_contract_version)
                  ) = 0
              )
          )
        BEGIN
            SELECT RAISE(
                ABORT,
                'StrategyBundle dimension contract is invalid'
            );
        END
    """)

    if not install_result_triggers:
        return

    invalid_contract = """
        NEW.strategy_bundle_id IS NULL
        OR NEW.strategy_snapshot_json IS NULL
        OR length(trim(NEW.strategy_snapshot_json)) < 3
        OR CASE
            WHEN json_valid(NEW.strategy_snapshot_json) = 0 THEN 1
            ELSE
                json_type(NEW.strategy_snapshot_json, '$') <> 'object'
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.bundle_id'
                ) IS NULL
                OR length(json_extract(
                    NEW.strategy_snapshot_json, '$.canonical_hash'
                )) <> 64
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.schema_version'
                ) NOT IN ('strategy-bundle-v1','strategy-bundle-v2')
                OR json_type(
                    NEW.strategy_snapshot_json, '$.model_config'
                ) <> 'object'
                OR json_type(
                    NEW.strategy_snapshot_json, '$.prompt_a'
                ) <> 'object'
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_a.id'
                ) IS NULL
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.prompt_a.version'
                ) IS NULL
                OR json_type(
                    NEW.strategy_snapshot_json, '$.prompt_b'
                ) IS NULL
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.rubric_version'
                ) IS NULL
                OR json_extract(
                    NEW.strategy_snapshot_json, '$.engine_version'
                ) IS NULL
                OR json_type(
                    NEW.strategy_snapshot_json, '$.sampling_policy'
                ) IS NULL
                OR json_type(
                    NEW.strategy_snapshot_json, '$.risk_review_version'
                ) IS NULL
          END
        OR NOT EXISTS (
            SELECT 1
            FROM strategy_bundles AS bundle
            WHERE bundle.id = NEW.strategy_bundle_id
              AND bundle.id = json_extract(
                  NEW.strategy_snapshot_json, '$.bundle_id'
              )
              AND bundle.canonical_hash = json_extract(
                  NEW.strategy_snapshot_json, '$.canonical_hash'
              )
              AND bundle.strategy_schema_version = json_extract(
                  NEW.strategy_snapshot_json, '$.schema_version'
              )
              AND bundle.model_id = json_extract(
                  NEW.strategy_snapshot_json, '$.model_id'
              )
              AND bundle.prompt_a_version = json_extract(
                  NEW.strategy_snapshot_json, '$.prompt_a.version'
              )
              AND bundle.prompt_b_version IS json_extract(
                  NEW.strategy_snapshot_json, '$.prompt_b.version'
              )
              AND bundle.rubric_version = json_extract(
                  NEW.strategy_snapshot_json, '$.rubric_version'
              )
              AND bundle.engine_version = json_extract(
                  NEW.strategy_snapshot_json, '$.engine_version'
              )
              AND bundle.sampling_policy_revision IS json_extract(
                  NEW.strategy_snapshot_json,
                  '$.sampling_policy.revision'
              )
              AND bundle.risk_review_version IS json_extract(
                  NEW.strategy_snapshot_json, '$.risk_review_version'
              )
              AND (
                  bundle.strategy_schema_version = 'strategy-bundle-v1'
                  OR (
                      bundle.strategy_schema_version
                          = 'strategy-bundle-v2'
                      AND json_type(
                          NEW.strategy_snapshot_json,
                          '$.dimension_schema_set'
                      ) = 'object'
                      AND json_type(
                          NEW.strategy_snapshot_json,
                          '$.label_field_set'
                      ) = 'object'
                      AND json_type(
                          NEW.strategy_snapshot_json,
                          '$.resolved_dimensions_snapshot'
                      ) = 'object'
                      AND json_type(
                          NEW.strategy_snapshot_json,
                          '$.route_decision_snapshot'
                      ) = 'object'
                      AND json_type(
                          NEW.strategy_snapshot_json,
                          '$.resolved_dimension_schema_id'
                      ) = 'integer'
                      AND length(json_extract(
                          NEW.strategy_snapshot_json,
                          '$.resolved_dimension_schema_hash'
                      )) = 64
                      AND length(json_extract(
                          NEW.strategy_snapshot_json,
                          '$.resolved_snapshot_hash'
                      )) = 64
                      AND bundle.dimension_route_policy_id = json_extract(
                          NEW.strategy_snapshot_json,
                          '$.dimension_route_policy_id'
                      )
                      AND bundle.resolved_schema_contract_version
                          = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_schema_contract_version'
                          )
                      AND json(json_extract(
                          NEW.strategy_snapshot_json,
                          '$.dimension_schema_set'
                      )) = json(bundle.dimension_schema_set_snapshot)
                      AND json(json_extract(
                          NEW.strategy_snapshot_json,
                          '$.label_field_set'
                      )) = json(bundle.label_field_set_snapshot)
                      AND json_extract(
                          NEW.strategy_snapshot_json,
                          '$.route_decision_snapshot.policy_id'
                      ) = bundle.dimension_route_policy_id
                      AND json_extract(
                          NEW.strategy_snapshot_json,
                          '$.route_decision_snapshot.dimension_schema_id'
                      ) = json_extract(
                          NEW.strategy_snapshot_json,
                          '$.resolved_dimension_schema_id'
                      )
                      AND json_extract(
                          NEW.strategy_snapshot_json,
                          '$.route_decision_snapshot.dimension_schema_hash'
                      ) = json_extract(
                          NEW.strategy_snapshot_json,
                          '$.resolved_dimension_schema_hash'
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM dimension_schemas AS schema
                          WHERE schema.id = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_id'
                          )
                            AND schema.schema_key = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_key'
                            )
                            AND schema.version = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_version'
                            )
                            AND schema.canonical_hash = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_hash'
                            )
                            AND json(schema.definition_json) = json(
                              json_extract(
                                  NEW.strategy_snapshot_json,
                                  '$.resolved_dimensions_snapshot'
                              )
                            )
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM json_each(
                              bundle.dimension_schema_set_snapshot,
                              '$.schemas'
                          ) AS frozen
                          WHERE json_extract(
                              frozen.value, '$.schema_key'
                          ) = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_key'
                          )
                            AND json_extract(
                              frozen.value, '$.version'
                          ) = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_version'
                          )
                            AND json_extract(
                              frozen.value, '$.canonical_hash'
                          ) = json_extract(
                              NEW.strategy_snapshot_json,
                              '$.resolved_dimension_schema_hash'
                          )
                      )
                  )
              )
        )
    """
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_evaluation_results_require_strategy_insert
        BEFORE INSERT ON evaluation_results
        WHEN {invalid_contract}
        BEGIN
            SELECT RAISE(
                ABORT,
                'EvaluationResult strategy binding is required'
            );
        END
    """)
    connection.exec_driver_sql(f"""
        CREATE TRIGGER trg_evaluation_results_require_strategy_update
        BEFORE UPDATE OF strategy_bundle_id, strategy_snapshot_json
        ON evaluation_results
        WHEN {invalid_contract}
        BEGIN
            SELECT RAISE(
                ABORT,
                'EvaluationResult strategy binding is required'
            );
        END
    """)


def _install_v2_paired_strategy_snapshot_trigger(
    connection: Connection,
) -> None:
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS "
        "trg_paired_regression_strategy_snapshot_insert"
    )
    snapshot_invalid = """
        json_valid({snapshot}) = 0
        OR json_type({snapshot}, '$') IS NOT 'object'
        OR json_extract({snapshot}, '$.schema_version')
            NOT IN ('strategy-bundle-v1','strategy-bundle-v2')
        OR json_type({snapshot}, '$.model_config') IS NOT 'object'
        OR json_type({snapshot}, '$.model_config.name') IS NULL
        OR json_type({snapshot}, '$.model_config.provider') IS NULL
        OR json_type({snapshot}, '$.model_config.base_url') IS NULL
        OR json_type({snapshot}, '$.model_config.api_path') IS NULL
        OR json_type({snapshot}, '$.model_config.model_id') IS NULL
        OR json_type({snapshot}, '$.model_config.temperature') IS NULL
        OR json_type({snapshot}, '$.model_config.max_tokens') IS NULL
        OR json_type(
            {snapshot}, '$.model_config.timeout_seconds'
        ) IS NULL
        OR json_type({snapshot}, '$.model_config.max_retries') IS NULL
        OR json_type(
            {snapshot}, '$.model_config.max_concurrency'
        ) IS NULL
        OR json_type(
            {snapshot}, '$.model_config.structured_output'
        ) IS NULL
        OR json_type(
            {snapshot}, '$.model_config.high_risk_review_enabled'
        ) IS NULL
        OR json_type({snapshot}, '$.prompt_a') IS NOT 'object'
        OR json_type({snapshot}, '$.prompt_b') IS NOT 'object'
        OR json_extract({snapshot}, '$.prompt_a.stage') <> 'A'
        OR json_extract({snapshot}, '$.prompt_b.stage') <> 'B'
        OR json_type({snapshot}, '$.prompt_a.id') IS NULL
        OR json_type({snapshot}, '$.prompt_b.id') IS NULL
        OR json_type({snapshot}, '$.prompt_a.name') IS NULL
        OR json_type({snapshot}, '$.prompt_b.name') IS NULL
        OR json_type({snapshot}, '$.prompt_a.rubric_version') IS NULL
        OR json_type({snapshot}, '$.prompt_b.rubric_version') IS NULL
        OR json_type({snapshot}, '$.prompt_a.system_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_b.system_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_a.user_prompt') IS NULL
        OR json_type({snapshot}, '$.prompt_b.user_prompt') IS NULL
        OR json_type({snapshot}, '$.sampling_policy') IS NULL
        OR json_type({snapshot}, '$.sampling_policy')
            NOT IN ('object', 'null')
        OR json_type({snapshot}, '$.risk_review_version') IS NULL
        OR NOT EXISTS (
            SELECT 1
            FROM strategy_bundles AS bundle
            WHERE bundle.id = {bundle_id}
              AND bundle.id = json_extract({snapshot}, '$.bundle_id')
              AND bundle.canonical_hash =
                  json_extract({snapshot}, '$.canonical_hash')
              AND bundle.strategy_schema_version =
                  json_extract({snapshot}, '$.schema_version')
              AND bundle.model_id =
                  json_extract({snapshot}, '$.model_id')
              AND json(bundle.model_config_snapshot) =
                  json(json_extract({snapshot}, '$.model_config'))
              AND bundle.prompt_a_version =
                  json_extract({snapshot}, '$.prompt_a.version')
              AND bundle.prompt_b_version =
                  json_extract({snapshot}, '$.prompt_b.version')
              AND bundle.rubric_version =
                  json_extract({snapshot}, '$.rubric_version')
              AND bundle.engine_version =
                  json_extract({snapshot}, '$.engine_version')
              AND bundle.sampling_policy_revision IS
                  json_extract(
                      {snapshot}, '$.sampling_policy.revision'
                  )
              AND bundle.risk_review_version IS
                  json_extract({snapshot}, '$.risk_review_version')
              AND (
                  bundle.strategy_schema_version = 'strategy-bundle-v1'
                  OR (
                      json_type(
                          {snapshot}, '$.dimension_schema_set'
                      ) = 'object'
                      AND json_type(
                          {snapshot}, '$.label_field_set'
                      ) = 'object'
                      AND bundle.dimension_route_policy_id =
                          json_extract(
                              {snapshot},
                              '$.dimension_route_policy_id'
                          )
                      AND bundle.resolved_schema_contract_version =
                          json_extract(
                              {snapshot},
                              '$.resolved_schema_contract_version'
                          )
                      AND json(json_extract(
                          {snapshot}, '$.dimension_schema_set'
                      )) = json(bundle.dimension_schema_set_snapshot)
                      AND json(json_extract(
                          {snapshot}, '$.label_field_set'
                      )) = json(bundle.label_field_set_snapshot)
                  )
              )
        )
    """
    baseline_invalid = snapshot_invalid.format(
        snapshot="NEW.baseline_strategy_snapshot_json",
        bundle_id="NEW.baseline_strategy_bundle_id",
    )
    candidate_invalid = snapshot_invalid.format(
        snapshot="NEW.candidate_strategy_snapshot_json",
        bundle_id="NEW.candidate_strategy_bundle_id",
    )
    connection.exec_driver_sql(f"""
        CREATE TRIGGER
            trg_paired_regression_strategy_snapshot_insert
        BEFORE INSERT ON prompt_regression_runs
        WHEN NEW.regression_mode = 'paired'
          AND ({baseline_invalid} OR {candidate_invalid})
        BEGIN
            SELECT RAISE(
                ABORT,
                'Paired regression strategy snapshot is invalid'
            );
        END
    """)


def _migration_027_bind_dimension_contract_to_strategy(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "strategy_bundles" not in tables:
        violations = connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).all()
        if violations:
            raise RuntimeError(
                "无 StrategyBundle 分叉库 foreign_key_check 失败："
                f"{violations[:3]}"
            )
        return

    bundle_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(strategy_bundles)"
        )
    }
    additions = (
        (
            "strategy_schema_version",
            "VARCHAR(40) NOT NULL DEFAULT 'strategy-bundle-v1'",
        ),
        ("dimension_route_policy_id", "VARCHAR(100)"),
        ("dimension_schema_set_snapshot", "TEXT"),
        ("label_field_set_snapshot", "TEXT"),
        ("resolved_schema_contract_version", "VARCHAR(80)"),
    )
    for column_name, definition in additions:
        if column_name not in bundle_columns:
            connection.exec_driver_sql(
                "ALTER TABLE strategy_bundles ADD COLUMN "
                f"{column_name} {definition}"
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_strategy_bundles_schema_version "
        "ON strategy_bundles(strategy_schema_version)",
        "CREATE INDEX IF NOT EXISTS "
        "ix_strategy_bundles_dimension_route_policy_id "
        "ON strategy_bundles(dimension_route_policy_id)",
        "CREATE INDEX IF NOT EXISTS "
        "ix_strategy_bundles_resolved_schema_contract_version "
        "ON strategy_bundles(resolved_schema_contract_version)",
    ):
        connection.exec_driver_sql(statement)

    invalid_existing = connection.exec_driver_sql("""
        SELECT id
        FROM strategy_bundles
        WHERE strategy_schema_version <> 'strategy-bundle-v1'
           OR dimension_route_policy_id IS NOT NULL
           OR dimension_schema_set_snapshot IS NOT NULL
           OR label_field_set_snapshot IS NOT NULL
           OR resolved_schema_contract_version IS NOT NULL
        LIMIT 1
    """).first()
    if invalid_existing is not None:
        raise RuntimeError(
            "迁移前 StrategyBundle 存在无法解释的维度合同字段"
        )

    result_columns = (
        {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(evaluation_results)"
            )
        }
        if "evaluation_results" in tables
        else set()
    )
    _install_v2_strategy_result_triggers(
        connection,
        install_result_triggers={
            "strategy_bundle_id",
            "strategy_snapshot_json",
        }.issubset(result_columns),
    )
    if "prompt_regression_runs" in tables:
        regression_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(prompt_regression_runs)"
            )
        }
        if {
            "regression_mode",
            "baseline_strategy_bundle_id",
            "candidate_strategy_bundle_id",
            "baseline_strategy_snapshot_json",
            "candidate_strategy_snapshot_json",
        }.issubset(regression_columns):
            _install_v2_paired_strategy_snapshot_trigger(connection)
    violations = connection.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    if violations:
        raise RuntimeError(
            "StrategyBundle 维度合同迁移 foreign_key_check 失败："
            f"{violations[:3]}"
        )


def _migration_028_add_dimension_route_policies(
    connection: Connection,
) -> None:
    from ..dimension_route_registry import (
        materialized_p2_dimension_schema_rows,
        materialized_route_policy_rows,
    )

    schema_rows = materialized_p2_dimension_schema_rows()
    core_row = next(
        row for row in schema_rows if row["schema_type"] == "core"
    )
    product_row = next(
        row for row in schema_rows if row["family_key"] == "product"
    )

    def insert_or_verify_schema(
        row: dict[str, object],
        *,
        core_schema_id: int | None,
    ) -> int:
        existing = connection.exec_driver_sql(
            """
            SELECT id, schema_type, family_key, display_name, status,
                   core_schema_id, definition_json, canonical_hash
            FROM dimension_schemas
            WHERE schema_key = ? AND version = ?
            """,
            (row["schema_key"], row["version"]),
        ).mappings().first()
        expected = {
            "schema_type": row["schema_type"],
            "family_key": row["family_key"],
            "display_name": row["display_name"],
            "status": row["status"],
            "core_schema_id": core_schema_id,
            "definition_json": row["definition_json"],
            "canonical_hash": row["canonical_hash"],
        }
        if existing is not None:
            actual = {key: existing[key] for key in expected}
            if actual != expected:
                raise RuntimeError(
                    "已存在的 P2 DimensionSchema 与迁移定义不一致"
                )
            return int(existing["id"])
        published = row["status"] in {"published", "retired"}
        published_at = (
            connection.exec_driver_sql("SELECT CURRENT_TIMESTAMP").scalar_one()
            if published
            else None
        )
        connection.exec_driver_sql(
            """
            INSERT INTO dimension_schemas (
                schema_key, version, schema_type, family_key,
                display_name, status, core_schema_id,
                definition_json, canonical_hash, created_by,
                published_by, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["schema_key"],
                row["version"],
                row["schema_type"],
                row["family_key"],
                row["display_name"],
                row["status"],
                core_schema_id,
                row["definition_json"],
                row["canonical_hash"],
                "system:dimension-route-bootstrap",
                "system:dimension-route-bootstrap" if published else None,
                published_at,
            ),
        )
        schema_id = connection.exec_driver_sql(
            """
            SELECT id FROM dimension_schemas
            WHERE schema_key = ? AND version = ?
            """,
            (row["schema_key"], row["version"]),
        ).scalar_one()
        return int(schema_id)

    core_schema_id = insert_or_verify_schema(
        core_row,
        core_schema_id=None,
    )
    expected_core_ref = product_row.get("core_schema_ref")
    if (
        not isinstance(expected_core_ref, dict)
        or expected_core_ref.get("schema_key") != core_row["schema_key"]
        or expected_core_ref.get("version") != core_row["version"]
        or expected_core_ref.get("canonical_hash")
        != core_row["canonical_hash"]
    ):
        raise RuntimeError("单品候选包的 L0 核心维引用不一致")
    insert_or_verify_schema(
        product_row,
        core_schema_id=core_schema_id,
    )

    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS dimension_route_policies (
            id INTEGER PRIMARY KEY,
            policy_key VARCHAR(80) NOT NULL,
            version VARCHAR(64) NOT NULL,
            display_name VARCHAR(160) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            definition_json TEXT NOT NULL,
            canonical_hash VARCHAR(64) NOT NULL,
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_by VARCHAR(80),
            published_at DATETIME,
            retired_at DATETIME,
            CONSTRAINT ck_dimension_route_policies_policy_key
                CHECK(length(trim(policy_key)) > 0),
            CONSTRAINT ck_dimension_route_policies_version
                CHECK(length(trim(version)) > 0),
            CONSTRAINT ck_dimension_route_policies_status
                CHECK(status IN (
                    'draft','candidate','published','retired'
                )),
            CONSTRAINT ck_dimension_route_policies_definition_json
                CHECK(
                    json_valid(definition_json)
                    AND json_type(definition_json, '$') = 'object'
                ),
            CONSTRAINT ck_dimension_route_policies_canonical_hash
                CHECK(
                    length(canonical_hash) = 64
                    AND canonical_hash = lower(canonical_hash)
                    AND canonical_hash NOT GLOB '*[^0-9a-f]*'
                ),
            CONSTRAINT ck_dimension_route_policies_publish_audit
                CHECK(
                    (
                        status IN ('published','retired')
                        AND published_by IS NOT NULL
                        AND published_at IS NOT NULL
                    )
                    OR
                    (
                        status IN ('draft','candidate')
                        AND published_by IS NULL
                        AND published_at IS NULL
                    )
                ),
            CONSTRAINT ck_dimension_route_policies_retired_at
                CHECK(
                    (status = 'retired' AND retired_at IS NOT NULL)
                    OR (status <> 'retired' AND retired_at IS NULL)
                ),
            CONSTRAINT uq_dimension_route_policies_key_version
                UNIQUE(policy_key, version),
            CONSTRAINT uq_dimension_route_policies_canonical_hash
                UNIQUE(canonical_hash)
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_dimension_route_policies_policy_key "
        "ON dimension_route_policies(policy_key)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_route_policies_version "
        "ON dimension_route_policies(version)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_route_policies_status "
        "ON dimension_route_policies(status)",
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_route_policies_canonical_hash "
        "ON dimension_route_policies(canonical_hash)",
        "CREATE INDEX IF NOT EXISTS ix_dimension_route_policies_registry "
        "ON dimension_route_policies(policy_key, status)",
    ):
        connection.exec_driver_sql(statement)

    for row in materialized_route_policy_rows():
        existing = connection.exec_driver_sql(
            """
            SELECT display_name, status, definition_json, canonical_hash
            FROM dimension_route_policies
            WHERE policy_key = ? AND version = ?
            """,
            (row["policy_key"], row["version"]),
        ).mappings().first()
        expected = {
            key: row[key]
            for key in (
                "display_name",
                "status",
                "definition_json",
                "canonical_hash",
            )
        }
        if existing is not None:
            if dict(existing) != expected:
                raise RuntimeError(
                    "已存在的 DimensionRoutePolicy 与迁移定义不一致"
                )
            continue
        connection.exec_driver_sql(
            """
            INSERT INTO dimension_route_policies (
                policy_key, version, display_name, status,
                definition_json, canonical_hash, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["policy_key"],
                row["version"],
                row["display_name"],
                row["status"],
                row["definition_json"],
                row["canonical_hash"],
                "system:dimension-route-bootstrap",
            ),
        )

    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS
            trg_dimension_route_policies_published_no_update
        BEFORE UPDATE ON dimension_route_policies
        WHEN OLD.status IN ('published','retired')
        BEGIN
            SELECT RAISE(
                ABORT,
                'Published DimensionRoutePolicy is immutable; create a new version'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS
            trg_dimension_route_policies_published_no_delete
        BEFORE DELETE ON dimension_route_policies
        WHEN OLD.status IN ('published','retired')
        BEGIN
            SELECT RAISE(
                ABORT,
                'Published DimensionRoutePolicy cannot be deleted'
            );
        END
    """)
    violations = connection.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    if violations:
        raise RuntimeError(
            "DimensionRoutePolicy 迁移 foreign_key_check 失败："
            f"{violations[:3]}"
        )


def _install_v3_strategy_bundle_trigger(connection: Connection) -> None:
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS trg_strategy_bundles_contract_insert"
    )
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_strategy_bundles_contract_insert
        BEFORE INSERT ON strategy_bundles
        WHEN NEW.strategy_schema_version NOT IN (
                'strategy-bundle-v1',
                'strategy-bundle-v2',
                'strategy-bundle-v3'
             )
          OR (
              NEW.strategy_schema_version = 'strategy-bundle-v1'
              AND (
                  NEW.dimension_route_policy_id IS NOT NULL
                  OR NEW.dimension_schema_set_snapshot IS NOT NULL
                  OR NEW.label_field_set_snapshot IS NOT NULL
                  OR NEW.resolved_schema_contract_version IS NOT NULL
                  OR NEW.dimension_route_policy_snapshot IS NOT NULL
                  OR NEW.evaluation_profile_set_snapshot IS NOT NULL
              )
          )
          OR (
              NEW.strategy_schema_version = 'strategy-bundle-v2'
              AND (
                  NEW.dimension_route_policy_id IS NULL
                  OR length(trim(NEW.dimension_route_policy_id)) = 0
                  OR json_valid(NEW.dimension_schema_set_snapshot) = 0
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$'
                  ) <> 'object'
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) <> 'array'
                  OR json_array_length(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) < 1
                  OR json_valid(NEW.label_field_set_snapshot) = 0
                  OR json_type(
                      NEW.label_field_set_snapshot, '$'
                  ) <> 'object'
                  OR NEW.resolved_schema_contract_version IS NULL
                  OR length(
                      trim(NEW.resolved_schema_contract_version)
                  ) = 0
                  OR NEW.dimension_route_policy_snapshot IS NOT NULL
                  OR NEW.evaluation_profile_set_snapshot IS NOT NULL
              )
          )
          OR (
              NEW.strategy_schema_version = 'strategy-bundle-v3'
              AND (
                  NEW.prompt_b_version IS NOT NULL
                  OR NEW.dimension_route_policy_id IS NULL
                  OR length(trim(NEW.dimension_route_policy_id)) = 0
                  OR json_valid(NEW.dimension_schema_set_snapshot) = 0
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$'
                  ) <> 'object'
                  OR json_type(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) <> 'array'
                  OR json_array_length(
                      NEW.dimension_schema_set_snapshot, '$.schemas'
                  ) < 1
                  OR json_valid(NEW.label_field_set_snapshot) = 0
                  OR json_type(
                      NEW.label_field_set_snapshot, '$'
                  ) <> 'object'
                  OR NEW.resolved_schema_contract_version IS NULL
                  OR length(
                      trim(NEW.resolved_schema_contract_version)
                  ) = 0
                  OR json_valid(
                      NEW.dimension_route_policy_snapshot
                  ) = 0
                  OR json_type(
                      NEW.dimension_route_policy_snapshot, '$'
                  ) <> 'object'
                  OR json_extract(
                      NEW.dimension_route_policy_snapshot,
                      '$.format_version'
                  ) <> 'dimension-route-policy-snapshot-v1'
                  OR json_type(
                      NEW.dimension_route_policy_snapshot,
                      '$.definition'
                  ) <> 'object'
                  OR length(json_extract(
                      NEW.dimension_route_policy_snapshot,
                      '$.canonical_hash'
                  )) <> 64
                  OR NEW.dimension_route_policy_id <> (
                      json_extract(
                          NEW.dimension_route_policy_snapshot,
                          '$.policy_key'
                      )
                      || '@'
                      || json_extract(
                          NEW.dimension_route_policy_snapshot,
                          '$.version'
                      )
                  )
                  OR json_valid(
                      NEW.evaluation_profile_set_snapshot
                  ) = 0
                  OR json_type(
                      NEW.evaluation_profile_set_snapshot, '$'
                  ) <> 'object'
                  OR json_extract(
                      NEW.evaluation_profile_set_snapshot,
                      '$.format_version'
                  ) <> 'evaluation-profile-set-v1'
                  OR json_extract(
                      NEW.evaluation_profile_set_snapshot,
                      '$.execution_context'
                  ) NOT IN ('calibration','production')
                  OR json_type(
                      NEW.evaluation_profile_set_snapshot,
                      '$.profiles'
                  ) <> 'object'
                  OR NOT EXISTS (
                      SELECT 1
                      FROM json_each(
                          NEW.evaluation_profile_set_snapshot,
                          '$.profiles'
                      )
                  )
                  OR length(json_extract(
                      NEW.evaluation_profile_set_snapshot,
                      '$.canonical_hash'
                  )) <> 64
              )
          )
        BEGIN
            SELECT RAISE(
                ABORT,
                'StrategyBundle routed dimension contract is invalid'
            );
        END
    """)


def _migration_029_add_routed_strategy_bundles(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "strategy_bundles" not in tables:
        violations = connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).all()
        if violations:
            raise RuntimeError(
                "无 StrategyBundle 分叉库 foreign_key_check 失败："
                f"{violations[:3]}"
            )
        return
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(strategy_bundles)"
        )
    }
    additions = (
        ("dimension_route_policy_snapshot", "TEXT"),
        ("evaluation_profile_set_snapshot", "TEXT"),
    )
    for column_name, definition in additions:
        if column_name not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE strategy_bundles ADD COLUMN "
                f"{column_name} {definition}"
            )
    invalid_existing = connection.exec_driver_sql("""
        SELECT id
        FROM strategy_bundles
        WHERE strategy_schema_version NOT IN (
                'strategy-bundle-v1','strategy-bundle-v2'
              )
           OR dimension_route_policy_snapshot IS NOT NULL
           OR evaluation_profile_set_snapshot IS NOT NULL
        LIMIT 1
    """).first()
    if invalid_existing is not None:
        raise RuntimeError(
            "迁移前 StrategyBundle 存在无法解释的 v3 冻结字段"
        )
    _install_v3_strategy_bundle_trigger(connection)
    violations = connection.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    if violations:
        raise RuntimeError(
            "v3 StrategyBundle 迁移 foreign_key_check 失败："
            f"{violations[:3]}"
        )


def _install_dimension_calibration_triggers(
    connection: Connection,
) -> None:
    trigger_names = (
        "trg_dimension_calibration_runs_insert_guard",
        "trg_dimension_calibration_runs_transition_guard",
        "trg_dimension_calibration_runs_frozen_guard",
        "trg_dimension_calibration_runs_terminal_guard",
        "trg_dimension_calibration_runs_delete_guard",
        "trg_dimension_calibration_items_insert_guard",
        "trg_dimension_calibration_items_transition_guard",
        "trg_dimension_calibration_items_frozen_guard",
        "trg_dimension_calibration_items_terminal_guard",
        "trg_dimension_calibration_items_delete_guard",
    )
    for trigger_name in trigger_names:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {trigger_name}"
        )
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_runs_insert_guard
        BEFORE INSERT ON dimension_calibration_runs
        WHEN NEW.status <> 'queued'
          OR NEW.processing <> 0
          OR NEW.completed <> 0
          OR NEW.core_fallback <> 0
          OR NEW.blocked <> 0
          OR NEW.unassessable <> 0
          OR NEW.failed <> 0
          OR NEW.finished_at IS NOT NULL
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationRun must start queued'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_runs_transition_guard
        BEFORE UPDATE OF status ON dimension_calibration_runs
        WHEN NEW.status <> OLD.status
         AND NOT (
             (OLD.status = 'queued' AND NEW.status = 'running')
             OR (
                 OLD.status = 'running'
                 AND NEW.status IN (
                     'completed','partial_failed','failed'
                 )
             )
         )
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationRun state transition is invalid'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_runs_frozen_guard
        BEFORE UPDATE ON dimension_calibration_runs
        WHEN OLD.run_key IS NOT NEW.run_key
          OR OLD.strategy_bundle_id IS NOT NEW.strategy_bundle_id
          OR OLD.strategy_bundle_hash IS NOT NEW.strategy_bundle_hash
          OR OLD.strategy_snapshot_json IS NOT NEW.strategy_snapshot_json
          OR OLD.asset_manifest_json IS NOT NEW.asset_manifest_json
          OR OLD.definition_hash IS NOT NEW.definition_hash
          OR OLD.total IS NOT NEW.total
          OR OLD.created_by IS NOT NEW.created_by
          OR OLD.created_at IS NOT NEW.created_at
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationRun frozen fields are immutable'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_runs_terminal_guard
        BEFORE UPDATE ON dimension_calibration_runs
        WHEN OLD.status IN ('completed','partial_failed','failed')
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationRun terminal record is immutable'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_runs_delete_guard
        BEFORE DELETE ON dimension_calibration_runs
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationRun cannot be deleted'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_items_insert_guard
        BEFORE INSERT ON dimension_calibration_items
        WHEN NEW.status <> 'queued'
          OR NEW.worker_id IS NOT NULL
          OR NEW.resolution_snapshot_json IS NOT NULL
          OR NEW.precheck_json IS NOT NULL
          OR NEW.aesthetic_json IS NOT NULL
          OR NEW.scoring_json IS NOT NULL
          OR NEW.raw_response_a IS NOT NULL
          OR NEW.raw_response_b IS NOT NULL
          OR NEW.score IS NOT NULL
          OR NEW.level IS NOT NULL
          OR NEW.confidence IS NOT NULL
          OR NEW.error_type IS NOT NULL
          OR NEW.error_message <> ''
          OR NEW.started_at IS NOT NULL
          OR NEW.finished_at IS NOT NULL
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationItem must start queued'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_items_transition_guard
        BEFORE UPDATE OF status ON dimension_calibration_items
        WHEN NEW.status <> OLD.status
         AND NOT (
             (OLD.status = 'queued' AND NEW.status = 'processing')
             OR (
                 OLD.status = 'processing'
                 AND NEW.status IN (
                     'completed','core_fallback','blocked',
                     'unassessable','failed'
                 )
             )
         )
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationItem state transition is invalid'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_items_frozen_guard
        BEFORE UPDATE ON dimension_calibration_items
        WHEN OLD.run_id IS NOT NEW.run_id
          OR OLD.asset_id IS NOT NEW.asset_id
          OR OLD.asset_snapshot_json IS NOT NEW.asset_snapshot_json
          OR OLD.created_at IS NOT NEW.created_at
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationItem frozen fields are immutable'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_items_terminal_guard
        BEFORE UPDATE ON dimension_calibration_items
        WHEN OLD.status IN (
            'completed','core_fallback','blocked','unassessable','failed'
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationItem terminal record is immutable'
            );
        END
    """)
    connection.exec_driver_sql("""
        CREATE TRIGGER trg_dimension_calibration_items_delete_guard
        BEFORE DELETE ON dimension_calibration_items
        BEGIN
            SELECT RAISE(
                ABORT,
                'DimensionCalibrationItem cannot be deleted'
            );
        END
    """)


def _ensure_dimension_calibration_registry_dependencies(
    connection: Connection,
) -> None:
    """Add the current calibration candidates without mutating older versions."""
    from ..dimension_route_registry import (
        materialized_p2_dimension_schema_rows,
        materialized_route_policy_rows,
    )

    schema_rows = materialized_p2_dimension_schema_rows()
    core_row = next(
        row for row in schema_rows if row["schema_type"] == "core"
    )
    product_row = next(
        row for row in schema_rows if row["family_key"] == "product"
    )
    core = connection.exec_driver_sql(
        """
        SELECT id, canonical_hash
        FROM dimension_schemas
        WHERE schema_key = ? AND version = ?
        """,
        (core_row["schema_key"], core_row["version"]),
    ).mappings().first()
    if (
        core is None
        or core["canonical_hash"] != core_row["canonical_hash"]
    ):
        raise RuntimeError("维度校准依赖的 L0 核心维不存在或已损坏")
    core_schema_id = int(core["id"])

    existing_product = connection.exec_driver_sql(
        """
        SELECT schema_type, family_key, display_name, status,
               core_schema_id, definition_json, canonical_hash
        FROM dimension_schemas
        WHERE schema_key = ? AND version = ?
        """,
        (product_row["schema_key"], product_row["version"]),
    ).mappings().first()
    expected_product = {
        "schema_type": product_row["schema_type"],
        "family_key": product_row["family_key"],
        "display_name": product_row["display_name"],
        "status": product_row["status"],
        "core_schema_id": core_schema_id,
        "definition_json": product_row["definition_json"],
        "canonical_hash": product_row["canonical_hash"],
    }
    if existing_product is None:
        connection.exec_driver_sql(
            """
            INSERT INTO dimension_schemas (
                schema_key, version, schema_type, family_key,
                display_name, status, core_schema_id,
                definition_json, canonical_hash, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_row["schema_key"],
                product_row["version"],
                product_row["schema_type"],
                product_row["family_key"],
                product_row["display_name"],
                product_row["status"],
                core_schema_id,
                product_row["definition_json"],
                product_row["canonical_hash"],
                "system:dimension-calibration-bootstrap",
            ),
        )
    elif dict(existing_product) != expected_product:
        raise RuntimeError(
            "已存在的维度校准单品候选包与迁移定义不一致"
        )

    for policy_row in materialized_route_policy_rows():
        existing_policy = connection.exec_driver_sql(
            """
            SELECT display_name, status, definition_json, canonical_hash
            FROM dimension_route_policies
            WHERE policy_key = ? AND version = ?
            """,
            (policy_row["policy_key"], policy_row["version"]),
        ).mappings().first()
        expected_policy = {
            key: policy_row[key]
            for key in (
                "display_name",
                "status",
                "definition_json",
                "canonical_hash",
            )
        }
        if existing_policy is None:
            connection.exec_driver_sql(
                """
                INSERT INTO dimension_route_policies (
                    policy_key, version, display_name, status,
                    definition_json, canonical_hash, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_row["policy_key"],
                    policy_row["version"],
                    policy_row["display_name"],
                    policy_row["status"],
                    policy_row["definition_json"],
                    policy_row["canonical_hash"],
                    "system:dimension-calibration-bootstrap",
                ),
            )
        elif dict(existing_policy) != expected_policy:
            raise RuntimeError(
                "已存在的维度校准路由策略与迁移定义不一致"
            )


def _migration_030_add_dimension_calibration_results(
    connection: Connection,
) -> None:
    _ensure_dimension_calibration_registry_dependencies(connection)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS dimension_calibration_runs (
            id INTEGER PRIMARY KEY,
            run_key VARCHAR(120) NOT NULL,
            strategy_bundle_id INTEGER NOT NULL,
            strategy_bundle_hash VARCHAR(64) NOT NULL,
            strategy_snapshot_json TEXT NOT NULL,
            asset_manifest_json TEXT NOT NULL,
            definition_hash VARCHAR(64) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'queued',
            total INTEGER NOT NULL,
            processing INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            core_fallback INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            unassessable INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME,
            CONSTRAINT uq_dimension_calibration_runs_key UNIQUE (run_key),
            CONSTRAINT ck_dimension_calibration_runs_key
                CHECK (length(trim(run_key)) > 0),
            CONSTRAINT ck_dimension_calibration_runs_status
                CHECK (status IN (
                    'queued','running','completed',
                    'partial_failed','failed'
                )),
            CONSTRAINT ck_dimension_calibration_runs_total
                CHECK (total BETWEEN 1 AND 100),
            CONSTRAINT ck_dimension_calibration_runs_counts
                CHECK (
                    processing >= 0 AND completed >= 0
                    AND core_fallback >= 0 AND blocked >= 0
                    AND unassessable >= 0 AND failed >= 0
                    AND processing + completed + core_fallback
                        + blocked + unassessable + failed <= total
                ),
            CONSTRAINT ck_dimension_calibration_runs_bundle_hash
                CHECK (
                    length(strategy_bundle_hash) = 64
                    AND strategy_bundle_hash = lower(strategy_bundle_hash)
                    AND strategy_bundle_hash
                        NOT GLOB '*[^0-9a-f]*'
                ),
            CONSTRAINT ck_dimension_calibration_runs_definition_hash
                CHECK (
                    length(definition_hash) = 64
                    AND definition_hash = lower(definition_hash)
                    AND definition_hash NOT GLOB '*[^0-9a-f]*'
                ),
            CONSTRAINT ck_dimension_calibration_runs_strategy_snapshot
                CHECK (
                    json_valid(strategy_snapshot_json)
                    AND json_type(strategy_snapshot_json, '$') = 'object'
                ),
            CONSTRAINT ck_dimension_calibration_runs_asset_manifest
                CHECK (
                    json_valid(asset_manifest_json)
                    AND json_type(asset_manifest_json, '$') = 'object'
                ),
            FOREIGN KEY(strategy_bundle_id)
                REFERENCES strategy_bundles(id) ON DELETE RESTRICT
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS dimension_calibration_items (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            asset_id INTEGER NOT NULL,
            asset_snapshot_json TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'queued',
            worker_id VARCHAR(120),
            resolution_snapshot_json TEXT,
            precheck_json TEXT,
            aesthetic_json TEXT,
            scoring_json TEXT,
            raw_response_a TEXT,
            raw_response_b TEXT,
            score FLOAT,
            level VARCHAR(10),
            confidence FLOAT,
            needs_review BOOLEAN NOT NULL DEFAULT 0,
            error_type VARCHAR(40),
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            finished_at DATETIME,
            CONSTRAINT uq_dimension_calibration_run_asset
                UNIQUE (run_id, asset_id),
            CONSTRAINT ck_dimension_calibration_items_status
                CHECK (status IN (
                    'queued','processing','completed','core_fallback',
                    'blocked','unassessable','failed'
                )),
            CONSTRAINT ck_dimension_calibration_items_asset_snapshot
                CHECK (
                    json_valid(asset_snapshot_json)
                    AND json_type(asset_snapshot_json, '$') = 'object'
                ),
            CONSTRAINT ck_dimension_calibration_items_resolution
                CHECK (
                    resolution_snapshot_json IS NULL
                    OR (
                        json_valid(resolution_snapshot_json)
                        AND json_type(
                            resolution_snapshot_json, '$'
                        ) = 'object'
                    )
                ),
            CONSTRAINT ck_dimension_calibration_items_precheck
                CHECK (
                    precheck_json IS NULL
                    OR (
                        json_valid(precheck_json)
                        AND json_type(precheck_json, '$') = 'object'
                    )
                ),
            CONSTRAINT ck_dimension_calibration_items_aesthetic
                CHECK (
                    aesthetic_json IS NULL
                    OR (
                        json_valid(aesthetic_json)
                        AND json_type(aesthetic_json, '$') = 'object'
                    )
                ),
            CONSTRAINT ck_dimension_calibration_items_scoring
                CHECK (
                    scoring_json IS NULL
                    OR (
                        json_valid(scoring_json)
                        AND json_type(scoring_json, '$') = 'object'
                    )
                ),
            CONSTRAINT ck_dimension_calibration_items_level
                CHECK (
                    level IS NULL
                    OR level IN ('L1','L2','L3','L4','L5')
                ),
            CONSTRAINT ck_dimension_calibration_items_lifecycle
                CHECK (
                    (
                        status = 'queued'
                        AND worker_id IS NULL
                        AND started_at IS NULL
                        AND finished_at IS NULL
                    )
                    OR (
                        status = 'processing'
                        AND length(trim(worker_id)) > 0
                        AND started_at IS NOT NULL
                        AND finished_at IS NULL
                    )
                    OR (
                        status IN (
                            'completed','core_fallback','blocked',
                            'unassessable','failed'
                        )
                        AND length(trim(worker_id)) > 0
                        AND started_at IS NOT NULL
                        AND finished_at IS NOT NULL
                    )
                ),
            CONSTRAINT ck_dimension_calibration_items_terminal_payload
                CHECK (
                    (
                        status = 'completed'
                        AND resolution_snapshot_json IS NOT NULL
                        AND precheck_json IS NOT NULL
                        AND aesthetic_json IS NOT NULL
                        AND scoring_json IS NOT NULL
                        AND score IS NOT NULL
                        AND level IS NOT NULL
                        AND confidence IS NOT NULL
                        AND error_type IS NULL
                        AND error_message = ''
                    )
                    OR (
                        status IN (
                            'core_fallback','blocked','unassessable'
                        )
                        AND resolution_snapshot_json IS NOT NULL
                        AND precheck_json IS NOT NULL
                        AND aesthetic_json IS NULL
                        AND scoring_json IS NULL
                        AND score IS NULL
                        AND level IS NULL
                        AND confidence IS NULL
                        AND error_type IS NULL
                        AND error_message = ''
                    )
                    OR (
                        status = 'failed'
                        AND length(trim(error_type)) > 0
                        AND length(trim(error_message)) > 0
                        AND aesthetic_json IS NULL
                        AND scoring_json IS NULL
                        AND score IS NULL
                        AND level IS NULL
                        AND confidence IS NULL
                    )
                    OR status IN ('queued','processing')
                ),
            FOREIGN KEY(run_id)
                REFERENCES dimension_calibration_runs(id)
                ON DELETE RESTRICT,
            FOREIGN KEY(asset_id)
                REFERENCES assets(id)
                ON DELETE RESTRICT
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_calibration_runs_bundle "
        "ON dimension_calibration_runs(strategy_bundle_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_calibration_runs_status "
        "ON dimension_calibration_runs(status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_calibration_items_run "
        "ON dimension_calibration_items(run_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_calibration_items_asset "
        "ON dimension_calibration_items(asset_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS "
        "ix_dimension_calibration_items_status "
        "ON dimension_calibration_items(status)"
    )
    _install_dimension_calibration_triggers(connection)
    violations = connection.exec_driver_sql(
        "PRAGMA foreign_key_check"
    ).all()
    if violations:
        raise RuntimeError(
            "维度校准持久化迁移 foreign_key_check 失败："
            f"{violations[:3]}"
        )


def _migration_031_add_evaluation_category_profiles(connection: Connection) -> None:
    """Add isolated category contracts without rewriting existing jobs."""
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS evaluation_category_profiles (
            id INTEGER PRIMARY KEY,
            category_key VARCHAR(40) NOT NULL UNIQUE,
            display_name VARCHAR(120) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            allowed_mime_types_json TEXT NOT NULL DEFAULT '[]',
            preprocess_config_json TEXT NOT NULL DEFAULT '{}',
            prompt_a_id INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL,
            prompt_b_id INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL,
            model_config_id INTEGER REFERENCES model_configs(id) ON DELETE SET NULL,
            rubric_version VARCHAR(40) NOT NULL DEFAULT 'rubric-v2.1',
            dimension_schema_key VARCHAR(80),
            dimension_schema_version VARCHAR(64),
            created_by VARCHAR(80) NOT NULL DEFAULT 'system',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (category_key IN ('space_image','pdf_text','material_image')),
            CHECK (status IN ('draft','active','retired'))
        )
        """
    )
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    package_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(material_packages)")
    } if "material_packages" in tables else set()
    if "material_packages" in tables and "category_key" not in package_columns:
        connection.exec_driver_sql(
            "ALTER TABLE material_packages ADD COLUMN category_key VARCHAR(40) NOT NULL DEFAULT 'space_image'"
        )
    job_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")
    } if "evaluation_jobs" in tables else set()
    if "evaluation_jobs" in tables and "category_key" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN category_key VARCHAR(40) NOT NULL DEFAULT 'space_image'"
        )
    # Partial historical fixtures may not have the referenced credential and
    # prompt tables yet. SQLite refuses inserts into a table whose FK target is
    # absent, so defer seeding until the complete schema exists; the repair/API
    # compatibility path will seed it later.
    if not {"prompt_versions", "model_configs"}.issubset(tables):
        return
    profiles = (
        ("space_image", "空间图片", '["image/jpeg","image/png","image/webp","image/gif"]', '{"preprocess":"image"}'),
        ("pdf_text", "PDF 方案文本", '["application/pdf"]', '{"preprocess":"pdf","max_pages":4,"max_text_chars":24000,"multimodal_summary":true}'),
        ("material_image", "材质图", '["image/jpeg","image/png","image/webp","image/gif"]', '{"preprocess":"image","material_focus":true}'),
    )
    for category_key, display_name, mime_types, preprocess in profiles:
        connection.exec_driver_sql(
            """
            INSERT OR IGNORE INTO evaluation_category_profiles
              (category_key, display_name, status, allowed_mime_types_json,
               preprocess_config_json, rubric_version, created_by,
               created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?, 'rubric-v2.1', 'system',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (category_key, display_name, mime_types, preprocess),
        )


def _migration_032_repair_evaluation_category_profiles(connection: Connection) -> None:
    """Backfill seeds for databases that applied v31 against ORM-created tables.

    v31 originally relied on SQL defaults that are not present when the table
    already exists from ``Base.metadata.create_all``. ``INSERT OR IGNORE`` then
    hid NOT NULL failures and left an apparently migrated database empty.
    This repair is deliberately idempotent and also tolerates historical
    partial databases that do not yet have material/job tables.
    """
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "evaluation_category_profiles" not in tables:
        _migration_031_add_evaluation_category_profiles(connection)
        return
    if not {"prompt_versions", "model_configs"}.issubset(tables):
        return
    for category_key, display_name, mime_types, preprocess in (
        ("space_image", "空间图片", '["image/jpeg","image/png","image/webp","image/gif"]', '{"preprocess":"image"}'),
        ("pdf_text", "PDF 方案文本", '["application/pdf"]', '{"preprocess":"pdf","max_pages":4,"max_text_chars":24000,"multimodal_summary":true}'),
        ("material_image", "材质图", '["image/jpeg","image/png","image/webp","image/gif"]', '{"preprocess":"image","material_focus":true}'),
    ):
        connection.exec_driver_sql(
            """
            INSERT OR IGNORE INTO evaluation_category_profiles
              (category_key, display_name, status, allowed_mime_types_json,
               preprocess_config_json, rubric_version, created_by,
               created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?, 'rubric-v2.1', 'system',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (category_key, display_name, mime_types, preprocess),
        )
    if "material_packages" in tables:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(material_packages)")}
        if "category_key" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE material_packages ADD COLUMN category_key VARCHAR(40) NOT NULL DEFAULT 'space_image'"
            )
    if "evaluation_jobs" in tables:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")}
        if "category_key" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_jobs ADD COLUMN category_key VARCHAR(40) NOT NULL DEFAULT 'space_image'"
            )


def _migration_033_add_evaluation_preprocess_snapshot(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "evaluation_results" not in tables:
        return
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_results)")
    }
    if "preprocess_json" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_results ADD COLUMN preprocess_json TEXT"
        )


def _migration_034_freeze_job_category_profile(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "evaluation_jobs" not in tables:
        return
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")
    }
    if "category_profile_snapshot_json" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_jobs ADD COLUMN category_profile_snapshot_json TEXT"
        )


def _migration_035_generalize_model_names_and_pdf_summary(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    model_columns = (
        {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(model_configs)"
            )
        }
        if "model_configs" in tables
        else set()
    )
    if "name" in model_columns:
        connection.exec_driver_sql(
            "UPDATE model_configs SET name = '主评测模型' "
            "WHERE name = '豆包主模型'"
        )
    optimizer_columns = (
        {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(optimizer_configs)"
            )
        }
        if "optimizer_configs" in tables
        else set()
    )
    if "name" in optimizer_columns:
        connection.exec_driver_sql(
            "UPDATE optimizer_configs SET name = '提示词诊断模型' "
            "WHERE name = 'SOL 提示词诊断模型'"
        )
    if "evaluation_category_profiles" not in tables:
        return
    profile_columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_category_profiles)"
        )
    }
    if not {"id", "category_key", "preprocess_config_json"}.issubset(
        profile_columns
    ):
        return
    rows = connection.exec_driver_sql(
        "SELECT id, preprocess_config_json "
        "FROM evaluation_category_profiles WHERE category_key = 'pdf_text'"
    ).fetchall()
    for profile_id, raw_config in rows:
        try:
            config = json.loads(raw_config or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(config, dict):
            continue
        config["multimodal_summary"] = True
        connection.exec_driver_sql(
            "UPDATE evaluation_category_profiles "
            "SET preprocess_config_json = ? WHERE id = ?",
            (
                json.dumps(
                    config,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                profile_id,
            ),
        )


def _migration_036_add_category_automation_isolation(
    connection: Connection,
) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    def add_column(table: str, name: str, ddl: str) -> None:
        if table not in tables:
            return
        columns = {
            row[1]
            for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
        }
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    add_column("evaluation_category_profiles", "optimizer_config_id", "INTEGER REFERENCES optimizer_configs(id) ON DELETE SET NULL")
    add_column(
        "evaluation_category_profiles",
        "automation_config_json",
        "TEXT NOT NULL DEFAULT '{\"enabled\":true,\"case_threshold\":1,\"cooldown_seconds\":0,\"max_candidates\":1}'",
    )
    add_column("evaluation_category_profiles", "automation_revision", "INTEGER NOT NULL DEFAULT 1")
    add_column("evaluation_category_profiles", "automation_last_triggered_at", "DATETIME")
    for table in ("optimization_case_queue", "automation_optimization_runs", "sample_sets"):
        add_column(table, "category_key", "VARCHAR(40) NOT NULL DEFAULT 'space_image'")
        if table in tables:
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_category_key ON {table}(category_key)"
            )

    result_columns = (
        {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_results)")
        }
        if "evaluation_results" in tables
        else set()
    )
    job_columns = (
        {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(evaluation_jobs)")
        }
        if "evaluation_jobs" in tables
        else set()
    )
    if (
        {"optimization_case_queue", "evaluation_results", "evaluation_jobs"}.issubset(tables)
        and "job_id" in result_columns
        and "category_key" in job_columns
    ):
        connection.exec_driver_sql(
            """
            UPDATE optimization_case_queue
            SET category_key = COALESCE(
                (SELECT job.category_key
                 FROM evaluation_results result
                 JOIN evaluation_jobs job ON job.id = result.job_id
                 WHERE result.id = optimization_case_queue.evaluation_id),
                category_key, 'space_image'
            )
            """
        )
    if (
        {"sample_sets", "sample_set_items", "evaluation_results", "evaluation_jobs"}.issubset(tables)
        and "source_result_id" in {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(sample_set_items)")
        }
        and "job_id" in result_columns
        and "category_key" in job_columns
    ):
        connection.exec_driver_sql(
            """
            UPDATE sample_sets
            SET category_key = COALESCE(
                (SELECT job.category_key
                 FROM sample_set_items item
                 JOIN evaluation_results result ON result.id = item.source_result_id
                 JOIN evaluation_jobs job ON job.id = result.job_id
                 WHERE item.sample_set_id = sample_sets.id
                 ORDER BY item.id LIMIT 1),
                category_key, 'space_image'
            )
            """
        )
    if "automation_optimization_runs" in tables:
        connection.exec_driver_sql(
            """
            UPDATE automation_optimization_runs
            SET category_key = COALESCE(json_extract(frozen_input_json, '$.category_key'), category_key, 'space_image')
            """
        )
    if "automation_policies" in tables:
        connection.exec_driver_sql(
            """
            UPDATE automation_policies
            SET updated_by = 'migration-v36'
            WHERE updated_by IS NULL OR updated_by = ''
            """
        )


def _migration_037_add_asset_category_channel(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "assets" not in tables:
        return
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(assets)")
    }
    if "category_key" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE assets ADD COLUMN category_key VARCHAR(40) NOT NULL DEFAULT 'space_image'"
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_assets_category_key ON assets(category_key)"
    )
    if "material_package_items" in tables and "material_packages" in tables:
        connection.exec_driver_sql(
            """
            UPDATE assets
            SET category_key = COALESCE(
                (SELECT packages.category_key
                 FROM material_package_items items
                 JOIN material_packages packages ON packages.id = items.package_id
                 WHERE items.asset_id = assets.id
                 ORDER BY items.id
                 LIMIT 1),
                category_key,
                'space_image'
            )
            WHERE category_key = 'space_image'
            """
        )


def _migration_038_add_material_package_status(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "material_packages" not in tables:
        return
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(material_packages)")
    }
    if "status" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE material_packages ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'"
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_material_packages_status ON material_packages(status)"
    )
    # Preserve immutable package metadata while allowing the explicit soft
    # delete marker used by the package-management API.
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_material_packages_no_update")
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS trg_material_packages_no_metadata_update
        BEFORE UPDATE ON material_packages
        WHEN NEW.package_key <> OLD.package_key
          OR NEW.name <> OLD.name
          OR NEW.source <> OLD.source
          OR NEW.category_key <> OLD.category_key
          OR NEW.created_by <> OLD.created_by
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'MaterialPackage is immutable');
        END
        """
    )


def _migration_039_add_accounts_and_model_registry(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    def add_columns(table: str, columns: tuple[tuple[str, str], ...]) -> None:
        if table not in tables:
            return
        existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )

    add_columns(
        "users",
        (
            ("role", "VARCHAR(30) NOT NULL DEFAULT 'admin'"),
            ("permissions_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("last_login_at", "DATETIME"),
        ),
    )
    if "users" in tables:
        connection.exec_driver_sql(
            "UPDATE users SET role = CASE WHEN is_admin = 1 THEN 'admin' ELSE 'reviewer' END "
            "WHERE role IS NULL OR role = ''"
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_users_role ON users(role)")
    add_columns(
        "model_configs",
        (
            ("protocol", "VARCHAR(40) NOT NULL DEFAULT 'openai_chat'"),
            ("capabilities_json", "TEXT NOT NULL DEFAULT '[\"text\",\"vision\",\"structured_output\"]'"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
        ),
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS model_node_bindings ("
        "id INTEGER PRIMARY KEY,"
        "node_key VARCHAR(40) NOT NULL,"
        "model_config_id INTEGER NOT NULL REFERENCES model_configs(id) ON DELETE RESTRICT,"
        "category_key VARCHAR(40),"
        "enabled BOOLEAN NOT NULL DEFAULT 1,"
        "updated_by VARCHAR(80) NOT NULL DEFAULT 'system',"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_node_bindings_model ON model_node_bindings(model_config_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_model_node_bindings_category ON model_node_bindings(category_key)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_node_bindings_node_category "
        "ON model_node_bindings(node_key, COALESCE(category_key, ''))"
    )


def _migration_040_modular_category_pipelines(connection: Connection) -> None:
    tables = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "evaluation_category_profiles" not in tables:
        return
    table_sql = str(
        connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='evaluation_category_profiles'"
        ).scalar_one_or_none()
        or ""
    )
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(evaluation_category_profiles)"
        )
    }
    complete_profile_dependencies = {
        "prompt_versions", "model_configs", "optimizer_configs"
    }.issubset(tables)
    needs_rebuild = (
        "category_key IN ('space_image','pdf_text','material_image')" in table_sql
        and complete_profile_dependencies
    )
    if needs_rebuild:
        prompt_fk = (
            "INTEGER REFERENCES prompt_versions(id) ON DELETE SET NULL"
            if "prompt_versions" in tables else "INTEGER"
        )
        model_fk = (
            "INTEGER REFERENCES model_configs(id) ON DELETE SET NULL"
            if "model_configs" in tables else "INTEGER"
        )
        optimizer_fk = (
            "INTEGER REFERENCES optimizer_configs(id) ON DELETE SET NULL"
            if "optimizer_configs" in tables else "INTEGER"
        )
        connection.exec_driver_sql("DROP TABLE IF EXISTS evaluation_category_profiles_v40")
        connection.exec_driver_sql("""
            CREATE TABLE evaluation_category_profiles_v40 (
                id INTEGER PRIMARY KEY,
                category_key VARCHAR(40) NOT NULL UNIQUE,
                display_name VARCHAR(120) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','active','retired')),
                allowed_mime_types_json TEXT NOT NULL DEFAULT '[]',
                preprocess_config_json TEXT NOT NULL DEFAULT '{}',
                pipeline_config_json TEXT NOT NULL DEFAULT '{}',
                pipeline_revision INTEGER NOT NULL DEFAULT 1,
                prompt_a_id %s,
                prompt_b_id %s,
                model_config_id %s,
                optimizer_config_id %s,
                automation_config_json TEXT NOT NULL DEFAULT '{"enabled":true,"case_threshold":1,"cooldown_seconds":0,"max_candidates":1}',
                automation_revision INTEGER NOT NULL DEFAULT 1,
                automation_last_triggered_at DATETIME,
                rubric_version VARCHAR(40) NOT NULL DEFAULT 'rubric-v2.1',
                dimension_schema_key VARCHAR(80),
                dimension_schema_version VARCHAR(64),
                created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """ % (prompt_fk, prompt_fk, model_fk, optimizer_fk))
        ordered = (
            "id", "category_key", "display_name", "description", "status",
            "allowed_mime_types_json", "preprocess_config_json", "pipeline_config_json",
            "pipeline_revision", "prompt_a_id", "prompt_b_id", "model_config_id",
            "optimizer_config_id", "automation_config_json", "automation_revision",
            "automation_last_triggered_at", "rubric_version", "dimension_schema_key",
            "dimension_schema_version", "created_by", "created_at", "updated_at",
        )
        shared = [name for name in ordered if name in columns]
        connection.exec_driver_sql(
            "INSERT INTO evaluation_category_profiles_v40 ("
            + ",".join(shared)
            + ") SELECT "
            + ",".join(shared)
            + " FROM evaluation_category_profiles"
        )
        connection.exec_driver_sql("DROP TABLE evaluation_category_profiles")
        connection.exec_driver_sql(
            "ALTER TABLE evaluation_category_profiles_v40 "
            "RENAME TO evaluation_category_profiles"
        )
    else:
        for name, definition in (
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("pipeline_config_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("pipeline_revision", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE evaluation_category_profiles ADD COLUMN {name} {definition}"
                )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_evaluation_category_profiles_category_key "
        "ON evaluation_category_profiles(category_key)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_evaluation_category_profiles_status "
        "ON evaluation_category_profiles(status)"
    )
    rows = connection.exec_driver_sql(
        "SELECT id, category_key, preprocess_config_json, pipeline_config_json "
        "FROM evaluation_category_profiles"
    ).fetchall()
    for row in rows:
        try:
            existing = json.loads(row[3] or "{}")
        except json.JSONDecodeError:
            existing = {}
        if existing.get("schema_version") == "category-pipeline-v1":
            continue
        try:
            preprocess = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            preprocess = {}
        frozen = pipeline_json(legacy_preprocess_to_pipeline(str(row[1]), preprocess))
        connection.exec_driver_sql(
            "UPDATE evaluation_category_profiles SET pipeline_config_json = ?, "
            "pipeline_revision = 1 WHERE id = ?",
            (frozen, row[0]),
        )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"v40 类目模板迁移后外键校验失败：{violations[:3]}")


def _migration_041_unified_label_platform_contract(connection: Connection) -> None:
    """Local-first upstream projection, publish read model and transactional outbox."""
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS content_records (
            id INTEGER PRIMARY KEY,
            source_system VARCHAR(120) NOT NULL,
            source_content_id VARCHAR(160) NOT NULL,
            category_key VARCHAR(40) NOT NULL,
            source_version VARCHAR(120) NOT NULL,
            source_occurred_at DATETIME NOT NULL,
            asset_id INTEGER REFERENCES assets(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL DEFAULT 'awaiting_material'
                CHECK(status IN ('awaiting_material','ready','deleted')),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_system, source_content_id)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS content_ingress_events (
            id INTEGER PRIMARY KEY,
            event_id VARCHAR(160) NOT NULL UNIQUE,
            schema_version VARCHAR(40) NOT NULL,
            event_type VARCHAR(40) NOT NULL
                CHECK(event_type IN ('content.created','content.updated','content.deleted')),
            source_system VARCHAR(120) NOT NULL,
            occurred_at DATETIME NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            payload_json TEXT NOT NULL,
            content_record_id INTEGER REFERENCES content_records(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL
                CHECK(status IN ('applied','stale','awaiting_material')),
            received_by VARCHAR(80) NOT NULL DEFAULT 'system',
            received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS label_releases (
            id INTEGER PRIMARY KEY,
            release_key VARCHAR(160) NOT NULL UNIQUE,
            content_key VARCHAR(320) NOT NULL,
            category_key VARCHAR(40) NOT NULL,
            evaluation_id INTEGER REFERENCES evaluation_results(id) ON DELETE RESTRICT,
            final_review_id INTEGER REFERENCES human_reviews(id) ON DELETE RESTRICT,
            source_release_id INTEGER REFERENCES label_releases(id) ON DELETE RESTRICT,
            label_schema_version VARCHAR(40) NOT NULL,
            label_payload_json TEXT NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'pending_review'
                CHECK(status IN ('pending_review','approved','published','rejected')),
            requested_by VARCHAR(80) NOT NULL,
            requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_by VARCHAR(80),
            approved_at DATETIME,
            published_at DATETIME
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS published_labels (
            id INTEGER PRIMARY KEY,
            release_id INTEGER NOT NULL UNIQUE REFERENCES label_releases(id) ON DELETE RESTRICT,
            content_key VARCHAR(320) NOT NULL,
            category_key VARCHAR(40) NOT NULL,
            version INTEGER NOT NULL,
            label_schema_version VARCHAR(40) NOT NULL,
            label_payload_json TEXT NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'published'
                CHECK(status IN ('published','superseded')),
            published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            superseded_at DATETIME,
            UNIQUE(content_key, version)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS label_outbox_events (
            id INTEGER PRIMARY KEY,
            event_id VARCHAR(160) NOT NULL UNIQUE,
            release_id INTEGER NOT NULL REFERENCES label_releases(id) ON DELETE RESTRICT,
            published_label_id INTEGER NOT NULL REFERENCES published_labels(id) ON DELETE RESTRICT,
            content_key VARCHAR(320) NOT NULL,
            operation VARCHAR(30) NOT NULL CHECK(operation IN ('published','rolled_back')),
            payload_hash VARCHAR(64) NOT NULL,
            payload_json TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(release_id, operation)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS consumer_sync_checkpoints (
            id INTEGER PRIMARY KEY,
            consumer_name VARCHAR(120) NOT NULL UNIQUE,
            cursor INTEGER NOT NULL DEFAULT 0 CHECK(cursor >= 0),
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_content_records_status ON content_records(status)",
        "CREATE INDEX IF NOT EXISTS ix_content_records_category ON content_records(category_key)",
        "CREATE INDEX IF NOT EXISTS ix_content_ingress_received_at ON content_ingress_events(received_at)",
        "CREATE INDEX IF NOT EXISTS ix_label_releases_content_status ON label_releases(content_key, status)",
        "CREATE INDEX IF NOT EXISTS ix_published_labels_current ON published_labels(content_key, status, version)",
        "CREATE INDEX IF NOT EXISTS ix_label_outbox_cursor ON label_outbox_events(id)",
    ):
        connection.exec_driver_sql(statement)
    for statement in (
        """CREATE TRIGGER IF NOT EXISTS trg_content_ingress_events_append_only
        BEFORE UPDATE ON content_ingress_events
        BEGIN SELECT RAISE(ABORT, 'ContentIngressEvent is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_content_ingress_events_no_delete
        BEFORE DELETE ON content_ingress_events
        BEGIN SELECT RAISE(ABORT, 'ContentIngressEvent cannot be deleted'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_label_outbox_events_append_only
        BEFORE UPDATE ON label_outbox_events
        BEGIN SELECT RAISE(ABORT, 'LabelOutboxEvent is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_label_outbox_events_no_delete
        BEFORE DELETE ON label_outbox_events
        BEGIN SELECT RAISE(ABORT, 'LabelOutboxEvent cannot be deleted'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_published_labels_immutable
        BEFORE UPDATE ON published_labels
        WHEN OLD.status = 'superseded'
          OR NEW.release_id <> OLD.release_id
          OR NEW.content_key <> OLD.content_key
          OR NEW.category_key <> OLD.category_key
          OR NEW.version <> OLD.version
          OR NEW.label_schema_version <> OLD.label_schema_version
          OR NEW.label_payload_json <> OLD.label_payload_json
          OR NEW.payload_hash <> OLD.payload_hash
          OR NEW.published_at <> OLD.published_at
        BEGIN SELECT RAISE(ABORT, 'PublishedLabel is immutable'); END""",
    ):
        connection.exec_driver_sql(statement)
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"v41 统一标签平台迁移外键校验失败：{violations[:3]}")


def _migration_042_add_automation_worker_status(connection: Connection) -> None:
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS automation_worker_statuses (
            worker_id VARCHAR(120) PRIMARY KEY,
            started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_tick_at DATETIME,
            last_status VARCHAR(80) NOT NULL DEFAULT 'starting',
            last_error TEXT NOT NULL DEFAULT '',
            last_result_json TEXT NOT NULL DEFAULT '{}',
            consecutive_errors INTEGER NOT NULL DEFAULT 0
                CHECK(consecutive_errors >= 0),
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_automation_worker_statuses_last_seen "
        "ON automation_worker_statuses(last_seen_at)"
    )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"v42 自动优化 Worker 状态迁移外键校验失败：{violations[:3]}")


def _migration_043_add_evaluation_packages(connection: Connection) -> None:
    """Persist the immutable EvaluationPackage review/release aggregate."""
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS evaluation_packages (
            id INTEGER PRIMARY KEY,
            package_key VARCHAR(160) NOT NULL UNIQUE,
            request_hash VARCHAR(64) NOT NULL,
            category_key VARCHAR(40) NOT NULL,
            prompt_mode VARCHAR(20) NOT NULL
                CHECK(prompt_mode IN ('single','dual')),
            prompt_a_id INTEGER NOT NULL
                REFERENCES prompt_versions(id) ON DELETE RESTRICT,
            prompt_b_id INTEGER
                REFERENCES prompt_versions(id) ON DELETE RESTRICT,
            dimension_schema_id INTEGER
                REFERENCES dimension_schemas(id) ON DELETE RESTRICT,
            dimension_route_policy_id INTEGER
                REFERENCES dimension_route_policies(id) ON DELETE RESTRICT,
            sample_set_id INTEGER NOT NULL
                REFERENCES sample_sets(id) ON DELETE RESTRICT,
            baseline_strategy_bundle_id INTEGER
                REFERENCES strategy_bundles(id) ON DELETE RESTRICT,
            candidate_strategy_bundle_id INTEGER NOT NULL
                REFERENCES strategy_bundles(id) ON DELETE RESTRICT,
            regression_run_id INTEGER NOT NULL
                REFERENCES prompt_regression_runs(id) ON DELETE RESTRICT,
            automation_run_id INTEGER
                REFERENCES automation_optimization_runs(id) ON DELETE RESTRICT,
            metric_snapshot_id INTEGER
                REFERENCES prompt_metric_snapshots(id) ON DELETE RESTRICT,
            canonical_manifest_json TEXT NOT NULL
                CHECK(json_valid(canonical_manifest_json)
                      AND json_type(canonical_manifest_json, '$') = 'object'),
            canonical_manifest_hash VARCHAR(64) NOT NULL,
            ai_recommendation VARCHAR(40) NOT NULL DEFAULT 'pending',
            change_summary TEXT NOT NULL DEFAULT '',
            status VARCHAR(30) NOT NULL DEFAULT 'validating'
                CHECK(status IN (
                    'validating','awaiting_review','approved','rejected',
                    'published','archived'
                )),
            review_revision INTEGER NOT NULL DEFAULT 0
                CHECK(review_revision >= 0),
            review_decision VARCHAR(20),
            review_note TEXT NOT NULL DEFAULT '',
            reviewed_by VARCHAR(80),
            reviewed_at DATETIME,
            published_by VARCHAR(80),
            published_at DATETIME,
            archived_by VARCHAR(80),
            archived_at DATETIME,
            archive_reason TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(80) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(length(request_hash) = 64
                  AND request_hash = lower(request_hash)
                  AND request_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK(length(canonical_manifest_hash) = 64
                  AND canonical_manifest_hash = lower(canonical_manifest_hash)
                  AND canonical_manifest_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK((prompt_mode = 'single' AND prompt_b_id IS NULL)
                  OR (prompt_mode = 'dual' AND prompt_b_id IS NOT NULL))
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_evaluation_packages_status "
        "ON evaluation_packages(status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_packages_category "
        "ON evaluation_packages(category_key, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_packages_automation "
        "ON evaluation_packages(automation_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_packages_regression "
        "ON evaluation_packages(regression_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_packages_candidate "
        "ON evaluation_packages(candidate_strategy_bundle_id)",
    ):
        connection.exec_driver_sql(statement)
    for statement in (
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_packages_frozen_identity
        BEFORE UPDATE ON evaluation_packages
        WHEN NEW.package_key IS NOT OLD.package_key
          OR NEW.request_hash IS NOT OLD.request_hash
          OR NEW.category_key IS NOT OLD.category_key
          OR NEW.prompt_mode IS NOT OLD.prompt_mode
          OR NEW.prompt_a_id IS NOT OLD.prompt_a_id
          OR NEW.prompt_b_id IS NOT OLD.prompt_b_id
          OR NEW.dimension_schema_id IS NOT OLD.dimension_schema_id
          OR NEW.dimension_route_policy_id IS NOT OLD.dimension_route_policy_id
          OR NEW.sample_set_id IS NOT OLD.sample_set_id
          OR NEW.baseline_strategy_bundle_id IS NOT OLD.baseline_strategy_bundle_id
          OR NEW.candidate_strategy_bundle_id IS NOT OLD.candidate_strategy_bundle_id
          OR NEW.regression_run_id IS NOT OLD.regression_run_id
          OR NEW.automation_run_id IS NOT OLD.automation_run_id
          OR NEW.metric_snapshot_id IS NOT OLD.metric_snapshot_id
        BEGIN SELECT RAISE(ABORT, 'EvaluationPackage frozen identity is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_packages_reviewed_manifest
        BEFORE UPDATE ON evaluation_packages
        WHEN OLD.status <> 'validating'
         AND (NEW.canonical_manifest_json IS NOT OLD.canonical_manifest_json
          OR NEW.canonical_manifest_hash IS NOT OLD.canonical_manifest_hash
          OR NEW.ai_recommendation IS NOT OLD.ai_recommendation
          OR NEW.change_summary IS NOT OLD.change_summary)
        BEGIN SELECT RAISE(ABORT, 'EvaluationPackage reviewed manifest is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_packages_state_transition
        BEFORE UPDATE OF status ON evaluation_packages
        WHEN NEW.status <> OLD.status AND NOT (
             (OLD.status = 'validating' AND NEW.status = 'awaiting_review')
          OR (OLD.status = 'awaiting_review' AND NEW.status IN ('approved','rejected'))
          OR (OLD.status = 'approved' AND NEW.status IN ('published','archived'))
          OR (OLD.status IN ('rejected','published') AND NEW.status = 'archived')
        )
        BEGIN SELECT RAISE(ABORT, 'EvaluationPackage illegal state transition'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_packages_no_delete
        BEFORE DELETE ON evaluation_packages
        BEGIN SELECT RAISE(ABORT, 'EvaluationPackage cannot be deleted'); END""",
    ):
        connection.exec_driver_sql(statement)
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"v43 评测包聚合迁移外键校验失败：{violations[:3]}"
        )


def _migration_044_add_evaluation_production_runs(connection: Connection) -> None:
    """Add the mutable orchestration record kept separate from final packages."""
    connection.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS evaluation_production_runs (
            id INTEGER PRIMARY KEY,
            idempotency_key VARCHAR(160) NOT NULL UNIQUE,
            request_hash VARCHAR(64) NOT NULL,
            material_package_id INTEGER NOT NULL
                REFERENCES material_packages(id) ON DELETE RESTRICT,
            category_key VARCHAR(40) NOT NULL,
            category_profile_snapshot_json TEXT NOT NULL
                CHECK(json_valid(category_profile_snapshot_json)
                      AND json_type(category_profile_snapshot_json, '$') = 'object'),
            category_profile_hash VARCHAR(64) NOT NULL,
            job_ids_json TEXT NOT NULL DEFAULT '[]'
                CHECK(json_valid(job_ids_json)
                      AND json_type(job_ids_json, '$') = 'array'),
            batch_key VARCHAR(120) NOT NULL UNIQUE,
            automation_run_id INTEGER
                REFERENCES automation_optimization_runs(id) ON DELETE RESTRICT,
            regression_run_id INTEGER
                REFERENCES prompt_regression_runs(id) ON DELETE RESTRICT,
            evaluation_package_id INTEGER UNIQUE
                REFERENCES evaluation_packages(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL DEFAULT 'preparing'
                CHECK(status IN (
                    'preparing','queued','evaluating','first_review',
                    'optimizing','regressing','awaiting_review','approved',
                    'rejected','published','blocked','failed','archived'
                )),
            current_stage VARCHAR(40) NOT NULL DEFAULT 'preparing',
            blockers_json TEXT NOT NULL DEFAULT '[]'
                CHECK(json_valid(blockers_json)
                      AND json_type(blockers_json, '$') = 'array'),
            error_code VARCHAR(80) NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            audit_revision INTEGER NOT NULL DEFAULT 1
                CHECK(audit_revision >= 1),
            created_by VARCHAR(80) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_reconciled_at DATETIME,
            finished_at DATETIME,
            archived_at DATETIME,
            CHECK(length(request_hash) = 64
                  AND request_hash = lower(request_hash)
                  AND request_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK(length(category_profile_hash) = 64
                  AND category_profile_hash = lower(category_profile_hash)
                  AND category_profile_hash NOT GLOB '*[^0-9a-f]*')
        )
    """)
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_evaluation_production_runs_status "
        "ON evaluation_production_runs(status, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_production_runs_material "
        "ON evaluation_production_runs(material_package_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_production_runs_category "
        "ON evaluation_production_runs(category_key, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_production_runs_automation "
        "ON evaluation_production_runs(automation_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_production_runs_regression "
        "ON evaluation_production_runs(regression_run_id)",
    ):
        connection.exec_driver_sql(statement)
    for statement in (
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_production_runs_frozen
        BEFORE UPDATE ON evaluation_production_runs
        WHEN NEW.idempotency_key IS NOT OLD.idempotency_key
          OR NEW.request_hash IS NOT OLD.request_hash
          OR NEW.material_package_id IS NOT OLD.material_package_id
          OR NEW.category_key IS NOT OLD.category_key
          OR NEW.category_profile_snapshot_json IS NOT OLD.category_profile_snapshot_json
          OR NEW.category_profile_hash IS NOT OLD.category_profile_hash
          OR NEW.batch_key IS NOT OLD.batch_key
          OR NEW.created_by IS NOT OLD.created_by
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.started_at IS NOT OLD.started_at
        BEGIN SELECT RAISE(ABORT, 'EvaluationProductionRun frozen fields are immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_evaluation_production_runs_no_delete
        BEFORE DELETE ON evaluation_production_runs
        BEGIN SELECT RAISE(ABORT, 'EvaluationProductionRun cannot be deleted'); END""",
    ):
        connection.exec_driver_sql(statement)
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"v44 评测生产编排迁移外键校验失败：{violations[:3]}"
        )


MIGRATIONS = [
    Migration(1, "add_sample_expected_level", _migration_001_add_sample_expected_level),
    Migration(2, "add_review_corrections", _migration_002_add_review_corrections),
    Migration(3, "add_evaluation_job_refs", _migration_003_add_evaluation_job_refs),
    Migration(4, "add_evaluation_job_updated_at", _migration_004_add_evaluation_job_updated_at),
    Migration(5, "add_prompt_version_updated_at", _migration_005_add_prompt_version_updated_at),
    Migration(6, "add_sample_set_kind_status", _migration_006_add_sample_set_kind_status),
    Migration(7, "add_sample_item_truth_fields", _migration_007_add_sample_item_truth_fields),
    Migration(8, "add_model_high_risk_review", _migration_008_add_model_high_risk_review),
    Migration(9, "add_result_risk_review_fields", _migration_009_add_result_risk_review_fields),
    Migration(10, "add_result_updated_at", _migration_010_add_result_updated_at),
    Migration(11, "add_strategy_bundles", _migration_011_add_strategy_bundles),
    Migration(
        12,
        "add_paired_strategy_regression",
        _migration_012_add_paired_strategy_regression,
    ),
    Migration(
        13,
        "freeze_paired_strategy_snapshots",
        _migration_013_freeze_paired_strategy_snapshots,
    ),
    Migration(
        14,
        "add_loop_queue_and_breakers",
        _migration_014_add_loop_queue_and_breakers,
    ),
    Migration(
        15,
        "harden_loop_retry_and_scheduler",
        _migration_015_harden_loop_retry_and_scheduler,
    ),
    Migration(
        16,
        "finalize_retry_and_loop_guards",
        _migration_016_finalize_retry_and_loop_guards,
    ),
    Migration(
        17,
        "add_canary_run_persistence",
        _migration_017_add_canary_run_persistence,
    ),
    Migration(
        18,
        "add_prompt_optimizer_stage_audit",
        _migration_018_add_prompt_optimizer_stage_audit,
    ),
    Migration(
        19,
        "add_staged_human_review_and_candidate_gate",
        _migration_019_add_staged_human_review_and_candidate_gate,
    ),
    Migration(
        20,
        "add_material_packages_and_review_panels",
        _migration_020_add_material_packages_and_review_panels,
    ),
    Migration(
        21,
        "add_prompt_metric_snapshots",
        _migration_021_add_prompt_metric_snapshots,
    ),
    Migration(
        22,
        "add_phase_b_automation_feedback_benchmarks",
        _migration_022_add_phase_b_automation_feedback_benchmarks,
    ),
    Migration(
        23,
        "enforce_material_package_immutability",
        _migration_023_enforce_material_package_immutability,
    ),
    Migration(
        24,
        "add_real_executor_safety",
        _migration_024_add_real_executor_safety,
    ),
    Migration(
        25,
        "add_baseline_regression_and_repair_prompt_fk",
        _migration_025_add_baseline_regression_and_repair_prompt_fk,
    ),
    Migration(
        26,
        "add_dimension_schemas",
        _migration_026_add_dimension_schemas,
    ),
    Migration(
        27,
        "bind_dimension_contract_to_strategy",
        _migration_027_bind_dimension_contract_to_strategy,
    ),
    Migration(
        28,
        "add_dimension_route_policies",
        _migration_028_add_dimension_route_policies,
    ),
    Migration(
        29,
        "add_routed_strategy_bundles",
        _migration_029_add_routed_strategy_bundles,
    ),
    Migration(
        30,
        "add_dimension_calibration_results",
        _migration_030_add_dimension_calibration_results,
    ),
    Migration(
        31,
        "add_evaluation_category_profiles",
        _migration_031_add_evaluation_category_profiles,
    ),
    Migration(
        32,
        "repair_evaluation_category_profiles",
        _migration_032_repair_evaluation_category_profiles,
    ),
    Migration(
        33,
        "add_evaluation_preprocess_snapshot",
        _migration_033_add_evaluation_preprocess_snapshot,
    ),
    Migration(
        34,
        "freeze_job_category_profile",
        _migration_034_freeze_job_category_profile,
    ),
    Migration(
        35,
        "generalize_model_names_and_pdf_summary",
        _migration_035_generalize_model_names_and_pdf_summary,
    ),
    Migration(
        36,
        "add_category_automation_isolation",
        _migration_036_add_category_automation_isolation,
    ),
    Migration(
        37,
        "add_asset_category_channel",
        _migration_037_add_asset_category_channel,
    ),
    Migration(
        38,
        "add_material_package_status",
        _migration_038_add_material_package_status,
    ),
    Migration(
        39,
        "add_accounts_and_model_registry",
        _migration_039_add_accounts_and_model_registry,
    ),
    Migration(
        40,
        "modular_category_pipelines",
        _migration_040_modular_category_pipelines,
    ),
    Migration(
        41,
        "unified_label_platform_contract",
        _migration_041_unified_label_platform_contract,
    ),
    Migration(
        42,
        "add_automation_worker_status",
        _migration_042_add_automation_worker_status,
    ),
    Migration(
        43,
        "add_evaluation_packages",
        _migration_043_add_evaluation_packages,
    ),
    Migration(
        44,
        "add_evaluation_production_runs",
        _migration_044_add_evaluation_production_runs,
    ),
]


def run_migrations(connection: Connection) -> None:
    _probe_sqlite_json_functions(connection)
    _ensure_schema_migrations_table(connection)

    applied_versions = {
        row[0] for row in connection.exec_driver_sql("SELECT version FROM schema_migrations")
    }

    for migration in MIGRATIONS:
        if migration.version not in applied_versions:
            migration.up(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
