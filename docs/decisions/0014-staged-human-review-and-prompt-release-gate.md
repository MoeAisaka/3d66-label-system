# ADR-0014：分阶段人工审核、历史纠偏预览与提示词发布前门禁

- 状态：Accepted
- 日期：2026-07-29

## 背景

原有人工审核只有一个共享队列。`HumanReview` 虽然允许追加记录，但系统没有
区分初审、二审与仲裁，也没有乐观锁；前端和回归逻辑通常直接采用最后一条审核，
可能把尚未完成的多轮结论误当成正式真值。

历史 `已处理样本3d&SU` 已由 Owner 确认为人工高置信纠偏来源，但五个工作簿
缺少当前八维人工分数、审核阶段与运行版本。它们适合生成纠偏候选和分层样本计划，
不适合静默写入业务数据库或直接形成 Gold。

提示词优化已经能够生成候选文本，配对回归也已存在，但二者没有不可覆盖的绑定。
旧发布接口仍可能先发布、再创建普通黄金回归，不能证明优化候选在发布前已经完成
三角色配对验证与人工批准。

## 决策

### 分阶段人工审核

- `EvaluationResult.review_stage` 为
  `initial / secondary / arbitration / completed`。
- `EvaluationResult.review_revision` 从零开始，每次成功提交递增一；审核请求必须
  同时提交 `expected_stage` 和 `expected_review_revision`，陈旧快照返回 409。
- `HumanReview.stage` 记录本次事件所属阶段。审核记录只追加；数据库禁止更新
  已存在的审核事件。
- 初审普通 `approved` 可直接完成；高风险、L4/L5、`corrected` 或 `rejected`
  进入二审。
- 二审与初审的标准答案完全一致时完成，否则进入仲裁；仲裁提交后完成。
- API 同时返回 `review_stage`、`review_revision`、`review_history` 和
  `review_truth_status`。流程未完成时可展示最新暂定答案，但不得标为 Gold。
- 回归真值只接受已完成审核链的最终事件。迁移前的 `approved/corrected` 历史
  记录由迁移显式回填为 `completed`；其他结果回填为 `initial`。

### 历史纠偏安全预览

- `POST /api/historical-corrections/preview` 只接受认证后的 multipart XLSX，
  复用 P0-E 受限 ZIP/XML 解析器。
- 入口只生成 `files / summary / items` 预览，不联网、不下载、不调用模型、
  不写业务数据库、不形成 Gold。
- `评测等级` 只映射为 overall correction 候选；`hwreason` 原样保留。
- 文件名包含 `3Dreason` 的数据固定为 `reason_only`，不得把模型等级伪装成
  人工最终等级。
- 以稳定业务键和行哈希去重；默认按稳定哈希保留 20% `blind_holdout`。
  其余样本标为 `target_error`、`stable_control` 或 `reason_only`。
- 每条记录保留源文件、工作表、源行、文件哈希、行哈希及
  `owner_confirmed=true`。

### 优化候选物化、配对验证和发布

- 提示词优化完成仍只保存候选文本，不自动创建或发布版本。
- 显式调用
  `POST /api/prompt-optimizations/{run_id}/materialize-and-validate`
  才能把候选物化为新的 `PromptVersion(status=draft)`。
- `source_optimization_run_id` 对 PromptVersion 唯一，同一优化任务重放只返回
  原草稿和原配对回归，不覆盖版本。
- 请求必须显式提供基线 StrategyBundle、锁定黄金样本的三种角色及指标规则。
  候选 StrategyBundle 复用基线模型、A 提示词、Rubric、Engine、抽样策略和
  风险复核版本；模型配置发生漂移时拒绝把多变量变化伪装成提示词单变量回归。
- `GET /api/strategy-bundles?prompt_b_id=...` 提供基线发现；优化诊断的
  `sample_policy.sample_items` 提供可直接物化的 `{sample_item_id, role}`，
  盲测样本不暴露答案。
- 配对回归以 `trigger_prompt_id` 绑定候选草稿并冻结人工真值。批准接口仍只
  冻结批准结论，返回 `published=false`。
- 优化候选只有在绑定的配对回归
  `recommendation=pass AND approval_status=approved` 后才允许发布；其他情况
  返回 409。发布动作仍须由人显式调用。
- 旧库、种子已发布版本与非优化来源的历史提示词保持兼容，不因缺少新绑定而
  被迁移锁死。

## 后果

- 初审、二审与仲裁可以由独立工作台消费同一结果状态，但不能覆盖彼此证据。
- 尚未完成的审核链不会进入配对回归真值，降低错误 Gold 污染风险。
- 历史样本可以安全预览、去重和分层，但正式导入、图片冻结与 Gold 升级仍需
  后续显式执行器和人工确认。
- 优化候选从“文本建议”到“草稿—三角色回归—人工批准—发布”形成可审计链路。
- 新候选验证可能创建 validation 队列任务；本 ADR 和对应测试不会执行真实
  模型，运行任务仍受现有 Worker、凭据和队列门禁控制。

## 验证

- 分阶段审核覆盖普通直接完成、高风险二审一致、二审冲突转仲裁、陈旧请求
  409、审核历史追加及未完成真值不可用于回归。
- 历史预览覆盖字段映射、`3Dreason` 降级、稳定去重、认证及全部 False
  安全不变量。
- 优化候选覆盖显式物化、幂等、StrategyBundle 发现、未批准发布拒绝以及
  配对通过并人工批准后的显式发布。
- 迁移覆盖既有审核回填、修订计数、阶段校验和审核事件不可更新。

## 不可破坏约束

- 不得取消审核提交的 stage/revision 乐观锁，不得原地更新 HumanReview。
- 未完成审核链的最新答案只能作为暂定展示，不能形成 Gold 或正式回归真值。
- 历史预览不得写 Asset、SampleSet、EvaluationResult 或任何业务表。
- `3Dreason` 不得因为存在模型等级而生成虚假的人工最终等级。
- 优化候选不得覆盖旧 PromptVersion，不得绕过三角色配对回归和人工批准，
  不得在批准接口中自动发布。
