from __future__ import annotations

import re
from typing import Callable

from sqlalchemy import Connection


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
