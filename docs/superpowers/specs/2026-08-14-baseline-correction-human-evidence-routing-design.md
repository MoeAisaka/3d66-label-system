# 基准纠偏人工证据与机制路由设计

## 背景

当前基准纠偏只冻结人工期望等级、模型预测等级、分数、置信度和模型自己的等级解释。
运营在评测详情中提交的节点纠偏、原因、可定位证据、审核备注与字段纠错没有进入自动候选
生成输入，导致系统只能从混淆方向猜测应该调整调用 A、调用 B 还是 V3 规则。

## 已冻结目标

补齐以下链路：

1. 基准纠偏创建时，从每个偏差条目绑定的 `EvaluationResult` 冻结节点纠偏历史和人工审核事实；
2. 节点纠偏保留旧值、新值、原因、证据、纠偏人、时间与是否触发下游重算；
3. 人工审核保留审核记录、审核备注、字段纠错、审核阶段、审核修订号和最终审核身份；
4. 自动纠偏记录必须标记为 `automatic`，不计入人工证据覆盖率或路由判断；
5. 确定性报告保留逐样本人工证据摘要、覆盖率和受影响层；
6. 调优模型仍只接收文本结构化输入，不在本批增加图片多模态；
7. 纯调用 A 人工证据只允许生成 A 提示词候选，纯调用 B 人工证据只允许生成 B 提示词候选；
8. 混合层、仅 V3 或没有可路由人工证据时，允许在现有 A/B 合法范围内选择，但必须继续输出完整统一 revision 并回归。

## 文件边界

只修改：

- `backend/app/baseline_regression.py`
- `backend/app/baseline_correction_orchestration.py`
- 基准纠偏专项测试
- 本规格与实施计划

明确不修改 `models.py`、`main.py`、数据库迁移、前端、3D 只读接入、影子消费、部署与运行配置。
这避免与并行分支 `codex/3d-shadow-consumption-mvp-v1` 的 `main.py`、`models.py`、迁移等改动重叠。

## 冻结输入合同

`baseline-correction-input-v2` 的每个条目新增 `correction_context`：

- `evaluation_id`、`review_stage`、`review_revision`、`final_review_id`；
- `node_corrections`：全部节点纠偏事件，每条显式标记 `source=human|automatic`；
- `human_reviews`：当前可见的审核记录及字段纠错；未完成盲审组的隐藏投票不进入输入；
- `human_evidence_count`、`automatic_evidence_count`；
- `affected_layers`：只由人工证据推导，固定为 `A`、`B`、`V3` 的有序子集。

节点路由固定为：

- 调用 A：`call_a_field`、`precheck_field`、`redline`、`track`；
- 调用 B：人工审核中 `target_type=dimension` 的维度纠错；
- V3：`dimension_rule`、`final_level`；
- 人工审核中 `target_type=key_field` 属于调用 A；
- 旧审核若只有人工最终等级、没有可识别字段纠错，归入 V3。

自动节点纠偏的判定以已有机器元数据为准：存在 `corrector_confidence` 或
`corrector_policy` 即为自动来源；不能通过纠偏人名称猜测来源。

## 报告与候选路由

`baseline-correction-report-v2` 新增：

- `evidence_summary`：样本数、人工证据覆盖样本数、覆盖率、人工/自动证据数、受影响层计数；
- `sample_evidence`：逐样本人工节点纠偏与审核摘要，自动证据只记录排除数量；
- `candidate_routing`：`affected_layers`、`allowed_prompt_stages`、可选的
  `required_prompt_stage` 与 `policy=human_evidence_only`。

路由规则：

- `affected_layers == [A]`：只允许 A；
- `affected_layers == [B]`：只允许 B；
- 其他情况：允许 A 或 B。

候选生成后的服务端校验是最终门禁。即使调优模型忽略约束，纯 A 返回 B 或纯 B 返回 A
也必须以稳定错误码失败，不能创建错误阶段的草稿提示词或候选回归。

## 兼容与失败策略

- 旧冻结输入没有 `correction_context` 时按“无人工证据”处理，保持 A/B 兼容选择；
- 节点纠偏历史或审核字段 JSON 损坏时，创建纠偏分析关闭失败，不静默丢证据；
- 没有绑定 `EvaluationResult` 的历史条目保留空证据上下文，不伪造人工判断；
- 现有候选 revision、最终人工启用门、回归比较和发布边界不变。

## 验收

- 人工节点修改、原因、证据、审核备注和字段纠错出现在冻结输入及调优模型文本输入中；
- 自动纠偏事件可审计但不计入人工覆盖率或受影响层；
- 纯 A/纯 B 错阶段候选被服务端拒绝；混合或仅 V3 候选仍可合法生成；
- 不修改并行分支占用文件；专项、相关回归、后端全量与 `git diff --check` 通过。
