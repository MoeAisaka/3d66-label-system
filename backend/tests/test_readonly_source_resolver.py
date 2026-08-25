"""上游只读取数适配器解析器的回归。

背景：``/api/upstream-source-contracts/{id}/poll`` 此前直接返回 503
``SOURCE_ADAPTER_UNAVAILABLE``，而机制（合同、游标、分页、只读校验、读取运行
记录）都已完整，只差「从合同拿到真实连接」这一步。本文件锁定接上之后的行为。

和下游影子投影的解析器是同一形状、共用 ``external_datasources``，所以这里锁的
安全口径也一致：

1. **连接串与物理表名都不从合同里取。** 合同的 ``connection_locator`` 只是逻辑
   引用，真实 DSN 与表名配在部署侧。用**不同的物理列名**建表，以此证明字段映射
   真的在起作用（而不是碰巧同名）。
2. **只读是数据库层强制的。** SQLite 的 ``PRAGMA query_only`` 会被真正打开，
   所以适配器的只读探测拿到的是真实 True，写操作会被数据库拒绝。
3. **schema 指纹从活库实算。** 传合同登记值会让漂移门禁自证同一、形同废除。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.external_datasources import (
    ExternalDatasourceError,
    compute_live_schema_fingerprint,
    readonly_connection_factory,
    reset_engine_cache,
    resolve_locator_table,
)
from app.models import (
    Asset,
    EvaluationCategoryProfile,
    UpstreamSourceContract,
)
from app.readonly_sources import (
    ReadOnlySourceError,
    SourceCursor,
    create_upstream_source_contract,
    poll_upstream_source,
    resolve_configured_readonly_source_adapter,
)

#: 合同里存的逻辑引用（不是 DSN）。
_LOCATOR = "source-registry:fixture-3d"
_DSN_ENV = "LABEL_SYSTEM_DATASOURCE_SOURCE_REGISTRY_FIXTURE_3D"
_TABLE_ENV = _DSN_ENV + "_TABLE"
_TABLE = "dw_content_events"

#: 物理列名故意与逻辑字段名**都不一样**，用来证明字段映射真的生效。
_FIELD_MAPPINGS = {
    "content_id": "ll_content_id",
    "source_version": "ver",
    "category_key": "cat",
    "occurred_at": "happened_at",
    "asset_id": "material_id",
}

_SOURCE_DDL = f"""
CREATE TABLE {_TABLE} (
    ll_content_id TEXT NOT NULL,
    ver TEXT NOT NULL,
    cat TEXT NOT NULL,
    happened_at TEXT NOT NULL,
    material_id INTEGER
)
"""

_ROWS = [
    ("ll-100001", "v1", "space_image", "2026-08-24T10:00:00+00:00", 9001),
    ("ll-100002", "v1", "space_image", "2026-08-24T10:05:00+00:00", 9002),
    ("ll-100003", "v1", "space_image", "2026-08-24T10:10:00+00:00", None),
]


@pytest.fixture(autouse=True)
def _clean_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


@pytest.fixture
def upstream(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """建一个真实的上游库并配好部署侧变量，返回**逻辑引用**。"""
    path = tmp_path / "upstream.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(_SOURCE_DDL))
        for row in _ROWS:
            connection.execute(
                text(
                    f"INSERT INTO {_TABLE}"
                    " (ll_content_id, ver, cat, happened_at, material_id)"
                    " VALUES (:c, :v, :k, :t, :a)"
                ),
                {"c": row[0], "v": row[1], "k": row[2], "t": row[3], "a": row[4]},
            )
    engine.dispose()
    reset_engine_cache()
    monkeypatch.setenv(_DSN_ENV, f"sqlite:///{path}")
    monkeypatch.setenv(_TABLE_ENV, _TABLE)
    return _LOCATOR


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def local_prerequisites(db: Session) -> None:
    """建出 poll 落库所需的两项本地前提。

    ``poll_upstream_source`` 对每一行都会调 ``ingest_content_event``，那里有两道
    要求，缺任一整批都会被判 ``SOURCE_READ_FAILED``：

    1. **类目必须已启用**——需要一条 ``status="active"`` 的
       ``EvaluationCategoryProfile``。上游事件要落到已启用的类目上，否则后续的
       评测路由无从挂靠。
    2. **``asset_id`` 必须能反查到本地素材**——非空 asset_id 查不到就拒绝整批。
    """
    db.add(
        EvaluationCategoryProfile(
            category_key="space_image",
            display_name="空间图",
            status="active",
        )
    )
    db.add_all(
        Asset(
            id=asset_id,
            original_name=f"upstream-{asset_id}.png",
            stored_name=f"upstream-{asset_id}.png",
            mime_type="image/png",
            size_bytes=256,
            sha256=f"{asset_id:064d}",
            category_key="space_image",
        )
        for asset_id in (9001, 9002)
    )
    db.flush()


def _live_fingerprint(locator: str) -> str:
    return compute_live_schema_fingerprint(locator, None, table_name=_TABLE)


def _contract(
    db: Session,
    locator: str,
    *,
    fingerprint: str,
    adapter_key: str = "sql",
    read_only: bool = True,
    status: str = "active",
    field_mappings: dict[str, str] | None = None,
) -> UpstreamSourceContract:
    return create_upstream_source_contract(
        db,
        contract_key="dw-content-events",
        adapter_key=adapter_key,
        source_system="fixture-3d",
        category_key="space_image",
        connection_locator=locator,
        secret_reference="secret-ref:fixture-3d-readonly",
        field_mappings=field_mappings or dict(_FIELD_MAPPINGS),
        cursor_definition={"fields": ["content_id", "source_version"]},
        page_size=100,
        read_only=read_only,
        schema_fingerprint=fingerprint,
        owner="tpeng-3d",
        status=status,
        created_by="admin",
    )


# --- 1. 真的能读到数据 -----------------------------------------------------


def test_resolved_adapter_reads_rows_through_field_mappings(
    db: Session, upstream: str
) -> None:
    """取数走通，且物理列名经映射还原成逻辑字段。

    物理列叫 ``ll_content_id``/``ver``/``cat``/``happened_at``/``material_id``，
    逻辑字段是 ``content_id`` 等——能对上就说明映射真的生效了。
    """
    adapter = resolve_configured_readonly_source_adapter(
        _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    )
    page = adapter.fetch_page(cursor=None, limit=10)

    assert [row.content_id for row in page.rows] == [
        "ll-100001",
        "ll-100002",
        "ll-100003",
    ]
    first = page.rows[0]
    assert first.source_version == "v1"
    assert first.category_key == "space_image"
    assert first.asset_id == 9001
    assert first.occurred_at.isoformat() == "2026-08-24T10:00:00+00:00"
    # 空值列要如实还原成 None，不能变成 0。
    assert page.rows[2].asset_id is None


def test_cursor_pagination_advances_without_gaps_or_repeats(
    db: Session, upstream: str
) -> None:
    adapter = resolve_configured_readonly_source_adapter(
        _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    )
    first = adapter.fetch_page(cursor=None, limit=2)
    assert [row.content_id for row in first.rows] == ["ll-100001", "ll-100002"]

    second = adapter.fetch_page(cursor=first.next_cursor, limit=2)
    assert [row.content_id for row in second.rows] == ["ll-100003"]

    third = adapter.fetch_page(cursor=second.next_cursor, limit=2)
    assert third.rows == ()


# --- 2. 只读是数据库层强制的 -----------------------------------------------


def test_read_only_evidence_is_genuinely_true(db: Session, upstream: str) -> None:
    """只读证据必须是真实探测出来的。

    连接层会打开 SQLite 的 ``PRAGMA query_only``，所以适配器的探测拿到真 True。
    若只靠「退出时回滚」而不在数据库层设只读，这里会是 False，poll 会被门禁拦住。
    """
    adapter = resolve_configured_readonly_source_adapter(
        _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    )
    assert adapter.verify_read_only().read_only is True


def test_write_through_readonly_connection_is_rejected(upstream: str) -> None:
    """经只读连接写入必须被**数据库**拒绝，而不是先写成功再回滚。"""
    factory = readonly_connection_factory(upstream, None)
    with pytest.raises(Exception) as excinfo:
        with factory() as connection:
            connection.execute(
                text(f"INSERT INTO {_TABLE} (ll_content_id, ver, cat, happened_at)"
                     " VALUES ('x', 'v', 'k', '2026-01-01T00:00:00+00:00')")
            )
    assert "readonly" in str(excinfo.value).lower() or "read-only" in str(
        excinfo.value
    ).lower()


def test_readonly_connection_leaves_no_trace(upstream: str) -> None:
    """只读连接用完后，上游数据行数不变。"""
    factory = readonly_connection_factory(upstream, None)
    with factory() as connection:
        before = connection.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar()
    reset_engine_cache()
    verifier = create_engine(os.environ[_DSN_ENV])
    with verifier.connect() as connection:
        after = connection.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar()
    verifier.dispose()
    assert before == after == len(_ROWS)


# --- 3. 指纹从活库实算 -----------------------------------------------------


def test_fingerprint_comes_from_live_schema_not_contract(
    db: Session, upstream: str
) -> None:
    """合同登记一个错误指纹，适配器给出的必须是活库真值。

    否则 poll 的漂移比对会永远相等、彻底失效。
    """
    wrong = "0" * 64
    adapter = resolve_configured_readonly_source_adapter(
        _contract(db, upstream, fingerprint=wrong)
    )
    page = adapter.fetch_page(cursor=None, limit=1)
    assert page.schema_fingerprint == _live_fingerprint(upstream)
    assert page.schema_fingerprint != wrong


def test_poll_detects_schema_drift(db: Session, upstream: str) -> None:
    """登记后上游表结构变化，poll 必须报漂移而不是照常取数。"""
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    engine = create_engine(os.environ[_DSN_ENV])
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {_TABLE} ADD COLUMN extra TEXT"))
    engine.dispose()
    reset_engine_cache()

    adapter = resolve_configured_readonly_source_adapter(contract)
    # poll 不抛异常，而是把这次运行标成 blocked 并记下错误码——运维要能在运行
    # 记录里看到「为什么没取数」，而不是只收到一个 500。
    run = poll_upstream_source(
        db, contract=contract, adapter=adapter, limit=10, actor="tester"
    )
    assert run.status == "blocked"
    assert run.error_code == "SOURCE_SCHEMA_DRIFT"


# --- 4. poll 端到端 --------------------------------------------------------


def test_poll_records_run_and_returns_rows(
    db: Session, upstream: str, local_prerequisites: None
) -> None:
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    adapter = resolve_configured_readonly_source_adapter(contract)
    run = poll_upstream_source(
        db, contract=contract, adapter=adapter, limit=10, actor="tester"
    )
    assert run.row_count == len(_ROWS)
    assert run.source_contract_id == contract.id
    assert run.status == "succeeded"


def test_poll_resumes_from_cursor(
    db: Session, upstream: str, local_prerequisites: None
) -> None:
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    adapter = resolve_configured_readonly_source_adapter(contract)
    run = poll_upstream_source(
        db,
        contract=contract,
        adapter=adapter,
        limit=10,
        actor="tester",
        cursor=SourceCursor({"content_id": "ll-100001", "source_version": "v1"}),
    )
    assert run.row_count == len(_ROWS) - 1


# --- 5. 拒绝不该解析的合同 -------------------------------------------------


def test_non_read_only_contract_is_rejected(upstream: str) -> None:
    """未声明只读的合同必须被拒。

    用未落库对象来测——建合同时 ``read_only=False`` 可能被数据库约束挡住，
    而解析器本身也必须复核，不盲信传进来的合同。
    """
    contract = UpstreamSourceContract(
        contract_key="not-read-only",
        adapter_key="sql",
        source_system="fixture-3d",
        category_key="space_image",
        connection_locator=upstream,
        secret_reference="secret-ref:x",
        field_mappings_json="{}",
        cursor_definition_json="{}",
        page_size=100,
        read_only=False,
        schema_fingerprint="0" * 64,
        owner="tpeng-3d",
        status="active",
        created_by="admin",
    )
    with pytest.raises(ReadOnlySourceError) as excinfo:
        resolve_configured_readonly_source_adapter(contract)
    assert excinfo.value.code == "SOURCE_CONTRACT_NOT_READ_ONLY"


def test_non_sql_adapter_key_is_rejected(db: Session, upstream: str) -> None:
    contract = _contract(
        db,
        upstream,
        fingerprint=_live_fingerprint(upstream),
        adapter_key="fixture-readonly",
    )
    with pytest.raises(ReadOnlySourceError) as excinfo:
        resolve_configured_readonly_source_adapter(contract)
    assert excinfo.value.code == "SOURCE_ADAPTER_UNAVAILABLE"


def test_unconfigured_table_fails_closed(
    db: Session, upstream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部署侧没配物理表名时必须报错并指明该设哪个变量。"""
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    monkeypatch.delenv(_TABLE_ENV, raising=False)
    with pytest.raises(ReadOnlySourceError) as excinfo:
        resolve_configured_readonly_source_adapter(contract)
    assert excinfo.value.code == "SOURCE_ADAPTER_UNAVAILABLE"
    assert _TABLE_ENV in str(excinfo.value)


def test_unconfigured_dsn_fails_closed(
    db: Session, upstream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    monkeypatch.delenv(_DSN_ENV, raising=False)
    with pytest.raises(ReadOnlySourceError) as excinfo:
        resolve_configured_readonly_source_adapter(contract)
    assert _DSN_ENV in str(excinfo.value)


def test_missing_table_in_live_db_is_rejected(
    db: Session, upstream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配的表在上游库里不存在时必须拒绝，而不是解析出必然取数失败的适配器。"""
    contract = _contract(db, upstream, fingerprint=_live_fingerprint(upstream))
    monkeypatch.setenv(_TABLE_ENV, "no_such_table")
    with pytest.raises(ReadOnlySourceError) as excinfo:
        resolve_configured_readonly_source_adapter(contract)
    assert excinfo.value.code == "SOURCE_ADAPTER_UNAVAILABLE"


def test_locator_table_resolution_rejects_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部署侧配了非法表名也要拒——表名会拼进 SQL。"""
    monkeypatch.setenv(_TABLE_ENV, 'x"; DROP TABLE y;--')
    with pytest.raises(ExternalDatasourceError) as excinfo:
        resolve_locator_table(_LOCATOR)
    assert excinfo.value.code == "DATASOURCE_TABLE_INVALID"
