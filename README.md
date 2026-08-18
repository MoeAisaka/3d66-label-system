# 特鹏标签中台（Label System）

> 新标签体系的统一产品载体，也是标签/内容中台重构的通用底座。
>
> 当前状态更新于 2026-08-18：代码主线和公司内网测试环境已经具备可运行、可演示、可确定性验证的工程底座；真实上游、真实模型、生产数据库写入和生产发布仍需真人研发按专项合同完成。

## 5 分钟读懂项目

Label System 不是单一的美感评测工具，也不再与“标签体系重构”并列建设。平台统一承载：

```text
下游字段需求合同
→ 素材接入与身份解析
→ 自动标注 / 美感与质量评测
→ 人工审核纠偏
→ AI 生成候选机制并自动回归
→ 人工决定是否启用机制
→ 人工决定是否发布标签事实
→ 大维表 / 职责小表投影与对账
→ 下游消费效果和 Badcase 回流
```

3D/SU、灵感图、Proposal PDF 等类目只扩展自己的字段、Prompt、等级规则、规则和专用编辑视图，不能复制模型中心、任务调度、人工审核、版本发布、投影和回流等平台公共能力。

当前最高优先级是 45 天 MVP：在 2026-09-30 前跑通知识图谱国内/海外、整体/单体四批真实模型素材打标，写入国内和海外两张目标表，并完成生产、消费、对账、回退和 Badcase 回流。

## 当前权威基线

| 项目 | 当前状态 |
|---|---|
| 唯一代码仓库 | Codeup `3d66/tepeng/3d66.label-system` |
| 当前主线 | `main@fbe6e835b5a95d6e1fc84d871badbb3b236a25c2` |
| 公司内网测试环境 | `http://192.168.1.35:8081` |
| 数据库迁移 | migration 73 |
| 测试环境状态 | 健康检查与 readiness 通过，静态资源包含 build `fbe6e83` |
| 自动组批 | 总开关关闭，保持 dry-run，不调用真实调优模型 |
| 生产状态 | 尚未接入真实上游、真实生产模型及目标表 DML，不得表述为生产上线 |

历史章节、旧分支回执和旧部署记录只用于追溯。判断当前能力时以 Codeup `main`、本文件、`PROJECT_STATUS.md` 顶部“当前权威基线”和最新迁移为准。

## 已经具备什么

### 已进入主线和测试环境的工程底座

- 增量评测、存量回归、运行中心和质量资产工作区；
- 主模型、调优模型注册与凭据引用；
- 类目 Profile、Prompt A/B、等级规则、Rubric 和多版本机制管理；
- 3D/SU、灵感图、Proposal PDF 等类目扩展骨架；
- 自动标注、人工审核、节点纠偏、理由和证据留痕；
- 纠偏样本到候选 Prompt/V3/规则、自动回归和人工二审的编排；
- 机制启用与标签事实发布两条独立人工门；
- 黄金集、挑战集、真值 revision、CSV/JSON/Manifest 导出；
- 字段级 Precision/Recall、L1-L5 五档矩阵及推荐/常规/过滤三档附加指标；
- 受控脚本注册、工作流注册、五队列、检查点、重试和恢复；
- 字段需求合同、资产版本、语义事实、发布版本和 Outbox；
- 大维表/小表投影合同、Shadow 投影、Manifest 和对账；
- 下游反馈、Badcase 回流、自动组批泳道、预算与证据工作台。

### 已有代码或合同，但还不是生产能力

- 国内 `ll_id` 和海外 `res_id` 的来源合同与确定性演练；
- 四批知识图谱任务的 fixed-snapshot Manifest 设计；
- 3D/SU whole/single 路由和 readiness 门禁；
- Shadow Projection 和本地投影适配器；
- 自动组批、候选包、自动回归和质量门禁；
- macOS、Windows 和 Docker 的受控安装、备份、恢复及部署脚本。

这些能力证明产品和技术路线可行，但不能替代真实数据探查、正式模型联调、生产 Writer、容量压测、监控告警、灰度和回退演练。

## 业务接入与迁移原则

### 下游字段先盘清，再进入迁移

算法、搜索池、知识图谱、海外等下游必须先提交一份字段盘点，逐项写清：当前正在消费的字段、来源表/查询、字段含义、更新频率、质量要求、负责人和下游验收方式。这些字段进入“迁移支持范围”。同时登记未来新增字段、使用场景、优先级和期望上线时间，进入“增量支持范围”。平台不凭页面猜字段，也不因为某个业务临时加列就绕过字段需求合同。

| 盘点结果 | 平台处理方式 | 验收方式 |
|---|---|---|
| 当前正在消费 | 先兼容旧字段语义，建立来源、版本和对账规则 | 下游读取结果与旧链路对账 |
| 后续新增需求 | 新建字段需求合同，经过评测、质量门禁和发布流程 | 字段级 Precision/Recall、缺失率和下游验收 |
| 已弃用字段 | 保留历史追溯和迁移映射，不再进入新生产默认输出 | 明确下线日期和回退策略 |

### 真人研发第一批任务：先审旧链路，再分期迁移

第一批研发工作不是立即复制旧代码，而是复盘旧标签链路并形成四类清单：

1. 已弃用：没有下游消费、没有历史追溯价值或已被新字段替代；
2. 直接迁移：语义、来源和质量口径清楚，可以接入新平台合同；
3. 需要改造：仍在消费但来源、口径、表结构或任务方式不适合新平台；
4. 外部依赖：需要下游、算法、数仓或权限负责人补齐信息后才能迁移。

研发需按“先 3D/SU 知识图谱四批真实批次，再高价值共用字段，再其他类目和存量”的顺序给出分期计划。每一期都要有旧链路对账、迁移结果、回退点和下游验收，不做一次性全量搬迁。

## 正式生产链路（研发必须按此验收）

```text
上游增量素材
    ↓
身份解析与类目路由（国内/海外、整体/单体）
    ↓
按类目自动分配工作流
    ↓
调用评测机制：逐字段标注 + 美感分/质量分
    ↓
等级撮合器：将维度结果撮合为美感等级
    ↓
汇总正式标签事实：美感等级 + 下游固定字段
    ↓
写入统一大维表和职责小表（需正式 DDL、Writer 和对账）
    ↓
下游改造旧链路，从正式发布表消费
    ↓
效果、Badcase 和使用反馈回流平台
```

真实生产验收必须同时覆盖上游接入、中间机制执行、人工纠偏、双人工发布门、目标表建表需求、正式落表、下游读取改造和回流。任何候选机制、实验结果或人工过程都不能被下游直接读取。

## 新研发从哪里开始

研发接手时按以下顺序阅读：

1. [本 README](README.md)：项目定位、工程入口、下游迁移原则和当前边界；
2. [产品原则](PRODUCT.md)：不可改变的产品定位与数据边界；
3. [开发规则](AGENTS.md)：修改、测试、迁移和完成定义；
4. [知识图谱四批两表合同](docs/contracts/2026-08-17-kg-four-batch-target-table-request-v1.md)；
5. [3D/SU readiness 合同](docs/contracts/3d-su-readiness-freeze-v1.md)；
6. [当前项目状态](PROJECT_STATUS.md)；
7. [架构决策](docs/decisions/README.md)。

旧的 `CODEX_HANDOFF.md` 保存早期 Demo 演进、历史故障和旧环境约束，只用于追溯，不得覆盖上述最新文档。

## 技术架构

| 层 | 当前实现 | 主要职责 |
|---|---|---|
| 前端 | React、TypeScript、Vite | 工作区、任务操作、审核纠偏、配置、证据和对账 |
| API | FastAPI、Pydantic | 合同校验、权限、任务、版本、发布和查询 |
| 领域与持久化 | SQLAlchemy、当前 SQLite | 资产、评测、真值、机制、事实、运行和审计 |
| Worker | 五队列调度与后台 Worker | 执行、租约、重试、检查点、恢复和组批 |
| 模型层 | Provider Adapter + 凭据引用 | 主模型评测、调优模型分析和调用治理 |
| 投影层 | Projection Registry、Outbox、Manifest | 大维表/小表输出、幂等、对账和回退 |

### 不可破坏的数据边界

- `semantic.*`、`quality.*`、`governance.*` 属于平台 Canonical 事实；
- 人工真值、来源、模型、Prompt、规则、机制、审核和发布状态必须可追溯；
- 机制候选启用和标签事实发布是两道独立人工门；
- 下游只能读取正式发布事实；
- 数据库表、搜索索引、知识图谱和向量索引是可重建投影，不是事实主库；
- Query×素材相关性、排序权重和知识图谱内部关系不进入素材事实；
- 业务类目不得复制平台公共能力。

## 代码地图

### 后端核心入口

| 路径 | 职责 |
|---|---|
| `backend/app/main.py` | FastAPI 应用入口和路由装配 |
| `backend/app/models.py` | SQLAlchemy 领域模型 |
| `backend/app/migrations/runner.py` | 追加式、幂等数据库迁移 |
| `backend/app/asset_identity.py` | 素材身份和版本 |
| `backend/app/field_demand_contracts.py` | 下游字段需求合同 |
| `backend/app/label_governance.py` | 候选事实、人工审批和正式发布 |
| `backend/app/semantic_tag_quality.py` | 字段级 Precision/Recall 和质量快照 |
| `backend/app/baseline_correction_orchestration.py` | 人工纠偏到 AI 候选和自动回归 |
| `backend/app/workflow_runtime.py` | 工作流运行、步骤、检查点和恢复 |
| `backend/app/queue_scheduler.py` | 五队列、配额、租约和恢复调度 |
| `backend/app/automation_*.py` | 自动组批泳道、候选、路由和证据 API |
| `backend/app/projection_contracts.py` | 大维表/小表投影合同和对账 |
| `backend/app/production_feedback.py` | 下游反馈和 Badcase 回流 |
| `backend/app/three_d_*.py` | 3D/SU Profile、readiness 和闭环 fixture |

### 前端核心入口

| 路径 | 职责 |
|---|---|
| `frontend/src/App.tsx` | 路由和应用入口 |
| `frontend/src/pages/incremental-workspace-page.tsx` | 增量链路 |
| `frontend/src/pages/stock-workspace-page.tsx` | 存量链路 |
| `frontend/src/pages/operations-center-page.tsx` | 运行中心 |
| `frontend/src/pages/model-registry-page.tsx` | 模型注册中心 |
| `frontend/src/pages/tag-demand-contracts-page.tsx` | 字段需求合同 |
| `frontend/src/pages/projection-governance-page.tsx` | 投影、Manifest 和对账 |
| `frontend/src/pages/quality-assets-page.tsx` | 黄金集和质量资产 |
| `frontend/src/pages/automation-overview-page.tsx` | 自动组批和候选二审 |
| `frontend/src/features/baseline-regression/` | 回归、指标和纠偏工作台 |
| `frontend/src/features/mechanism-config/` | 类目机制编辑器注册 |

## 在干净环境启动

### macOS

```bash
./scripts/macos/install.sh --check
./scripts/macos/install.sh
./scripts/macos/doctor.sh
./scripts/macos/start.sh
```

### Windows

```powershell
.\scripts\windows\install.ps1 -Check
.\scripts\windows\install.ps1
.\scripts\windows\doctor.ps1
.\scripts\windows\start.ps1
```

受控启动脚本默认监听 `127.0.0.1`，不会自动修改防火墙、系统服务、注册表、PowerShell 策略或公司网络。对局域网开放前必须完成单独审批。

运行数据必须放在仓库之外。Git 只保存代码、文档、提示词和配置结构；数据库、图片、日志、备份、`.env`、`.venv`、`node_modules` 和真实凭据不得进入 Git。

## 开发验证

### 后端全量测试

测试必须使用全新的临时数据目录：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" PYTHONPATH=. .venv/bin/python -m pytest -q backend/tests
```

### 前端验证

```bash
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
```

前端专项合同位于 `frontend/scripts/check-*.ts`。修改字段、模型、工作区、运行中心、3D/SU、自动组批或回归指标时，必须执行对应 `contract:*` / `test:*` 脚本。

### 完成前最低门禁

- 受影响专项测试通过；
- 后端全量测试通过；
- 前端 lint、production build 和相关合同脚本通过；
- `git diff --check` 通过；
- 数据变更完成迁移测试和旧库升级验证；
- 页面变更完成真实桌面浏览器检查；
- `PROJECT_STATUS.md` 已同步当前状态和回退边界。

## 数据库和迁移规则

- 当前测试和本地形态使用 SQLite；真人研发需评估生产数据库选型，但不能改变现有 API、版本、幂等、Outbox 和发布合同；
- 迁移只允许追加，禁止修改、删除或重排已经发布的 migration；
- 每个迁移必须同时支持全新库初始化和旧库升级；
- 不回填、覆盖或重新解释历史评测、人工真值、机制和标签事实；
- 部署前创建数据库快照并执行 `integrity_check`、外键检查和迁移版本核对；
- 数据库恢复是独立破坏性操作，不能因为代码回滚自动执行。

## 凭据、权限和安全

- macOS 使用 Keychain、Windows 使用当前用户 DPAPI；数据库只保存凭据引用或版本化密文；
- Token、API Key、Cookie、会话、密码和私钥不得进入代码、日志、截图、Markdown、测试或 Git；
- 真实 DataWorks/ODPS 查询、目标表 DML、模型调用和生产发布均需要精确授权；
- 当前四批真实接入仍保持 `pending_external_signoff`；
- 未签认身份、字段、黄金集、权限、RACI、灰度和回退时必须 fail-closed。

## Git、MR 和部署

1. Codeup `main` 是唯一代码主线；
2. 每项需求使用独立分支和 MR，不在服务器直接改代码；
3. MR 必须写明目标、范围、非目标、迁移、测试、风险、回退和验收；
4. 推送或合并不等于授权部署；
5. 共享测试环境只允许部署 Codeup `main` 的明确完整 SHA；
6. 部署前确认没有运行中的评测、回归、纠偏、重跑和投影任务；
7. 部署后核对服务器 HEAD、静态 build SHA、健康检查、迁移、数据库完整性和运行队列；
8. 禁止 force push 覆盖未知远端历史，禁止把旧候选分支整包覆盖主线。

测试服受保护部署入口为：

```bash
./scripts/deploy-test-server.sh
```

脚本要求操作者核对摘要并显式确认。生产部署不在当前仓库文档的默认授权范围内。

## 45 天 MVP：研发真正要交付什么

MVP 不是“把现有页面再开发一遍”，而是在现有底座上补齐真实生产链路：

1. 国内 `ll_id`、海外 `res_id` 两套正式 Source Adapter；
2. 国内整体、国内单体、海外整体、海外单体四批固定快照与 Manifest；
3. 正式模型、Prompt、等级规则、成本、限流、超时和重试；
4. 至少 100 条锁定黄金/挑战样本和字段级质量报告；
5. 双人工门、人工纠偏、AI 候选和自动回归；
6. 国内 `kg_model_tag_recognition_cn` 与海外 `relebook_kg_model_tag_recognition` 受控 Writer；
7. 行数、缺失、重复、版本、哈希、重试和回退对账；
8. 下游读取验收和至少一次 Badcase 回流。

以下外部依赖不阻塞研发先完成接口和工作流开发，但会阻塞真实跑批：目标表正式 DDL、数据账号与最小权限、来源物理字段/删除语义/唯一性探查、正式模型版本、黄金集真值以及下游验收 Owner。

## 新研发第一天检查表

- 从 Codeup `main` 干净克隆，不使用聊天附件或历史恢复副本；
- 阅读本 README、`PRODUCT.md`、`AGENTS.md`、方案和真人研发交接文档；
- 完成受控安装、doctor、后端全量测试和前端构建；
- 确认 migration 73、当前测试环境 build 和自动组批关闭状态；
- 说明资产、评测、人工真值、机制、标签事实和投影之间的关系；
- 输出“直接复用 / 需要重构 / 需要生产化 / 外部依赖”四类代码审查结果；
- 第一周完成旧标签链路审计、下游字段盘点和分期迁移计划，先按四批知识图谱任务建立可验证的真实链路。

## 操作与历史资料

- 一线操作：[docs/user-guide.md](docs/user-guide.md)
- 当前进度：[PROJECT_STATUS.md](PROJECT_STATUS.md)
- 历史演进：[CODEX_HANDOFF.md](CODEX_HANDOFF.md)
- 架构决策：[docs/decisions/](docs/decisions/)
- 验证回执：[docs/superpowers/receipts/](docs/superpowers/receipts/)
- 研发接手材料和宣讲方案由项目负责人单独维护，确认后再按发布边界进入仓库。

任何文档与 Codeup `main`、最新 migration 或当前正式合同冲突时，应先停止实施并修正文档，不得用口头约定绕过版本和发布门禁。
真人后端研发交接：[后端研发交接文档](docs/handoff/2026-08-17-label-system-human-backend-handoff.md)
- 未发布迭代与合流边界：[未发布包台账](docs/handoff/2026-08-17-unpublished-package-ledger.md)
- 当前实现状态：[PROJECT_STATUS.md](PROJECT_STATUS.md)
- 长期产品原则：[PRODUCT.md](PRODUCT.md)
- 架构决策：[docs/decisions/](docs/decisions/)
- 代码仓库：`https://codeup.aliyun.com/3d66/tepeng/3d66.label-system`

当前工程由 Vibe Coding 快速搭建并持续经过确定性测试，后续需要由真人后端研发完成正式接管、代码审查和生产化。测试环境、fixture、Shadow 投影或 dry-run 结果不能当成生产上线事实。

## 当前能力

面向一线审核员与二审管理员的完整操作说明见 [操作手册](docs/user-guide.md)。

- 生产目标仍是 Windows 主机运行；当前已增加与 macOS 同级的 Windows
  安装、诊断、前台启动、脱敏备份和恢复工具；macOS 部署链已用于 MacBook
  功能验收，但尚未在真实 Windows 公司服务器执行，默认也不对局域网开放。
- 批量图片、整个文件夹或 ZIP 上传会自动汇总为不可变素材包；也可从现有
  素材手工整理新包。相同内容按 SHA-256 复用素材，但保留每次导入来源。
- 素材删除采用可恢复的逻辑删除：默认列表和新任务不再显示，历史评测、真值、
  素材包来源与本地文件继续保留；再次上传相同内容时恢复。
- “素材选择”已并入“素材包”页面，可按包筛选后创建任务。
- 豆包与提示词优化模型配置全部在后台管理。Windows 使用当前用户的
  DPAPI；macOS 使用当前登录用户的 Keychain。数据库只保存版本化密文或
  Keychain 引用，前端不会再次读回完整密钥。
- 使用用户提供的 Doubao-Seed-2.0-Lite V2.1 提示词，按 A（分类/形态/画质）和 B（美感维度）两次调用。
- 灵感图现行等级规则已使用规则扣分：调用 B 不再打 1–5 分，而是逐条返回命中规则、
  置信度和中文证据，最终分数由服务端确定性聚合。旧 grade 结果保留兼容路径。
- 等级规则配置页可编辑每维扣分规则与媒介降权开关；后端支持带逐条证据的
  节点级纠偏，并从被修改节点确定性重算下游。
- 兼容只有一版完整提示词的单次调用模式；任务和结果明确记录为“单提示词”，不会伪装成 A/B 两个版本。
- 总分和 L1～L5 由服务端固定评分引擎计算，模型不直接决定最终总分。
- 随拍图或画质受损（`slight` 及以上）时最终等级最高为 L2；严重或不可用画质满足证据阈值时最高为 L1。
- 人工纠正只修改错误维度，最终人工分数和等级由同一服务端评分引擎自动重算，不能手工指定。
- 保存模型原始响应、解析结果、模型 ID、A/B 提示词版本、规则版本和评分引擎版本。
- 提示词修改采用“AI 提议草案 → 人工编辑 → 另存新版本 → 评测 → 人工发布”，不会覆盖已发布版本。
- 模型迁移使用旧模型历史结果做基线；新模型只重跑分层样本，人工只处理差异、低置信度和约 5% 的一致样本抽检。
- 智能抽样策略可配置常规抽样比例、置信度阈值、冷启动必审数和高等级必审范围；每次保存生成可追溯的策略修订号。
- 独立样本集可长期保存人工确认图片、基准等级和判断备注；迁移时可选择固定样本集，确保不同模型版本评测同一批图片。
- 基准回归可按素材包整包声明 L1～L5 真值，也可逐张设置；报告提供精确命中率、
  相邻命中率、混淆矩阵、逐张偏差和冻结评测理由，偏差样本可加入提示词找补
  队列。图片名中的 `L1`～`L5` 或“好/中等/中差/极差/过滤”只用于预填等级，
  创建前可逐张修改；单个基准集最多 10000 张，页面按 200 张分页预览，整包由服务端
  冻结。当前素材主流程不从 Excel 或图片 URL 获取原图。
- 标准评分协议会额外输出并校验搜索推荐消费字段，包括标题、SEO 标题、分类、风格、
  标签、缺点点评、设计理念、调用 A 初步分、过滤原因、水印与素材特征；这些字段可在
  初审中逐项纠偏，并随正式标签版本发布。自由实验仍保留任意原始输出，不强制该协议。
- 优化与回归按“案例池 → 组批与安全试跑 → 候选与配对回归 → 人工发布”连续
  展示；安全试跑不调用模型、不计费，也不会自动发布提示词。
- 明亮白底审图界面，无暗色模式；品牌色 `#CCED46`。

## 在当前电脑启动

Windows 依赖已安装且前端已构建时，双击：

`启动3d66标签系统.cmd`

两个 CMD 都只是 `scripts/windows/start.ps1` 的兼容壳；正式启动一定先执行
doctor。若中文文件名入口在某台电脑上被安全软件拦截，也可以双击纯英文备用
入口 `start-3d66.cmd`。

看到“3d66 标签系统已启动”但浏览器没有自动出现时，手动打开：

`http://127.0.0.1:8080`

管理员账号为 `sol`，密码使用项目最初约定的 Demo 密码。请勿把密码或 API Key 写进 Git。

## 到公司电脑重新配置

1. 从 Git 克隆项目到非 OneDrive、非 junction/reparse point 目录，建议
   `D:\3d66-label-system`。
2. 由公司软件分发准备 Python 3.11/3.12、Node.js 20～26 和 npm 10/11；
   仓库脚本不会安装系统软件。
3. 以普通用户运行 `scripts\windows\install.ps1`；`首次安装.cmd` 只是同一
   脚本的兼容壳。
4. 运行 `scripts\windows\doctor.ps1`，通过后再运行
   `scripts\windows\start.ps1` 或双击启动 CMD。
5. 登录后进入“模型配置”，填写公司电脑上的豆包 API Key，保存并测试连接。
6. 重新上传图片、创建评测任务。Demo 阶段不需要迁移家里电脑的数据。

Git 只保存代码、提示词和配置结构；`.venv`、`node_modules`、构建产物、数据库、图片、日志和 `.env` 都已排除。

## Windows 受控部署生命周期

Windows 唯一受控实现位于 `scripts/windows/`，要求普通用户权限，不修改
注册表、Windows 服务、防火墙、计划任务或 PowerShell 执行策略：

```powershell
.\scripts\windows\install.ps1 -Check
.\scripts\windows\install.ps1 -DryRun
.\scripts\windows\install.ps1
.\scripts\windows\doctor.ps1
.\scripts\windows\start.ps1
```

安装门禁固定为 Python 3.11/3.12、Node.js 20.x～26.x 和 npm 10.x/11.x。
实际安装只创建仓库内 `.venv`，按既有 requirements 安装依赖，再执行
`npm ci` 和正式前端构建；不创建或修改业务数据，也不启动服务。

`DATA_DIR` 优先级是显式 `-DataDir`、进程环境变量、仓库 `.env`、最后
`%LOCALAPPDATA%\3d66-label-system`。前三者必须是绝对路径，且任何解析结果
都不能位于代码仓库内。doctor 只读检查现有父目录、SQLite 完整性/迁移版本和
Windows 凭据引用，不创建数据目录、不调用 DPAPI 解密。

`start.ps1` 默认强制 `127.0.0.1`。只有本次调用前显式设置进程环境变量才会
改变监听地址，例如：

```powershell
$env:APP_HOST = '0.0.0.0'
.\scripts\windows\start.ps1
```

对外监听前必须单独完成公司网络、TLS、身份和防火墙审批；脚本不会替操作员
修改系统配置。

创建脱敏备份和只读验证/实际恢复：

```powershell
.\scripts\windows\backup.ps1
.\scripts\windows\backup.ps1 -BackupDir 'E:\3d66 backups'
.\scripts\windows\restore.ps1 -Backup 'E:\3d66 backups\3d66-backup-v1-YYYYMMDDTHHMMSSZ' -DryRun
.\scripts\windows\restore.ps1 -Backup 'E:\3d66 backups\3d66-backup-v1-YYYYMMDDTHHMMSSZ'
```

Windows 正式备份使用 SQLite backup API，不直接复制活跃数据库；会清空登录
会话和主/优化模型凭据字段，排除 logs、`.env` 和 DPAPI/Keychain 内容，并用
Windows v1 manifest 保存迁移版本、文件大小和 SHA-256。恢复拒绝路径穿越、
NTFS 特殊路径、symlink/junction/reparse point、篡改、未来迁移和仍在使用的
服务端口；实际替换前创建同卷 rollback snapshot，失败时自动补偿。

以上能力只在 macOS 上以临时假数据做过自动测试和静态审查，尚未完成真实
Windows/Windows Server、PowerShell parser、Ctrl+C、junction 和 DPAPI
当前用户范围实机验收。完整清单见 ADR-0017。

## macOS 凭据安全层状态

macOS Keychain 工程接线已完成：

- 通过 `ctypes` 直接调用 Security.framework 的通用密码 API，不经过
  `security` CLI、shell、命令行参数或临时文件；
- 主模型与提示词优化模型使用不同的稳定 account；同一 account 再次保存时
  原位覆盖；
- SQLite 的 `encrypted_api_key` 只保存
  `keychain:v1:model-config` 或 `keychain:v1:optimizer-config`，真实密钥只
  存在当前登录用户的 Keychain；
- Windows 新写入使用 `dpapi:v1:` 前缀，并继续兼容既有未加前缀的 DPAPI
  密文；Keychain 与 DPAPI 引用不能跨平台读取。

这只代表安全层及隔离 Keychain 测试已经完成，不代表 MacBook 安装部署或
真实模型联调已经完成。换系统或换用户后应在目标电脑重新填写 API Key。

## macOS 首次安装与启动

macOS 受控入口位于 `scripts/macos/`。所有脚本都可从任意工作目录运行，
路径支持空格；不会使用 `sudo`、Homebrew、远程安装脚本、shell rc、
launchd、系统配置或防火墙修改。

版本门禁：

- Python 3.11 或 3.12；
- Node.js 20.x～26.x；
- npm 10.x 或 11.x。

先做完全离线的只读检查或演练：

```bash
./scripts/macos/install.sh --check
./scripts/macos/install.sh --dry-run
```

首次安装：

```bash
./scripts/macos/install.sh
```

安装脚本只会在仓库内创建 `.venv`、按已有
`backend/requirements.txt` 安装依赖、执行 `frontend/npm ci` 和生产构建；
不会创建、删除或覆盖 `DATA_DIR`。

启动前诊断与前台启动：

```bash
./scripts/macos/doctor.sh
./scripts/macos/start.sh
```

`start.sh` 必须先通过 doctor，随后复用现有 Python launcher 并保持前台
运行；按 `Ctrl-C` 由 launcher 清理 worker。macOS 脚本默认只监听
`127.0.0.1`。只有用户在调用脚本时显式设置 `APP_HOST` 才会改变监听地址，
例如 `APP_HOST=0.0.0.0 ./scripts/macos/start.sh`；暴露到局域网前应另行完成
目标环境安全评估。脚本不安装 daemon，也不创建 launchd 服务。

以上说明的是代码能力和离线测试结果，不代表目标 MacBook 已完成安装、登录、
页面保存 Keychain 凭据或真实模型联调。

## macOS 备份与恢复

创建脱敏备份：

```bash
./scripts/macos/backup.sh
```

默认输出到 `~/Documents/3d66-label-system-backups`；也可显式指定仓库和数据
目录之外的位置：

```bash
./scripts/macos/backup.sh --backup-dir "/Volumes/Safe Disk/3d66 backups"
```

备份使用 Python `sqlite3` backup API 生成一致数据库副本，复制 `images/`，
并生成版本化 `manifest.json`（时间、数据库迁移版本、可用时的 Git commit、
相对文件路径、大小与 SHA-256）。目录权限收紧为 `700`，文件为 `600`。

正式备份不会包含 `logs/`、`.env`、API Key、Keychain/DPAPI 内容或登录会话。
数据库副本会清空 `session_tokens`，同时清空主模型和优化模型的
`encrypted_api_key` 字段，再执行 `VACUUM` 后计算哈希。因此恢复后会话不会
恢复，API Key 必须在目标机重新填写；禁止跨平台复制 Keychain 或 DPAPI。

恢复前可单独做只读校验：

```bash
./scripts/macos/restore.sh \
  --backup "/path/to/3d66-backup-v1-YYYYMMDDTHHMMSSZ" \
  --dry-run
```

实际恢复使用同一命令去掉 `--dry-run`。脚本仍会自动先完成一次 dry-run，
校验 manifest schema、相对路径、SHA-256、SQLite `integrity_check` 和迁移
版本，再检查服务端口必须停止。通过后先为当前 database/images 创建权限
收紧的本地临时回滚快照，再做原子替换；失败时自动补偿恢复，成功或成功
回滚后删除临时快照。

## 局域网访问

launcher 可能显示本机和局域网地址，但是否可达由实际绑定地址决定：

- 当前电脑：`http://127.0.0.1:8080`
- 同一局域网：例如 `http://192.168.1.20:8080`

受控脚本默认绑定 `127.0.0.1`，因此局域网地址默认不可达。只有显式完成安全
审批并设置进程 `APP_HOST` 后，其他审核员才可使用局域网地址；本仓库脚本不
修改防火墙。主机需要保持开机，启动窗口不能关闭。

## 日常操作

1. 在“素材”批量上传 JPG、PNG 或 WebP。
2. 选择图片并创建任务。
3. 后台处理器先调用 A；只有 `in_scope` 或 `boundary` 才继续调用 B。
4. 服务端按固定权重和等级限制计算最终分数。
5. 在“结果审核”查看原图、八维证据、缺陷、限制和版本快照；审核账号自动取当前登录账号。
6. 需要调整提示词时进入“提示词”；AI 只生成草案，保存后仍是新版本草稿。

没有配置 API Key 时，任务会保持排队，不会被标记为失败。

## 发布共享测试环境

项目主仓库为云效 Codeup：
`https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git`。

发布脚本会读取 Codeup `main` 的最新提交，制作临时发布包，通过 SSH 上传到
测试服务器 `192.168.1.35`，再执行服务器上的受保护发布脚本。服务器项目目录、
测试容器和业务数据目录彼此独立，发布失败会自动回滚到上一个提交。

当前 Windows 发布机已配置专用 SSH 密钥，正常发布不再要求输入服务器密码；
密钥路径为 `~/.ssh/3d66_label_test_ed25519`，不会进入项目仓库。更换电脑时，
需要先把新电脑的公钥加入测试服务器，并保留服务器上的固定免密发布规则。

仓库根目录不放置双击部署入口。Windows 和 macOS 的双击入口由仓库外的独立
部署工具目录提供；仓库内统一使用下面的命令，确认摘要无误后输入 `DEPLOY`：

```bash
python3 scripts/deploy-test.py
```

首次使用或只想检查发布包时，可执行：

```bash
python3 scripts/deploy-test.py --dry-run
```

发布完成后访问 `http://192.168.1.35:8081`，健康检查地址为
`http://192.168.1.35:8081/api/health`。这个流程不依赖 Jenkins；后续需要提交
审批、自动触发、构建记录或多人权限管理时，再把同一个脚本接入云效流水线即可。

## 从豆包 1.8 迁移到 2.0

旧模型停止服务后不需要重开 1.8，只要历史结果仍保存在本系统：

1. 先确认 1.8 结果包含模型、提示词、规则和引擎版本快照，并完成核心图片的人工确认。
2. 在“样本集”创建黄金样本，将人工确认图片收录进去；可修改基准等级并记录判断依据。
3. 在“模型配置”把候选模型改为 2.0，填写或更新 API Key，并测试连接。
4. 进入“模型迁移”，选择 1.8 历史结果作为旧模型基线，并选择固定样本集。也可以不选样本集，让系统自动分层抽样。
5. 固定样本集会全部重跑；自动抽样正式首轮建议 200 张，不足 200 时使用全部可用图片。
6. 系统使用当前 2.0 配置重跑同一批图片，并以样本集的人工等级为质量基准。
7. 高置信度且等级/分类一致的样本自动通过；等级或分类变化、低置信度、模型主动请求复核，以及约 5% 的一致样本进入人工队列。
8. 审核员只需判断“旧模型更好 / 效果相当 / 新模型更好”。出现人工确认的“旧模型更好”时，批次标记为发现回退。

“样本验收通过”表示该分层样本未发现人工确认的回退，不是对全部未来图片的绝对保证。上线后应持续抽检少量新图，监控数据漂移。

## 什么是“保存完整模型响应”

系统同时保存：

- 豆包接口返回的原始 JSON；
- 从文本中解析出的结构化 JSON；
- 服务端计算的分数、等级和限制；
- 模型、提示词、规则和评分引擎版本。

这样可以在模型升级、提示词调整或异常排查时还原“当时模型实际返回了什么”。原始响应可能增大数据库体积，也可能包含图片分析内容，因此只在当前电脑保存，不通过 Git 同步。

## 数据位置

Windows 默认数据位于：

`%LOCALAPPDATA%\3d66-label-system`

macOS 默认数据位于：

`~/Library/Application Support/3d66-label-system`

其中包含 SQLite 数据库、图片和日志。Windows 的 DPAPI 密文绑定当前用户；
macOS 数据库只持有当前用户 Keychain 条目的稳定引用。复制数据库到另一台
电脑、另一系统或另一用户后都不能直接取得原 API Key，必须重新填写。

如需改变数据目录，把 `.env.example` 复制为 `.env`，取消 `DATA_DIR` 注释并
填写仓库之外的绝对路径；显式 `DATA_DIR` 优先。`.env` 不会进入 Git。

## 开发验证

前端生产构建：

```powershell
cd frontend
npm run build
```

后端测试：

```powershell
cd backend
..\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

当前 Windows 生命周期专项：`29 passed`；安全层专项：
`15 passed, 2 skipped`；全后端：`420 passed, 2 skipped, 1 warning`。
Python 3.12 编译、脚本严格模式/UTF-8/参数/退出码/危险命令静态回归和
`git diff --check` 通过。当前 MacBook 没有 `pwsh`，未做 PowerShell parser
机检；本阶段未修改前端源码，未重新执行前端构建或浏览器验收。

以上 macOS 侧自动化测试全部只使用临时目录和明显假数据，未部署、未访问真实
Windows 目录或生产数据、未读取 DPAPI、未调用真实模型。Windows-only DPAPI
实机用例在 macOS 跳过；当前执行沙箱不允许访问登录 Keychain（OSStatus -50），
该真实 Keychain 用例也明确跳过，其他错误仍会失败。

### 原生 Windows 实机验收（2026-07-31）

上面跳过的 Windows-only 部分已在一台原生 Windows 11 验证机（PowerShell
5.1.26100.8115 Desktop、Python 3.11.4、Node v24.15.0）上单独跑过：
`doctor.ps1` 全量门禁 9/9 通过 ×3 轮（`CurrentUser`、`LocalMachine`、默认
`DATA_DIR`），含真实 DPAPI 内存回环；非空 API Key 保存后落库为 `dpapi:v1:` /
`dpapi-machine:v1:` 引用，明文不进数据库、不进接口响应、不进日志；解密结果
经字节级比对与原文一致；`current-user` 写入的引用在 `local-machine` 运行时下
仍能正常解密，即**切换 DPAPI 范围不会锁死既有凭据**。

两点必须注意：

- 该验收**绕过了 `install.ps1` 与 `start.ps1`**（手工复制其安装与启动步骤），
  因为这两个脚本在 PowerShell 5.1 上实测不可用——`start.ps1` 无论怎么调都起
  不了服务。仓库根三个 `.cmd` 入口都调 `powershell.exe`（即 5.1），所以在修掉
  缺陷或统一要求 PowerShell 7 之前，操作员双击 `.cmd` 的路径走不通。
- PowerShell 脚本层 `-DpapiScope` 默认 `LocalMachine`，而 Python 层
  `API_KEY_DPAPI_SCOPE` 默认 `current-user`；两层默认值相反，靠启动脚本注入
  环境变量才对齐。直接运行 `python -m app.launcher` 得到的是 `current-user`。
