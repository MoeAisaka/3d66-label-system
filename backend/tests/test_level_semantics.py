"""ADR-0033 Task 2 tests: level-semantics-version scaffold (read-only, no flip).

Covers the pure ``level_semantics`` helpers and migration 54:
- both version constants exist and differ; v3 constant reuses the aggregator's.
- ``describe_level_semantics`` returns correct directions per version and
  fail-closed ``known=False`` for unknown versions (no raise).
- migration 54 is idempotent and table-guarded (missing table does not crash).
- adding ``level_semantics_version`` leaves authoritative fields intact.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

from app.category_evaluation_aggregator import LEVEL_SEMANTICS_VERSION
from app.database import Base
from app.level_semantics import (
    LEVEL_SEMANTICS_V1_L5_BEST,
    LEVEL_SEMANTICS_V3_L5_WORST,
    describe_level_semantics,
)
from app.migrations import run_migrations
from app.migrations.runner import (
    _migration_054_add_evaluation_result_level_semantics,
)


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #


def test_two_distinct_version_constants() -> None:
    assert LEVEL_SEMANTICS_V1_L5_BEST == "v1-l5-best"
    assert LEVEL_SEMANTICS_V3_L5_WORST == "doc-l5-worst-v1"
    assert LEVEL_SEMANTICS_V1_L5_BEST != LEVEL_SEMANTICS_V3_L5_WORST


def test_v3_constant_reuses_aggregator() -> None:
    # v3 must be the SAME value the aggregator already tags — imported, not
    # redefined, so the two can never drift.
    assert LEVEL_SEMANTICS_V3_L5_WORST == LEVEL_SEMANTICS_VERSION


# --------------------------------------------------------------------------- #
# describe_level_semantics
# --------------------------------------------------------------------------- #


def test_describe_v1_l5_best() -> None:
    desc = describe_level_semantics(LEVEL_SEMANTICS_V1_L5_BEST)
    assert desc["known"] is True
    assert desc["best_level"] == "L5"
    assert desc["worst_level"] == "L1"
    assert desc["levels"]["L5"] == "best"
    assert desc["levels"]["L1"] == "worst"


def test_describe_v3_l5_worst() -> None:
    desc = describe_level_semantics(LEVEL_SEMANTICS_V3_L5_WORST)
    assert desc["known"] is True
    assert desc["best_level"] == "L1"
    assert desc["worst_level"] == "L5"
    assert desc["levels"]["L1"] == "best"
    assert desc["levels"]["L5"] == "worst"


def test_describe_two_versions_are_opposite() -> None:
    v1 = describe_level_semantics(LEVEL_SEMANTICS_V1_L5_BEST)
    v3 = describe_level_semantics(LEVEL_SEMANTICS_V3_L5_WORST)
    # The whole point: the two are directionally reversed.
    assert v1["best_level"] == v3["worst_level"]
    assert v1["worst_level"] == v3["best_level"]


@pytest.mark.parametrize("bad", ["", "l5-best", "v2", "doc-l5-worst-v2", "unknown"])
def test_describe_unknown_version_is_fail_closed(bad: str) -> None:
    desc = describe_level_semantics(bad)
    assert desc["known"] is False
    assert desc["best_level"] is None
    assert desc["worst_level"] is None
    assert desc["levels"] == {}


# --------------------------------------------------------------------------- #
# migration 54 idempotency + table guard
# --------------------------------------------------------------------------- #


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def _columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def test_migration_54_missing_table_does_not_crash(connection: Connection) -> None:
    # No evaluation_results table at all → guard returns early, no exception.
    _migration_054_add_evaluation_result_level_semantics(connection)
    assert "evaluation_results" not in {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def test_migration_54_adds_column_idempotently(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE evaluation_results (id INTEGER PRIMARY KEY, level VARCHAR(10))"
    )
    _migration_054_add_evaluation_result_level_semantics(connection)
    assert "level_semantics_version" in _columns(connection, "evaluation_results")
    # Second run is a no-op (must not raise on duplicate ALTER).
    _migration_054_add_evaluation_result_level_semantics(connection)
    assert "level_semantics_version" in _columns(connection, "evaluation_results")


def test_full_migrations_install_column() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migrations(conn)
        cols = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(evaluation_results)")
        }
    engine.dispose()
    assert "level_semantics_version" in cols
    # The scaffold never touches the authoritative score/level columns.
    assert {"score", "level"}.issubset(cols)


def test_new_column_is_nullable_default_none() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migrations(conn)
        info = {
            row[1]: row
            for row in conn.exec_driver_sql("PRAGMA table_info(evaluation_results)")
        }
    engine.dispose()
    row = info["level_semantics_version"]
    # PRAGMA table_info: col[3] = notnull flag (0 → nullable), col[4] = default.
    assert row[3] == 0
    assert row[4] is None
