from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    f"sqlite:///{settings.database_path.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 10},
    pool_pre_ping=True,
)


def _sqlite_version_tuple() -> tuple[int, int, int]:
    parts = sqlite3.sqlite_version.split(".")[:3]
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # 2026 年 SQLite 官方修复了 WAL reset 竞态。旧运行时使用默认日志模式更稳妥。
    if _sqlite_version_tuple() >= (3, 51, 3):
        cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_database() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        migration_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(migration_items)")
        }
        if "sample_expected_level" not in migration_columns:
            connection.exec_driver_sql(
                "ALTER TABLE migration_items ADD COLUMN sample_expected_level VARCHAR(10)"
            )
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
        if "updated_at" not in job_columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_jobs ADD COLUMN updated_at DATETIME"
            )
            connection.exec_driver_sql(
                "UPDATE evaluation_jobs SET updated_at = created_at WHERE updated_at IS NULL"
            )
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
        model_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(model_configs)")
        }
        if "high_risk_review_enabled" not in model_columns:
            connection.exec_driver_sql(
                "ALTER TABLE model_configs ADD COLUMN high_risk_review_enabled BOOLEAN NOT NULL DEFAULT 1"
            )
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
        if "updated_at" not in result_columns:
            connection.exec_driver_sql(
                "ALTER TABLE evaluation_results ADD COLUMN updated_at DATETIME"
            )
            connection.exec_driver_sql(
                "UPDATE evaluation_results SET updated_at = created_at WHERE updated_at IS NULL"
            )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
