# 3d66 标签系统｜新对话开发交接

> 更新时间：2026-07-29
> 项目目录：Windows 为 `D:\3d66-label-system`；macOS 研发仓库见当前工作目录
> 当前分支：`main`
> 功能基线提交：以 `git log -1` 的实时结果为准
> 当前工作树：内联纠偏、分阶段审核、历史纠偏安全预览及提示词发布前回归门禁
> 已完成编码与自动化验证；部署状态以 `PROJECT_STATUS.md` 最新段落为准

## 0. 新对话应当如何接手

把新对话的工作目录切换到当前系统的 3d66-label-system 仓库，先执行只读检查：

```powershell
git status --short
git log --oneline -10
```

2026-07-29 以后接手时，还必须先阅读 ADR-0014，保留以下新约束：

- 人工审核按初审、二审、仲裁、完成分工作台，事件只追加并带阶段/修订乐观锁。
- 历史 `已处理样本3d&SU` 在正式执行器落地前只能安全预览，不可直接形成 Gold。
- 优化候选必须经过三角色配对回归和人工批准，且只能由人显式发布。

然后完整阅读：

1. 本文件 `CODEX_HANDOFF.md`
2. `README.md`
3. `PRODUCT.md`
4. 与当前需求相关的代码和测试

不要从头重建项目，不要覆盖数据库，不要重置用户已有改动。每次开发都应完成：代码修改 → 后端测试 → 前端正式构建 → 重启服务 → 浏览器验收 → Git 提交。

## 1. 用户真正要解决的问题

这不是一个普通图片上传后台，而是一个面向 3D66 空间、建筑和软装图片的**模型评测、人工校准、提示词优化与模型迁移系统**。

核心目标：

- 使用豆包视觉大模型批量判断图片的分类、素材形态、拍摄方式、画质和美感质量。
- 每张素材可以被不同模型、不同 A/B 提示词版本反复评测；每次运行都是独立、可追溯的评测结果。
- 人工不仅修改最终 L1～L5，还要指出具体哪个维度错误、正确分数、错误原因和图片证据。
- 把人工纠错沉淀为黄金样本，供提示词更新和模型更新时做全量回归。
- 豆包从 1.8 升级到 2.0、旧模型停服时，不依赖重新调用旧模型；使用保存的旧结果和人工真值评估新模型。
- 尽量减少人工审核量，但不能通过降低质量来节约人力；高风险结果必审，稳定结果采用可复现抽样。
- 可以接入一个能力更强的提示词诊断模型，分析人工纠错样本并提出新提示词，但 AI 只能生成候选草案，不能自行覆盖和发布正式版本。

成功标准不是“模型返回了一个分数”，而是：

1. 结果结构完整且可解释；
2. 人工能快速定位错误；
3. 模型、提示词、评分规则和人工真值都可追溯；
4. 新版本发布前能证明在黄金样本上不低于旧版本；
5. 线上通过智能抽样持续发现漂移和回退。

## 2. 已确认的产品边界

当前是本地 Demo：

- 每天图片量：1000 张以内。
- 部署：Windows 主机运行，同一局域网内其他审核员通过浏览器访问。
- 登录：已有单一管理员登录；暂不做角色权限。
- 多人审核：需要，但“任务认领、防止同时修改”还未完成。
- 图片保存：本地永久保存，暂不做自动清理。
- 暂不接现有素材库、OSS、搜索或推荐系统。
- 主评测模型：暂时只接豆包。
- 提示词优化模型：另有 OpenAI 兼容配置入口，用于 SOL/高能力模型诊断。
- 中文界面，不做中英文双语。
- 项目代码必须放在非 OneDrive 目录；当前正确位置是 `D:\3d66-label-system`。
- Demo 换电脑时不迁移数据：在公司电脑重新配置 API Key、重新上传并重跑即可。

## 3. 不可破坏的业务原则

### 3.1 素材与评测结果必须分离

数据关系是：

```text
素材 Asset 1 ─── N 评测任务 EvaluationJob 1 ─── 1 评测结果 EvaluationResult
                                         └── N 人工审核 HumanReview
```

- “素材”页面只展示素材本身，不应混入某一次模型评分。
- 同一素材可以使用多个模型、多个提示词组合重复评测。
- “评测结果”列表按评测运行展示，所以同一素材可以出现多行。
- 进入审核详情时使用 `evaluation=<结果ID>`，不能只使用素材 ID。

该结构已经在提交 `48268e8` 完成，不要改回“素材只有一个最新结果”的旧结构。

### 3.2 A/B 两阶段提示词

- A：判断范围、分类、素材形态、拍摄方式、画质。
- B：仅对 `in_scope` 或 `boundary` 图片输出八个美感维度。
- `out_of_scope` 不调用 B，八维为空属于正常流程，不是数据丢失。
- B 的总分和最终 L1～L5 由服务端评分引擎计算；模型提供维度与证据，不直接控制最终总分。
- 高风险结果可触发一次短的自动复核；复核只能保持或降级，不能抬高分数。

曾经使用过过长的综合提示词，Doubao Lite 容易漏字段、机械同分和虚高。现在采用精简 A/B 提示词、严格 JSON 结构、服务端校验和高风险复核。不要重新合并成长提示词。

### 3.3 Skill 不能替代生产提示词

用户曾讨论“是否不用提示词、改用 Skill”。已确认的方向是：

- 生产环境调用豆包 API 时，提示词、JSON Schema、评分规则和版本快照仍是运行时标准。
- Skill 适合封装 Codex 的开发流程、评测方法或提示词维护方法，不能代替发送给豆包的生产提示词。
- 提示词是可版本化的业务资产；Skill 可以帮助创建和维护它，但不是同一层概念。

### 3.4 模型与提示词是有关联的组合，但不能绑死

- 模型与提示词分别版本化。
- 每次结果保存 `model_id + prompt_a_version + prompt_b_version + rubric_version + engine_version`。
- 更换模型不代表理论上必须重写提示词，但必须把“新模型 + 当前提示词”视为一个新候选组合重新回归。
- 如果回归暴露偏差，再创建适配新模型的新提示词版本；禁止覆盖旧提示词。
- 当前数据库通过任务和结果快照隐式记录组合，尚未建立独立的“模型×提示词发布配置”实体，见后续优先事项。

### 3.5 人工真值高于任何单个模型

- ChatGPT/SOL 可以辅助分析豆包错误，但不能自动成为黄金答案。
- 黄金答案应由人工确认，并包含等级、八维真值、分类/形态真值、原因和修订历史。
- 旧模型结果只能作为迁移基线，不能天然被当成正确答案。

## 4. 当前已经实现的完整工作流

### 4.1 登录和运行

- 有登录页和 Cookie 会话。
- 默认管理员账号为 `sol`。
- Demo 密码曾由用户口头指定；为避免泄漏，不在 Git 文档中保存明文。种子数据中只存哈希。
- API Key 不写入代码、Git 或交接文档。

### 4.2 素材

- 批量上传 JPG、PNG、WebP。
- 单张最大 25 MB。
- 通过 SHA-256 去重。
- 原图永久保存到本机数据目录。
- 素材列表不展示评测结论。

### 4.3 评测任务

- 创建任务时可选择 A、B 提示词版本。
- 后台 Worker 处理任务。
- 有全局暂停、继续和取消所有任务。
- 任务列表展示实际运行的提示词版本。
- 没有 API Key 时任务保持排队，不直接判失败。

### 4.4 模型配置

豆包主模型全部放在后台配置：

- Base URL、API Path、Model ID、API Key；
- temperature、max tokens、超时、重试、并发；
- structured output；
- 高风险自动复核开关。

API Key 在 Windows 使用当前用户 DPAPI，在 macOS 使用当前登录用户
Keychain；前端不会再次读回完整密钥。数据库在 macOS 只保存稳定 Keychain
引用。换系统、换用户或换电脑后必须重新填写。

### 4.5 评测结果与人工纠错

- 评测结果是列表，不是一张张大图入口。
- 支持文件名、状态、等级、分类、形态、画质、置信度、模型、提示词、审核人等筛选。
- 大图详情展示原图、A 结果、八维分数、证据、缺陷、等级限制、版本快照和高风险复核结果。
- 人工可以：确认、退回复核、纠正最终等级。
- 纠正时还能逐维填写：错误维度、模型值、人工值、原因码和补充说明。
- 文件名使用灰色细字；按钮加粗；审核按钮和箭头保持一行。

### 4.6 样本集与黄金真值

- 样本集分为普通测试集和黄金样本集。
- 上传/加入样本时可以批量指定预期等级。
- 黄金样本可记录完整 `truth_json`，并保存每次人工修订历史。
- 样本集可从草稿锁定；锁定黄金样本用于正式回归。
- 每个样本可以查看历史人工记录和历次提示词/模型评测明细。

### 4.7 提示词版本和 AI 优化

- 提示词 A/B 分别版本化，状态为 draft / published / archived。
- 新建和 AI 修改都生成新草稿，不覆盖旧版本。
- 简单“AI 修改”接口当前使用豆包主模型生成草稿。
- “提示词优化任务”使用单独的优化模型配置，读取样本集中的人工纠错，输出诊断、候选 system prompt、candidate user prompt 和 change note。
- 优化模型默认配置为 OpenAI 兼容接口，密钥同样只在后台填写。
- AI 产物仍需人工编辑、另存新版本、回归和发布。

### 4.8 黄金样本回归

- 可以选择 A/B 提示词和已锁定黄金样本集创建回归。
- 回归会对黄金集全部重跑并逐字段比较，记录通过率和失败项。
- 发布提示词时会自动创建相关黄金回归任务。

重要缺口：当前发布接口是“先把提示词设为 published，再创建回归”，并未真正阻止未通过版本上线。应改造成发布门禁，详见第 10 节。

### 4.9 模型迁移

- 可选择旧模型历史结果作为 baseline。
- 可使用固定样本集，或自动分层抽样。
- 新模型重跑同一批图，比较等级、分类、置信度和人工真值。
- 一致且高置信度结果可自动通过；差异、低置信度、模型主动复核和约 5% 一致样本进入人工队列。
- 人工判定：新模型更好 / 相当 / 旧模型更好。
- 发现“旧模型更好”后迁移批次标记为回退。

### 4.10 智能抽样审核 v1

最新提交 `5cca790` 已完成智能抽样审核。

四类队列：

| 队列 | 含义 |
|---|---|
| 必须审核 | 明确存在风险或属于冷启动/黄金样本 |
| 抽样审核 | 中风险信号或稳定随机抽中的常规结果 |
| 暂缓审核 | 当前没有明显风险且未被抽中 |
| 已审核 | 已人工确认或纠正 |

必须审核的主要规则：

- 人工曾退回；
- 属于已锁定黄金样本；
- 模型标记需要人工复核；
- 置信度缺失或低于 70%；
- L4/L5 高等级；
- 被模型判定为专业摄影；
- 严重、不可用或待确认画质；
- 高风险自动复核不稳定或发生降级；
- 同素材相邻结果相差至少 2 个等级；
- 新的模型×A×B 组合前 5 条结果；
- 八维不完整；
- 八个维度完全同分。

抽样审核的主要规则：

- 置信度 70%～89%；
- 中度画质问题；
- 同素材相邻结果相差 1 级；
- 范围边界或范围外抽查；
- 其余常规结果按 SHA-256 稳定抽取 10%，刷新后不会随机变化。

列表显示队列、P0～P100 优先级和原因；详情页显示完整入队依据。规则位于 `backend/app/review_sampling.py`。

2026-07-19 本地数据快照：69 条结果中，63 条必须审核、3 条抽样审核、0 条暂缓、3 条已审核。必审比例过高主要来自历史异常结果和大量模型×提示词冷启动组合，不代表未来长期比例。

### 4.11 智能抽样策略配置

- “模型配置”页可调整常规稳定抽样比例、低/中置信度阈值、新组合冷启动必审数和高等级必审起点。
- 默认值保持 v1 行为：10% 常规抽样、低于 70% 必审、低于 90% 抽样、新组合前 5 条必审、L4 起必审。
- 每次保存递增策略修订号；结果列表和详情返回 `smart-sampling-v1.1/policy-N`，便于定位当前队列采用的规则。
- 当前策略仍是全局单例；按组合、任务批次和时间范围组织待办，以及审核完成时固化策略快照，仍属于后续 P0。

### 4.12 P0-E 安全离线导入、E2 状态机与 E3 持久化 API（E0/E1/E2/E3）

本仓库为 P0-E 的独立隔离工作副本。**E0/E1 代码已由上游 OpenClaw 控制器提交为 `16cd2c75a5c39ddf94157e388860075cfaffcd4c`，并独立验证 39 项针对性测试及 228 项通用后端测试（macOS 上 1 项 Windows-only DPAPI 测试取消选择）。** E2 状态机编排层、E3 持久化/API 接线，以及 E3 前端安全编排工作区已于 2026-07-28 完成。

**明确边界**：前端只登记外部流程已验证的门禁证据。当前仍未真实上传
XLSX、联网下载图片、调用任何模型、写业务数据库、形成任何 Gold 样本或
发布任何候选。

**E0/E1 文件**（均已在上游控制器提交并验证）：

- `backend/app/p0e_safe_import.py`：只读 XLSX 预检。仅 `.xlsx`；受限 ZIP/XML 解析并拒绝公式/宏/外部关系/异常 ZIP/超限/不安全 XML；重复表头 → 稳定内部名并原样保留 RAW；`farmat → format` 仅候选映射（需人工确认），从不静默覆盖；按文件字节 SHA-256 得幂等批次键；Gold 锁缺失/已锁即 fail-closed；不写业务库。
- `backend/app/p0e_image_freeze.py`：默认拒绝任意 URL；仅显式精确域名 + HTTPS + 无 userinfo；逐跳校验 A/AAAA 公网属性并重校验重定向；默认传输 fail-closed 返回 `DNS_PINNING_UNAVAILABLE`（防 DNS rebinding）；Content-Type + 魔数 + Pillow 三重校验；流式临时落盘 + SHA-256 去重 + 原子替换 + 中断清理；来源 URL 去除 query/userinfo。
- `backend/app/p0e_candidate_package.py`：固定 seed 的离线确定性分层预览；排除非 3D、缺人工真值（含 3Dreason 缺 GT）、重复 URL 与冲突样本；始终 `forms_gold=false`、`downloads_performed=false`、`model_runs_performed=false`。
- `backend/tests/test_p0e_safe_import.py`：覆盖上述全部安全与确定性用例（含 SSRF 参数化、重定向重校验、DNS 变更、MIME/长度欺骗、临时清理、SHA 去重、manifest 原子确定性、697 排除等）。
- `backend/tests/conftest.py`：把 `backend/` 加入 `sys.path`，使 `import app` 在仓库根与 `backend/` 两种工作目录下都可用；从 `backend/` 运行为无操作。

**E2 新增文件**：

- `backend/app/p0e_canary_run.py`：纯函数状态机编排层。实现六态单调状态机（`draft → preflight_ready → approvals_ready → freeze_ready → candidate_ready → human_review_ready`）加终止态 `failed`/`cancelled`；所有转换为纯函数（无 I/O、无数据库、无模型），fail-closed，证据 URL 安全扫描，幂等 SHA-256 指纹，机器可读 `CanaryRunError`；五项不变量在所有快照中显式记录。
- `backend/tests/test_p0e_canary_run.py`：确定性测试，覆盖完整快乐路径、所有跳跃门控、回退禁止、终止态无法继续、所有缺失/无效审批场景、静默映射尝试、空白名单、固定 IP 未证明、不完整/不匹配 manifest、不足候选预览、候选声称 Gold/下载/模型调用、证据 URL 含 query/fragment/userinfo 拒绝、幂等性、机器可读错误、不变量全链路验证。
- `docs/decisions/0010-p0e-e2-canary-run-state-machine.md`：ADR-0010。

**E3 新增与修改**：

- `backend/app/models.py`、`backend/app/migrations/runner.py`：新增独立
  `CanaryRun` 表和迁移 17，保存规范计划、累积证据、当前 E2 快照、快照
  指纹、创建者和时间。
- `backend/app/p0e_canary_api.py`：独立 DTO、持久化服务和认证路由。创建
  幂等；转换只接受期望快照指纹与当前门禁证据；条件更新提供乐观锁；
  E2 错误保留稳定字段；响应检查五项 False 不变量并脱敏。
- `backend/app/main.py`：仅接入 E3 路由，复用现有登录用户依赖。
- `backend/tests/test_p0e_canary_api.py`：17 项迁移、认证、顺序、幂等、
  冲突、终止、URL、不变量与无业务副作用测试。
- `docs/decisions/0011-p0e-e3-canary-persistence-api.md`：ADR-0011。

**E3 前端工作区（当前未提交工作树）**：

- `frontend/src/App.tsx`、`frontend/src/components/app-shell.tsx`：新增
  09“金丝雀运行”导航与 `/canary-runs` lazy 路由。
- `frontend/src/lib/types.ts`：增加 `CanaryRun`、严格状态、计划和五项
  不变量响应类型。
- `frontend/src/lib/api.ts`：`ApiError` 向后兼容字符串 detail，并支持
  结构化 code/message/current_state/attempted_transition/retryable；
  不再把对象隐式变成 `[object Object]`。
- `frontend/src/pages/canary-runs-page.tsx`：创建运行、列表/详情、手动
  刷新、六态时间线、逐状态唯一下一门禁表单、当前指纹乐观锁、409/422
  可读错误、取消/失败二次确认、累积证据摘要和五项不变量告警。
- 页面始终醒目标明自己只是“安全编排与证据登记层”；预检表单明确是
  导入器产物的人工接线占位，冻结表单明确不会执行下载，候选交接明确
  `forms_gold/downloads_performed/model_runs_performed=false`。

不可破坏约束（本次严格遵守）：未引入通用 `Pipeline`/`Candidate` 实体；
未改变 `Asset 1:N EvaluationResult`、`evaluation_id` 审核、
StrategyBundle、五类队列及 P0-A/B/C.1/D 既有合同；未修改任何 E0/E1/E2
纯函数模块；E3 前端没有真实上传/下载执行器、真实数据、模型、Gold 或
发布效果。

验证说明：E3 API 专项 `17 passed`；E0/E1/E2/E3 指定组合
`124 passed`；迁移专项 `17 passed`；全后端排除 Windows-only DPAPI 后为
`313 passed, 1 deselected`。Python `compileall`、`git diff --check`、E3
OpenAPI 路径核验均通过。E3 前端新增后，`npm run lint` 与
`npm run build` 均通过，页面生成独立 lazy chunk。另以隔离 SQLite、临时
测试账号和真实 headless Chrome 完成登录、09 导航、创建、五步顺序推进、
终止态只读、五项 False 不变量与刷新后持久化恢复验收。首轮验收发现新建
运行后列表刷新可能短暂回选旧运行，已用缓存预置与 pending selection
保护修复，并在修复后重跑完整浏览器链通过。

仍未完成：真实 XLSX/图片冻结执行器接线、MacBook 安装部署、Windows
研发部署、真实数据/模型联调、Gold 形成和发布门禁。macOS Keychain
代码与隔离测试已由后续安全层阶段完成；后续阶段不得把前端登记动作当作
其他真实效果已经发生。

### 4.13 macOS Keychain 凭据安全层

基线提交 `9c67118` 上完成的跨平台凭据引用已进入提交 `b7d1574`，决策记录
见 ADR-0012：

- `backend/app/security.py` 使用标准库 `ctypes` 直接调用 macOS
  Security.framework 的 generic password API；没有使用 `security` CLI、
  shell、命令行参数、临时文件或第三方依赖。
- macOS 主模型固定使用 `model-config` account，提示词优化模型固定使用
  `optimizer-config` account。SQLite 只保存
  `keychain:v1:<account>`，保存同 account 时原位覆盖。
- Windows 新写入为 `dpapi:v1:<base64 ciphertext>`，继续兼容旧的无前缀
  DPAPI 密文。Keychain/DPAPI 引用不能跨平台读取，未知格式和不支持平台
  fail-closed。
- 配置 API 请求使用秘密类型，空密钥拒绝；API 响应、异常和 DTO 表示不
  回显明文。
- 平台无关测试模拟 Security.framework 与平台；macOS 真实隔离测试用随机
  service 和明显假密钥完成新增、读取、覆盖、读取新值及 `finally` 删除。

验证：安全专项 `15 passed, 1 skipped`；全后端
`328 passed, 1 skipped, 1 warning`。Windows 真实 DPAPI 用例在 macOS
由测试自身跳过；Python `compileall` 与 `git diff --check` 通过。未使用
真实 API Key，未调用任何真实模型。

边界：这代表代码与当前 Mac 的隔离 Keychain 验收完成，不代表目标 MacBook
已安装部署。MacBook 页面保存凭据、Windows 研发机 DPAPI、真实数据与真实
模型联调仍待目标环境验收。

### 4.14 macOS 首次安装、诊断、前台启动与脱敏灾备

基线提交 `b7d1574` 上的未提交工作树已完成 ADR-0013：

- macOS 默认 `DATA_DIR` 改为
  `~/Library/Application Support/3d66-label-system`；Windows
  `%LOCALAPPDATA%` 逻辑与显式 `DATA_DIR` 优先不变。
- `scripts/macos/` 提供 `install.sh`、`doctor.sh`、`start.sh`、
  `backup.sh`、`restore.sh`。均 `set -euo pipefail`、支持任意 cwd 和空格
  路径，拒绝非 macOS；不使用远程脚本、sudo、Homebrew、shell rc、
  launchd、系统设置或防火墙修改。
- 安装门禁：Python 3.11/3.12、Node.js 20～26、npm 10/11。
  `--check/--dry-run` 完全离线且不修改；实际安装只创建仓库 `.venv`、
  安装已有 requirements、`npm ci` 和生产构建，不处理用户数据。
- start 必须先 doctor，复用现有 launcher 前台运行并保留 `Ctrl-C` 清理；
  默认 `127.0.0.1`，仅接受调用时用户显式 `APP_HOST` 覆盖，不创建 daemon。
- `backend/app/macos_deploy.py` 用标准库承载 doctor/backup/restore。正式
  备份用 SQLite backup API，并复制普通图片文件；v1 manifest 保存 UTC
  时间、迁移版本、可得 Git commit、相对路径、大小与 SHA-256。
- 备份副本清空 `session_tokens`、主模型和优化模型
  `encrypted_api_key`，`VACUUM` 后才计算哈希；不含 logs、`.env`、API Key、
  Keychain/DPAPI 内容或引用。目录 700、文件 600。
- restore 实际写入前自动先 dry-run，校验 manifest、路径穿越、文件清单、
  hash、SQLite integrity、迁移上界及脱敏状态；服务端口占用即拒绝。通过后
  创建临时 rollback snapshot，以同文件系统 staging 原子替换
  database/images，失败自动恢复原数据。

验证：专项 `20 passed`；全后端隔离临时 `DATA_DIR`
`348 passed, 1 skipped, 1 warning`；五脚本 `bash -n`、Python
`compileall`、从仓库外 cwd 执行 `install.sh --check/--dry-run`、
`git diff --check` 通过。所有新增测试只使用 `tmp_path` 明显假数据。

边界：未在目标 MacBook 安装或启动，未登录或通过页面保存真实 Keychain
凭据，未访问外网，未调用真实模型，未接 XLSX/图片冻结执行器，未形成 Gold
或发布。Windows 部署与真实 DPAPI 回归仍待完成。

## 5. 美感维度和评分约束

当前固定八维：

1. 构图与机位 `composition_viewpoint`
2. 光影与氛围 `lighting_atmosphere`
3. 色彩与材质 `color_material`
4. 空间设计与家具软装 `spatial_design_furnishing`
5. 视觉层级 `visual_hierarchy`
6. 细节完成度 `detail_completion`
7. 灵感与参考价值 `inspiration_reference`
8. 画面呈现完整性 `presentation_integrity`

用户已经明确提出：维度未来可能变化。因此不要把“八维永远固定”当作最终产品假设。当前维度仍散落在提示词、评分引擎、前端标签和校验代码中；未来需要引入正式的 Rubric/Dimension Schema 版本实体。

## 6. 视觉与交互约束

- 网站名：`3d66 标签系统`。
- 品牌色：`#CCED46`。
- 所有页面使用白底/浅色，不做暗色模式，尤其审图画布必须白底。
- 全站字体：微软雅黑；不要再改回衬线字体。
- 所有按钮文字加粗。
- 分割线要淡，避免粗重边框。
- 文件名使用灰色、较细字体。
- 风格目标：前沿、编辑感、建筑刊物式、克制、信息密度高。
- 禁止模板化后台、紫蓝渐变、玻璃拟态、霓虹光、重阴影、胶囊堆叠和无意义卡片网格。
- 核心原则：证据先于结论，状态不能只依赖颜色表达。

详细设计原则见 `PRODUCT.md` 和 `DESIGN.md`。

## 7. 技术栈与代码地图

### 技术栈

- 后端：Python、FastAPI、SQLAlchemy、SQLite、Uvicorn。
- 前端：React 19、TypeScript、Vite、Tailwind CSS 4、TanStack Query、React Router、Radix、Phosphor Icons。
- 模型接口：豆包 Ark OpenAI-compatible Chat Completions。
- 凭据保护：Windows DPAPI；macOS Security.framework Keychain。数据库只
  保存版本化 DPAPI 密文或不可逆 Keychain account 引用。
- 没有使用 PHP，这是用户确认后的技术选择。

### 关键后端文件

| 文件 | 作用 |
|---|---|
| `backend/app/main.py` | API、认证、素材、任务、提示词、审核、样本、回归、迁移 |
| `backend/app/models.py` | 数据模型 |
| `backend/app/worker.py` | 评测任务处理器和 A/B 调用流程 |
| `backend/app/doubao.py` | 豆包接口客户端与响应解析 |
| `backend/app/scoring.py` | 服务端评分、等级和限制 |
| `backend/app/schema_adapter.py` | 兼容/修复历史提示词返回结构 |
| `backend/app/risk_review.py` | 高风险自动复核 |
| `backend/app/review_sampling.py` | 智能抽样 v1 |
| `backend/app/regression.py` | 黄金真值与回归比较 |
| `backend/app/migration.py` | 模型迁移比较 |
| `backend/app/optimizer.py` | 使用高能力模型分析人工纠错并生成候选提示词 |
| `backend/app/security.py` | 密码、会话、Windows DPAPI 与 macOS Keychain 凭据引用 |
| `backend/app/seed.py` | 默认账号、模型配置和提示词种子 |

### 关键前端文件

| 文件 | 作用 |
|---|---|
| `frontend/src/pages/assets-page.tsx` | 纯素材列表和创建任务 |
| `frontend/src/pages/jobs-page.tsx` | 任务列表、暂停/继续/取消 |
| `frontend/src/pages/review-list.tsx` | 评测结果列表、筛选、智能抽样队列 |
| `frontend/src/pages/review-page.tsx` | 大图审核详情 |
| `frontend/src/pages/review-correction-form.tsx` | 逐维纠错表单 |
| `frontend/src/pages/prompts-page.tsx` | 提示词版本、AI 草案、优化任务和回归 |
| `frontend/src/pages/model-page.tsx` | 豆包与优化模型配置 |
| `frontend/src/pages/sample-sets-page.tsx` | 普通/黄金样本、真值和历史 |
| `frontend/src/pages/migrations-page.tsx` | 模型迁移批次和人工比较 |
| `frontend/src/index.css` | 全局视觉、微软雅黑、品牌色和文件名样式 |

提示词源文件位于 `prompts/`。Lite 候选版本是：

- `space-precheck-v1.4-lite.1.md`
- `space-aesthetic-v1.4-lite.1.md`

## 8. 本机运行、数据和 Git

### 启动

依赖和前端已构建时，双击：

- `启动3d66标签系统.cmd`
- 或英文备用入口 `start-3d66.cmd`

访问：

- 本机：`http://127.0.0.1:8080`
- 局域网：启动窗口展示的 `http://局域网IP:8080`

### 数据

Windows 默认数据目录：

```text
%LOCALAPPDATA%\3d66-label-system
```

macOS 默认数据目录：

```text
~/Library/Application Support/3d66-label-system
```

其中保存：

- `database/app.db`
- `images/`
- `logs/`

数据库、图片、日志、`.env`、`.venv`、`node_modules` 和构建产物都不进
Git。Windows DPAPI 密文绑定当前用户；macOS 运行数据库只有当前用户
Keychain 引用。ADR-0013 正式备份会连引用一并清空，因此恢复后必须重新
登录并填写 API Key；禁止跨平台迁移 Keychain 或 DPAPI。

### Git 状态

- 当前是本地 Git 仓库，分支 `main`。
- `origin` 已配置为私有项目既有远程；当前 `main` 相对 `origin/main`
  领先 6 个提交。本阶段按任务要求不 commit、不 push。
- 后续推送前仍必须确认工作树只包含本次相关文件，不得公开秘密或无关修改。

## 9. 最近完成的重要提交

```text
b7d1574 feat: secure macOS credentials with Keychain
9c67118 feat: add P0-E canary orchestration workspace
85f41d8 feat: persist authenticated P0-E canary runs
5cca790 feat: add intelligent review sampling
48268e8 refactor: separate assets from evaluation runs
9a946ff feat: add compact lite prompts and risk review
c5f9cd2 feat: add golden sample regression workflow
005dac0 fix: prevent quality and grade calibration collapse
a44c664 style: switch interface to Microsoft YaHei
d6902bf fix: calibrate aesthetic grades and result states
094a879 feat: split comprehensive aesthetic prompt
```

不要仅看提交标题判断现状；修改前仍需阅读相关代码和测试。

## 10. 下一步开发优先级

### P0：把智能抽样从规则展示升级为可运营闭环

1. 队列默认支持按“当前模型×提示词组合 / 当前任务批次 / 时间范围”查看，避免所有历史结果一起进入今日待办。
2. 新增审核任务认领、审核人分配、占用状态和乐观锁，避免多人同时修改同一结果。
3. 增加批次进度：应审、已审、剩余、发现问题数、问题率和预计人力。
4. 人工审核完成后要自动从待办队列移出，并记录当时的抽样规则版本。

### P0：建立真正的提示词发布门禁

当前流程会先发布再回归，需要改为：

```text
候选草稿 → 锁定模型×A×B组合 → 全量黄金回归 → 达标 → 人工批准发布
                                      └→ 未达标：阻止发布并展示失败样本
```

- 禁止回归未完成或未达阈值的候选直接成为 published。
- 如允许人工强制发布，必须填写理由并保留审计记录。
- 发布门禁应同时固定模型 ID、A、B、Rubric 和评分引擎版本。

### P0：让“模型×提示词组合”成为一等配置

新增类似 `EvaluationProfile` / `ReleaseBundle` 的实体，建议包含：

- 名称和版本；
- model config/model ID；
- A prompt ID；
- B prompt ID；
- rubric/dimension schema；
- scoring engine version；
- sampling policy version；
- draft / validating / active / retired 状态；
- 回归报告和发布时间。

这样更换模型时不会误以为“只改 Model ID 就是同一版本”。

### P1：维度和评分规则版本化

- 把八维定义、权重、等级锚点、适用素材类型和等级限制从硬编码迁移为版本实体。
- 支持新增、删除或重命名维度，但必须保留旧结果的旧 Schema 快照。
- 跨 Schema 版本不能直接逐字段比较，需要明确映射规则。

### P1：大数据量和审核效率

- 前端目前一次最多加载 1000 条结果并在浏览器筛选；永久保存后会超过 1000，需要服务端分页、筛选和聚合计数。
- 智能抽样接口当前每次请求扫描全部历史结果，Demo 可接受，数据增长后应持久化 sampling decision 或做增量计算。
- 增加快捷键、下一条自动跳转、批量确认低风险抽样和审核原因模板，但批量确认必须有限制和审计。

### P1：质量报表

按模型×提示词×素材类型统计：

- 等级准确率、相邻等级准确率；
- 分类/形态/画质准确率；
- 八维 MAE 和每维偏高/偏低；
- L4/L5 精确率；
- 专业摄影误判率、画质正常误判率；
- 空字段率、JSON 解析失败率、八维同分率；
- 人工介入率、抽样问题率和版本回退率。

### P2：当前暂缓的能力

- 角色权限；
- 对象存储；
- 素材库接入；
- 图片保留策略；
- 搜索/推荐同步；

除非用户重新授权，不要提前扩展这些范围。

## 11. 已知风险和注意事项

1. 当前本地历史评分包含早期长提示词造成的空字段、画质误判、专业摄影误判和机械同分，不应直接当黄金真值。
2. Lite 模型对长指令较敏感；优先使用短 A/B、硬约束、JSON Schema 和服务端校验，不要只靠继续堆提示词。
3. 智能抽样 v1 的规则和 10% 比例硬编码在 `review_sampling.py`。
4. 当前 63/69 必审比例不适合作为长期目标，后续应按活跃组合/任务批次收敛队列。
5. 提示词发布尚未被回归结果真正阻断。
6. 多人可同时打开页面，但还没有防冲突的任务认领或版本锁。
7. 测试计数随里程碑变化；以当前命令结果为准。2026-07-28 macOS 部署
   生命周期阶段全后端为 `348 passed, 1 skipped, 1 warning`。
8. 测试有一条 FastAPI TestClient/httpx 兼容性弃用警告，不影响当前运行，但未来升级依赖时需处理。
9. 本地 Git 暂无 remote，机器损坏会丢失未备份代码。
10. 不要在输出、日志、截图、Git 或 Markdown 中展示真实 API Key。

## 12. 当前验证基线

最近一次完整后端验证（macOS 部署生命周期阶段）：

- 部署专项：`20 passed`。
- 全后端（隔离临时 `DATA_DIR`）：`348 passed, 1 skipped, 1 warning`。
- 五个 shell 脚本 `bash -n`、Python `compileall`、
  `install.sh --check/--dry-run` 与 `git diff --check` 通过。
- 本阶段没有修改前端，未重新执行前端构建、服务启动或浏览器验收。
- 未部署、未访问外网、未读取真实 Keychain、未使用真实 API Key 或调用
  真实模型；目标 MacBook 与 Windows 部署均尚未验收。

验证命令：

```powershell
cd D:\3d66-label-system\backend
..\.venv\Scripts\python.exe -X utf8 -m pytest tests -q

cd D:\3d66-label-system\frontend
npm.cmd run build
```

## 13. 给新对话的工作准则

- 先复现问题并查看真实模型原始响应，再改解析器、提示词或评分规则；不要凭截图猜测。
- 修改提示词时创建新版本，不覆盖 published/archived 历史。
- 修改评分维度、等级边界或限制时必须提升 Rubric/Engine 版本并保留旧结果解释能力。
- 任何模型升级都必须以黄金人工真值为主、旧模型历史结果为辅。
- 优先节约“重复确认正确结果”的人力，不要减少高风险和冷启动样本的审核。
- UI 继续保持白底、微软雅黑、淡分割线、品牌绿和高信息密度。
- 新功能应有后端测试；界面修改应正式构建并用真实浏览器验收。
- 当前工作树可能包含用户或前一对话未提交的修改；绝不使用 `git reset --hard` 或覆盖式恢复。

## 14. 建议新对话的第一条指令

可以直接对新对话说：

> 请进入 `D:\3d66-label-system`，完整阅读 `CODEX_HANDOFF.md`、`README.md` 和 `PRODUCT.md`，检查 Git 状态和当前服务。不要重建项目，也不要覆盖本地数据。先向我概括当前产品目标、已完成功能和最高优先级缺口，然后继续我接下来的开发要求。
