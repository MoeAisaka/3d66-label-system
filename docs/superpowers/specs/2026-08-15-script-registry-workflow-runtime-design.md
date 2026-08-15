# TPENG 标签实验台脚本注册与工作流运行时底座设计

- 状态：待 Owner 书面复核
- 日期：2026-08-15
- 产品主体：TPENG 标签实验台（LabelLab）
- 适用目标：标签体系重构统一事实产品、标签/内容中台通用底座
- 本阶段运行模式：本地 dry-run；不接真实 DataWorks、真实模型或外部数据库

## 1. 设计结论

在现有 LabelLab 调度内核之上增加受控脚本注册、可冻结工作流版本和通用运行时记录。类目评测、语义打标、美感评测、人工纠偏、机制回归、发布、投影和反馈都作为标准步骤或类目插件接入同一运行时，不再为每个类目建设独立队列或独立执行器。

本阶段只交付可审计、可恢复的本地 dry-run 底座。首批执行器为确定性 fixture/no-op，用于验证合同、路由、幂等、租约、超时、重试、检查点和桌面运行中心；真实模型、真实数据库 DML、DataWorks 调度和外部网络连接留到后续经 Owner 冻结的批次。

运行时不允许从页面上传或执行任意 Python、JavaScript、SQL、Shell。页面只能选择已经研发发布并处于可用状态的脚本版本，配置合同允许开放的参数，并从已验证的工作流模板创建不可变运行快照。

## 2. 范围与非目标

### 2.1 本阶段范围

1. 受控脚本注册中心：定义、版本、制品哈希、输入输出 Schema、权限、资源和生命周期。
2. 工作流定义中心：版本、标准步骤、依赖边、条件路由、合同校验和冻结快照。
3. 通用运行时：`ProductionRun`、`StepAttempt`、幂等键、租约、超时、重试、检查点和恢复。
4. 调度接入：复用既有 `validation`、`interactive`、`production_batch`、`canary`、`recovery` 五队列、配额、借用、deficit、恢复间隔和熔断能力。
5. 桌面端运行中心：展示脚本版本、工作流快照、队列、当前步骤、最后检查点、责任人、阻塞原因和恢复动作。
6. 本地 fixture 执行器和专项测试：覆盖成功、条件分支、失败重试、租约过期、断点恢复和幂等重放。
7. migration 71：增量、幂等、可回滚，不改写历史运行，不强制回填旧评测记录。

### 2.2 非目标

- 不接入真实 DataWorks、真实模型供应商、真实大维表/小表或外部数据库。
- 不执行真实标签事实发布、投影 DML、存量覆盖或生产流量。
- 不引入 Redis、PostgreSQL、Kafka、外部任务队列或第二套调度器。
- 不增加第六类队列，不改变五队列的既有语义和默认配额。
- 不开放任意代码上传，不提供网页终端、Shell、SQL 控制台或脚本在线编辑器。
- 不替换现有 `EvaluationProductionRun`、`EvaluationJob`、纠偏和发布历史模型，不迁移或重算历史记录。
- 不在本阶段实现真实 3D/SU 数据接入；3D/SU 仅作为验证类目 Profile 的工作流引用方。
- 不建设移动端；工作台按公司内网桌面浏览器设计。

## 3. 现有能力复用与边界

### 3.1 直接复用

- `backend/app/queue_scheduler.py` 的 `QUEUE_CLASSES`、`QueuePolicy`、`DeterministicQueueScheduler`、配额分配、受控借用、deficit 和 recovery 间隔。
- 现有 `QueueSchedulerState` 的持久化调度状态；调度状态仍与任务领取在同一数据库事务内更新。
- `worker.py` 的有限租约领取、心跳、过期恢复和错误分类模式。
- `optimization_automation.py` 的租约、过期回收和 Worker 状态能力。
- `operations-center-page.tsx` 的五队列、并发、熔断、重试和恢复摘要，并在同一一级运行中心增加通用运行信息。
- 现有类目 Profile、模型中心、Prompt/V3 机制版本、双发布轴和审计字段；工作流只引用其不可变版本，不复制这些治理能力。

### 3.2 新增但不形成第二套队列

新增一个通用运行时 dispatch 适配层。它把 `StepAttempt` 暴露为既有调度器可领取的 dispatch item，使用同一个 `QueuePolicy`、同一个 `QueueSchedulerState`、同一个熔断/恢复判定。现有评测任务和通用工作流步骤可以分别实现 dispatch adapter，但不得各自维护配额、轮询、重试或恢复算法。

`EvaluationProductionRun` 继续作为评测产品聚合记录；通用 `ProductionRun` 记录执行事实。二者通过 `source_type + source_id` 可选关联，历史评测记录不做回填。未来评测流程接入通用运行时时，只创建新的关联和快照，不重写旧运行。

## 4. 受控脚本注册中心

### 4.1 对象模型

#### `ScriptDefinition`

表示一个稳定的脚本能力键，不包含可执行源码。字段至少包括：

- `script_key`：全局稳定键，例如 `fixture.identity.v1`；唯一且不可复用。
- `name`、`description`、`owner`、`allowed_categories`、`step_types`。
- `status`：`active` 或 `retired`；定义退休后禁止新版本注册，历史版本仍可解释。
- `created_at`、`updated_at`、`created_by`。

#### `ScriptVersion`

表示可被工作流冻结引用的不可变版本。字段至少包括：

- `script_definition_id`、`version`、`display_name`。
- `executor_kind`：首批只允许 `deterministic_fixture`。
- `artifact_sha256`：制品内容哈希，64 位小写十六进制；版本创建后不可改变。
- `manifest_json`：执行器、参数默认值、资源声明和兼容性信息。
- `input_schema_json`、`output_schema_json`：受限 JSON Schema 子集及其版本。
- `required_permissions_json`：允许访问的逻辑资源声明，不保存凭据。
- `idempotency_template`、`timeout_seconds`、`max_attempts`、`retry_policy_json`。
- `concurrency_limit`、`rate_limit_key`、`estimated_cost_json`。
- `status`：`draft`、`validating`、`active`、`deprecated`、`retired`、`blocked`。
- `validation_report_json`、`blocked_reason`、`created_by`、时间戳。

版本唯一键为 `script_definition_id + version`。`artifact_sha256`、Schema、权限、重试和资源策略都属于版本内容；任何变化必须创建新版本。`blocked` 版本保留历史解释，但不能进入新工作流或新运行。

### 4.2 生命周期与门禁

```text
draft → validating → active → deprecated → retired
                    ↘ blocked
```

- `draft` 只能由管理员编辑元数据，不能被工作流引用。
- `validating` 执行制品哈希、Schema、权限、执行器和 fixture 合同检查。
- `active` 可被新工作流版本引用。
- `deprecated` 可完成历史运行和恢复，但默认不允许新工作流版本引用。
- `retired` 仅用于审计和历史重放，不可执行。
- 任意校验失败进入 `blocked`，必须说明稳定错误码和字段路径；不得自动猜测修复。

本阶段没有页面上传制品接口。fixture 版本由受控种子或内部注册命令创建，接口只接收已审核的制品元数据和哈希；后续如需接入真实执行器，必须单独冻结安全、凭据和发布合同。

### 4.3 脚本注册 API（设计合同）

| 方法 | 路径 | 规则 |
|---|---|---|
| `GET` | `/api/scripts` | 按状态、步骤类型、类目和 Owner 过滤；默认隐藏 retired |
| `POST` | `/api/scripts` | 创建定义，不创建可执行版本 |
| `GET` | `/api/scripts/{script_key}/versions` | 返回版本、验证摘要、引用次数和可用状态 |
| `POST` | `/api/scripts/{script_key}/versions` | 只创建新版本；拒绝覆盖已有版本 |
| `POST` | `/api/scripts/{script_key}/versions/{version}/validate` | 幂等校验并写入报告 |
| `POST` | `/api/scripts/{script_key}/versions/{version}/transition` | 只允许合法生命周期转移并记录操作者 |

普通业务账号只读。管理员可以注册和停用版本，但不能绕过验证将 `draft` 直接设为 `active`。

## 5. 工作流定义、冻结与条件路由

### 5.1 对象模型

#### `WorkflowDefinition`

稳定的工作流键和业务语义，例如 `label.incremental.production`、`label.stock.regression`。字段包括名称、用途、Owner、允许类目、状态和审计信息。它不携带可变执行配置。

#### `WorkflowVersion`

不可变的工作流合同，字段至少包括：

- `workflow_definition_id`、`version`、`status`、`workflow_schema_version`。
- `step_manifest_json`：标准步骤、脚本版本、输入映射、输出映射和参数。
- `edge_manifest_json`：依赖关系和条件路由。
- `input_schema_json`、`output_schema_json`、`resource_policy_json`。
- `canonical_hash`、`validation_report_json`、`created_by`、时间戳。

工作流版本可以被 `draft`、`validating`、`active`、`deprecated`、`retired`、`blocked` 生命周期管理。版本一旦被运行引用，所有 manifest 和 hash 不可变。

### 5.2 标准步骤

步骤类型固定为：

```text
connector
identity
transform
model_call
rule_eval
human_task
release_gate
projection
reconcile
feedback
```

步骤只引用已注册 `ScriptVersion`，不包含源码。类目专有能力通过 `CategoryProfile` 选择标准步骤和受控参数快插快拔，不能复制队列、模型中心、纠偏、发布或投影模块。

### 5.3 条件路由 DSL

条件只允许声明式 JSON DSL：

```json
{
  "all": [
    {"path": "steps.identity.output.verified", "op": "eq", "value": true},
    {"path": "steps.model_call.output.confidence", "op": "gte", "value": 0.8}
  ]
}
```

允许的操作为 `eq`、`neq`、`in`、`not_in`、`exists`、`gte`、`gt`、`lte`、`lt`、`all`、`any`、`not`。禁止 Python/JavaScript 表达式、SQL、网络请求、动态字段名、循环和递归调用。校验器必须拒绝未知操作、未知步骤引用、类型不兼容、超过最大嵌套深度和非 DAG 依赖。

### 5.4 发布前验证

工作流版本进入 `active` 前必须通过：

1. 所有步骤类型和脚本版本存在且可用。
2. 输入、输出 Schema 能沿依赖边兼容；必需字段有来源。
3. 依赖图无环、无孤立步骤；入口和终点唯一且可解释。
4. 条件引用只指向前序步骤已声明的输出。
5. 超时、最大尝试次数、并发和权限均在平台允许范围内。
6. 五队列中使用的队列类合法，不能声明新队列。
7. 类目 Profile、字段合同、模型/机制版本引用已冻结或可在运行创建时冻结。
8. fixture dry-run 能生成最小合法输出并完成一次回归。

失败报告必须给出稳定的 `path + error_code + message`，例如 `steps[2].script_version`、`edge[1].condition.path`，不能只返回“工作流无效”。

### 5.5 工作流 API（设计合同）

| 方法 | 路径 | 规则 |
|---|---|---|
| `GET` | `/api/workflows` | 返回定义和可用版本摘要 |
| `POST` | `/api/workflows` | 创建稳定定义 |
| `POST` | `/api/workflows/{workflow_key}/versions` | 创建不可变 draft |
| `POST` | `/api/workflows/{workflow_key}/versions/{version}/validate` | 校验并保存报告 |
| `POST` | `/api/workflows/{workflow_key}/versions/{version}/transition` | 合法状态转移，active 必须通过校验 |
| `GET` | `/api/workflows/{workflow_key}/versions/{version}` | 返回 manifest、hash、校验和引用摘要 |

### 5.6 运行时冻结快照

创建 `ProductionRun` 时一次性冻结：

- workflow version 与 `workflow_canonical_hash`；
- 所有 script version、artifact hash 和执行参数；
- 类目 Profile、字段需求合同、Schema/词表版本；
- 主模型/调优模型、Prompt、V3 合同、规则和机制版本；
- 输入资产/素材包版本与身份验证证据；
- queue policy version、队列类、租约/重试策略；
- 运行创建者、原因、请求幂等键和环境标识。

快照 canonical JSON 计算 `snapshot_hash` 并持久化。运行中禁止读取“最新版本”替换快照；版本漂移只形成新的运行或明确的恢复失败。

## 6. 通用运行时

### 6.1 `ProductionRun`

建议表名 `production_runs`，与现有 `evaluation_production_runs` 并存。字段至少包括：

- `run_key`、`idempotency_key`、`source_type`、`source_id`；同一请求幂等回读，不创建重复运行。
- `workflow_definition_id`、`workflow_version_id`、`snapshot_json`、`snapshot_hash`。
- `category_key`、`queue_class`、`status`、`current_step_key`、`blockers_json`。
- `requested_by`、`owner`、`reason`、`environment`（本阶段固定 `dry_run`）。
- `total_steps`、`completed_steps`、`failed_steps`、`last_checkpoint_id`。
- `lease_owner`、`lease_token`、`lease_expires_at`、`heartbeat_at`。
- `attempt_count`、`next_retry_at`、`error_code`、`error_message`。
- `created_at`、`started_at`、`finished_at`、`updated_at`。

状态为：

```text
planned → queued → running → paused → succeeded
                         ↘ failed → retryable
                         ↘ blocked
                         ↘ canceled
```

`retryable` 只能由系统根据策略进入恢复队列或由授权操作员明确重试；`blocked` 必须有可操作原因；`succeeded`、`failed`、`canceled` 为终态，不原地重开，重跑创建新 `run_key` 并保留 `source_run_id`。

### 6.2 `StepAttempt`

建议表名 `production_step_attempts`。每一个工作流步骤至少拥有一个逻辑步骤记录和一个或多个技术尝试，字段包括：

- `run_id`、`step_key`、`step_type`、`sequence`、`script_version_id`。
- `status`：`pending`、`leased`、`running`、`succeeded`、`retryable`、`failed`、`blocked`、`skipped`。
- `attempt_no`、`idempotency_key`、`input_manifest_json`、`input_hash`。
- `output_manifest_json`、`output_hash`、`checkpoint_json`、`checkpoint_hash`。
- `lease_owner`、`lease_token`、`lease_expires_at`、`heartbeat_at`。
- `started_at`、`finished_at`、`last_error_code`、`last_error_message`、时间戳。

唯一键为 `run_id + step_key + attempt_no`；逻辑步骤的幂等键由 `run_key + step_key + input_hash` 确定性生成。重复领取同一个逻辑步骤必须回读已成功的 output/checkpoint，不再重复执行。

### 6.3 调度适配

运行时创建一个轻量 dispatch item，携带 `step_attempt_id`、`queue_class`、优先级和可领取时间；调度器继续使用现有 `QueuePolicy` 和 `QueueSchedulerState`。领取事务必须同时：

1. 锁定可领取 dispatch item；
2. 校验运行和步骤状态、租约、熔断、重试时间；
3. 写入租约 token、owner、过期时间和调度状态；
4. 持久化调度器 deficit/dispatch_count；
5. 提交后才允许执行器运行。

模型调用、fixture 执行和人工等待不得持有数据库写事务。短事务只负责冻结、领取、心跳、检查点和结果落库。

### 6.4 租约、超时与恢复

- 租约默认 5 分钟，平台允许范围 30 秒至 1 小时；心跳在租约剩余一半时续租。
- 超过 `timeout_seconds` 先记录 `STEP_TIMEOUT`，按重试策略进入 `recovery`，不直接丢失检查点。
- 租约过期由恢复扫描回收；旧 owner 使用旧 token 写入时必须被拒绝，不得覆盖新 owner 的输出。
- 重试次数达到上限转 `failed` 或 `blocked`，错误码和最后 checkpoint 保留。
- 恢复优先从最后一个成功 checkpoint 继续；已成功步骤不重跑，失败步骤及其下游按依赖规则重排。
- 进程重启后，未完成但租约已过期的步骤进入恢复队列；仍有效的租约不被第二个 worker 抢占。

### 6.5 检查点与幂等证据

每个成功步骤都必须写入：输入 hash、脚本/工作流版本、输出 hash、执行器版本、完成时间、worker id 和结果摘要。检查点是事实证据，不是可执行代码。恢复时先比较 input hash 和 checkpoint hash；不一致时阻塞并要求新运行，禁止静默覆盖。

## 7. 首批确定性 fixture 执行器

首批只提供以下受控执行器：

- `fixture.identity`：复制并规范化输入身份，输出固定 Schema。
- `fixture.transform`：执行白名单字段映射、常量填充和排序，禁止任意表达式。
- `fixture.route`：按已验证条件 DSL 选择下一节点。
- `fixture.noop`：写入稳定的开始/结束证据，不产生外部副作用。
- `fixture.fail_once`：仅用于测试重试和恢复，在第一次技术尝试按声明错误失败，后续尝试按固定输出成功。

fixture 输入、输出和错误均由 manifest 冻结，使用确定性 JSON 排序和 SHA-256。不得访问网络、文件系统外部路径、环境变量中的秘密或真实模型服务。

## 8. 前端运行中心

### 8.1 一级页保留信息

在现有运行中心保留五队列、并发、熔断、重试和恢复摘要，新增当前通用运行列表的最少字段：

- 运行 ID、来源（增量/存量/评测/回归）、类目和责任人；
- 工作流版本、脚本版本摘要、快照 hash；
- 当前步骤、状态、队列和进度；
- 最后成功检查点、下一次重试时间、阻塞原因；
- 可执行动作：查看证据、暂停、恢复、重试或取消（按权限和状态显示）。

Prompt 全文、V3 JSON、完整脚本 manifest、输入输出 Schema、重试历史和检查点内容进入二级抽屉，不在一级页面平铺。

### 8.2 运行详情抽屉

详情抽屉按“运行摘要 → 步骤时间线 → 版本快照 → 检查点/错误 → 恢复动作”分组。每个步骤直接说明：执行了什么、使用哪个脚本版本、输入/输出 hash、是否命中缓存、失败后系统将自动做什么。对于 `blocked`，必须显示可操作原因和责任人，不让用户自行寻找配置页。

### 8.3 权限

- 观察者：只读运行、版本、检查点和错误。
- 操作员：暂停、恢复、重试和取消自己有权限范围内的 dry-run。
- 管理员：注册脚本/工作流版本、执行校验、状态转移和查看全局审计。
- 任何角色都不能从页面上传任意代码或直接执行外部命令。

## 9. 数据库与迁移 71

迁移 71 只新增运行时基础表和必要索引，采用“表不存在才创建、列不存在才添加、索引不存在才创建”的幂等模式：

1. `script_definitions`。
2. `script_versions`，包含状态、哈希、Schema、策略和校验报告。
3. `workflow_definitions`。
4. `workflow_versions`，包含 canonical manifest、hash 和校验报告。
5. `production_runs`。
6. `production_step_attempts`。
7. `runtime_dispatch_items`，作为既有五队列调度器的通用 dispatch 适配记录。
8. `runtime_audit_events`，记录注册、校验、状态转移、领取、恢复、暂停、取消和结果摘要，不保存秘密。

迁移不得删除或改写 `evaluation_production_runs`、`evaluation_jobs`、纠偏、机制发布和标签发布历史。外键使用 `RESTRICT` 保护版本和快照；审计事件和运行记录只追加，不通过级联删除清理事实证据。migration 版本落到 71 后，旧数据库可重复运行迁移而不生成重复表、列、索引或种子。

## 10. 测试与验收

### 10.1 后端专项

- 脚本生命周期合法转移、非法转移、哈希不可变和 blocked 版本拒绝。
- 工作流未知脚本、Schema 不兼容、未知条件路径、循环依赖、孤立步骤和第六队列全部拒绝。
- 同一幂等键重复创建运行只回读原运行；不同快照 hash 不得复用旧运行。
- 同一步骤并发领取只有一个有效租约；旧 token 写入被拒绝。
- `fail_once` 按固定策略重试，成功后不重复执行已成功步骤。
- 超时、租约过期、worker 重启和 recovery 队列恢复均保留最后 checkpoint。
- 调度适配使用既有五队列和 `QueueSchedulerState`，无第二套配额或调度状态。
- fixture 禁止网络、外部文件、秘密和任意代码执行。
- migration 71 在空库、当前库和重复运行三种场景通过，历史运行行数与内容不变。

### 10.2 前端与合同

- 运行中心显示五队列、工作流/脚本版本、checkpoint、阻塞和责任人。
- 详情抽屉承载高级配置，一级页不出现大段 manifest 平铺。
- 权限矩阵与按钮状态一致；blocked 状态给出下一步动作。
- 前端合同脚本、lint、build、桌面宽度 1440×900 与 1280×720 无横向溢出。

### 10.3 完成门禁

本阶段只有在后端全量测试、专项测试、前端合同/lint/build、迁移幂等、`git diff --check` 和本地 dry-run 端到端回执全部通过后，才可进入下一轮 Owner 评审。没有测试服部署、Codeup 推送或真实模型/数据库验收；这些动作属于后续独立授权。

## 11. 实施顺序（供下一阶段计划使用）

1. 先落 migration 71 与模型/Schema 校验纯函数，不接 worker。
2. 实现脚本注册和工作流版本 API、状态机与审计。
3. 实现 `ProductionRun`、`StepAttempt`、dispatch adapter、租约和检查点。
4. 接入 fixture 执行器和恢复扫描，复用现有 scheduler/worker。
5. 扩展运行中心和二级详情抽屉。
6. 运行专项、全量和前端验证，形成 dry-run 验收回执。

本顺序不改变当前冻结的 3D/SU 身份、字段合同、真实接入和双人工门边界；真实生产闭环仍以 2026-09-30 的独立验收合同为准。

