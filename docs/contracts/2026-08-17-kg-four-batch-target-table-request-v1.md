# 知识图谱模型标签识别：四批真实数据与两张目标表建表需求 v1

> 状态：提交大数据团队评审建表；本会话不执行 DDL、DML、真实模型调用或外部表写入
>
> 日期：2026-08-17
>
> 正式需求单：IDMQXY-5507《知识图谱标签识别诉求》

## 1. 交付目标

本次不是只交字段合同或 Prompt 试跑，而是为以下交付准备可落地的建表合同：

- 四个真实数据批次全部跑通并可验收；
- 两张下游目标表全部创建并可接收正式发布事实投影；
- 目标表只作为知识图谱/下游消费投影，不成为标签事实主库；
- 所有行都能追溯到素材身份、资产版本、标签合同、机制版本、模型版本、Prompt 版本和投影批次。

四批共用同一平台字段合同和同一投影逻辑，仅通过来源站点、语言、整体/单体和 Prompt 变体路由。

## 2. 四批路由合同

| 批次 | 目标表 | `site_scope` | `locale` | `asset_scope` | `is_single` | `prompt_variant` | 当前状态 |
|---|---|---|---|---|---:|---|---|
| 国内整体模型 | `kg_model_tag_recognition_cn` | `domestic` | `zh` | `whole` | 0 | `whole` | 目标表待建；国内来源表由大数据确认 |
| 国内单体模型 | `kg_model_tag_recognition_cn` | `domestic` | `zh` | `single` | 1 | `single` | 目标表待建；国内来源表由大数据确认 |
| 海外整体模型 | `relebook_kg_model_tag_recognition` | `overseas` | `en` | `whole` | 0 | `whole` | 来源口径已核验 |
| 海外单体模型 | `relebook_kg_model_tag_recognition` | `overseas` | `en` | `single` | 1 | `single` | 来源口径已核验 |

正式首批使用同一个 T-1 快照：`2026-08-16`。后续三批继续使用该日期的同一快照，不因批次执行时间不同而重新取数。

## 3. 已核验的海外来源口径

海外两批的输入合同固定为：

```sql
FROM aliyun_3d66_dw.ods_ll_relebook_res AS src
JOIN aliyun_3d66_dw.dim_res_info AS dim
  ON src.ll_id = dim.ll_id
 AND src.res_type = dim.res_type
WHERE src.dt = '20260816'
  AND src.res_type = 6
  AND dim.is_delete = 0
```

说明：

- 关联键必须是 `(ll_id, res_type)`，禁止只用 `ll_id`；
- `dim.is_single=0` 路由到整体批次，`dim.is_single=1` 路由到单体批次；
- `ods_ll_relebook_res` 当前已核验为海外素材主表，分区字段为 `dt`；
- `dim_res_info` 提供 `ll_id`、`res_id`、`res_type`、`is_single`、`is_delete`；
- `is_delete=1` 的行不得进入四批正式输入。

国内两批的正式生产来源表、过滤条件和快照字段由大数据 Owner 在建表需求回执中补齐；不得把海外表名或未核验的历史候选表名直接套用到国内链路。

## 4. 目标表

请在标准模式下分别创建开发映射表和生产目标表，生产目标表名固定为：

1. `aliyun_3d66_dw.kg_model_tag_recognition_cn`
2. `aliyun_3d66_dw.relebook_kg_model_tag_recognition`

目标表按 `dt` 分区，首批分区为 `dt='20260816'`。若大数据平台不支持下表中的逻辑类型，使用无损等价类型，并在 DDL 回执中注明映射关系。

## 5. 逻辑字段合同

### 5.1 资产身份与路由字段

| 字段 | 逻辑类型 | 必填 | 说明 |
|---|---|---:|---|
| `dt` | `STRING` 分区 | 是 | 来源快照日期；首批固定 `20260816` |
| `batch_id` | `STRING` | 是 | 四批之一的稳定批次 ID，重跑同批次幂等 |
| `site_scope` | `STRING` | 是 | `domestic` / `overseas` |
| `locale` | `STRING` | 是 | `zh` / `en` |
| `category_key` | `STRING` | 是 | 当前首切片为 `model_3d_su` |
| `asset_scope` | `STRING` | 是 | `whole` / `single` |
| `is_single` | `TINYINT` | 是 | 兼容投影字段，仅允许 0/1；由 `asset_scope` 派生 |
| `res_type` | 源表同类型 | 是 | 来源资源类型；海外首批固定为 6 |
| `ll_id` | 源表同类型 | 是 | 业务主身份之一 |
| `res_id` | 源表同类型 | 否 | 来源资源 ID；如源表存在则保留 |
| `source_snapshot_dt` | `STRING` | 是 | 与 `dt` 同值，显式记录取数快照 |
| `source_deleted` | `TINYINT` | 是 | 正式投影必须为 0 |

建议幂等业务键：`batch_id + res_type + ll_id + dt`。如平台不允许复合唯一约束，必须通过批次级去重和对账任务保证同一逻辑键最多一行。

### 5.2 平台语义字段

以下字段在两张表中保持同名，字段适用性由类目合同决定；不适用字段使用明确空值语义，不使用空字符串伪造识别结果。

| 字段 | 逻辑类型 | 投影格式 |
|---|---|---|
| `space` | `STRING` | 当前主值名称；完整结构化值进入 `semantic_detail_json` |
| `object` | `STRING` | 按 `rank` 排序的兼容字符串，例如 `sofa_0.7,coffee table_0.5,floor lamp_0.3` |
| `style` | `STRING` | 当前主值或按合同约定的兼容名称 |
| `material` | `STRING` | 按 `rank` 排序的兼容字符串，保留相对重要性等级 |
| `structural_features` | `STRING` | 兼容名称串；完整数组进入 JSON |
| `architectural_element` | `STRING` | 兼容名称串；完整数组进入 JSON |
| `soft_decoration` | `STRING` | 兼容名称串；完整数组进入 JSON |
| `hard_decoration` | `STRING` | 兼容名称串；完整数组进入 JSON |
| `color` | `STRING` | 标准色名称或兼容名称串 |
| `title` | `STRING` | 按 locale 输出的本地化标题；无权威来源时保留空值语义 |
| `semantic_detail_json` | `STRING` | Canonical 结构化字段、实体 ID、locale、rank、weight、空值语义和 evidence 的 JSON 快照 |

`object`、`material` 的数值固定为**相对重要性等级**：

- 每个值单独保留 0～1 的等级；
- `rank` 负责排序；
- `0.7/0.5/0.3` 可以同时出现，不做总和归一化；
- 不解释为概率、置信度或占比；
- 同一实体因别名或多次证据重复出现时，Canonical 合并取最高等级，不累加；
- 兼容字符串只服务下游读取，Canonical 事实仍以结构化 JSON 为准。

### 5.3 版本、质量与治理字段

| 字段 | 逻辑类型 | 说明 |
|---|---|---|
| `tag_contract_key` / `tag_contract_version` | `STRING` | 平台语义标签合同身份 |
| `mechanism_release_id` | `STRING` | 正式评测/标注机制发布版本 |
| `model_version` | `STRING` | 实际产生候选结果的模型版本 |
| `prompt_version` | `STRING` | 整体/单体及语言对应 Prompt 版本 |
| `normalization_version` | `STRING` | 标准化版本 |
| `mapping_version` | `STRING` | 实体/同义词映射版本 |
| `label_fact_version` | `STRING` | 正式标签事实版本 |
| `review_status` | `STRING` | 只能投影正式发布状态，不得写入候选/实验中间态 |
| `quality_status` | `STRING` | 字段质量门禁汇总状态 |
| `source_evaluation_id` / `source_review_id` | `STRING` | 评测与人工审核证据引用 |
| `projection_batch_id` | `STRING` | 投影运行批次 |
| `payload_hash` | `STRING` | Canonical payload 的确定性 SHA-256 |
| `generated_at` / `published_at` | `TIMESTAMP` | 生成和正式发布时间 |
| `invalidated_at` | `TIMESTAMP` | 失效时填写；未失效为空 |
| `provenance_json` | `STRING` | 完整来源、证据、发布和回退信息 |

## 6. 分区、回退与投影约束

- 两张目标表必须支持按 `dt` 重建、按 `batch_id` 重跑和按 `payload_hash` 对账；
- 目标表只接收 `PublishedLabel.status=published` 的正式事实；
- 不允许写入候选机制、实验结果、原始模型响应、人工处理中间态或 Query×素材策略；
- 失败批次只能重试、补偿或回退到最近正式事实，不得反向修改 Canonical；
- 每个批次必须产出 Manifest，至少包含输入快照、行数、缺失、重复、版本匹配、哈希、失败原因和回退点；
- `dt='20260816'` 首批分区验收完成前，不创建新的业务日期分区替代首批快照。

## 7. 建表回执需由大数据团队补齐

请回执以下内容后，LabelLab 才能冻结四批真实跑批合同：

1. 两张目标表的正式 DDL、开发/生产表映射和 Schema 版本；
2. 国内来源表、主键/唯一性、T-1 快照字段和删除字段；
3. `res_type`、`ll_id`、`res_id`、`is_single` 的实际物理类型；
4. 表 Owner、读写账号、最小权限、分区保留周期和回退操作人；
5. 目标表写入方式（DataWorks 节点/离线任务/API 投影）及单写者约束；
6. 四批预计行数、SLA、失败重试上限和验收窗口。

在上述回执前，本会话保持：不申请建表权限、不执行 DDL/DML、不连接真实业务数据库、不调用真实模型、不发布生产标签事实。

