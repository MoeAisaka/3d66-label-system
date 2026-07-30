# ADR-0018：ADR-0016 真实执行器与分阶段启用

- 状态：Accepted
- 日期：2026-07-30

## 背景

ADR-0016 已接受自动优化、不可变生产回流和三模型横评的安全状态机，但当时
真实执行层尚未接通：自动优化只能停在 `awaiting_executor`，横评只有确定性
`test` 模式，生产回流只有浏览器会话保护的接收骨架。真实启用需要复用现有
模型配置和系统凭据体系，同时把 usage、预算、冻结样本、质量门、租约恢复、
机器鉴权和人工发布门禁变成服务端不可绕过的合同。

## 决策

### 1. 真实优化复用现有优化模型配置

- 自动优化只使用现有 `OptimizerConfig`、`DoubaoClient` 和诊断—合成链路，
  不建立第二套模型客户端、密钥表或厂商专用运行时。
- `OptimizerConfig` 增加输入/输出每百万 token 的 micros 计价和单次输入上限。
  缺少密钥、输入上限或任一非零计价时，真实 adapter 视为未配置。
- 运行状态为 `awaiting_executor -> processing -> succeeded/failed`。调用前按
  管理员上限保守预留预算；调用后只接受模型返回的 input/output/total usage，
  并按真实 usage 原子结算。
- usage 缺失、非法或实际成本超过预留均 fail-closed。失败只保存稳定错误码，
  不保存 URL、请求头、密钥、原始异常或完整上游响应。
- 候选始终创建新的 `PromptVersion(status=draft)`，并绑定锁定黄金样本中的
  `target_error / stable_control / blind_holdout` 三角色配对回归。候选、策略包、
  回归和验证任务在数据库保存点内原子创建；任何路径都不自动发布。

### 2. 日预算采用原子预留与结算

- `AutomationBudgetDay` 按 UTC 日期记录 `reserved_micros` 和 `spent_micros`。
- worker 在模型调用前使用条件 UPDATE 预留；并发预留总额不得超过
  `daily_budget_micros`。预算不足时在调用前返回 `budget_blocked`。
- 成功按真实 usage 结算；模型调用已开始但无法取得可信 usage 时，按预留上限
  计费，避免把未知成本当零。
- 案例租约使用 owner、token 和过期时间。并发 worker 只能有一个成功 claim；
  过期租约恢复会释放案例、将原运行标记失败并保守结算一次，随后按最大尝试
  次数和指数退避决定是否重试。

### 3. 横评真实模式冻结执行合同

- `execution_mode` 新增 `real`，但请求默认仍为 `test`。创建和运行真实横评只
  允许管理员。
- 每个 Sol/Terra/Luna 逻辑变体必须绑定现有 `ModelConfig.id`。endpoint、key、
  model 全部从服务端模型配置读取；key 只以 Keychain/DPAPI 引用持久化，不进入
  API、日志、错误状态或冻结快照。
- 多模型配置继续使用现有系统凭据实现。macOS Keychain/Windows DPAPI 账户只
  扩展严格的 `model-config-<数字ID>` 命名，不接受任意账户名。
- 创建时冻结 cohort、素材文件 SHA-256、人工真值、Prompt 全文、Rubric、
  Engine、AgentPlan、StrategyBundle、非秘密模型设置、transport fingerprint、
  计价、预测成本和单轮上限。
- 真实运行首先检查人工批准的质量门，然后检查实验快照哈希、bundle/Prompt/
  AgentPlan/cohort 版本、数据库素材哈希、文件哈希和模型配置快照。任何漂移均
  在执行器调用前拒绝。
- 单轮上限必须为正数，预测成本不得超过上限。每次 A/B 调用返回后立即校验
  usage 和累计真实成本；usage 缺失时不得继续 B 阶段、后续样本或后续模型。
- 横评选择仍是 `quality-gate-first`，结论只提供人工候选建议，不自动切换生产。

### 4. 生产回流使用独立机器 token

- `POST /api/production-feedback-events` 不接受浏览器会话，必须提供服务端环境
  变量 `PRODUCTION_FEEDBACK_TOKEN` 对应的 Bearer token，并使用恒定时间比较。
- 未配置服务端 token 返回 `503`；缺失或错误 token 返回 `401`。
- `event_id` 是幂等键。schema、类型、来源、发生时间和 payload 全部一致时可
  重放；同键任一内容不同返回 `409`，不覆盖不可变事件。
- 当前认证合同不增加未验证的自定义签名。部署链路使用 HTTPS，token 轮换按
  维护窗口执行。

## 启用 Runbook

### 1. dry-run 观察

1. 保持 `enabled=false`、`dry_run=true`、`daily_budget_micros=0`。
2. 配置优化模型密钥、输入上限和计价，但不打开消费者。
3. 接入生产回流 token，观察事件幂等、P0/P1 优先级、组批阈值、审计和队列量。
4. 手工启用消费者但继续 `dry_run=true`，确认只生成计划、不产生模型费用。

### 2. 小预算放开优化执行

1. 锁定包含三角色的黄金样本，并验证基础 StrategyBundle 可解析。
2. 设置很小的正数日预算、短并合理的候选上限，保持最大尝试次数保守。
3. 人工确认后设置 `dry_run=false`；观察 usage、实际成本、失败码、退避、租约和
   候选草稿/配对回归完整性。
4. 候选只能由人工完成回归审批和发布，自动运行成功不等于发布门禁通过。

### 3. 横评 real 单轮成本上限

1. 为三个模型分别创建服务端配置，核对 endpoint、model、密钥、输入上限和
   双向计价，显式打开 `benchmark_enabled`。
2. 冻结同一 cohort 和 StrategyBundle，人工批准质量门。
3. 设置覆盖保守预测但可接受的单轮上限，二次确认后运行一次 `real`。
4. usage 缺失、预测超限、快照漂移或实际累计达到上限时停止，不扩大调用。

### 4. 分阶段扩大、回滚与停机

- 只有连续小预算运行的质量、成本、租约和回归证据稳定后，才逐步增加日预算或
  cohort；每次只改变一个变量并记录管理员与原因。
- 优化紧急停机：设置 `enabled=false`。费用紧急停机：同时将
  `daily_budget_micros=0` 并保持 `dry_run=true`。
- 横评停机：不创建或运行新的 `real` 实验，并关闭相关模型配置的
  `benchmark_enabled`。已冻结实验不修改，保留审计证据。
- 回滚是关闭执行开关和恢复上一已发布版本的人工动作，不删除运行、候选、预算、
  回流事件或横评历史。

### 5. 监控、审计、恢复与密钥轮换

- 监控：当日 spent/reserved/remaining、usage 缺失、预算阻断、429/5xx、租约
  过期、最大尝试耗尽、回流 401/409/422/503、横评冻结漂移和单轮上限阻断。
- 审计：保存执行者、策略修订、模型配置 ID、Prompt/Bundle/AgentPlan 版本、
  快照哈希、预测/实际成本、usage、候选 ID 和回归 ID；不保存秘密或原始异常。
- 租约恢复：先确认原 worker 已停止或租约确已过期，再允许下一 worker 重试；
  未知 usage 的过期运行按预留上限保守结算。
- 密钥轮换：暂停对应执行入口，更新系统凭据，验证安全状态和连接，再创建新的
  冻结实验。旧冻结实验因配置 fingerprint/更新时间变化而拒绝运行，应重新创建。

## 后果

- 默认部署和旧数据库升级不会自动产生费用；真实能力只能由管理员逐项配置。
- 真实 usage 和预算预留提高了成本可信度，但未知 usage 会保守占用预算，可能
  需要管理员调查而不能自动归零。
- 冻结快照校验降低了误用变化样本或配置的风险，但密钥轮换、端点调整和计价
  调整后必须创建新的横评实验。
- 多模型密钥继续依赖本机 Keychain/DPAPI，换电脑后必须重新填写。

## 不可破坏约束

1. 默认始终为 `enabled=false / dry_run=true / daily_budget_micros=0 / execution_mode=test`。
2. 缺失计价、输入上限、usage、质量门、冻结证据或预算上限时必须 fail-closed。
3. key 不得进入业务表明文、API、日志、错误、快照、测试数据或文档。
4. 自动候选只能新建草稿并绑定三角色配对回归，绝不自动发布。
5. 同一案例同一时刻只能由一个有效租约执行；预算预留和结算不得重复。
6. `real` 横评只能执行创建时冻结且运行前复核一致的样本和配置。
7. 生产回流不得复用浏览器会话或管理员口令，且同键异载荷必须返回 `409`。
