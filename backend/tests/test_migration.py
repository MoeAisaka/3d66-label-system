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
    "add_canary_run_persistence",
    "add_prompt_optimizer_stage_audit",
    "add_staged_human_review_and_candidate_gate",
    "add_material_packages_and_review_panels",
    "add_prompt_metric_snapshots",
    "add_phase_b_automation_feedback_benchmarks",
    "enforce_material_package_immutability",
    "add_real_executor_safety",
    "add_baseline_regression_and_repair_prompt_fk",
    "add_dimension_schemas",
    "bind_dimension_contract_to_strategy",
    "add_dimension_route_policies",
    "add_routed_strategy_bundles",
    "add_dimension_calibration_results",
    "add_evaluation_category_profiles",
    "repair_evaluation_category_profiles",
    "add_evaluation_preprocess_snapshot",
    "freeze_job_category_profile",
    "generalize_model_names_and_pdf_summary",
    "add_category_automation_isolation",
    "add_asset_category_channel",
    "add_material_package_status",
    "add_accounts_and_model_registry",
    "modular_category_pipelines",
    "unified_label_platform_contract",
    "add_automation_worker_status",
    "add_evaluation_packages",
    "add_evaluation_production_runs",
]


def result(level: str, category: str, confidence: float = 0.9) -> dict:
    return {
        "level": level,
        "score": 82.0,
        "confidence": confidence,
        "needs_review": False,
        "precheck": {"classification": {"primary_category": category}},
    }


def test_v40_removes_fixed_category_check_and_preserves_profiles(tmp_path) -> None:
    engine = _engine(tmp_path, "v39-category-profile.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE prompt_versions (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE model_configs (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE optimizer_configs (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_category_profiles (
                    id INTEGER PRIMARY KEY,
                    category_key VARCHAR(40) NOT NULL UNIQUE,
                    display_name VARCHAR(120) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    allowed_mime_types_json TEXT NOT NULL DEFAULT '[]',
                    preprocess_config_json TEXT NOT NULL DEFAULT '{}',
                    rubric_version VARCHAR(40) NOT NULL DEFAULT 'rubric-v2.1',
                    created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (category_key IN ('space_image','pdf_text','material_image')),
                    CHECK (status IN ('draft','active','retired'))
                )
            """)
            connection.exec_driver_sql(
                "INSERT INTO evaluation_category_profiles "
                "(id, category_key, display_name, allowed_mime_types_json, preprocess_config_json) "
                "VALUES (1, 'space_image', '空间图片', '[\"image/jpeg\"]', '{\"preprocess\":\"image\"}')"
            )
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:39], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, name),
                )
            run_migrations(connection)
            table_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='evaluation_category_profiles'"
            ).scalar_one()
            assert "category_key IN ('space_image','pdf_text','material_image')" not in table_sql
            pipeline = json.loads(connection.exec_driver_sql(
                "SELECT pipeline_config_json FROM evaluation_category_profiles WHERE id=1"
            ).scalar_one())
            assert pipeline["schema_version"] == "category-pipeline-v1"
            connection.exec_driver_sql(
                "INSERT INTO evaluation_category_profiles "
                "(category_key, display_name, status, allowed_mime_types_json, "
                "preprocess_config_json, pipeline_config_json) "
                "VALUES ('landscape_image', '景观效果图', 'draft', '[\"image/jpeg\"]', '{}', '{}')"
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


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
        assert [row[0] for row in rows] == list(
            range(1, len(MIGRATION_NAMES) + 1)
        )
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
        assert [row[0] for row in versions] == list(
            range(1, len(MIGRATION_NAMES) + 1)
        )
    finally:
        engine.dispose()


def test_migration_35_only_rewrites_legacy_default_names_and_pdf_contract(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "migration-35.db")
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO model_configs (id, name, provider, base_url, api_path, "
                "model_id, temperature, max_tokens, timeout_seconds, max_retries, "
                "max_concurrency, structured_output, high_risk_review_enabled, active, "
                "updated_at) "
                "VALUES (1, '豆包主模型', 'doubao', 'https://example.test/v1', "
                "'/chat/completions', 'legacy', 0.1, 4096, 120, 1, 2, 1, 1, 1, "
                "CURRENT_TIMESTAMP), "
                "(2, '用户自定义豆包实验', 'custom', 'https://example.test/v1', "
                "'/chat/completions', 'custom', 0.1, 4096, 120, 1, 2, 1, 1, 1, "
                "CURRENT_TIMESTAMP)"
            )
            connection.exec_driver_sql(
                "INSERT INTO optimizer_configs (id, name, provider, base_url, api_path, "
                "model_id, temperature, max_tokens, timeout_seconds, max_retries, "
                "structured_output, updated_at) VALUES "
                "(1, 'SOL 提示词诊断模型', 'openai', 'https://example.test/v1', "
                "'/chat/completions', 'legacy-sol', 0.1, 4096, 120, 1, 1, "
                "CURRENT_TIMESTAMP)"
            )
            connection.exec_driver_sql(
                "INSERT INTO evaluation_category_profiles "
                "(id, category_key, display_name, status, allowed_mime_types_json, "
                "preprocess_config_json, rubric_version, created_by, created_at, "
                "updated_at) VALUES "
                "(1, 'pdf_text', 'PDF 方案文本', 'active', '[\"application/pdf\"]', "
                "'{\"preprocess\":\"pdf\",\"max_pages\":8}', "
                "'rubric-v2.1', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            run_migrations(connection)
            names = connection.exec_driver_sql(
                "SELECT id, name FROM model_configs ORDER BY id"
            ).fetchall()
            assert names == [(1, "主评测模型"), (2, "用户自定义豆包实验")]
            assert connection.exec_driver_sql(
                "SELECT name FROM optimizer_configs WHERE id = 1"
            ).scalar_one() == "提示词诊断模型"
            pdf_config = json.loads(
                connection.exec_driver_sql(
                    "SELECT preprocess_config_json "
                    "FROM evaluation_category_profiles WHERE id = 1"
                ).scalar_one()
            )
            assert pdf_config == {
                "max_pages": 8,
                "multimodal_summary": True,
                "preprocess": "pdf",
            }
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
            assert [row[0] for row in versions] == list(
                range(1, len(MIGRATION_NAMES) + 1)
            )
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
                == len(MIGRATION_NAMES)
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
            assert versions == list(range(1, len(MIGRATION_NAMES) + 1))

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
            assert versions == list(range(1, len(MIGRATION_NAMES) + 1))
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
                    == len(MIGRATION_NAMES)
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
                == len(MIGRATION_NAMES)
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
                == len(MIGRATION_NAMES)
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


def test_complete_v17_database_adds_optimizer_stage_audit(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v17-to-v18-optimizer-audit.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE prompt_optimization_runs (
                    id INTEGER PRIMARY KEY,
                    base_prompt_id INTEGER NOT NULL,
                    sample_set_id INTEGER NOT NULL,
                    optimizer_model_id VARCHAR(200) NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'queued',
                    progress INTEGER NOT NULL DEFAULT 0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    corrected_count INTEGER NOT NULL DEFAULT 0,
                    diagnosis_json TEXT NOT NULL DEFAULT '{}',
                    candidate_system_prompt TEXT NOT NULL DEFAULT '',
                    candidate_user_prompt TEXT NOT NULL DEFAULT '',
                    change_note TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_by VARCHAR(80) NOT NULL DEFAULT 'system',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME
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
                MIGRATION_NAMES[:17], start=1
            ):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql("""
                INSERT INTO prompt_optimization_runs (
                    id, base_prompt_id, sample_set_id, optimizer_model_id,
                    status, diagnosis_json
                ) VALUES (1, 11, 22, 'optimizer-v1', 'completed', '{}')
            """)

            run_migrations(connection)
            run_migrations(connection)

            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(prompt_optimization_runs)"
                )
            }
            assert {
                "diagnostic_audit_json",
                "synthesis_audit_json",
            }.issubset(columns)
            row = connection.exec_driver_sql("""
                SELECT diagnostic_audit_json, synthesis_audit_json
                FROM prompt_optimization_runs
                WHERE id = 1
            """).one()
            allowed_fields = {
                "status",
                "attempt_count",
                "upstream_status_code",
                "request_correlation_id",
                "elapsed_ms",
                "error_type",
                "error_message",
                "output_budget",
                "reasoning_effort",
            }
            for raw_audit in row:
                audit = json.loads(raw_audit)
                assert set(audit) == allowed_fields
                assert audit["status"] == "not_recorded"
                assert audit["attempt_count"] == 0
            versions = list(
                connection.exec_driver_sql(
                    "SELECT version, name FROM schema_migrations "
                    "ORDER BY version"
                )
            )
            assert [item[0] for item in versions] == list(
                range(1, len(MIGRATION_NAMES) + 1)
            )
            assert [item[1] for item in versions] == MIGRATION_NAMES
    finally:
        engine.dispose()


def test_v19_backfills_staged_review_state_and_keeps_history_append_only(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v18-to-v19-staged-review.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE evaluation_results (
                    id INTEGER PRIMARY KEY,
                    needs_review BOOLEAN NOT NULL DEFAULT 0
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE human_reviews (
                    id INTEGER PRIMARY KEY,
                    evaluation_id INTEGER NOT NULL,
                    reviewer_name VARCHAR(80) NOT NULL,
                    decision VARCHAR(30) NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE prompt_versions (
                    id INTEGER PRIMARY KEY,
                    version VARCHAR(40) NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:18], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql(
                "INSERT INTO evaluation_results(id) VALUES (1), (2)"
            )
            connection.exec_driver_sql("""
                INSERT INTO human_reviews(
                    id, evaluation_id, reviewer_name, decision
                ) VALUES
                    (1, 1, 'legacy', 'approved'),
                    (2, 1, 'legacy', 'corrected'),
                    (3, 2, 'legacy', 'rejected')
            """)
            run_migrations(connection)
            states = list(
                connection.exec_driver_sql("""
                    SELECT id, review_stage, review_revision
                    FROM evaluation_results
                    ORDER BY id
                """)
            )
            assert [tuple(row) for row in states] == [
                (1, "completed", 2),
                (2, "initial", 1),
            ]
            review_stages = list(
                connection.exec_driver_sql(
                    "SELECT stage FROM human_reviews ORDER BY id"
                )
            )
            assert [row[0] for row in review_stages] == [
                "initial",
                "initial",
                "initial",
            ]
            prompt_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(prompt_versions)"
                )
            }
            assert "source_optimization_run_id" in prompt_columns
            with pytest.raises(IntegrityError, match="append-only"):
                connection.exec_driver_sql(
                    "UPDATE human_reviews SET decision = 'approved' WHERE id = 3"
                )
    finally:
        engine.dispose()


def test_v23_real_executor_migration_preserves_rows_and_adds_safe_defaults(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "v23-to-v24-real-executors.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE model_configs (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE optimizer_configs (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE prompt_versions (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE evaluation_results (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE human_reviews (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE production_feedback_events (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("""
                CREATE TABLE automation_optimization_runs (
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
                    CHECK(estimated_cost_micros >= 0 AND actual_cost_micros >= 0)
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE optimization_case_queue (
                    id INTEGER PRIMARY KEY,
                    idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                    evaluation_id INTEGER REFERENCES evaluation_results(id),
                    final_review_id INTEGER REFERENCES human_reviews(id),
                    source_type VARCHAR(30) NOT NULL DEFAULT 'human_review',
                    source_event_id INTEGER UNIQUE REFERENCES production_feedback_events(id),
                    prompt_version VARCHAR(40) NOT NULL,
                    severity VARCHAR(10) NOT NULL DEFAULT 'P2'
                        CHECK(severity IN ('P0','P1','P2','P3')),
                    case_json TEXT NOT NULL,
                    status VARCHAR(30) NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','batched','processing','completed','failed')),
                    lease_owner VARCHAR(120),
                    lease_token VARCHAR(80),
                    lease_expires_at DATETIME,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at DATETIME,
                    last_error TEXT NOT NULL DEFAULT '',
                    automation_run_id INTEGER REFERENCES automation_optimization_runs(id),
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
            connection.exec_driver_sql("""
                CREATE TABLE model_benchmark_experiments (
                    id INTEGER PRIMARY KEY,
                    experiment_key VARCHAR(160) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft','running','completed','failed','cancelled')),
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
                CREATE TABLE model_benchmark_variants (
                    id INTEGER PRIMARY KEY,
                    experiment_id INTEGER NOT NULL REFERENCES model_benchmark_experiments(id),
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
            connection.exec_driver_sql("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for version, name in enumerate(MIGRATION_NAMES[:23], start=1):
                connection.exec_driver_sql(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, name),
                )
            connection.exec_driver_sql(
                "INSERT INTO users(id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO model_configs(id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO optimizer_configs(id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO prompt_versions(id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO evaluation_results(id) VALUES (1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO human_reviews(id) VALUES (1)"
            )
            connection.exec_driver_sql("""
                INSERT INTO automation_optimization_runs (
                    id, run_key, base_prompt_version, policy_revision, status,
                    trigger_reason, case_ids_json, frozen_input_json
                ) VALUES (1, 'legacy-run', 'B1', 2, 'awaiting_executor',
                          'case_threshold', '[1]', '{}')
            """)
            connection.exec_driver_sql("""
                INSERT INTO optimization_case_queue (
                    id, idempotency_key, evaluation_id, final_review_id,
                    source_type, prompt_version, case_json, status,
                    automation_run_id
                ) VALUES (1, 'legacy-case', 1, 1, 'human_review',
                          'B1', '{}', 'batched', 1)
            """)
            connection.exec_driver_sql("""
                INSERT INTO model_benchmark_experiments (
                    id, experiment_key, name, execution_mode, cohort_hash,
                    snapshot_hash, frozen_snapshot_json, quality_gate_json
                ) VALUES (1, 'legacy-benchmark', 'legacy', 'test',
                          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                          'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                          '{}', '{}')
            """)
            connection.exec_driver_sql("""
                INSERT INTO model_benchmark_variants (
                    id, experiment_id, model_key, provider, model_id, pricing_json
                ) VALUES (1, 1, 'sol', 'test', 'legacy-model', '{}')
            """)

            run_migrations(connection)
            run_migrations(connection)

            assert connection.exec_driver_sql(
                "SELECT run_key, status, input_tokens, retryable "
                "FROM automation_optimization_runs WHERE id=1"
            ).one() == ("legacy-run", "awaiting_executor", None, 0)
            assert connection.exec_driver_sql(
                "SELECT idempotency_key, status, automation_run_id "
                "FROM optimization_case_queue WHERE id=1"
            ).one() == ("legacy-case", "batched", 1)
            assert connection.exec_driver_sql(
                "SELECT experiment_key, execution_mode, max_round_cost_micros "
                "FROM model_benchmark_experiments WHERE id=1"
            ).one() == ("legacy-benchmark", "test", 0)
            assert connection.exec_driver_sql(
                "SELECT model_id, model_config_id, actual_cost_micros "
                "FROM model_benchmark_variants WHERE id=1"
            ).one() == ("legacy-model", None, 0)
            assert connection.exec_driver_sql(
                "SELECT is_admin FROM users WHERE id=1"
            ).scalar_one() == 1
            assert connection.exec_driver_sql(
                "SELECT input_micros_per_million_tokens, benchmark_enabled "
                "FROM model_configs WHERE id=1"
            ).one() == (0, 0)
            assert connection.exec_driver_sql(
                "SELECT input_micros_per_million_tokens "
                "FROM optimizer_configs WHERE id=1"
            ).scalar_one() == 0
            connection.exec_driver_sql("""
                INSERT INTO model_benchmark_experiments (
                    id, experiment_key, name, execution_mode, cohort_hash,
                    snapshot_hash, frozen_snapshot_json, quality_gate_json
                ) VALUES (2, 'real-benchmark', 'real', 'real',
                          'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                          'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                          '{}', '{}')
            """)
            connection.exec_driver_sql("""
                INSERT INTO automation_optimization_runs (
                    id, run_key, base_prompt_version, policy_revision, status,
                    trigger_reason, case_ids_json, frozen_input_json
                ) VALUES (2, 'processing-run', 'B1', 2, 'processing',
                          'case_threshold', '[]', '{}')
            """)
            with pytest.raises(IntegrityError, match="immutable"):
                connection.exec_driver_sql(
                    "UPDATE model_benchmark_experiments "
                    "SET max_round_cost_micros=999 WHERE id=2"
                )
            assert connection.exec_driver_sql(
                "SELECT max(version) FROM schema_migrations"
            ).scalar_one() == len(MIGRATION_NAMES)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE material_packages SET name = 'changed' WHERE id = 1",
            "MaterialPackage is immutable",
        ),
        (
            "DELETE FROM material_packages WHERE id = 1",
            "MaterialPackage cannot be deleted",
        ),
        (
            "UPDATE material_package_items "
            "SET original_name = 'changed.jpg' WHERE id = 1",
            "MaterialPackageItem is immutable",
        ),
        (
            "DELETE FROM material_package_items WHERE id = 1",
            "MaterialPackageItem cannot be deleted",
        ),
    ],
    ids=[
        "package-update",
        "package-delete",
        "package-item-update",
        "package-item-delete",
    ],
)
def test_material_package_database_guards_reject_mutation(
    tmp_path,
    statement: str,
    message: str,
) -> None:
    engine = _engine(tmp_path, f"{message.split()[0]}-{statement.split()[0]}.db")
    _create_latest_and_run_migrations(engine)
    try:
        with Session(engine) as db:
            asset = models.Asset(
                original_name="source.jpg",
                stored_name="source.jpg",
                mime_type="image/jpeg",
                size_bytes=10,
                sha256="f" * 64,
            )
            package = models.MaterialPackage(
                id=1,
                package_key="immutable-package",
                name="不可变素材包",
                source="manual_upload",
                created_by="test",
            )
            db.add_all([asset, package])
            db.flush()
            db.add(
                models.MaterialPackageItem(
                    id=1,
                    package_id=package.id,
                    asset_id=asset.id,
                    original_name=asset.original_name,
                    duplicate=False,
                    position=1,
                )
            )
            db.commit()

        with pytest.raises(IntegrityError, match=message):
            with engine.begin() as connection:
                connection.exec_driver_sql(statement)

        with engine.connect() as connection:
            package_row = connection.exec_driver_sql(
                "SELECT name FROM material_packages WHERE id = 1"
            ).one()
            item_row = connection.exec_driver_sql(
                "SELECT original_name FROM material_package_items WHERE id = 1"
            ).one()
            assert package_row[0] == "不可变素材包"
            assert item_row[0] == "source.jpg"
    finally:
        engine.dispose()


def test_migration_26_repairs_dangling_prompt_fk_and_allows_real_insert(
    tmp_path,
) -> None:
    engine = _engine(tmp_path, "migration-26-prompt-fk.db")
    _create_latest_and_run_migrations(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM schema_migrations WHERE version = 25"
            )
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.exec_driver_sql("""
                UPDATE sqlite_master
                SET sql = replace(
                    sql,
                    'automation_optimization_runs',
                    'automation_optimization_runs_v24'
                )
                WHERE type='table' AND name='prompt_versions'
            """)
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
            version = connection.exec_driver_sql(
                "PRAGMA schema_version"
            ).scalar_one()
            connection.exec_driver_sql(f"PRAGMA schema_version={version + 1}")
            assert any(
                row[2] == "automation_optimization_runs_v24"
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(prompt_versions)"
                )
            )
            run_migrations(connection)
            assert connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []
            assert any(
                row[2] == "automation_optimization_runs"
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(prompt_versions)"
                )
            )
            connection.exec_driver_sql("""
                INSERT INTO automation_optimization_runs (
                    id, run_key, base_prompt_version, policy_revision,
                    status, dry_run, trigger_reason, case_ids_json,
                    frozen_input_json, result_json, candidate_count,
                    estimated_cost_micros, actual_cost_micros, retryable,
                    error_message, created_by, created_at
                ) VALUES (
                    9001, 'm26-smoke', 'B1', 1, 'planned', 1, 'test',
                    '[]', '{}', '{}', 0, 0, 0, 0, '', 'test',
                    CURRENT_TIMESTAMP
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO prompt_versions (
                    id, stage, name, version, system_prompt, user_prompt,
                    source_automation_run_id
                ) VALUES (9001, 'B', 'smoke', 'm26-smoke', 's', 'u', 9001)
            """)
            assert connection.exec_driver_sql(
                "SELECT source_automation_run_id FROM prompt_versions "
                "WHERE id=9001"
            ).scalar_one() == 9001
            connection.exec_driver_sql("DELETE FROM prompt_versions WHERE id=9001")
            connection.exec_driver_sql(
                "DELETE FROM automation_optimization_runs WHERE id=9001"
            )
            assert connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []
    finally:
        engine.dispose()
