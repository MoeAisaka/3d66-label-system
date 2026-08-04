# ADR-0034：v3 规则扣分、节点纠偏与媒介开关

日期：2026-08-04
状态：已实施

## 决策

新评测不再让多模态模型为维度打 1–5 分。调用 B 只判定每个维度命中了
哪些冻结扣分规则，并为每条命中返回置信度和独立中文证据。服务端验证
`rule_id`，按合同扣分值累加，再用冻结权重完成确定性聚合。

- 维度分：`max(0, 100 - Σ命中规则扣分)`。
- 旧合同没有 `deduction_rules` 时，仍走 `grade_points` 兼容路径；该字段不删除。
- 调用 B 供应商失败时，主观节点按空命中返回维度满分，但必须记 warning 并
  进入人工复核；合同损坏仍 fail-closed。
- 媒介降权由 `media_type_penalty.enabled` 控制；关闭时保留扣分配置，聚合节点
  记为 `media_skipped` 且 penalty=0。
- 新增只追加的 `NodeCorrection` 历史。`correct-node` 用 `old_value` 做冲突检测，
  用 `correction_key` 保证重试幂等，并基于结果中的冻结 v3 上下文重放下游节点。
- 完整流水线为调用 A → 规则调用 B → 聚合；已定性素材可调用简易流水线，
  直接重放冻结预检和规则命中，不再调用模型。

## 数据与迁移

`category_evaluation_v3_configs` 增加规则镜像和媒介开关，`evaluation_results` 增加
节点纠偏历史。数据升级脚本幂等：灵感图保持 active/媒介开，三个老类目保持
draft/媒介关。只更新四条 v3 配置，旧 `EvaluationResult` 不回溯重算。

## 界面

配置页延续现有高密度白底运营工具风格，不增依赖。每个维度可编辑规则标识、
中文描述、扣分值和标签，并显示扣分上限。媒介开关关闭时数值表格置灰但不清空。

## 可追溯性

结果保存调用 B 原始响应、规则命中输出、聚合步骤、引擎版本、合同 revision 和
冻结 v3 context。节点纠偏只追加事件，不篡改原始模型响应。

## 实践核验

2026-08-04 只读核验 FastAPI、Pydantic、Alembic 官方 GitHub 主干及 FastAPI 官方
full-stack template。采用其明确请求模型、依赖注入权限、严格输入验证、增量迁移与
可回放测试做法；未引入 Alembic，因仓库已有编号迁移器，换框架会扩大生产风险。
普通仓库的 `correction_history_json` 搜索结果缺乏成熟度证据，未盲目照搬。

核验快照：FastAPI `153c54c91196`、Pydantic `c82f870d4cb6`、Alembic
`7a4a9b73ccc5`、FastAPI full-stack template `750d3d0bc6df`（均为 2026-08-04
访问时官方主干最新提交）。
