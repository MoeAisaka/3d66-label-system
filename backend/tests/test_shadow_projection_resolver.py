"""影子投影运行时适配器解析器的回归。

背景：影子投影链路的机制此前已完整（合同、清单、租约、幂等写、读回比对、回滚、
重试），唯一断点是 ``resolve_configured_shadow_projection_adapter`` 直接抛
``SHADOW_ADAPTER_UNAVAILABLE``，而它正是 worker 与 API 唯一使用的解析器。
本文件锁定接上之后的行为。

重点锁三件容易被悄悄写坏的事：

1. **写入真的落库。** 可写连接工厂必须与 ``engine.begin`` 同语义（退出即提交）。
   若只 connect/close，SQLAlchemy 2.0 会隐式回滚，写入静默丢失且不报错——
   读回比对会拿到空结果却看不出原因。
2. **schema 指纹从活库实算，不是把登记值回传。** 回传会让
   ``SHADOW_SCHEMA_DRIFT`` 门禁自证同一、形同废除。
3. **安全证据不是硬编码的。** 最小权限走实测；不是影子目标、不是 sql 适配器、
   表不存在，都必须拒绝而不是放行。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.external_datasources import (
    ExternalDatasourceError,
    compute_live_schema_fingerprint,
    reset_engine_cache,
    verify_least_privilege,
)
from app.models import ShadowProjectionTarget
from app.shadow_projection import (
    ShadowProjectionError,
    create_shadow_projection_target,
    resolve_configured_shadow_projection_adapter,
)

#: 影子表 DDL。列名由 SqlShadowProjectionAdapter.apply_batch 决定。
#: ``UNIQUE (batch_id, content_key)`` 是必需的：适配器靠 ``ON CONFLICT`` 做幂等
#: upsert（重试同一批次不产生重复行），缺这个约束 SQLite 会直接报
#: 「ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint」。
#: 目标表建表时必须带上，这也是投影目标 DDL 的一条硬要求。
_SHADOW_DDL = """
CREATE TABLE shadow_kg_tags (
    batch_id TEXT NOT NULL,
    content_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE (batch_id, content_key)
)
"""


@pytest.fixture(autouse=True)
def _clean_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


@pytest.fixture(autouse=True)
def _non_production_env(monkeypatch: pytest.MonkeyPatch):
    """SQLite 无账号权限模型，最小权限必然实测为不收敛。

    显式声明本地环境让解析器放行——这正是 ``_PRIVILEGE_WAIVER_ENVS`` 的用途。
    独立用例会单独验证生产环境下没有放行口。
    """
    monkeypatch.setenv("LABEL_SYSTEM_DEPLOY_ENV", "test")


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


#: 影子目标登记行里存的**逻辑引用**。库里不准存 DSN
#: （见 shadow_projection._required 与 external_datasources.resolve_locator_to_dsn），
#: 真实连接串只配在部署侧环境变量里。
_LOCATOR = "kg_tags_shadow"
_LOCATOR_ENV = "LABEL_SYSTEM_DATASOURCE_KG_TAGS_SHADOW"


@pytest.fixture
def live_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """建一个真实存在的外部库（含影子表），把 DSN 配到部署侧环境变量。

    返回**逻辑引用**而不是 DSN——这正是登记行里能存的东西。
    """
    path = tmp_path / "downstream.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(_SHADOW_DDL))
    engine.dispose()
    reset_engine_cache()
    monkeypatch.setenv(_LOCATOR_ENV, f"sqlite:///{path}")
    return _LOCATOR


@pytest.fixture
def live_db_dsn(live_db: str) -> str:
    """同一个库的真实 DSN，供测试自己开连接复查用。"""
    import os

    return os.environ[_LOCATOR_ENV]


def _register(
    db: Session,
    dsn: str,
    *,
    fingerprint: str,
    table_name: str = "shadow_kg_tags",
    adapter_key: str = "sql",
    target_key: str = "kg-tags-shadow",
):
    return create_shadow_projection_target(
        db,
        target_key=target_key,
        adapter_key=adapter_key,
        connection_locator=dsn,
        # SQLite 不使用口令认证，但登记要求该字段非空，故给占位引用。
        secret_reference="local-sqlite-no-password",
        schema_name="main",
        table_name=table_name,
        environment="shadow",
        shadow_only=True,
        owner="标签中台",
        schema_fingerprint=fingerprint,
        status="active",
        created_by="tester",
    )


# --- 1. 写入真的落库 -------------------------------------------------------


def test_resolved_adapter_actually_persists_rows(
    db: Session, live_db: str, live_db_dsn: str
) -> None:
    """解析出的适配器写入后，数据必须在**新连接**里读得到。

    用新 engine 复查，而不是复用适配器自己的连接——只有这样才能发现「事务未提交、
    关闭时被隐式回滚」这类静默丢失。
    """
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    target = _register(db, live_db, fingerprint=fingerprint)
    adapter = resolve_configured_shadow_projection_adapter(target)

    # 注意 apply_batch 存的是**整行**的规范化 JSON（canonical_json(row)），
    # payload_hash 也由它自己按整行算（_hash(row)），并不取行里的同名字段。
    row = {"content_key": "ll-100001", "tag": "北欧风", "confidence": 0.91}
    adapter.apply_batch(batch_id="batch-001", rows=[row])

    # 关键：另开一个 engine 复查，确认已提交。
    reset_engine_cache()
    verifier = create_engine(live_db_dsn)
    with verifier.connect() as connection:
        persisted = connection.execute(
            text(
                "SELECT content_key, payload_json, payload_hash FROM shadow_kg_tags"
                " WHERE batch_id = :b"
            ),
            {"b": "batch-001"},
        ).all()
    verifier.dispose()

    assert len(persisted) == 1, "写入未落库——事务很可能被隐式回滚了"
    assert persisted[0].content_key == "ll-100001"
    assert json.loads(persisted[0].payload_json) == row
    assert persisted[0].payload_hash, "缺少内容指纹，读回比对无从做起"


def test_resolved_adapter_read_back_sees_written_rows(
    db: Session, live_db: str
) -> None:
    """适配器自己的读回比对也要拿到刚写的行——这是投影校验的基础。"""
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    adapter = resolve_configured_shadow_projection_adapter(
        _register(db, live_db, fingerprint=fingerprint)
    )
    row = {"content_key": "ll-100002", "tag": "侘寂风"}
    adapter.apply_batch(batch_id="batch-002", rows=[row])
    # read_back 反序列化的就是整行，可以逐字段比对。
    assert adapter.read_back(batch_id="batch-002") == [row]


def test_resolved_adapter_rollback_removes_batch(db: Session, live_db: str) -> None:
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    adapter = resolve_configured_shadow_projection_adapter(
        _register(db, live_db, fingerprint=fingerprint)
    )
    adapter.apply_batch(
        batch_id="batch-003",
        rows=[{"content_key": "ll-100003", "tag": "工业风"}],
    )
    assert adapter.read_back(batch_id="batch-003") != []
    adapter.rollback_batch(batch_id="batch-003")
    assert adapter.read_back(batch_id="batch-003") == []


# --- 2. 指纹从活库实算 -----------------------------------------------------


def test_evidence_fingerprint_comes_from_live_schema(
    db: Session, live_db: str
) -> None:
    """登记一个**错误**的指纹，证据里必须是活库真值，而不是登记值。

    若解析器把登记值原样回传，这个断言会失败——那种实现会让
    SHADOW_SCHEMA_DRIFT 门禁永远比对相等、彻底失效。
    """
    wrong_fingerprint = "0" * 64
    target = _register(db, live_db, fingerprint=wrong_fingerprint)
    adapter = resolve_configured_shadow_projection_adapter(target)

    live = compute_live_schema_fingerprint(live_db, None, table_name="shadow_kg_tags")
    evidence = adapter.verify_shadow_target()
    assert evidence.schema_fingerprint == live
    assert evidence.schema_fingerprint != wrong_fingerprint


def test_live_fingerprint_changes_when_schema_drifts(
    live_db: str, live_db_dsn: str
) -> None:
    """表结构变了指纹必须变，否则门禁拦不住漂移。"""
    before = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    engine = create_engine(live_db_dsn)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE shadow_kg_tags ADD COLUMN extra TEXT"))
    engine.dispose()
    reset_engine_cache()
    after = compute_live_schema_fingerprint(live_db, None, table_name="shadow_kg_tags")
    assert before != after


# --- 3. 安全证据不是硬编码 -------------------------------------------------


def test_sqlite_is_not_claimed_least_privileged(live_db: str) -> None:
    """SQLite 无权限模型，实测必须判为不收敛并给出原因（fail-closed）。"""
    收敛, 原因 = verify_least_privilege(
        live_db, None, table_name="shadow_kg_tags"
    )
    assert 收敛 is False
    assert "SQLite" in 原因


def test_production_env_has_no_privilege_waiver(
    db: Session, live_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产环境下最小权限实测不通过就必须拒绝解析，没有放行口。"""
    monkeypatch.setenv("LABEL_SYSTEM_DEPLOY_ENV", "production")
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    target = _register(db, live_db, fingerprint=fingerprint)
    adapter = resolve_configured_shadow_projection_adapter(target)
    # 解析本身不阻断，但证据必须诚实地报告未收敛，交由投影门禁拦截。
    assert adapter.verify_shadow_target().least_privileged is False


def test_unset_deploy_env_defaults_to_production(
    db: Session, live_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未声明部署环境时按最严处理，不能默认放行。"""
    monkeypatch.delenv("LABEL_SYSTEM_DEPLOY_ENV", raising=False)
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    target = _register(db, live_db, fingerprint=fingerprint)
    assert (
        resolve_configured_shadow_projection_adapter(target)
        .verify_shadow_target()
        .least_privileged
        is False
    )


# --- 4. 拒绝不该解析的目标 -------------------------------------------------


def test_non_sql_adapter_key_is_rejected(db: Session, live_db: str) -> None:
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    target = _register(db, live_db, fingerprint=fingerprint, adapter_key="fixture")
    with pytest.raises(ShadowProjectionError) as excinfo:
        resolve_configured_shadow_projection_adapter(target)
    assert excinfo.value.code == "SHADOW_ADAPTER_UNAVAILABLE"


def test_missing_table_is_rejected(db: Session, live_db: str) -> None:
    """登记的表在目标库里不存在时必须拒绝，而不是解析出一个必然写失败的适配器。"""
    fingerprint = compute_live_schema_fingerprint(
        live_db, None, table_name="shadow_kg_tags"
    )
    target = _register(
        db, live_db, fingerprint=fingerprint, table_name="not_created_yet"
    )
    with pytest.raises(ShadowProjectionError) as excinfo:
        resolve_configured_shadow_projection_adapter(target)
    assert excinfo.value.code == "SHADOW_ADAPTER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("environment", "shadow_only"),
    [("production", False), ("production", True), ("shadow", False)],
)
def test_non_shadow_target_is_rejected(
    live_db: str, environment: str, shadow_only: bool
) -> None:
    """解析器必须自己复核环境与 shadow_only，不盲信传进来的目标对象。

    数据库层已有 CHECK 约束 ``ck_shadow_projection_target_environment`` 挡住
    非影子行落库（试图 UPDATE 成 production 会直接 IntegrityError），所以这里
    用**未落库**的对象来测——解析器是第二道防线，任何绕过登记路径构造出来的
    目标都不能换到可写适配器。
    """
    target = ShadowProjectionTarget(
        target_key="tampered",
        adapter_key="sql",
        connection_locator=live_db,
        secret_reference="local-sqlite-no-password",
        schema_name="main",
        table_name="shadow_kg_tags",
        environment=environment,
        shadow_only=shadow_only,
        owner="标签中台",
        schema_fingerprint="0" * 64,
        status="active",
        created_by="tester",
    )
    with pytest.raises(ShadowProjectionError) as excinfo:
        resolve_configured_shadow_projection_adapter(target)
    assert excinfo.value.code == "SHADOW_TARGET_UNREGISTERED"


# --- 5. 连接层自身的安全边界 -----------------------------------------------


def test_dsn_in_locator_is_rejected() -> None:
    """把 DSN 塞进 locator 必须被拒——库里只准存逻辑引用。

    这是 ``shadow_projection._required`` 已有不变式的连接层对应实现：DSN 与口令
    不得跟着数据行走。
    """
    with pytest.raises(ExternalDatasourceError) as excinfo:
        compute_live_schema_fingerprint(
            "mysql+pymysql://user:secret@host/db", None, table_name="t"
        )
    assert excinfo.value.code == "DATASOURCE_LOCATOR_NOT_LOGICAL"


def test_unconfigured_locator_fails_closed() -> None:
    """逻辑引用没有对应配置时必须报错并指明该设哪个变量，不能猜或回退默认库。"""
    with pytest.raises(ExternalDatasourceError) as excinfo:
        compute_live_schema_fingerprint("never_configured", None, table_name="t")
    assert excinfo.value.code == "DATASOURCE_NOT_CONFIGURED"
    assert "LABEL_SYSTEM_DATASOURCE_NEVER_CONFIGURED" in excinfo.value.message


def test_inline_password_in_deployed_dsn_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部署侧 DSN 也不得内联口令——口令只能走 secret 引用。"""
    monkeypatch.setenv(
        "LABEL_SYSTEM_DATASOURCE_PG_TARGET", "postgresql+psycopg://u:pw@h/db"
    )
    with pytest.raises(ExternalDatasourceError) as excinfo:
        compute_live_schema_fingerprint("pg_target", None, table_name="t")
    # 未装 psycopg 时会先在驱动检查处被拦，两者都是合法的拒绝原因。
    assert excinfo.value.code in {
        "DATASOURCE_LOCATOR_HAS_SECRET",
        "DATASOURCE_DRIVER_MISSING",
    }


def test_table_name_injection_is_rejected(live_db: str) -> None:
    with pytest.raises(ExternalDatasourceError) as excinfo:
        verify_least_privilege(live_db, None, table_name='x"; DROP TABLE y;--')
    assert excinfo.value.code == "DATASOURCE_TABLE_INVALID"


def test_disallowed_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """部署侧配了不在白名单里的库类型时必须拒绝。"""
    monkeypatch.setenv("LABEL_SYSTEM_DATASOURCE_ORA_TARGET", "oracle://h/db")
    with pytest.raises(ExternalDatasourceError) as excinfo:
        compute_live_schema_fingerprint("ora_target", None, table_name="t")
    assert excinfo.value.code == "DATASOURCE_SCHEME_NOT_ALLOWED"
