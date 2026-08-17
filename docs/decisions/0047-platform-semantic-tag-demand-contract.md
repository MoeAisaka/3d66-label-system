# ADR-0047：平台级语义标签需求合同

- 状态：Accepted
- 日期：2026-08-14

## 背景

知识图谱下游需要 `space`、`object`、`style`、`material` 等结构化语义字段，以及字段级空值语义、证据和质量门槛。若这些字段按 3D/SU 批次单独实现，会形成重复的平台能力和无法复用的事实合同。TPENG 标签实验台已确定为标签与内容中台重构的统一产品载体；本 ADR 将语义字段需求冻结为平台级合同。

## 决策

1. 平台冻结 `tag-demand-contract-v1` 与 `semantic-tag-schema-v1`，核心字段为 `space`、`object`、`style`、`material`、`structural_features`、`architectural_element`、`soft_decoration`、`hard_decoration` 和 `color`。字段值保持结构化数组，携带实体、语言、rank、权重、权重语义、来源、证据、版本和审核状态；不得用逗号拼接字符串替代结构化值。
2. 字段适用性固定使用 `required`、`optional`、`not_applicable`、`not_detected` 和 `needs_review` 语义。`not_applicable` 与 `not_detected` 必须保持空值数组；`required` 字段不得发布空值。`object`、`material` 的 `weight_semantics` 固定为 `relative_importance_level`：每个值单独保留 0～1 等级，`rank` 负责排序，不做总和归一化，不解释为概率或占比；重复实体合并时取最高等级，不累加。
3. 国内/海外与整体/单体通过同一合同的执行变体表达：国内绑定 `zh`，海外绑定 `en`；`whole` 绑定 `prompt_variant=whole`，`single` 绑定 `prompt_variant=single`。`is_single` 仅是由 `asset_scope` 派生的兼容投影别名，不是 Canonical 字段。
4. 3D/SU 仅作为第一条验证切片，不获得专属平台 Schema 或独立实现。类目差异通过适用性矩阵和类目扩展字段表达。
5. 标签实验台 Canonical 事实是唯一事实来源。搜索索引、知识图谱、向量索引和下游数据库表都属于可重建消费投影，外部投影不得成为事实 Owner，也不得反向覆盖 Canonical。
6. 本 ADR 只冻结 schema、validator、canonical hash 和文档索引；不新增数据库表、API、前端、真实模型/上游/下游接入、生产发布或部署授权。

## 后果

- 四个执行变体共用同一字段合同，后续类目可以复用字段验证、版本、证据和质量门槛。
- 下游可以按需要投影本地化名称或兼容别名，但必须保留 Canonical 字段与 null 语义的可追溯性。
- 首个验证切片以 3D/SU 为范围样本，不能据此把平台合同收窄为仅供该切片使用的能力。
- 真实词表、实体 Owner、阈值、更新/回退规则和下游 SLA 仍需相关 Owner 共同签认；在签认前只允许本地模拟和 dry-run。

## 不可破坏约束

- 原始模型输出是证据，不是知识图谱最终实体值，也不是 Canonical 事实。
- `is_single` 必须由 `asset_scope` 派生：`whole=0`、`single=1`；Canonical 合同不得反向依赖该别名。
- `object`、`material` 的数值是相对重要性等级，不是概率/占比；`0.7/0.5/0.3` 可以同时出现，禁止因兼容下游而归一化或截断。
- 外部投影不得写成事实主库或反向修改标签实验台 Canonical 事实。
- 不得因本 ADR 直接执行真实数据库写入、真实模型调用、生产发布、批量重跑或部署。
- 如出现未授权写入、投影反向覆盖、候选自动启用或版本无法对账，停止并回退到最近正式发布事实。
