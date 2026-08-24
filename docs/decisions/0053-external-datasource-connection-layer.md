# ADR-0053：外部数据源连接层与上下游适配器解析

- 状态：Accepted
- 日期：2026-08-24
- 关联：ADR-0029、ADR-0046、ADR-0047

## 背景

知识图谱第一期需要打通两端：上游从数仓取素材事件，下游把标签结果投影到外部表。
调研发现两端**卡在同一个形状**上——机制都已完整，只差「从合同拿到真实连接」这一步：

- 上游：`readonly_sources.SqlReadOnlySourceAdapter` 能真正读表、能做只读校验、能按
  游标翻页，但 `/api/upstream-source-contracts/{id}/poll` 直接返回 503
  `SOURCE_ADAPTER_UNAVAILABLE`。
- 下游：影子投影的合同、清单、租约、幂等写、读回比对、回滚、重试全部就绪，
  `SqlShadowProjectionAdapter` 也能真正写表，但
  `resolve_configured_shadow_projection_adapter` 直接抛 `SHADOW_ADAPTER_UNAVAILABLE`，
  而它正是 worker 与 API 唯一使用的解析器。

两侧各写一份连接解析会导致安全口径分叉：一侧强制只读、另一侧忘了；一侧校验 DSN
白名单、另一侧没校验。而这两处恰恰是整个系统唯一对外连接的地方。

另有一处解释歧义必须先定：ADR-0046 第 16 条禁止 `model_3d_su` 复用 `three_d` 的
影子投影。这条禁的是**复用那份具体配置**（原文四个宾语——profile、编辑器、只读来源、
影子投影——都挂在 `three_d` 限定词下），不是禁用影子投影这个机制；该条前半句要求
「在共享目录、startup seed 与通用 pipeline 注册表中**并列**增加」，本身就是鼓励复用
通用机制、并列新增自己的配置。

## 决策

1. **新增单一连接层 `app/external_datasources.py`，上下游共用。** 两端的解析器都只
   经它取连接，不各自 `create_engine`。安全口径因此只有一处需要维护和审计。

2. **库里只存逻辑引用，DSN 与物理表名都配在部署侧。** `connection_locator` 是逻辑名
   （如 `kg_tags_shadow`），经 `LABEL_SYSTEM_DATASOURCE_<大写逻辑名>` 解析成 DSN，
   物理表名走同名加 `_TABLE` 后缀的变量。这延续 `shadow_projection._required` 与
   `readonly_sources` 已有的不变式——该校验明确拒绝含 `://` 或 `password=` 的定位符，
   即 DSN 与口令不得跟着数据行走。上游合同刻意没有 `table_name` 列，物理表名同属
   部署细节，一并留在部署侧。口令仍走 `security.protect_secret` 的引用机制。
   未配置即 fail-closed，并在错误里指明该设哪个变量，不猜、不回退默认库。

3. **只读在数据库层强制，不只靠「退出时回滚」。** 只读连接会打开 SQLite 的
   `PRAGMA query_only`、PostgreSQL 的 `SET TRANSACTION READ ONLY`、MySQL 的
   `SET SESSION TRANSACTION READ ONLY`，写操作被数据库直接拒绝；退出时仍总是回滚作为
   次防线。这样 `SqlReadOnlySourceAdapter.verify_read_only` 探测到的只读证据是**真实
   通过**的，而不是绕过门禁得来的。

4. **schema 指纹一律从活库实算，禁止回传登记值。** 两侧适配器的 `schema_fingerprint`
   都是构造参数原样回传，而两侧的漂移门禁（上游 `SOURCE_SCHEMA_DRIFT`、下游
   `SHADOW_SCHEMA_DRIFT`）比对的是「登记值 vs 此刻真实结构」。若解析器把登记值传回去，
   门禁就变成自证同一、形同废除。登记与校验必须调用同一个
   `compute_live_schema_fingerprint`，否则算法分叉会导致门禁永久阻断。

5. **最小权限实测，生产环境无放行口。** 下游的 `least_privileged` 证据由
   `verify_least_privilege` 实测得出：PostgreSQL 要求对目标表有 INSERT 且不持库级
   CREATE、非超级用户；MySQL 读 `SHOW GRANTS` 判断有无全局或 DDL 授权；SQLite 无账号
   权限模型，一律判为不收敛。实测不通过时仅当 `LABEL_SYSTEM_DEPLOY_ENV` 属
   `local`/`dev`/`test`/`ci` 才放行，且放行原因写进日志；该变量未声明时按 `production`
   处理。**在此硬编码 `True` 等于废除 `SHADOW_PERMISSION_OVERBROAD` 门禁**，不允许。

6. **权限探测零副作用。** 早期实现用「建临时探针表再回滚」来试探 DDL 权限，实测
   pysqlite 对 DDL 隐式提交，探针表留在了目标库里。改为只读内省（查数据库自身的权限
   视图）。往目标库遗留任何对象都不可接受——目标可能是生产库。

7. **仅支持白名单内的库类型**（sqlite、mysql+pymysql、postgresql+psycopg/psycopg2），
   新增类型需经评审显式加入，不让任意 scheme 从合同流进来。驱动缺失时给出明确的中文
   错误与安装命令，而不是连接超时。所有对外文本（错误、日志）中的 DSN 一律脱敏。

8. **`model_3d_su` 的下游投影按 ADR-0046 并列新增自己的目标登记，不复用 `three_d`
   的行。** 复用的是本 ADR 定义的通用连接层与影子投影机制，符合该条「并列增加」的
   要求。

## 后果

- 上游 poll 与下游影子投影从此可真正连库；两者共用一层，安全口径不会分叉。
- 部署需要新增环境变量：每个逻辑引用一对 `LABEL_SYSTEM_DATASOURCE_*` 与
  `..._TABLE`，以及 `LABEL_SYSTEM_DEPLOY_ENV`。缺失会在解析时 fail-closed 并指明变量名。
- 生产环境的数据库账号必须按最小权限配置，否则影子投影会被门禁拦住。这是刻意的：
  权限过宽的账号不该拿到写权限，改账号比改门禁正确。
- 影子表 DDL 必须带 `UNIQUE (batch_id, content_key)`，适配器靠它做幂等 upsert；
  缺这个约束 `ON CONFLICT` 会直接报错。
- 非 SQLite 驱动尚未加入 `requirements.txt`。上线前需按目标库类型安装对应驱动，
  连接层会在缺失时明确报出安装命令。
- 两张下游目标表的 DDL 仍待建。在建成之前，链路终点是影子投影本身——它正是为
  「真写之前先镜像验证」设计的，不是权宜之计。
