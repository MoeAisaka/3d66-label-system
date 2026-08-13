# 基准回归自动纠偏闭环设计

## 背景与根因

当前“启动纠偏分析”只执行本地确定性统计，随后把任务硬编码为
`awaiting_confirmation`，并要求人工另行创建候选。数据库状态约束、API
返回类型和前端页面都围绕这个中间人工门设计，因此用户无法从页面判断下一步，
也无法得到“点击一次后自动生成候选并完成回归”的产品闭环。

## 已冻结目标

用户点击一次“启动纠偏分析”后，系统自动完成：

1. 冻结人工纠偏样本并分析差异；
2. 调用当前类目绑定的调优模型，生成结构化机制改动；
3. 校验改动并创建一个不可变的统一机制候选 revision；
4. 使用该候选 revision 自动创建并运行候选回归；
5. 汇总基线与候选指标、退化项、风险和 AI 建议；
6. 只在最后进入人工“启用／拒绝”决策门。

提示词、维度、扣分/加分规则和等级边界属于同一个机制候选包，不允许拆成多个
候选要求人工拼装。批准候选前不得改变现役投影，且不得自动批准或自动发布。

## 非目标

- 不在测试或验收中调用真实付费模型；真实调用只由运行时配置决定。
- 不自动批准、自动切换生产机制或自动发布标签事实。
- 不改变机制发布轴与标签事实发布轴相互独立的边界。
- 不顺带修改存量重跑、下游事实发布或移动端能力。
- 不把本次改动并入尚未提交的“运行配置抽屉布局”修复。

## 状态机

`BaselineCorrectionRun.status` 使用以下状态：

- `processing`：编排正在执行；`stage` 标识 `analysis`、
  `candidate_generation`、`candidate_validation` 或 `regression`。
- `awaiting_decision`：候选回归已形成最终结论，等待人工启用或拒绝。
- `approved`：人工批准并完成现役机制投影原子切换。
- `rejected`：人工拒绝，候选与证据保留但不生效。
- `failed`：任一自动阶段失败，可按冻结输入重试；三次后停止自动重试。

旧数据中的 `awaiting_confirmation` 在迁移中转换为 `failed`，错误码为
`LEGACY_CORRECTION_INCOMPLETE`。旧报告仍保留，用户可显式重试以进入新链路。

状态和进度必须持久化。刷新页面后从服务端恢复，不依赖前端内存。

## 数据模型

在 `baseline_correction_runs` 增加：

- `stage`：当前自动阶段；
- `candidate_revision_id`：不可变统一机制候选；
- `regression_run_id`：候选回归任务；
- `decision`、`decided_by`、`decided_at`、`decision_note`：最终人工结论；
- `orchestration_json`：阶段时间、调优模型快照、候选差异摘要和回归绑定证据。

候选本体继续使用 `CategoryEvaluationV3Revision`。其 contract、classification map、
subcategory dimensions 与规则镜像全部由既有 validator 校验并冻结。现役
`CategoryEvaluationV3Config` 在人工批准前不发生变化。

候选回归继续使用既有基准回归任务模型，但所有候选评测 Job 的
`category_profile_snapshot_json.v3_authoritative_bundle` 必须绑定候选 revision；worker
已经优先读取这个冻结 bundle，因此不需要把候选写进现役投影。

## 自动编排边界

新增后端编排服务，依赖三个窄接口：

1. `CorrectionMechanismGenerator.generate(...)`：接收冻结样本、现役 revision 和调优
   模型快照，返回完整 `RevisionArtifacts` 与改动摘要；
2. `create_candidate_revision(...)`：复用既有不可变 revision 能力；
3. `create_candidate_baseline_run(...)`：复用基准集和评测 Job，候选 Job 显式冻结
   candidate revision bundle。

测试通过确定性 generator 注入完整候选，不进行网络调用。运行时 generator 只解析
`role=tuning` 且 active 的模型注册项；配置缺失、额度不可用或返回结构非法时进入
`failed`，并返回明确阶段、错误码和“重新执行”操作，不要求人工编辑机制。

候选回归是异步事实：创建后纠偏任务保持 `processing/regression`。读取纠偏任务或
worker 完成评测时调用刷新函数；回归达到终态后自动生成最终对比报告并进入
`awaiting_decision`。

## 人工决策与发布

新增 `POST /api/baseline-corrections/{id}/decision`：

- `decision=approved` 仅接受 `awaiting_decision` 且回归建议通过的任务；
- 批准在同一事务中校验候选仍是当前现役 revision 的后代、重新校验冻结产物、把
  现役 projection 原子切换到候选并追加审计记录；
- `decision=rejected` 只冻结拒绝结论，不修改 projection；
- 相同结论和备注幂等，冲突结论返回 409；
- 需要管理员权限，自动编排不得调用该接口。

现有 `MechanismRelease` 绑定 `EvaluationPackage`，不能伪造包身份来承载 revision。
本批次以 revision 状态、projection 指针和审计事件记录批准事实；把统一 revision
正式纳入 `MechanismRelease` 包发布模型留给后续已冻结的平台发布改造，不影响本次
人工门和原子切换验收。

## 前端交互

一级页面只保留：

- 选择偏差样本；
- “启动纠偏分析”按钮；
- 一条自动流程进度线；
- 最终候选对比摘要和“启用候选／拒绝候选”按钮；
- 失败阶段、原因和“重新执行”按钮。

不再展示“提示词或维度必须由人工确认后另建候选”或任何中间配置要求。候选差异、
详细指标和风险证据进入二级详情区域。按钮文案明确说明“启用会切换现役机制”，并在
提交前使用现有确认交互；拒绝不删除候选。

## 错误处理与回退

- 每阶段先写状态，再执行；失败时保留前序不可变产物，重试按绑定 ID 幂等复用。
- 候选生成失败：无 candidate revision，重试 generation。
- 候选回归创建失败：保留 candidate revision，重试 validation/regression。
- 回归任务失败：不自动新建候选，重试同一候选回归编排。
- 批准时发现现役 projection 漂移：409，候选保持未发布，要求重新执行纠偏分析。
- 回退代码时，迁移后的新字段保留；旧版本只会忽略新增列。已批准 revision 可按
  projection 指针和审计记录人工恢复到上一 revision，但本批次不新增自动回滚入口。

## 验收

- 单击启动后，没有人工中间配置步骤；状态最终到 `awaiting_decision` 或可操作失败。
- 候选 revision 是完整、不可变、可追溯的统一机制包，现役 projection 在批准前不变。
- 候选评测冻结候选 revision，不读取运行时现役机制替代候选。
- 回归未完成或建议不通过时不能批准。
- 批准和拒绝都必须由人工接口触发，且结论不可冲突修改。
- 刷新页面可恢复阶段、候选、回归和最终结论。
- 后端单元/API/迁移测试、前端源码合同、lint、build 和 `git diff --check` 通过。
