# ADR-0011：P0-E E3 金丝雀持久化与认证 API

- 状态：Accepted
- 日期：2026-07-28

## 背景

ADR-0010 定义了无 I/O 的 E2 金丝雀状态机。E2 快照只能存在于单次
进程调用中，尚不能在认证用户的多次请求之间保存状态，也不能防止两个
请求基于同一旧快照互相覆盖。

E3 只为 E2 编排快照增加本地持久化和认证 API。它不执行 E0/E1 的真实
下载，不调用模型，不创建 `Asset` 或 `EvaluationResult`，不形成 Gold，
也不产生发布效果。

## 决策

### 持久化事实源

新增 `CanaryRun`，以稳定 `run_id` 为主键和唯一标识，保存：

- 可选显示名称、当前状态；
- 规范 JSON 的计划、累积证据和完整当前快照；
- 当前 `snapshot_fingerprint`；
- 创建者、创建时间和更新时间。

迁移 17 创建 `canary_runs` 表、状态与 JSON 检查约束，以及状态、指纹和
更新时间索引。服务写入前使用 UTF-8、键排序、无多余空白的规范 JSON；
数据库约束验证状态和 JSON 结构；读取时重新计算指纹并核对完整快照与
五项 False 不变量，无法验证时 fail-closed。

### API 边界

`backend/app/p0e_canary_api.py` 独立承载 DTO、持久化服务和路由工厂；
`main.py` 只接入路由，并复用现有登录用户依赖。

全部端点均需认证：

- `POST /api/canary-runs`
- `GET /api/canary-runs`
- `GET /api/canary-runs/{run_id}`
- `POST /api/canary-runs/{run_id}/transitions/{transition}`
- `POST /api/canary-runs/{run_id}/cancel`
- `POST /api/canary-runs/{run_id}/fail`

创建请求只允许精确域值 `3D`、30～50 的目标数量、非空 seed 和可选显示
名称。转换请求只允许期望快照指纹与当前门禁证据；客户端不能提交运行
状态、当前指纹或运行级不变量。所有状态推进继续调用 ADR-0010 的纯函数，
API 层不复制或放宽门禁。

### 幂等与并发

- 相同规范计划产生相同 `run_id`；重复创建返回已有运行。
- 同一 `run_id` 对应的计划或显示名称发生漂移时返回 409。
- 更新使用 `run_id + expected_snapshot_fingerprint` 条件写入；条件未命中
  时不覆盖当前值并返回 409，禁止 last-write-wins。
- 已到达目标状态时，只有能重放为相同规范证据和相同指纹的请求才幂等
  返回；不同证据返回 409。
- 终止态不可恢复。相同取消或失败原因可幂等重试，不同原因冲突。

### 错误与脱敏

E2 `CanaryRunError` 映射为 422，并保留 `code`、`message`、
`current_state`、`attempted_transition`、`retryable` 五个稳定字段。不存在
的运行返回明确 404；幂等漂移和陈旧指纹返回 409。

所有将进入持久化或响应的证据递归检查 HTTP(S) URL；含 userinfo、query
或 fragment 时拒绝。响应固定包含五项 False 不变量、时间和原始创建者，
并在读取边界再次脱敏，避免返回凭证型 URL。

## 后果

- E2 状态可以跨认证请求持久保存，并能安全处理重复请求和并发更新。
- 新表独立于业务素材与评测表；E3 写入不改变 `Asset 1:N
  EvaluationResult`、`evaluation_id` 审核、Gold 或发布合同。
- 当前 API 没有触发 E0/E1 的真实执行器；证据仍由调用者显式提交并由
  E2 纯函数验证。
- 本阶段没有前端接线、真实数据导入、模型调用、Mac 部署、Gold 形成或
  发布。

## 不可破坏约束

- 不得绕过 `expected_snapshot_fingerprint` 做无条件覆盖。
- 不得由客户端指定状态、当前指纹或运行级五项不变量。
- 不得把重复请求当作覆盖入口；规范证据不同必须冲突。
- 不得把 `failed`、`cancelled` 或 `human_review_ready` 恢复为可推进状态。
- 不得从本 API 触发真实下载、模型、业务 `Asset`/`EvaluationResult`、
  Gold 或发布效果。
