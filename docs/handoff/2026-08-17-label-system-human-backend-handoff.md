# TPENG 标签实验台｜真人后端研发交接文档

> 日期：2026-08-17
>
> 交接对象：接手 LabelLab 长期开发、维护和生产化的后端研发团队
>
> 产品定位：LabelLab 是 TPENG 新标签体系的统一产品载体，也是标签/内容中台的通用底座
>
> 当前交接状态：Vibe Coding 形成的可运行工程和确定性测试基线；尚未授权真实生产数据写入

## 1. 交接目标

本次交接不是让研发“接着补几个接口”，而是把 LabelLab 从 AI 辅助快速搭建的工程，转成真人研发可以长期维护、评审、发布和追责的正式代码资产。

接手后的第一优先级是 45 天 MVP：完成知识图谱国内/海外整体与单体四批模型素材打标，写入两张目标表，跑通生产、消费、对账和 Badcase 回流。

## 2. 唯一代码仓库

云效 Codeup 主仓库：

`git@codeup.aliyun.com:3d66/tepeng/3d66.label-system.git`

浏览地址：

`https://codeup.aliyun.com/3d66/tepeng/3d66.label-system`

当前本地交接候选分支：

`codex/3d-shadow-dry-run-prep-20260816`

当前分支与交接候选状态：

- 当前功能基线提交：`2bdcd553793453678193ad2e043a4ae2d3b8d54d`；
- 当前 `origin/main`：`50e5b1572dd3ea5b65a7641ca50ae32fd850df07`；
- 当前分支相对 `origin/main` 为 ahead 11、behind 2；
- 本轮最终方案、DataWorks 关联键修订和本交接文档已在本地工作树形成交接候选，但尚未整理为新提交；
- 未经明确授权，不推送、不创建 MR、不合并、不部署。

正式交接时，研发只从 Codeup `main` 或明确的交接 MR 接手，不从聊天附件、临时压缩包、Synology 恢复副本或 `/Volumes/WorkSSD/OpenClaw/Codex` 旧副本接手。

## 3. 接手前必须阅读的文档

按顺序阅读：

1. `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md`：最终产品方案和 Roadmap；
2. `PRODUCT.md`：长期产品原则；
3. `AGENTS.md`：仓库工程约束；
4. `PROJECT_STATUS.md`：当前真实实现、验证和未发布边界；
5. `docs/decisions/0042-unified-labellab-product-carrier.md`：统一产品定位；
6. `docs/decisions/0043-canonical-facts-and-semantic-projection-boundaries.md`：事实和消费投影边界；
7. `docs/decisions/0045-dual-workspaces-and-table-projection-contract.md`：增量/存量和大表/小表合同；
8. `docs/decisions/0047-platform-semantic-tag-demand-contract.md`：平台语义字段合同；
9. `docs/contracts/2026-08-17-kg-four-batch-target-table-request-v1.md`：四批数据和两张目标表需求；
10. `docs/contracts/3d-su-readiness-freeze-v1.md`：真实接入前门禁；
11. `docs/discussion/tpeng-labellab-gap-register-20260813.md`：下一阶段 Gap；
12. 本交接文档。

### 3.1 方案交付形态

- `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md` 是可编辑权威源，所有需求、Roadmap 和边界变更先修改该文件；
- 同名 `.pdf` 是业务宣讲版，同名 `.docx` 是 13 页视觉手册，用于 Word 查看、评审和打印；
- 因当前 headless LibreOffice 对可编辑 OOXML 中文字形渲染不稳定，Word 手册使用已验收 PDF 页面图像生成，不得把 `.docx` 反向当作可编辑业务源；
- 重建命令为 `python scripts/build_tpeng_proposal_handout.py <source.md> <output.pdf> <output.docx>`，打印辅助位于 `scripts/print_tpeng_proposal_pdf.mjs`；
- 每次修改 Markdown 后必须同时重建 PDF/DOCX，再用 `render_docx.py` 逐页检查中文、表格换行、截断和空白页。

旧的 `CODEX_HANDOFF.md` 保存历史演进和早期 Demo 约束，可用于追溯，但不得覆盖上述最新文档的产品定位和执行边界。

## 4. 技术栈和运行形态

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11/3.12、FastAPI、SQLAlchemy、Pydantic |
| 数据库 | 当前为 SQLite；迁移为追加式、幂等，当前候选分支包含 migration 72 |
| Worker | 后台 Worker、五队列调度、检查点、重试和恢复 |
| 前端 | React、TypeScript、Vite |
| 部署 | Docker 测试环境；macOS/Windows 受控安装和启动脚本 |
| 测试 | pytest、前端 TypeScript 合同脚本、lint、production build |
| 凭据 | macOS Keychain、Windows DPAPI；Git 和数据库不保存明文 Token |

当前 SQLite 足以支持实验台和本地/测试验证。生产规模、并发和高可用要求上升时，后端团队需要评估迁移到公司统一关系数据库，但 API、版本、Outbox、幂等和发布合同不能因此改变。

## 5. 后端代码地图

### 5.1 应用入口与基础设施

| 文件 | 职责 |
|---|---|
| `backend/app/main.py` | FastAPI 主入口、路由注册和主要请求模型 |
| `backend/app/models.py` | SQLAlchemy 业务模型 |
| `backend/app/database.py` | 数据库会话和连接 |
| `backend/app/migrations/runner.py` | 追加式数据库迁移；禁止改写已发布迁移 |
| `backend/app/config.py` | 环境变量、数据目录和安全配置 |
| `backend/app/security.py` | 凭据引用和平台安全适配 |
| `backend/app/authz.py` | 权限和会话授权 |

### 5.2 标签生产和治理

| 文件 | 职责 |
|---|---|
| `backend/app/asset_identity.py` | 素材身份和版本 |
| `backend/app/field_demand_contracts.py` | 下游字段需求合同 |
| `backend/app/semantic_tag_contracts.py` | 语义字段 Schema 和校验 |
| `backend/app/semantic_tag_mapping.py` | 实体、别名和映射 |
| `backend/app/label_governance.py` | 候选事实、人工批准和正式发布 |
| `backend/app/semantic_tag_quality.py` | 字段级 Precision/Recall 和质量快照 |
| `backend/app/quality_assets.py` | 黄金集、挑战集和真值版本 |
| `backend/app/label_export.py` | 正式事实导出 |

### 5.3 模型、机制和纠偏

| 文件 | 职责 |
|---|---|
| `backend/app/evaluation_credentials.py` | 模型配置和凭据引用 |
| `backend/app/category_pipeline.py` | 类目流程注册 |
| `backend/app/mechanism_profiles.py` | 类目机制 Profile |
| `backend/app/category_evaluation_v3_revisions.py` | V3 合同多版本 |
| `backend/app/strategy_bundle.py` | 运行时冻结的模型/Prompt/机制组合 |
| `backend/app/baseline_regression.py` | 基准回归和冻结证据 |
| `backend/app/baseline_correction_orchestration.py` | 人工纠偏到 AI 候选和自动回归 |
| `backend/app/optimization_automation.py` | 自动优化编排 |

### 5.4 工作流、投影和回流

| 文件 | 职责 |
|---|---|
| `backend/app/script_registry.py` | 受控脚本定义和版本 |
| `backend/app/workflow_registry.py` | 工作流定义和版本 |
| `backend/app/workflow_runtime.py` | 生产运行、步骤、检查点和恢复 |
| `backend/app/queue_scheduler.py` | 五队列调度和并发配额 |
| `backend/app/projection_contracts.py` | 大维表/小表投影合同和对账 |
| `backend/app/shadow_projection.py` | Shadow 投影适配器和 Manifest |
| `backend/app/production_feedback.py` | 下游反馈和 Badcase 回流 |

### 5.5 3D/SU 首切片

| 文件 | 职责 |
|---|---|
| `backend/app/three_d_profile.py` | `model_3d_su` 类目 Profile |
| `backend/app/model_3d_su_category_seed.py` | 3D/SU 字段、Prompt 和 V3 合同 seed |
| `backend/app/three_d_readiness.py` | 真实接入 readiness 门禁 |
| `backend/app/source_identity_probe.py` | 来源身份只读探查合同 |
| `backend/app/three_d_workflow_fixture.py` | 3D/SU 确定性闭环 fixture |

## 6. 前端代码地图

| 目录/文件 | 职责 |
|---|---|
| `frontend/src/App.tsx` | 页面路由和应用入口 |
| `frontend/src/components/workspace-page.tsx` | 增量/存量工作区公共框架 |
| `frontend/src/pages/incremental-workspace-page.tsx` | 增量链路 |
| `frontend/src/pages/stock-workspace-page.tsx` | 存量链路 |
| `frontend/src/pages/operations-center-page.tsx` | 运行中心 |
| `frontend/src/pages/tag-demand-contracts-page.tsx` | 字段需求合同 |
| `frontend/src/pages/model-registry-page.tsx` | 模型注册中心 |
| `frontend/src/pages/projection-governance-page.tsx` | 投影和对账 |
| `frontend/src/pages/quality-assets-page.tsx` | 黄金集和质量资产 |
| `frontend/src/features/baseline-regression/` | 回归配置、五档矩阵和纠偏工作台 |
| `frontend/src/features/mechanism-config/` | 类目专用机制编辑器注册 |

前端只消费后端正式 API，不直接查询数据库，也不能读取候选表或人工处理中间表作为正式结果。

## 7. 45 天 MVP 的关键代码 Gap

以下是接手研发必须优先处理的真实 Gap：

1. `semantic_tag_contracts.py` 当前对 `model_3d_su` 的身份字段仍严格要求 `(res_type,ll_id)`，只覆盖国内来源；需要增加按 `site_scope` 路由的国内 `ll_id`、海外 `res_id` Source Adapter，不能把两套来源硬编码为一套。
2. `label_governance.py` 的 `content-ingress-v2` 当前只支持 `model_3d_su` 本地链路，需要接入真实来源合同、固定快照和批次 Manifest。
3. 海外主表与 extra 表虽已确认以 `res_id` 关联、同日 `dt` 对齐，但仍需大数据执行空值、重复、未匹配和多匹配探查。
4. 目标表 DDL、开发/生产映射、写入账号、单写者和回退操作人仍需大数据回执。
5. 当前 Shadow 投影适配器不能直接视为生产数据库 Writer；需要实现受控 Writer、最小权限、检查点、重试、补偿、熔断和对账。
6. 当前四批流程需要从 deterministic fixture 升级为真实工作流版本，但不能允许任意 SQL、Shell 或源码进入脚本注册中心。
7. 真实模型调用、调用上限、成本、超时、重试和限流必须纳入模型中心，不能写死在业务代码。
8. 下游 Badcase 入口、消费 Owner、SLA 和验收查询仍需与知识图谱团队完成联调。

## 8. 本地安装、启动和验证

### 8.1 macOS

```bash
./scripts/macos/install.sh --check
./scripts/macos/install.sh
./scripts/macos/doctor.sh
./scripts/macos/start.sh
```

### 8.2 Windows

```powershell
.\scripts\windows\install.ps1 -Check
.\scripts\windows\install.ps1
.\scripts\windows\doctor.ps1
.\scripts\windows\start.ps1
```

### 8.3 后端测试

测试必须使用隔离数据目录，禁止把开发测试写进现有业务数据：

```bash
TASK_DATA_DIR=$(mktemp -d)
DATA_DIR="$TASK_DATA_DIR" PYTHONPATH=. .venv/bin/python -m pytest -q backend/tests
```

### 8.4 前端验证

```bash
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build
```

关键合同脚本位于 `frontend/scripts/check-*.ts`，涉及字段、模型、工作区、运行中心、3D/SU readiness 和回归指标的改动必须运行对应合同测试。

## 9. 数据库和迁移规则

- 迁移只允许追加，不修改、删除或重排历史迁移；
- 每个迁移必须幂等，支持旧库升级和全新库初始化；
- 不回填或覆盖历史评测、人工真值、机制和正式标签事实；
- 结构变更必须补专项迁移测试，并在临时 `DATA_DIR` 跑全量测试；
- 部署前必须创建数据库快照，执行 `integrity_check` 和外键检查；
- 数据库恢复属于单独的破坏性操作，不能因为代码回滚自动执行。

## 10. Git、评审和发布规则

1. Codeup `main` 是唯一代码主线；
2. 每项需求使用独立分支和 MR，不直接在服务器改代码；
3. MR 必须列出目标、范围、非目标、迁移、测试、回退和验收；
4. 合并前至少通过受影响测试、后端全量、前端 lint/build 和 `git diff --check`；
5. 生产或共享测试环境只能部署 Codeup `main` 的明确提交；
6. 推送 main 不等于自动部署，部署必须由受保护脚本单独执行；
7. 部署前确认无运行中的评测、回归、纠偏、重跑和投影任务；
8. 部署后核对服务器 HEAD、静态 build SHA、健康检查、数据库完整性和迁移版本；
9. Token、密码、数据库、图片、日志、备份、`.env`、`.venv`、`node_modules` 和构建产物不得进入 Git。

## 11. 不可破坏的产品和数据边界

- LabelLab 是标签体系重构的统一产品载体，不再并行建设第二套系统；
- 业务类目只扩展 profile、字段、Prompt、规则和专用视图；
- 人工真值高于任何单个模型结果；
- AI 只能生成候选和回归证据，不能自动启用机制；
- 机制启用不等于标签事实发布，也不等于存量覆盖；
- 下游只读取正式发布事实；
- 搜索索引、知识图谱、向量索引和数据库表是消费投影，不是事实主库；
- Query×素材相关性、排序权重和知识图谱内部关系不进入素材事实；
- 发布和回滚都必须新增版本，不能改写历史；
- 未获授权不得执行真实 DataWorks SQL、数据库 DML、批量模型调用或生产发布。

## 12. 真人研发接管验收

接管完成必须同时满足：

1. 研发从 Codeup 干净克隆仓库，能够按文档安装和启动；
2. 后端全量测试、前端合同测试、lint 和 build 通过；
3. 研发能够说明资产、评测、人工真值、机制版本、标签事实和投影之间的关系；
4. 研发能够在测试环境完成一次 deterministic 3D/SU 闭环，并正确停在两个人工门；
5. 研发能够完成一次失败恢复、一次代码回滚演练和一次数据库恢复 dry-run；
6. 研发能够定位 45 天 MVP 的八项关键 Gap，并给出负责人和排期；
7. 研发确认不会把当前测试环境、fixture 或 Shadow 结果当成生产上线事实。

## 13. 接手后的第一张研发任务单

第一张任务单建议命名为：

**“知识图谱四批模型素材真实接入与双表投影 MVP”**

任务必须拆成以下可独立验收的子项：

1. 国内/海外 Source Adapter 与身份合同；
2. 四批固定快照 Manifest 和只读探查；
3. 字段合同、词表和整体/单体 Prompt 路由；
4. 真实模型执行、限流、成本和失败恢复；
5. 人工审核、纠偏、双人工门；
6. 两张目标表 Writer、幂等、对账和回退；
7. 下游读取验收和 Badcase 回流；
8. 端到端验收报告和运维手册。

任何子项未通过时，只阻塞对应阶段，不通过临时手工改表、跳过审批或绕过版本合同来“补齐结果”。
