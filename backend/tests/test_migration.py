from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models  # noqa: F401
from app.database import Base
from app.migration import compare_results
from app.migrations import run_migrations


MIGRATION_NAMES = [
    "add_sample_expected_level",
    "add_review_corrections",
    "add_evaluation_job_refs",
    "add_evaluation_job_updated_at",
    "add_prompt_version_updated_at",
    "add_sample_set_kind_status",
    "add_sample_item_truth_fields",
    "add_model_high_risk_review",
    "add_result_risk_review_fields",
    "add_result_updated_at",
    "add_strategy_bundles",
    "add_paired_strategy_regression",
    "freeze_paired_strategy_snapshots",
    "add_loop_queue_and_breakers",
    "harden_loop_retry_and_scheduler",
    "finalize_retry_and_loop_guards",
]


def result(level: str, category: str, confidence: float = 0.9) -> dict:
    return {
        "level": level,
        "score": 82.0,
        "confidence": confidence,
        "needs_review": False,
        "precheck": {"classification": {"primary_category": category}},
    }


def _paired_strategy_snapshot(bundle_id: int, canonical_hash: str) -> str:
    model_config = {
        "name": "model",
        "provider": "test",
        "base_url": "https://example.test/v1",
        "api_path": "/chat/completions",
        "model_id": "model",
        "temperature": 0.1,
        "max_tokens": 4096,
        "timeout_seconds": 120,
        "max_retries": 1,
        "max_concurrency": 2,
        "structured_output": True,
        "high_risk_review_enabled": True,
    }
    return json.dumps(
        {
            "bundle_id": bundle_id,
            "canonical_hash": canonical_hash,
            "schema_version": "strategy-bundle-v1",
            "model_id": "model",
            "model_config": model_config,
            "prompt_a": {
                "id": 1,
                "stage": "A",
                "version": "A1",
                "name": "A",
                "rubric_version": "R1",
                "system_prompt": "system A",
                "user_prompt": "user A",
            },
            "prompt_b": {
                "id": 2,
                "stage": "B",
                "version": "B1",
                "name": "B",
                "rubric_version": "R1",
                "system_prompt": "system B",
                "user_prompt": "user B",
            },
            "rubric_version": "R1",
            "engine_version": "E1",
            "sampling_policy": None,
            "risk_review_version": None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _engine(tmp_path, name: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _create_latest_and_run_migrations(engine) -> None:
    """Use the production startup order required by P0-A."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        run_migrations(connection)


def _create_complete_v10_database(engine) -> None:
    """Create every v10 table, omitting only migration-11 schema changes."""
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE evaluation_jobs (
                id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL
                    REFERENCES assets(id) ON DELETE CASCADE,
                prompt_a_id INTEGER
                    REFERENCES prompt_versions(id) ON DELETE SET NULL,
                prompt_b_id INTEGER
                    REFERENCES prompt_versions(id) ON DELETE SET NULL,
                regression_item_id INTEGER
                    REFERENCES prompt_regression_items(id)
                    ON DELETE SET NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'queued',
                stage VARCHAR(30) NOT NULL DEFAULT 'waiting',
                progress INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                worker_id VARCHAR(120),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                started_at DATETIME,
                finished_at DATETIME
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE evaluation_results (
                id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL
                    REFERENCES assets(id) ON DELETE CASCADE,
                job_id INTEGER NOT NULL UNIQUE
                    REFERENCES evaluation_jobs(id) ON DELETE CASCADE,
                precheck_json TEXT NOT NULL,
                aesthetic_json TEXT,
                scoring_json TEXT NOT NULL,
                raw_response_a TEXT NOT NULL,
                raw_response_b TEXT,
                raw_response_risk_review TEXT,
                risk_review_json TEXT,
                score FLOAT,
                level VARCHAR(10),
                confidence FLOAT,
                needs_review BOOLEAN NOT NULL,
                model_id VARCHAR(200) NOT NULL,
                prompt_a_version VARCHAR(40) NOT NULL,
                prompt_b_version VARCHAR(40),
                risk_review_version VARCHAR(40),
                rubric_version VARCHAR(40) NOT NULL,
                engine_version VARCHAR(40) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)

    v10_tables = [
        table
        for table in Base.metadata.tables.values()
        if table.name not in {
            "evaluation_jobs",
            "evaluation_results",
            "strategy_bundles",
        }
    ]
    Base.metadata.create_all(bind=engine, tables=v10_tables)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX ix_evaluation_results_asset_id "
            "ON evaluation_results(asset_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_evaluation_results_level "
            "ON evaluation_results(level)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_evaluation_results_needs_review "
            "ON evaluation_results(needs_review)"
        )
        connection.exec_driver_sql("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for version, name in enumerate(MIGRATION_NAMES[:10], start=1):
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )


def test_same_high_confidence_result_can_auto_pass() -> None:
    comparison = compare_results(result("L4", "住宅设计"), result("L4", "住宅设计"))
    assert comparison["requires_review"] is False
    assert comparison["reasons"] == []


def test_level_change_requires_review() -> None:
    comparison = compare_results(result("L4", "住宅设计"), result("L3", "住宅设计"))
    assert comparison["requires_review"] is True
    assert comparison["level_delta"] == -1


def test_small_agreement_audit_requires_review() -> None:
    comparison = compare_results(
        result("L4", "住宅设计"), result("L4", "住宅设计"), audit_sample=True
    )
    assert comparison["requires_review"] is True
    assert "一致样本 5% 抽检" in comparison["reasons"]


def test_human_corrected_baseline_level_is_used() -> None:
    baseline = result("L3", "住宅设计")
    baseline["final_level"] = "L4"
    comparison = compare_results(baseline, result("L4", "住宅设计"))
    assert comparison["requires_review"] is False
    assert comparison["baseline_level"] == "L4"


def test_schema_migrations_table_created_with_all_versions(tmp_path) -> None:
    engine = _engine(tmp_path, "versions.db")
    _create_latest_and_run_migrations(engine)
    try:
        with engine.connect() as connection:
            rows = list(
                connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations ORDER BY version"
                )
            )
        assert [row[0] for row in rows] == list(range(1, 17))
        assert [row[1] for row in rows] == MIGRATION_NAMES
    finally:
        engine.dispose()


def test_repeated_migration_is_idempotent(tmp_path) -> None:
    engine = _engine(tmp_path, "idempotent.db")
    _create_latest_and_run_migrations(engine)
    try:
        with engine.begin() as connection:
            run_migrations(connection)
            run_migrations(connection)
            versions = list(
                connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
        assert [row[0] for row in versions] == list(range(1, 17))
    finally:
        engine.dispose()


def test_existing_data_survives_create_all_then_migrations(tmp_path) -> None:
    engine = _engine(tmp_path, "preserve.db")
    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            asset = models.Asset(
                original_name="old.jpg",
                stored_name="old.jpg",
                mime_type="image/jpeg",
                size_bytes=10,
                sha256="a" * 64,
            )
            db.add(asset)
            db.flush()
            db.add_all(
                [
                    models.EvaluationJob(asset_id=asset.id, status="queued"),
                    models.EvaluationJob(asset_id=asset.id, status="completed"),
                ]
            )
            db.commit()

        with engine.begin() as connection:
            run_migrations(connection)
            rows = list(
                connection.exec_driver_sql(
                    "SELECT status FROM evaluation_jobs ORDER BY id"
                )
            )
        assert [row[0] for row in rows] == ["queued", "completed"]
    finally:
        engine.dispose()


def test_foreign_keys_remain_enabled(tmp_path) -> None:
    engine = _engine(tmp_path, "foreign-keys.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            Base.metadata.create_all(bind=connection)
            run_migrations(connection)
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_complete_v10_database_forward_migrates_versions_11_to_13(tmp_path) -> None:
    engine = _engine(tmp_path, "v10-forward.db")
    _create_complete_v10_database(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO assets (
                    id, original_name, stored_name, mime_type, size_bytes,
                    sha256, status, created_at
                )
                VALUES (
                    1, 'old.jpg', 'old.jpg', 'image/jpeg', 10,
                    ?, 'evaluated', CURRENT_TIMESTAMP
                )
                """,
                ("a" * 64,),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO evaluation_jobs (
                    id, asset_id, status, stage, progress, attempts,
                    error_message, created_at, updated_at
                )
                VALUES (
                    1, 1, 'completed', 'done', 100, 1, '',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO evaluation_results (
                    id, asset_id, job_id, precheck_json, scoring_json,
                    raw_response_a, needs_review, model_id, prompt_a_version,
                    rubric_version, engine_version, created_at, updated_at
                )
                VALUES (
                    1, 1, 1, '{}', '{}', '{}', 0, 'old-model', 'A-v0',
                    'rubric-v0', 'engine-v0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )

            before = list(
                connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            assert [row[0] for row in before] == list(range(1, 11))

            run_migrations(connection)

            versions = list(
                connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            assert [row[0] for row in versions] == list(range(1, 17))
            old_result = connection.exec_driver_sql(
                """
                SELECT model_id, prompt_a_version, strategy_bundle_id,
                       strategy_snapshot_json
                FROM evaluation_results
                WHERE id = 1
                """
            ).one()
            assert tuple(old_result) == ("old-model", "A-v0", None, None)

            result_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(evaluation_results)"
                )
            }
            assert {"strategy_bundle_id", "strategy_snapshot_json"} <= result_columns
            assert (
                connection.exec_driver_sql(
                    """
                    SELECT COUNT(*)
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'strategy_bundles'
                    """
                ).scalar_one()
                == 1
            )

            foreign_keys = list(
                connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(evaluation_results)"
                )
            )
            strategy_fk = next(
                row for row in foreign_keys if row[2] == "strategy_bundles"
            )
            assert strategy_fk[3] == "strategy_bundle_id"
            assert strategy_fk[6] in {"RESTRICT", "NO ACTION"}

            run_migrations(connection)
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).scalar_one()
                == 16
            )
    finally:
        engine.dispose()


def test_migration_11_database_guards_reject_bundle_update_and_delete(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v11-guards.db")
    _create_complete_v10_database(engine)
    try:
        with engine.begin() as connection:
            run_migrations(connection)
            connection.exec_driver_sql(
                """
                INSERT INTO strategy_bundles (
                    id, canonical_hash, model_id, model_config_snapshot,
                    prompt_a_version, rubric_version, engine_version,
                    created_at
                )
                VALUES (
                    1, ?, 'model', '{}', 'A1', 'R1', 'E1',
                    CURRENT_TIMESTAMP
                )
                """,
                ("a" * 64,),
            )

        with engine.begin() as connection:
            try:
                connection.exec_driver_sql(
                    "UPDATE strategy_bundles SET model_id = 'changed' WHERE id = 1"
                )
            except IntegrityError as exc:
                assert "immutable" in str(exc)
            else:
                raise AssertionError("数据库必须拒绝 StrategyBundle 更新")

        with engine.begin() as connection:
            try:
                connection.exec_driver_sql(
                    "DELETE FROM strategy_bundles WHERE id = 1"
                )
            except IntegrityError as exc:
                assert "cannot be deleted" in str(exc)
            else:
                raise AssertionError("数据库必须拒绝 StrategyBundle 删除")
    finally:
        engine.dispose()


def test_complete_v11_database_forward_migrates_paired_regression_contract(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v11-to-v13.db")
    try:
        with engine.begin() as connection:
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
            connection.exec_driver_sql("""
                CREATE TABLE prompt_regression_runs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    sample_set_id INTEGER NOT NULL,
                    prompt_a_id INTEGER NOT NULL,
                    prompt_b_id INTEGER NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued',
                    threshold FLOAT NOT NULL DEFAULT 0.9,
                    total INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    passed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE prompt_regression_items (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL,
                    sample_item_id INTEGER NOT NULL,
                    evaluation_id INTEGER,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued',
                    comparison_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE sample_set_items (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_results (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    strategy_bundle_id INTEGER
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_jobs (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued'
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE human_reviews (
                    id INTEGER PRIMARY KEY,
                    evaluation_id INTEGER NOT NULL,
                    decision VARCHAR(30) NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:11], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )

            run_migrations(connection)
            versions = [
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            assert versions == list(range(1, 17))

            run_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(prompt_regression_runs)"
                )
            }
            assert {
                "regression_mode",
                "baseline_strategy_bundle_id",
                "candidate_strategy_bundle_id",
                "baseline_strategy_snapshot_json",
                "candidate_strategy_snapshot_json",
                "sample_set_version",
                "sample_manifest_json",
                "metric_rules_version",
                "metric_rules_json",
                "summary_json",
                "recommendation",
                "approval_status",
                "approved_by",
                "approval_note",
                "approved_at",
            } <= run_columns
            item_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(prompt_regression_items)"
                )
            }
            assert {
                "sample_role",
                "source_evaluation_id",
                "source_review_id",
                "truth_snapshot_json",
                "baseline_evaluation_id",
                "candidate_evaluation_id",
                "baseline_result_json",
                "candidate_result_json",
            } <= item_columns

            for bundle_id, canonical in ((1, "a" * 64), (2, "b" * 64)):
                snapshot = json.loads(
                    _paired_strategy_snapshot(bundle_id, canonical)
                )
                connection.exec_driver_sql(
                    """
                    INSERT INTO strategy_bundles (
                        id, canonical_hash, model_id, model_config_snapshot,
                        prompt_a_version, prompt_b_version, rubric_version,
                        engine_version
                    )
                    VALUES (?, ?, 'model', ?, 'A1', 'B1', 'R1', 'E1')
                    """,
                    (
                        bundle_id,
                        canonical,
                        json.dumps(
                            snapshot["model_config"],
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            connection.exec_driver_sql(
                "INSERT INTO sample_set_items (id, asset_id) VALUES (1, 10)"
            )
            connection.exec_driver_sql(
                """
                INSERT INTO evaluation_results (
                    id, asset_id, strategy_bundle_id
                )
                VALUES (1, 10, 1), (2, 10, 2)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO human_reviews (id, evaluation_id, decision)
                VALUES (1, 1, 'approved')
                """
            )

            with pytest.raises(
                IntegrityError, match="Paired regression contract is invalid"
            ):
                connection.exec_driver_sql(
                    """
                    INSERT INTO prompt_regression_runs (
                        id, name, sample_set_id, prompt_a_id, prompt_b_id,
                        regression_mode, baseline_strategy_bundle_id,
                        candidate_strategy_bundle_id,
                        baseline_strategy_snapshot_json,
                        candidate_strategy_snapshot_json, sample_set_version,
                        sample_manifest_json, metric_rules_version,
                        metric_rules_json
                    )
                    VALUES (
                        1, 'invalid', 1, 1, 2, 'paired', 1, 1, ?, ?, ?,
                        '{}', 'rules-v1', '{}'
                    )
                    """,
                    (
                        _paired_strategy_snapshot(1, "a" * 64),
                        _paired_strategy_snapshot(1, "a" * 64),
                        "c" * 64,
                    ),
                )

            connection.exec_driver_sql(
                """
                INSERT INTO prompt_regression_runs (
                    id, name, sample_set_id, prompt_a_id, prompt_b_id,
                    regression_mode, baseline_strategy_bundle_id,
                    candidate_strategy_bundle_id,
                    baseline_strategy_snapshot_json,
                    candidate_strategy_snapshot_json, sample_set_version,
                    sample_manifest_json, metric_rules_version,
                    metric_rules_json
                )
                VALUES (
                    2, 'valid', 1, 1, 2, 'paired', 1, 2, ?, ?, ?,
                    '{}', 'rules-v1', '{}'
                )
                """,
                (
                    _paired_strategy_snapshot(1, "a" * 64),
                    _paired_strategy_snapshot(2, "b" * 64),
                    "d" * 64,
                ),
            )
            with pytest.raises(
                IntegrityError, match="Paired regression definition is immutable"
            ):
                connection.exec_driver_sql(
                    """
                    UPDATE prompt_regression_runs
                    SET sample_set_version = ?
                    WHERE id = 2
                    """,
                    ("e" * 64,),
                )
            with pytest.raises(IntegrityError, match="immutable"):
                connection.exec_driver_sql(
                    """
                    UPDATE prompt_regression_runs
                    SET candidate_strategy_snapshot_json = ?
                    WHERE id = 2
                    """,
                    (_paired_strategy_snapshot(1, "a" * 64),),
                )
            with pytest.raises(
                IntegrityError, match="Paired regression approval is invalid"
            ):
                connection.exec_driver_sql(
                    """
                    UPDATE prompt_regression_runs
                    SET approval_status = 'approved',
                        approved_by = 'reviewer',
                        approval_note = 'manual approval',
                        approved_at = CURRENT_TIMESTAMP
                    WHERE id = 2
                    """
                )

            connection.exec_driver_sql(
                """
                INSERT INTO prompt_regression_items (
                    id, run_id, sample_item_id, status, comparison_json,
                    sample_role, source_evaluation_id, source_review_id,
                    truth_snapshot_json
                )
                VALUES (
                    1, 2, 1, 'waiting_results', '{}', 'blind_holdout',
                    1, 1, ?
                )
                """,
                (
                    '{"source":{"evaluation_id":1,"review_id":1},'
                    '"truth":{"level":"L2"}}',
                ),
            )
            with pytest.raises(
                IntegrityError, match="Paired regression truth is immutable"
            ):
                connection.exec_driver_sql(
                    """
                    UPDATE prompt_regression_items
                    SET truth_snapshot_json = '{"changed":true}'
                    WHERE id = 1
                    """
                )
            connection.exec_driver_sql(
                """
                UPDATE prompt_regression_items
                SET status = 'completed',
                    evaluation_id = 2,
                    baseline_evaluation_id = 1,
                    candidate_evaluation_id = 2,
                    baseline_result_json = '{}',
                    candidate_result_json = '{}',
                    comparison_json = '{}'
                WHERE id = 1
                """
            )
            with pytest.raises(
                IntegrityError,
                match="Completed paired regression item is immutable",
            ):
                connection.exec_driver_sql(
                    """
                    UPDATE prompt_regression_items
                    SET comparison_json = '{"changed":true}'
                    WHERE id = 1
                    """
                )
    finally:
        engine.dispose()


def test_complete_v12_database_forward_adds_frozen_strategy_snapshots(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v12-to-v13.db")
    try:
        with engine.begin() as connection:
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
                    risk_review_version VARCHAR(40)
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE prompt_regression_runs (
                    id INTEGER PRIMARY KEY,
                    regression_mode VARCHAR(30) NOT NULL DEFAULT 'single',
                    baseline_strategy_bundle_id INTEGER,
                    candidate_strategy_bundle_id INTEGER
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_jobs (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued'
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(
                MIGRATION_NAMES[:12], start=1
            ):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (version, name),
                )

            run_migrations(connection)

            run_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(prompt_regression_runs)"
                )
            }
            assert {
                "baseline_strategy_snapshot_json",
                "candidate_strategy_snapshot_json",
            } <= run_columns
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger'"
                )
            }
            assert {
                "trg_paired_regression_strategy_snapshot_insert",
                "trg_paired_regression_strategy_snapshot_frozen",
            } <= triggers
            versions = [
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            assert versions == list(range(1, 17))
    finally:
        engine.dispose()


def test_complete_v13_database_forward_adds_loop_queue_and_breaker_contract(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v13-to-v14.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE assets (
                    id INTEGER PRIMARY KEY,
                    original_name VARCHAR(500) NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE strategy_bundles (
                    id INTEGER PRIMARY KEY
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_jobs (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued'
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:13], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql(
                "INSERT INTO assets (id, original_name) "
                "VALUES (1, 'old.jpg')"
            )
            connection.exec_driver_sql(
                "INSERT INTO strategy_bundles (id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO evaluation_jobs (id, asset_id) VALUES (1, 1)"
            )

            run_migrations(connection)
            migrated_job = connection.exec_driver_sql(
                """
                SELECT queue_class, origin_queue_class, technical_attempt
                FROM evaluation_jobs
                WHERE id = 1
                """
            ).one()
            assert tuple(migrated_job) == (
                "production_batch",
                "production_batch",
                0,
            )
            tables = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert {
                "loop_runs",
                "loop_attempts",
                "circuit_breakers",
            } <= tables
            assert (
                connection.exec_driver_sql(
                    "SELECT max(version) FROM schema_migrations"
                ).scalar_one()
                    == 16
            )

            with pytest.raises(
                IntegrityError,
                match="EvaluationJob queue contract is invalid",
            ):
                connection.exec_driver_sql(
                    """
                    INSERT INTO evaluation_jobs (
                        id, asset_id, queue_class, origin_queue_class
                    )
                    VALUES (2, 1, 'unknown', 'production_batch')
                    """
                )
    finally:
        engine.dispose()


def test_migration_14_database_guards_loop_scope_and_immutability(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v14-guards.db")
    _create_latest_and_run_migrations(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (
                    id, username, password_hash, display_name,
                    is_active, created_at
                )
                VALUES (
                    1, 'guard', 'unused', 'Guard', 1, CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO assets (
                    id, original_name, stored_name, mime_type, size_bytes,
                    sha256, status, created_at
                )
                VALUES (
                    1, 'guard.jpg', 'guard.jpg', 'image/jpeg', 10,
                    ?, 'uploaded', CURRENT_TIMESTAMP
                )
                """,
                ("f" * 64,),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO strategy_bundles (
                    id, canonical_hash, model_id, model_config_snapshot,
                    prompt_a_version, rubric_version, engine_version,
                    created_at
                )
                VALUES (
                    1, ?, 'model', '{}', 'A1', 'R1', 'E1',
                    CURRENT_TIMESTAMP
                )
                """,
                ("a" * 64,),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO loop_runs (
                    id, idempotency_key, request_fingerprint, asset_id,
                    strategy_bundle_id, status, current_round,
                    decision_json, created_by, created_at, updated_at
                )
                VALUES (
                    1, 'loop-guard-key', ?, 1, 1, 'waiting_result', 1,
                    '{}', 'guard', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                ("b" * 64,),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO loop_attempts (
                    id, loop_run_id, business_round, kind,
                    target_dimensions_json, input_evidence_json,
                    normalized_result_json, conflict_json, status,
                    technical_attempt, result_idempotency_key,
                    result_fingerprint, created_at, completed_at
                )
                VALUES (
                    1, 1, 1, 'base', '[]', '{}', '{"stable":true}',
                    '[]', 'completed', 0, 'round1-guard', ?,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                ("c" * 64,),
            )

        with engine.begin() as connection:
            with pytest.raises(
                IntegrityError,
                match="Completed LoopAttempt is immutable",
            ):
                connection.exec_driver_sql(
                    "UPDATE loop_attempts SET cost = 1 WHERE id = 1"
                )

        with engine.begin() as connection:
            with pytest.raises(
                IntegrityError,
                match="LoopRun strategy is immutable",
            ):
                connection.exec_driver_sql(
                    "UPDATE loop_runs SET strategy_bundle_id = 2 WHERE id = 1"
                )

        with engine.begin() as connection:
            with pytest.raises(
                IntegrityError,
                match="LoopAttempt target contract is invalid",
            ):
                connection.exec_driver_sql(
                    """
                    INSERT INTO loop_attempts (
                        id, loop_run_id, business_round, kind,
                        target_dimensions_json, input_evidence_json,
                        conflict_json, status, technical_attempt, created_at
                    )
                    VALUES (
                        2, 1, 2, 'targeted_recheck', '["all"]', '{}',
                        '[]', 'waiting_result', 0, CURRENT_TIMESTAMP
                    )
                    """
                )

        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    """
                    INSERT INTO loop_attempts (
                        id, loop_run_id, business_round, kind,
                        target_dimensions_json, input_evidence_json,
                        conflict_json, status, technical_attempt, created_at
                    )
                    VALUES (
                        3, 1, 4, 'arbitration', '["lighting"]', '{}',
                        '[]', 'waiting_result', 0, CURRENT_TIMESTAMP
                    )
                    """
                )
    finally:
        engine.dispose()


def test_complete_v14_database_forward_hardens_retry_chain_and_scheduler(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v14-to-v15.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_jobs (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    regression_item_id INTEGER,
                    strategy_bundle_id INTEGER,
                    loop_attempt_id INTEGER,
                    parent_job_id INTEGER,
                    queue_class VARCHAR(30) NOT NULL,
                    origin_queue_class VARCHAR(30) NOT NULL,
                    technical_attempt INTEGER NOT NULL,
                    batch_key VARCHAR(120),
                    status VARCHAR(30) NOT NULL,
                    CONSTRAINT uq_evaluation_jobs_regression_strategy
                        UNIQUE (
                            regression_item_id,
                            strategy_bundle_id
                        )
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE loop_attempts (
                    id INTEGER PRIMARY KEY,
                    loop_run_id INTEGER NOT NULL,
                    business_round INTEGER NOT NULL,
                    kind VARCHAR(30) NOT NULL,
                    target_dimensions_json TEXT NOT NULL DEFAULT '[]',
                    input_evidence_json TEXT NOT NULL DEFAULT '{}',
                    normalized_result_json TEXT,
                    conflict_json TEXT NOT NULL DEFAULT '[]',
                    status VARCHAR(30) NOT NULL DEFAULT 'waiting_result',
                    technical_attempt INTEGER NOT NULL DEFAULT 0,
                    cost FLOAT,
                    latency_ms INTEGER,
                    result_idempotency_key VARCHAR(160),
                    result_fingerprint VARCHAR(64),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:14], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, strategy_bundle_id, loop_attempt_id,
                    parent_job_id, queue_class, origin_queue_class,
                    technical_attempt, batch_key, status
                ) VALUES
                    (10, 1, 3, 7, NULL, 'interactive', 'interactive',
                     0, 'loop:1', 'retrying'),
                    (11, 1, 3, 7, 10, 'recovery', 'interactive',
                     1, 'loop:1', 'queued')
            """)

            run_migrations(connection)

            roots = list(
                connection.exec_driver_sql(
                    "SELECT id, root_job_id FROM evaluation_jobs ORDER BY id"
                )
            )
            assert [tuple(row) for row in roots] == [(10, 10), (11, 10)]
            assert (
                connection.exec_driver_sql(
                    "SELECT max(version) FROM schema_migrations"
                ).scalar_one()
                == 16
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM queue_scheduler_state"
                ).scalar_one()
                == 1
            )

            with pytest.raises(IntegrityError, match="UNIQUE"):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, strategy_bundle_id, loop_attempt_id,
                        parent_job_id, root_job_id, queue_class,
                        origin_queue_class, technical_attempt, batch_key,
                        status
                    ) VALUES (
                        12, 1, 3, 7, 10, 10, 'recovery',
                        'interactive', 1, 'loop:1', 'queued'
                    )
                """)
            with pytest.raises(
                IntegrityError,
                match="EvaluationJob retry chain is invalid",
            ):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, strategy_bundle_id, loop_attempt_id,
                        parent_job_id, root_job_id, queue_class,
                        origin_queue_class, technical_attempt, batch_key,
                        status
                    ) VALUES (
                        13, 1, 3, 7, 11, 10, 'recovery',
                        'production_batch', 2, 'loop:1', 'queued'
                    )
                """)
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, strategy_bundle_id, loop_attempt_id,
                    parent_job_id, root_job_id, queue_class,
                    origin_queue_class, technical_attempt, batch_key,
                    status
                ) VALUES (
                    13, 1, 3, 7, 11, 10, 'recovery',
                    'interactive', 2, 'loop:1', 'queued'
                )
            """)
            with pytest.raises(
                IntegrityError,
                match="EvaluationJob retry chain is invalid",
            ):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, strategy_bundle_id, loop_attempt_id,
                        parent_job_id, root_job_id, queue_class,
                        origin_queue_class, technical_attempt, batch_key,
                        status
                    ) VALUES (
                        14, 1, 3, 7, 13, 10, 'recovery',
                        'interactive', 3, 'loop:1', 'queued'
                    )
                """)
    finally:
        engine.dispose()


def test_migration_15_rejects_scope_update_and_empty_completed_attempt(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v15-loop-guards.db")
    _create_latest_and_run_migrations(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO assets (
                    id, original_name, stored_name, mime_type, size_bytes,
                    sha256, status, created_at
                ) VALUES (
                    1, 'guard.jpg', 'guard.jpg', 'image/jpeg', 1,
                    ?, 'uploaded', CURRENT_TIMESTAMP
                )
                """,
                ("f" * 64,),
            )
            connection.exec_driver_sql("""
                INSERT INTO strategy_bundles (
                    id, canonical_hash, model_id, model_config_snapshot,
                    prompt_a_version, rubric_version, engine_version,
                    created_at
                ) VALUES (
                    1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'model', '{}', 'A1', 'R1', 'E1', CURRENT_TIMESTAMP
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO loop_runs (
                    id, idempotency_key, request_fingerprint, asset_id,
                    strategy_bundle_id, status, current_round,
                    decision_json, created_by, created_at, updated_at
                ) VALUES (
                    1, 'migration15-loop',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    1, 1, 'waiting_result', 2, '{}', 'test',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO loop_attempts (
                    id, loop_run_id, business_round, kind,
                    target_dimensions_json, input_evidence_json,
                    conflict_json, status, technical_attempt, created_at
                ) VALUES (
                    1, 1, 2, 'targeted_recheck', '["lighting"]', '{}',
                    '[]', 'waiting_result', 0, CURRENT_TIMESTAMP
                )
            """)
            with pytest.raises(
                IntegrityError,
                match="LoopAttempt target contract is invalid",
            ):
                connection.exec_driver_sql("""
                    UPDATE loop_attempts
                    SET target_dimensions_json = '["all"]'
                    WHERE id = 1
                """)
            with pytest.raises(
                IntegrityError,
                match="Completed LoopAttempt contract is invalid",
            ):
                connection.exec_driver_sql("""
                    UPDATE loop_attempts
                    SET status = 'completed',
                        normalized_result_json = '{}',
                        result_idempotency_key = 'empty-result',
                        result_fingerprint =
                            'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                        completed_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """)
    finally:
        engine.dispose()


def test_complete_v15_database_replaces_root_index_and_hardens_payloads(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v15-to-v16-final-guards.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_jobs (
                    id INTEGER PRIMARY KEY,
                    asset_id INTEGER NOT NULL,
                    prompt_a_id INTEGER,
                    prompt_b_id INTEGER,
                    regression_item_id INTEGER,
                    strategy_bundle_id INTEGER,
                    loop_attempt_id INTEGER,
                    parent_job_id INTEGER,
                    root_job_id INTEGER,
                    queue_class VARCHAR(30) NOT NULL,
                    origin_queue_class VARCHAR(30) NOT NULL,
                    technical_attempt INTEGER NOT NULL,
                    batch_key VARCHAR(120),
                    status VARCHAR(30) NOT NULL,
                    CONSTRAINT uq_evaluation_jobs_regression_strategy
                        UNIQUE (
                            regression_item_id,
                            strategy_bundle_id
                        )
                )
            """)
            connection.exec_driver_sql("""
                CREATE UNIQUE INDEX
                    uq_evaluation_jobs_regression_strategy
                ON evaluation_jobs(
                    regression_item_id,
                    strategy_bundle_id
                )
                WHERE regression_item_id IS NOT NULL
                  AND strategy_bundle_id IS NOT NULL
            """)
            connection.exec_driver_sql("""
                CREATE TABLE loop_attempts (
                    id INTEGER PRIMARY KEY,
                    loop_run_id INTEGER NOT NULL,
                    business_round INTEGER NOT NULL,
                    kind VARCHAR(30) NOT NULL,
                    target_dimensions_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE job_refs (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL
                        REFERENCES evaluation_jobs(id)
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(
                MIGRATION_NAMES[:15], start=1
            ):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, prompt_a_id, prompt_b_id,
                    regression_item_id, strategy_bundle_id,
                    loop_attempt_id, parent_job_id, root_job_id,
                    queue_class, origin_queue_class,
                    technical_attempt, batch_key, status
                ) VALUES (
                    1, 10, 101, 102, 201, 301, 401, NULL, 1,
                    'validation', 'validation', 0, 'paired:1',
                    'retrying'
                )
            """)
            connection.exec_driver_sql(
                "INSERT INTO job_refs (id, job_id) VALUES (1, 1)"
            )

            run_migrations(connection)
            run_migrations(connection)
            assert (
                connection.exec_driver_sql(
                    "SELECT max(version) FROM schema_migrations"
                ).scalar_one()
                == 16
            )
            index_sql = connection.exec_driver_sql("""
                SELECT sql
                FROM sqlite_master
                WHERE type = 'index'
                  AND name =
                      'uq_evaluation_jobs_regression_strategy'
            """).scalar_one()
            assert "technical_attempt = 0" in index_sql
            assert (
                connection.exec_driver_sql(
                    "SELECT job_id FROM job_refs WHERE id = 1"
                ).scalar_one()
                == 1
            )
            assert (
                connection.exec_driver_sql(
                    "PRAGMA foreign_keys"
                ).scalar_one()
                == 1
            )
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, prompt_a_id, prompt_b_id,
                    regression_item_id, strategy_bundle_id,
                    loop_attempt_id, parent_job_id, root_job_id,
                    queue_class, origin_queue_class,
                    technical_attempt, batch_key, status
                ) VALUES (
                    2, 10, 101, 102, 201, 301, 401, 1, 1,
                    'recovery', 'validation', 1, 'paired:1',
                    'retrying'
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, prompt_a_id, prompt_b_id,
                    regression_item_id, strategy_bundle_id,
                    loop_attempt_id, parent_job_id, root_job_id,
                    queue_class, origin_queue_class,
                    technical_attempt, batch_key, status
                ) VALUES (
                    3, 10, 101, 102, 201, 301, 401, 2, 1,
                    'recovery', 'validation', 2, 'paired:1',
                    'queued'
                )
            """)
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, prompt_a_id, prompt_b_id,
                        regression_item_id, strategy_bundle_id,
                        loop_attempt_id, parent_job_id, root_job_id,
                        queue_class, origin_queue_class,
                        technical_attempt, batch_key, status
                    ) VALUES (
                        4, 10, 101, 102, 201, 301, 401,
                        NULL, 4, 'validation', 'validation', 0,
                        'paired:1', 'queued'
                    )
                """)
            with pytest.raises(
                IntegrityError,
                match="retry chain is invalid",
            ):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, prompt_a_id, prompt_b_id,
                        regression_item_id, strategy_bundle_id,
                        loop_attempt_id, parent_job_id, root_job_id,
                        queue_class, origin_queue_class,
                        technical_attempt, batch_key, status
                    ) VALUES (
                        5, 10, 101, 102, 201, 301, 401, 3, 1,
                        'recovery', 'validation', 3, 'paired:1',
                        'queued'
                    )
                """)
            connection.exec_driver_sql("""
                INSERT INTO evaluation_jobs (
                    id, asset_id, prompt_a_id, prompt_b_id,
                    regression_item_id, strategy_bundle_id,
                    loop_attempt_id, parent_job_id, root_job_id,
                    queue_class, origin_queue_class,
                    technical_attempt, batch_key, status
                ) VALUES (
                    10, 20, 111, 112, 211, 311, 411, NULL, 10,
                    'validation', 'validation', 0, 'paired:2',
                    'processing'
                )
            """)
            with pytest.raises(
                IntegrityError,
                match="retry chain is invalid",
            ):
                connection.exec_driver_sql("""
                    INSERT INTO evaluation_jobs (
                        id, asset_id, prompt_a_id, prompt_b_id,
                        regression_item_id, strategy_bundle_id,
                        loop_attempt_id, parent_job_id, root_job_id,
                        queue_class, origin_queue_class,
                        technical_attempt, batch_key, status
                    ) VALUES (
                        11, 999, 111, 112, 211, 311, 411, 10, 10,
                        'recovery', 'validation', 1, 'paired:2',
                        'queued'
                    )
                """)

            for invalid_target in (
                "{}",
                "[]",
                '["all"]',
            ):
                with pytest.raises(
                    IntegrityError,
                    match="target contract is invalid",
                ):
                    connection.exec_driver_sql(
                        """
                        INSERT INTO loop_attempts (
                            id, loop_run_id, business_round, kind,
                            target_dimensions_json
                        ) VALUES (?, 1, 2, 'targeted_recheck', ?)
                        """,
                        (100 + len(invalid_target), invalid_target),
                    )
            connection.exec_driver_sql("""
                INSERT INTO loop_attempts (
                    id, loop_run_id, business_round, kind,
                    target_dimensions_json
                ) VALUES (
                    200, 1, 2, 'targeted_recheck', '["lighting"]'
                )
            """)
            for invalid_target in ("{}", '["all"]'):
                with pytest.raises(
                    IntegrityError,
                    match="target contract is invalid",
                ):
                    connection.exec_driver_sql(
                        """
                        UPDATE loop_attempts
                        SET target_dimensions_json = ?
                        WHERE id = 200
                        """,
                        (invalid_target,),
                    )
    finally:
        engine.dispose()
