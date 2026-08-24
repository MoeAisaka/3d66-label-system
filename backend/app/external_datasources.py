"""外部数据源连接层：把「合同里的定位符 + 凭据引用」解析成真实连接。

## 为什么需要这一层

上游只读取数与下游影子投影**卡在同一个形状**上：两侧的机制都已完整（
``readonly_sources.SqlReadOnlySourceAdapter`` 与
``shadow_projection.SqlShadowProjectionAdapter`` 都能真正读写表），但两侧的
「从配置解析出连接」这一步都是空实现，分别抛
``SOURCE_ADAPTER_UNAVAILABLE`` 与 ``SHADOW_ADAPTER_UNAVAILABLE``。
本模块补上这一步，一处实现同时解锁两侧。

## 设计约束

- **驱动无关**：仓库当前没有安装任何非 SQLite 驱动（``requirements.txt`` 只有
  sqlalchemy＋httpx）。因此本层不 import 任何具体驱动，只在解析 DSN 时检查驱动
  是否可用，缺失时给出明确的中文错误与安装提示。用 SQLite 即可端到端验证机制，
  生产环境换 DSN 与装驱动即可，无需改代码。
- **凭据不落明文**：定位符（``connection_locator``）只存不含口令的 DSN，口令走
  ``security.protect_secret`` / ``unprotect_secret`` 的引用机制。
- **任何对外文本都脱敏**：错误信息、日志、异常 repr 一律走 :func:`redact_dsn`，
  绝不带出口令。
- **只读路径真只读**：上游连接在事务中打开并**总是回滚**，即使调用方写了也不会
  落库。这是代码层的护栏，不替代数据库账号本身的最小权限。
- **写入路径必须显式**：下游只接受 ``environment="shadow"`` 且
  ``shadow_only=True`` 的目标。写生产业务表是另一个决定，需要单独的合同与评审，
  本层不提供绕过口。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import contextmanager
import os
from importlib import util as importlib_util
from typing import Callable, ContextManager, Iterator

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from .security import SecretStorageError, unprotect_secret

#: 表名白名单。表名会拼进 DDL 探针语句，必须先过这道校验。
_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")

#: 逻辑引用 → 环境变量名的前缀。见 :func:`resolve_locator_to_dsn`。
_DATASOURCE_ENV_PREFIX = "LABEL_SYSTEM_DATASOURCE_"

#: 允许的 DSN scheme。故意保守：新增数据库类型应经评审后显式加入，
#: 而不是让任意 scheme 从合同里流进来。
ALLOWED_SCHEMES: frozenset[str] = frozenset(
    {
        "sqlite",
        "mysql+pymysql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }
)

#: scheme → (import 名, 安装提示)。用于在连不上之前给出可执行的中文错误。
_DRIVER_HINTS: dict[str, tuple[str, str]] = {
    "mysql+pymysql": ("pymysql", "pip install pymysql"),
    "postgresql+psycopg": ("psycopg", "pip install 'psycopg[binary]'"),
    "postgresql+psycopg2": ("psycopg2", "pip install psycopg2-binary"),
}

_engine_cache: dict[str, Engine] = {}
_engine_lock = threading.Lock()


class ExternalDatasourceError(RuntimeError):
    """外部数据源解析或连接失败。``code`` 供上层映射为稳定错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def redact_dsn(locator: str) -> str:
    """把 DSN 中的口令替换为 ``***``，供日志与错误信息使用。

    解析失败时不回显原文——原文可能本身就含口令。
    """
    try:
        url = make_url(locator)
    except (ArgumentError, ValueError):
        return "<无法解析的连接定位符>"
    return url.render_as_string(hide_password=True)


def resolve_locator_to_dsn(connection_locator: str) -> str:
    """把合同里的**逻辑引用**解析成部署侧配置的 DSN。

    为什么要这一层：``shadow_projection._required`` 明确禁止
    ``connection_locator`` 含 ``://`` 或 ``password=``，即库里只准存逻辑名
    （如 ``kg_tags_shadow``），真实连接串必须留在部署侧。这条不变式挡住了
    「DSN 和口令跟着数据行到处走」的整类问题，不能为了少写一层而放宽。

    映射规则：逻辑引用大写、非字母数字转下划线，加前缀
    ``LABEL_SYSTEM_DATASOURCE_`` 后查环境变量。例如 ``kg_tags_shadow`` →
    ``LABEL_SYSTEM_DATASOURCE_KG_TAGS_SHADOW``。

    未配置即 fail-closed 并给出该设哪个变量，不猜、不回退到默认库。
    """
    reference = (connection_locator or "").strip()
    if not reference:
        raise ExternalDatasourceError(
            "DATASOURCE_LOCATOR_INVALID", "连接定位符不能为空"
        )
    if "://" in reference:
        raise ExternalDatasourceError(
            "DATASOURCE_LOCATOR_NOT_LOGICAL",
            "连接定位符必须是逻辑引用而不是 DSN；真实连接串只能配在部署侧",
        )
    env_key = _DATASOURCE_ENV_PREFIX + re.sub(r"[^A-Za-z0-9]+", "_", reference).upper()
    dsn = (os.getenv(env_key) or "").strip()
    if not dsn:
        raise ExternalDatasourceError(
            "DATASOURCE_NOT_CONFIGURED",
            f"逻辑引用 {reference} 没有对应的连接配置；请设置环境变量 {env_key}",
        )
    return dsn


def resolve_locator_table(connection_locator: str) -> str:
    """读取逻辑引用对应的**物理表名**。

    为什么表名也在部署侧：上游合同（``UpstreamSourceContract``）刻意不含
    ``table_name`` 列——合同描述的是「读哪些字段、按什么游标翻页」这类语义约定，
    而具体读哪张物理表属于部署细节，和 DSN 一样不该跟着合同行走。

    环境变量名是 DSN 变量名加 ``_TABLE`` 后缀，例如逻辑引用
    ``source-registry:fixture-3d`` 对应
    ``LABEL_SYSTEM_DATASOURCE_SOURCE_REGISTRY_FIXTURE_3D_TABLE``。
    """
    reference = (connection_locator or "").strip()
    if not reference:
        raise ExternalDatasourceError(
            "DATASOURCE_LOCATOR_INVALID", "连接定位符不能为空"
        )
    env_key = (
        _DATASOURCE_ENV_PREFIX
        + re.sub(r"[^A-Za-z0-9]+", "_", reference).upper()
        + "_TABLE"
    )
    table = (os.getenv(env_key) or "").strip()
    if not table:
        raise ExternalDatasourceError(
            "DATASOURCE_TABLE_NOT_CONFIGURED",
            f"逻辑引用 {reference} 没有配置物理表名；请设置环境变量 {env_key}",
        )
    if not _TABLE_NAME.fullmatch(table):
        raise ExternalDatasourceError(
            "DATASOURCE_TABLE_INVALID", f"表名 {table!r} 不是合法标识符"
        )
    return table


def _validate_scheme(locator: str) -> str:
    try:
        url = make_url(locator)
    except (ArgumentError, ValueError) as exc:
        raise ExternalDatasourceError(
            "DATASOURCE_LOCATOR_INVALID", "连接定位符不是合法的 DSN"
        ) from exc
    scheme = url.drivername
    if scheme not in ALLOWED_SCHEMES:
        allowed = "、".join(sorted(ALLOWED_SCHEMES))
        raise ExternalDatasourceError(
            "DATASOURCE_SCHEME_NOT_ALLOWED",
            f"连接类型 {scheme} 不在允许列表内；当前允许：{allowed}",
        )
    return scheme


def _require_driver(scheme: str) -> None:
    hint = _DRIVER_HINTS.get(scheme)
    if hint is None:
        return
    module_name, install_hint = hint
    if importlib_util.find_spec(module_name) is None:
        raise ExternalDatasourceError(
            "DATASOURCE_DRIVER_MISSING",
            f"运行环境缺少 {module_name} 驱动，无法连接 {scheme}；"
            f"请先安装：{install_hint}",
        )


def resolve_dsn(connection_locator: str, secret_reference: str | None) -> str:
    """把**逻辑引用**与凭据引用合成完整 DSN。

    两步：先经 :func:`resolve_locator_to_dsn` 把逻辑名换成部署侧配置的 DSN
    （库里不准存 DSN，见该函数说明），再按需注入口令。

    ``secret_reference`` 为空时不注入口令——SQLite 与走 IAM／信任认证的场景
    合法地不需要口令。非空时必须能解引用，解不出即 fail-closed，不静默降级为
    无口令连接（那会连到错误的地方或以错误身份连接）。
    """
    dsn = resolve_locator_to_dsn(connection_locator)
    scheme = _validate_scheme(dsn)
    _require_driver(scheme)
    url = make_url(dsn)

    if url.password:
        raise ExternalDatasourceError(
            "DATASOURCE_LOCATOR_HAS_SECRET",
            "连接定位符不得内联口令，口令必须走 secret 引用",
        )

    if scheme == "sqlite":
        # SQLite 是文件型库，没有账号口令认证机制。影子目标登记要求
        # secret_reference 非空（见 create_shadow_projection_target 的 _required），
        # 所以本地／测试环境会带着一个占位引用进来。这里明确忽略它，而不是去解
        # 一个注定用不上的口令。注意这是「不适用」而非「跳过校验」：sqlite 连接
        # 本身不接受口令，配了也不会生效。
        return url.render_as_string(hide_password=False)

    reference = (secret_reference or "").strip()
    if not reference:
        return url.render_as_string(hide_password=False)

    try:
        password = unprotect_secret(reference)
    except SecretStorageError as exc:
        raise ExternalDatasourceError(
            "DATASOURCE_SECRET_UNAVAILABLE",
            f"凭据引用无法解析：{exc}",
        ) from exc
    if not password:
        raise ExternalDatasourceError(
            "DATASOURCE_SECRET_EMPTY", "凭据引用解析结果为空"
        )
    return url.set(password=password).render_as_string(hide_password=False)


def _engine_for(dsn: str, *, readonly: bool) -> Engine:
    """按 DSN 缓存 Engine。缓存键含读写意图，避免两种用途共用连接池。"""
    cache_key = f"{'ro' if readonly else 'rw'}|{dsn}"
    cached = _engine_cache.get(cache_key)
    if cached is not None:
        return cached
    with _engine_lock:
        cached = _engine_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            engine = create_engine(dsn, pool_pre_ping=True, future=True)
        except (SQLAlchemyError, ArgumentError, ModuleNotFoundError) as exc:
            raise ExternalDatasourceError(
                "DATASOURCE_ENGINE_FAILED",
                f"无法为 {redact_dsn(dsn)} 建立连接引擎：{type(exc).__name__}",
            ) from exc
        _engine_cache[cache_key] = engine
        return engine


def reset_engine_cache() -> None:
    """释放并清空缓存的 Engine。供测试与配置变更后调用。"""
    with _engine_lock:
        for engine in _engine_cache.values():
            engine.dispose()
        _engine_cache.clear()


def _enforce_connection_read_only(connection: Connection) -> None:
    """在**数据库层**把连接切成只读，而不只依赖「退出时回滚」。

    这样做有两个好处：写操作会被数据库直接拒绝（而不是先执行再回滚），并且
    ``readonly_sources.SqlReadOnlySourceAdapter._verify_connection_read_only``
    的只读探测（SQLite 查 ``PRAGMA query_only``、PostgreSQL 查
    ``SHOW transaction_read_only``、MySQL 查 ``@@transaction_read_only``）能拿到
    真实的 True——证据是真的，不是绕过门禁得来的。

    各后端的设置时机不同：SQLite 的 PRAGMA 与 MySQL 的 SESSION 级设置要在事务
    开启前做，PostgreSQL 的 ``SET TRANSACTION READ ONLY`` 必须在事务内。
    """
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.exec_driver_sql("PRAGMA query_only = ON")
    elif dialect in {"mysql", "mariadb"}:
        connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")


def _enforce_transaction_read_only(connection: Connection) -> None:
    """事务内生效的只读设置（PostgreSQL 专属）。"""
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def _readonly_connection(engine: Engine) -> Iterator[Connection]:
    """打开只读连接：数据库层切只读，且退出时**总是回滚**。

    双保险。数据库层只读是主防线（写会被拒），总是回滚是次防线（万一某个后端
    没有只读开关，也不会留下痕迹）。
    """
    connection = engine.connect()
    try:
        _enforce_connection_read_only(connection)
        # exec_driver_sql 会触发 SQLAlchemy 的 autobegin，若不先结束这个隐式事务，
        # 下面的 begin() 会报 "already initialized a Transaction"。
        # 回滚是安全的：SQLite 的 PRAGMA 与 MySQL 的 SESSION 设置都不是事务性的，
        # 回滚不会把只读状态撤掉。
        connection.rollback()
    except SQLAlchemyError as exc:
        connection.close()
        raise ExternalDatasourceError(
            "DATASOURCE_READONLY_ENFORCE_FAILED",
            f"无法把连接设为只读：{type(exc).__name__}",
        ) from exc
    transaction = connection.begin()
    try:
        _enforce_transaction_read_only(connection)
        yield connection
    finally:
        # 只读路径永不提交。异常与正常退出都回滚。
        try:
            transaction.rollback()
        finally:
            connection.close()


def readonly_connection_factory(
    connection_locator: str, secret_reference: str | None
) -> Callable[[], ContextManager[Connection]]:
    """构造上游只读连接工厂，交给 ``SqlReadOnlySourceAdapter``。"""
    dsn = resolve_dsn(connection_locator, secret_reference)
    engine = _engine_for(dsn, readonly=True)

    @contextmanager
    def factory() -> Iterator[Connection]:
        yield from _readonly_connection(engine)

    return factory


def writable_connection_factory(
    connection_locator: str, secret_reference: str | None
) -> Callable[[], ContextManager[Connection]]:
    """构造可写连接工厂，交给影子投影适配器。

    调用方负责事务边界：适配器自己 ``begin()`` 并提交。
    """
    dsn = resolve_dsn(connection_locator, secret_reference)
    engine = _engine_for(dsn, readonly=False)

    @contextmanager
    def factory() -> Iterator[Connection]:
        # 语义必须与 ``engine.begin`` 一致：进入时开启事务，正常退出时**提交**，
        # 异常时回滚。影子适配器的 apply_batch 只执行 INSERT、不自己提交，
        # 若这里只 connect/close，SQLAlchemy 2.0 会在关闭时隐式回滚，
        # 写入将静默丢失——读回比对会拿到空结果，且不报错。
        connection = engine.connect()
        transaction = connection.begin()
        try:
            yield connection
        except BaseException:
            transaction.rollback()
            raise
        else:
            transaction.commit()
        finally:
            connection.close()

    return factory


def compute_live_schema_fingerprint(
    connection_locator: str, secret_reference: str | None, *, table_name: str
) -> str:
    """从**活库**读出表结构并算指纹。

    影子投影的 schema 漂移门禁（``shadow_projection.py`` 内 ``SHADOW_SCHEMA_DRIFT``）
    比对的是「登记时的指纹」与「此刻真实表结构的指纹」。所以这里必须真去读库，
    绝不能把登记值原样回传——那会让门禁自证同一、形同废除。

    登记目标与校验目标都调用本函数，保证两侧算法一致。
    """
    if not _TABLE_NAME.fullmatch(table_name):
        raise ExternalDatasourceError(
            "DATASOURCE_TABLE_INVALID", f"表名 {table_name!r} 不是合法标识符"
        )
    factory = readonly_connection_factory(connection_locator, secret_reference)
    try:
        with factory() as connection:
            inspector = sa_inspect(connection)
            if not inspector.has_table(table_name):
                raise ExternalDatasourceError(
                    "DATASOURCE_TABLE_MISSING",
                    f"目标库中不存在表 {table_name}",
                )
            columns = [
                {"name": col["name"], "type": str(col["type"]).upper()}
                for col in inspector.get_columns(table_name)
            ]
    except ExternalDatasourceError:
        raise
    except SQLAlchemyError as exc:
        raise ExternalDatasourceError(
            "DATASOURCE_INSPECT_FAILED",
            f"读取 {table_name} 结构失败：{type(exc).__name__}",
        ) from exc
    payload = json.dumps(
        {"table": table_name, "columns": sorted(columns, key=lambda c: c["name"])},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_least_privilege(
    connection_locator: str, secret_reference: str | None, *, table_name: str
) -> tuple[bool, str]:
    """实测连接账号的权限是否收敛到「只往这张影子表写行」。

    返回 ``(是否收敛, 判定依据)``。判定依据会进日志与安全证据，便于事后追责。

    **只用只读内省，不做写探针。** 早期版本试过「建一张临时探针表再回滚」，实测
    在 pysqlite 上 DDL 会隐式提交、探针表留在了目标库里。往目标库遗留垃圾表不可
    接受（目标可能是生产库），所以改成查数据库自己的权限视图，零副作用。

    这是实测而非声明。影子投影的 ``SHADOW_PERMISSION_OVERBROAD`` 门禁就是用来拦
    权限过宽的账号的，在此硬编码 ``True`` 等于把那道门禁废掉。

    各后端判定口径：

    - **PostgreSQL**：要求对目标表有 INSERT，且**不得**持有库级 CREATE，且不是
      超级用户。
    - **MySQL**：读 ``SHOW GRANTS FOR CURRENT_USER()``，出现 ``ALL PRIVILEGES``、
      ``*.*`` 或 DDL 类授权即判过宽。
    - **SQLite**：没有账号权限模型，一律判为不收敛（fail-closed）。本地开发库
      因此必然落到「不收敛」，需由配置在非生产环境显式放行，见
      :func:`resolve_shadow_evidence`。
    - 其它后端：无内省口径，同样 fail-closed。
    """
    if not _TABLE_NAME.fullmatch(table_name):
        raise ExternalDatasourceError(
            "DATASOURCE_TABLE_INVALID", f"表名 {table_name!r} 不是合法标识符"
        )
    # 逻辑引用本身不含 scheme，必须先解析成部署侧 DSN 才能判断后端类型。
    scheme = _validate_scheme(resolve_locator_to_dsn(connection_locator))
    factory = readonly_connection_factory(connection_locator, secret_reference)
    try:
        with factory() as connection:
            if scheme.startswith("postgresql"):
                return _least_privilege_postgres(connection, table_name)
            if scheme.startswith("mysql"):
                return _least_privilege_mysql(connection)
            if scheme.startswith("sqlite"):
                return False, "SQLite 无账号权限模型，无法证明权限收敛"
            return False, f"{scheme} 缺少权限内省口径，按 fail-closed 处理"
    except ExternalDatasourceError:
        raise
    except SQLAlchemyError as exc:
        raise ExternalDatasourceError(
            "DATASOURCE_PRIVILEGE_PROBE_FAILED",
            f"权限内省失败：{type(exc).__name__}",
        ) from exc


def _least_privilege_postgres(
    connection: Connection, table_name: str
) -> tuple[bool, str]:
    row = connection.execute(
        text(
            "SELECT has_table_privilege(current_user, :tbl, 'INSERT') AS can_insert,"
            " has_database_privilege(current_user, current_database(), 'CREATE')"
            "   AS can_create,"
            " (SELECT usesuper FROM pg_user WHERE usename = current_user)"
            "   AS is_super"
        ),
        {"tbl": table_name},
    ).one()
    if not row.can_insert:
        return False, f"账号对 {table_name} 没有 INSERT 权限"
    if row.is_super:
        return False, "账号是超级用户，权限远超所需"
    if row.can_create:
        return False, "账号持有库级 CREATE 权限，对影子写入而言过宽"
    return True, f"账号仅有 {table_name} 的 INSERT，无 CREATE、非超级用户"


def _least_privilege_mysql(connection: Connection) -> tuple[bool, str]:
    grants = [
        str(row[0]).upper()
        for row in connection.execute(text("SHOW GRANTS FOR CURRENT_USER()"))
    ]
    overbroad = ("ALL PRIVILEGES", " ON *.*", "CREATE", "DROP", "ALTER", "SUPER")
    for grant in grants:
        for marker in overbroad:
            if marker in grant:
                return False, f"授权含过宽项 {marker.strip()}"
    return True, "SHOW GRANTS 未出现全局或 DDL 授权"


def probe_datasource(
    connection_locator: str, secret_reference: str | None, *, readonly: bool = True
) -> dict[str, object]:
    """连通性探测：只跑 ``SELECT 1``，不读业务数据、不写任何东西。

    返回值中的定位符已脱敏，可直接落库或回给接口。
    """
    factory = (
        readonly_connection_factory if readonly else writable_connection_factory
    )(connection_locator, secret_reference)
    try:
        with factory() as connection:
            connection.execute(text("SELECT 1"))
    except ExternalDatasourceError:
        raise
    except SQLAlchemyError as exc:
        raise ExternalDatasourceError(
            "DATASOURCE_CONNECT_FAILED",
            f"连接 {connection_locator} 失败：{type(exc).__name__}",
        ) from exc
    # 逻辑引用按构造就不含口令（``resolve_locator_to_dsn`` 拒绝含 :// 的输入），
    # 可以原样回显；不走 redact_dsn，那会把逻辑名误报成「无法解析」。
    return {
        "locator": connection_locator,
        "readonly": readonly,
        "reachable": True,
    }
