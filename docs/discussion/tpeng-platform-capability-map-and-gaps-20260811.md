# TPENG 标签实验台中台能力映射与 Gap 清单

> 日期：2026-08-11  
> 依据：ADR-0041、ADR-0042、ADR-0043 及当前分支 `codex/label-mechanism-v1` 代码证据。  
> 目的：记录上层架构约束与当前实现的对应关系，供【标签体系】重构会话冻结下一阶段范围。

## 结论先行

TPENG 标签实验台已经具备统一底座的主要控制面：素材接入、类目评测与任务、自动/人工结果、人工纠偏、
AI 优化样本回流、机制与标签事实双发布轴、存量重跑门禁、正式标签消费和基础对账。当前尚未形成的
中台能力主要集中在 Canonical 资产事实命名空间、独立字段需求合同、统一资产版本身份、资产语义投影
服务和 Embedding 生命周期；真实存量批量 Worker 仍是 ADR-0041 已知边界。

## 能力映射

| 能力域 | 当前实现证据 | 状态 | Gap / 边界 | 下一阶段候选（待 Owner 冻结） |
|---|---|---|---|---|
| 统一产品载体 | `PRODUCT.md`、ADR-0042；底座能力由类目复用 | 已实现 | 无本批次 Gap | 继续以 TPENG 标签实验台作为 PRD 主体 |
| 下游字段需求合同 | 当前有下游正式标签消费接口，但没有独立字段需求合同/字段注册中心 | Gap | 无一等合同对象、版本、审批和变更对账 | 设计字段需求合同与 Canonical 字段注册中心 |
| 素材接入 | `backend/app/main.py:7326` `/api/content-ingress/events`；`ContentRecord`、`ContentIngressEvent` | 已实现 | 当前是本地接入投影，不是外部系统镜像 | 补充来源版本与资产版本的统一关联 |
| 资产实体与版本 | `backend/app/models.py:358` `Asset` 有 `sha256`；`ContentRecord.source_version` | 部分实现 | 没有统一 `asset_version_id`/不可变资产版本身份；版本关系分散 | 定义资产版本、内容版本、来源版本和哈希对账合同 |
| 标注路径与评测任务 | `EvaluationJob`、`CategoryEvaluationV3Config`、`EvaluationPackage` | 已实现 | 类目差异由合同表达，平台能力不复制 | 按字段合同补齐任务输入/输出声明 |
| 自动标注结果 | `EvaluationResult` 及模型/提示词/规则快照 | 已实现 | Canonical 命名空间尚未统一到 `semantic.*`、`quality.*`、`governance.*` | 将结果字段映射到 Canonical 资产事实并保留历史兼容 |
| 人工标注与纠偏验收 | `HumanReview`、`ReviewPanel`、`EvaluationResult.correction_history_json` | 已实现 | 仍需与 Canonical 事实版本身份统一关联 | 补充真值、证据、审核状态的统一事实表/索引 |
| Badcase 回流 | `ProductionFeedbackEvent`、`OptimizationCaseQueue`；`backend/app/main.py:7214` 起 | 部分实现 | 已有接收与排队，但自动分析、查漏补缺、迭代报告和回归闭环不是本批次新增内容 | 设计“纠偏样本 → 调优模型 → 候选机制 → 回归报告”流水线 |
| 机制发布轴 | `MechanismRelease`、`POST /api/evaluation-packages/{id}/publish`、`GET /api/mechanism-releases` | 已实现 | 机制轴人工回滚 API 尚未实现 | 追加式 activation revision 回滚，不改写历史 release |
| 标签事实发布轴 | `LabelRelease`、`PublishedLabel`、`LabelOutboxEvent`；`/api/label-releases` 与回滚接口 | 已实现 | 当前下游同步仍为本地事件流/最终一致性 | 按资产/机制/模型版本补齐发布清单对账 |
| 下游消费与对账 | `/api/consumer/v1/labels/{content_key}`、`changes`、`checkpoints`、`reconciliation` | 已实现 | 仅覆盖标签发布读模型；没有通用投影对账 | 扩展投影级质量、延迟、缺失和哈希对账 |
| Canonical 事实主权 | 当前实体可追溯部分来源、评测、人工审核和发布信息 | Gap | `semantic.*`、`quality.*`、`governance.*` 尚未成为一等资产事实命名空间 | 建立 Canonical 字段注册、证据、来源和版本引用模型 |
| 机制/模型追溯 | `PublishedLabel.provenance` 已记录 evaluation/job/review/model/prompt/rubric/engine 等信息 | 部分实现 | 尚未显式记录 `mechanism_release_id`，统一资产版本引用也缺失 | 将机制版本、资产版本和模型版本纳入统一 provenance 合同 |
| 资产语义投影服务 | 当前没有通用 projection registry、失效、重建、回退服务 | Gap | 搜索索引、图谱和向量只能作为未来消费投影 | 设计投影注册、输入版本、失效、重建、回退和对账 API |
| Embedding 生命周期 | 当前未实现图片/文本/多模态 Embedding 存储和生命周期 | Gap | 本批次不实现 Embedding 生成或重算 | 按资产版本/模型版本定义生成、失效、重算、回退和质量对账 |
| 知识图谱责任 | 不属于当前 LabelLab 代码实现 | 外部边界 | 实体、关系、路径和图结构 Embedding 由知识图谱负责 | 只定义中台交接合同，不把图内关系写入素材事实 |
| 搜索/推荐责任 | 不属于当前 LabelLab 代码实现 | 外部边界 | Query Embedding、Query×素材相似度、召回融合、排序权重和在线实验由下游负责 | 只消费中台正式资产语义投影 |
| 存量重跑 | `StockRerun` 与 `/api/stock-reruns` 控制面、快照和 fail-closed 门禁 | 部分实现 | 当前固定 `dry_run_only`，无真实批量 Worker、差异工作台或批量发布申请 | Owner 单独冻结执行 Worker、差异验收和发布申请范围 |

## 本批次明确不扩大

- 不实现字段需求合同/字段注册中心、Canonical 资产事实重构、统一资产版本表。
- 不实现 Embedding 生成、存储、失效、重算、回退或质量对账。
- 不实现通用资产语义投影服务、投影 registry 或搜索/知识图谱接入。
- 不实现真实存量批量 Worker、结果差异工作台或自动批量发布。
- 不接生产数据库、真实模型批量调用、真实密钥或外部下游写入。
- 不改变 ADR-0041 的权限、非目标、回退、验收和停止条件。

## 交接与停止条件

1. 本清单由当前 TPENG 标签实验台会话维护，并返回【标签体系】重构会话作为下一阶段输入。
2. 在 Owner 明确冻结下一阶段目标、范围、权限、回退和验收前，Gap 只作为候选，不触发代码写入。
3. 下一阶段冻结后，仍由 TPENG 标签实验台会话作为唯一代码写入方；其他会话只提供需求、评审或证据。
