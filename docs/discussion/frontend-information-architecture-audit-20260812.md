# TPENG 标签实验台前端信息架构全路由审计

> 日期：2026-08-12  
> 范围：`frontend/src/App.tsx` 当前可达路由及其主导航/兼容入口。  
> 目的：记录一级主线与二级承载的长期重构顺序；本审计不授权本批次之外的实现。

## 统一判断口径

- 一级页面只保留当前任务的决策信息、状态摘要、筛选和高频操作。
- 复杂配置、历史、指标、原始证据和诊断进入抽屉、Dialog 或独立工作台。
- 合同编辑与逐条纠偏是独立工作台；类目差异通过受控 `profile_type` 插件表达。
- 桌面端验收尺寸为 1440×900 与 1280×720；不增加移动端验收要求。
- 业务类目是 TPENG 标签实验台底座上的场景扩展，不复制平台通用能力。

## 路由审计

| 路由 | 来源组件 | 主要用户/任务 | 当前平铺问题 | 一级保留 | 二级承载/复用 | 风险与建议批次 |
|---|---|---|---|---|---|---|
| `/workflow/production-line` | `EvaluationPackagePipelinePage` | 运营：选择素材包、类目并启动评测 | 素材、合同、队列和执行状态同屏 | 类目、素材包、启动、状态 | `WorkspacePageHeader`、抽屉、进度面板 | P1；保持生产流水线主线 |
| `/workflow/materials/packages` | `AssetsPage` | 运营：导入/整理素材包 | 上传、包详情、资产明细和历史混在一起 | 上传、包选择、资产数、下一步 | 素材包详情抽屉、`ImageLightbox` | P1；与生产流水线边界对齐 |
| `/workflow/materials/assets` | `App.tsx` redirect | 兼容入口 | 仅兼容跳转 | 无 | 跳转到素材包页 | 已完成；不新增页面 |
| `/workflow/materials/jobs` | `JobsPage` | 运营：查看评测进度 | 队列、错误、原始响应和重试平铺 | 进度、失败数、重试 | 任务详情/错误抽屉 | P1 |
| `/workflow/review/:reviewView` | `ReviewPage` | 审核员：处理待审核、会审、主审、已完成 | 列表、证据、表单、历史同屏 | 待处理列表、当前状态、下一步 | 审核工作台、证据抽屉 | P1；保留权限和会审门禁 |
| `/workflow/optimization/cases` | `OptimizationCasesPage` | 优化人员：查看纠偏案例池 | 筛选、样本、证据、队列操作平铺 | 队列筛选、状态、批量动作 | 案例详情/证据抽屉 | P1 |
| `/workflow/optimization/automation` | `AutomationControlPage` | 管理员：预算、协议、执行器 | 多组配置与运行日志同屏 | 开关、预算摘要、运行状态 | 配置抽屉、日志抽屉 | P1；不扩大真实执行权限 |
| `/workflow/optimization/feedback` | `ProductionFeedbackPage` | 优化人员：查看生产回流 | 回流事件、解析、关联案例平铺 | 事件状态、待处理数、批量动作 | 事件详情抽屉 | P1 |
| `/workflow/optimization/candidates` | `PromptCandidatesPage` | 机制负责人：管理候选版本 | 候选、差异、回归和发布门禁同屏 | 候选列表、状态、发布入口 | 差异/回归证据抽屉 | P1；候选不可直接激活 |
| `/workflow/optimization/category-evaluation-preview` | `CategoryEvaluationPreviewPage` | 管理员：查看类目合同 | 合同摘要、校验、运行边界平铺 | 类目、合同状态、校验结果 | JSON/校验详情抽屉 | P1；只读预览 |
| `/workflow/optimization/category-evaluation-v3-config` | `CategoryEvaluationV3ConfigPage` | 管理员：编辑 v3 合同 | 多步骤合同编辑、诊断和候选摘要同屏 | 步骤导航、当前草稿、保存 | 维度/规则/诊断抽屉 | 本批已重构；Proposal 插件保持独立 |
| `/workflow/optimization/paired-regression` | `PairedRegressionPage` | 机制负责人：候选配对回归 | 样本、运行、差异和发布建议平铺 | 候选、基线、运行状态 | 运行详情/差异抽屉 | P1；不自动发布 |
| `/workflow/optimization/baseline-regression` | `BaselineRegressionPage` | 审核员：存量基准回归与逐条纠偏 | 原页面长期平铺配置/指标/历史/纠偏 | 选择基准集、启动、逐条确认 | `BaselineSetDialog`、`RunConfigDrawer`、`MetricsDrawer`、`RunHistoryDrawer`、`CorrectionWorkbench` | 本批已完成；不新增真实重跑 Worker |
| `/workflow/releases/packages` | `EvaluationPackageReviewListPage` | 经理：二审评测包队列 | 包摘要、证据和动作平铺 | 队列、状态、下一步 | 包详情页/证据抽屉 | P1；发布门禁不变 |
| `/workflow/releases/packages/:packageId` | `EvaluationPackageDetailPage` | 经理：审核单个评测包 | 资产、评测、差异、发布动作同屏 | 包状态、审核动作、阻塞 | 资产/评测/审计抽屉 | P1 |
| `/workflow/releases/decisions` | `ReleaseWorkspacePage(view=decisions)` | 管理员：做正式标签发布决定 | 候选、对账、权限说明平铺 | 待决定列表、发布/驳回 | 对账和 provenance 抽屉 | P1；只消费正式事实 |
| `/workflow/releases/metrics` | `ReleaseWorkspacePage(view=metrics)` | 经理：看版本指标 | 指标、筛选、版本历史平铺 | 核心指标和版本选择 | 指标详情抽屉 | P1 |
| `/workflow/releases/history` | `ReleaseWorkspacePage(view=history)` | 审计/经理：追溯发布和回滚 | 时间线、事件、差异同屏 | 时间线和筛选 | 事件详情抽屉 | P1 |
| `/workflow/models/benchmark` | `BenchmarkPage` | 模型负责人：多模型横评 | 模型、样本、成本和结果平铺 | 模型选择、运行状态、结论 | 运行详情/成本抽屉 | P1；不触碰真实生产模型 |
| `/workflow/models/migration` | `MigrationsPage` | 模型负责人：历史迁移评估 | 迁移参数、样本、结果平铺 | 源/目标模型、状态、门禁 | 迁移详情抽屉 | P1 |
| `/workflow/models/candidates` | `CapabilityStatusPage(kind=candidates)` | 经理：查看模型候选 | 能力、风险、验收证据平铺 | 候选状态和下一步 | 能力/证据抽屉 | P1 |
| `/workflow/governance` | `SystemManagementPage` | 管理员：进入治理控制面 | 顶部重复入口和所有治理入口平铺 | 分组入口、版本、权限 | 入口详情/帮助抽屉 | 本批已收口；不恢复顶部重复入口 |
| `/workflow/governance/model-registry` | `ModelRegistryPage` | 管理员：维护模型注册中心 | 列表、表单、连接诊断平铺 | 列表、筛选、启停 | 新建/编辑/连接抽屉 | 已完成；密钥留空且不可回显 |
| `/workflow/governance/model-config` | `ModelPage` | 兼容模型配置入口 | 与注册中心重复 | 跳转/兼容提示 | 统一到注册中心 | P1；不复制模型能力 |
| `/workflow/governance/users` | `UsersPage` | 管理员：账号与权限 | 用户、角色、审计信息平铺 | 用户列表、角色、启停 | 用户详情/权限抽屉 | P1；RBAC 不变 |
| `/workflow/governance/canary` | `CanaryRunsPage` | 管理员：受控试运行 | 运行、样本、门禁、日志平铺 | 运行状态、门禁、停止 | 运行日志/证据抽屉 | P1；不自动生产发布 |
| `/workflow/governance/audit` | `AuditEventsPage` | 审计人员：查只追加事件 | 事件详情默认全部展开 | 时间、操作者、动作、筛选 | 事件 payload 抽屉 | P1 |
| `/assets`、`/jobs`、`/review` | `App.tsx` redirects | 历史兼容入口 | 兼容跳转，无独立产品语义 | 无 | 跳转目标页 | 已完成；保留兼容 |
| `/review/:reviewStage`、`/prompts`、`/model`、`/migrations`、`/canary-runs` | `App.tsx` redirects | 历史兼容入口 | 兼容跳转，无独立产品语义 | 无 | 跳转目标页 | 已完成；不新增重复入口 |
| `/sample-sets` | `App.tsx` redirect → `/legacy/sample-sets` | 审核员：历史黄金样本 | legacy 页面较平铺、职责边界旧 | 入口和只读状态 | legacy 页面内部抽屉 | P2；只审计，不在本批实现 |
| `/historical-corrections` | `App.tsx` redirect → `/legacy/historical-corrections` | 审核员：历史纠偏导入 | 导入、预览和说明平铺 | 导入状态、下一步 | 预览/映射抽屉 | P2；不直接形成黄金真值 |
| `/legacy/review/:reviewStage` | `ReviewPage` | 兼容审核流程 | 与新 review 路由存在职责重叠 | 兼容状态和跳转 | 复用审核工作台 | P2；待统一入口策略 |
| `/legacy/sample-sets` | `SampleSetsPage` | 历史样本管理 | 旧页面平铺且与基准回归概念相邻 | 只读摘要、迁移提示 | 样本详情抽屉 | P2；不复制基准集能力 |
| `/legacy/historical-corrections` | `HistoricalCorrectionsPage` | 历史纠偏预览 | 旧页面平铺、导入过程复杂 | 导入状态、风险提示 | 预览/证据抽屉 | P2 |

## 本批次结论

Task 10 只改造基准回归页，抽取出的 Dialog/Drawer/Workbench 可作为后续页面复用的结构样板。
其余路由本文件只提供审计和推荐批次；没有隐含实现授权，也不改变现有权限、发布轴、回退和验收合同。

