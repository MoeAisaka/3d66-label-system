# 3d66 标签系统｜当前项目状态

> 最后更新：2026-07-29
> 本文件只记录“现在做到哪里”；长期原则见 `PRODUCT.md` 和 `AGENTS.md`，历史背景见 `CODEX_HANDOFF.md`。

## 仓库状态

- 项目目录：`D:\3d66-label-system`
- 当前分支：`main`
- 当前功能基线：以 `main` 分支最新提交为准，精确提交号请执行 `git log -1` 查看。
- 远程仓库：`origin = https://github.com/chishiyu07-max/3d66-label-system.git`
- 分支关系：每次交付以 `git status -sb` 的实时结果为准。

## 最新完成：内联纠偏、分阶段审核与提示词发布前门禁（2026-07-29）

> 决策见 ADR-0014。当前代码与确定性验证已完成；历史纠偏入口仍是安全预览，
> 不会静默写入业务库或形成 Gold。

已完成：

- 人工纠偏改为模型八维评分卡原位编辑。审核员可直接点击 1～5 分、选择该维度
  的结构化原因并补充说明；页面显示“模型分 → 人工分”、支持单维撤销，并在
  底部预览服务端同规则重算的总分与等级。修改维度必须填写原因，最终总分和
  等级仍由服务端权威计算。
- 人工审核拆为初审、二审、冲突仲裁和已完成四个独立工作台。结果以
  `review_stage + review_revision` 乐观锁流转，审核事件只追加、不覆盖；高风险、
  L4/L5、纠正和退回进入二审，二审不一致进入仲裁。
- 新增“历史纠偏预览”。五份 `已处理样本3d&SU` XLSX 可在浏览器内一次选择并
  安全解析、稳定去重、按目标错例/稳定对照/盲测保留/原因专项分层；全程不联网、
  不下载图片、不调用模型、不写业务库、不形成 Gold。
- 真实五表预览结果：4,801 行、4,771 条去重记录、30 条重复；目标错例
  2,336、稳定对照 882、盲测保留 858、原因专项 695。
- 提示词优化页可把已完成的优化建议显式物化为新的候选草稿，并绑定目标错例、
  稳定对照、盲测三角色小样本配对回归。候选只有在回归结论为 pass 且人工批准后，
  才能由人显式发布；批准接口本身不会自动发布。
- 新增 StrategyBundle 发现接口、优化诊断 `sample_items` 与回归
  `trigger_prompt_id`，前端不再猜测内部标识。

验证：

- 全后端：`369 passed, 1 skipped, 1 warning`。
- Python `compileall`、前端 TypeScript/Vite 正式构建、`git diff --check`
  全部通过。
- 使用隔离 SQLite、临时测试账号和确定性评测记录完成认证浏览器验收：四个审核
  工作台、评分原位改分、结构化原因、总分/等级预览、历史纠偏入口和提示词页面
  均可达；应用控制台无错误。
- 测试未调用真实豆包或 SOL，未操作正式数据库，未创建 Gold，未发布提示词。

明确未完成：

- 历史纠偏目前只形成可审阅的分层预览；正式候选集导入、图片冻结、抽检确认和
  Gold 升级执行器仍未接通。
- 配对回归任务真实执行仍取决于模型凭据、Worker 和已锁定样本；本阶段只完成
  候选—回归—批准—发布的编排与硬门禁，没有替用户运行付费模型或发布候选。

## 最新完成：macOS 首次安装与脱敏灾备生命周期（2026-07-28）

> 基线提交：`b7d1574 feat: secure macOS credentials with Keychain`。本次
> 修改保留为未提交工作树，未提交、未推送、未部署；决策见 ADR-0013。

已完成：

- macOS 默认数据目录改为
  `~/Library/Application Support/3d66-label-system`；Windows
  `%LOCALAPPDATA%` 与显式 `DATA_DIR` 优先行为保持不变。
- `scripts/macos/` 新增 `install.sh`、`doctor.sh`、`start.sh`、
  `backup.sh`、`restore.sh`；全部从脚本位置解析仓库、支持路径空格、
  `set -euo pipefail`，不使用远程脚本、sudo、Homebrew、shell rc、
  launchd、系统设置或防火墙修改。
- 安装门禁为 Python 3.11/3.12、Node.js 20～26、npm 10/11；
  `--check/--dry-run` 不安装、不构建、不联网。实际安装只允许仓库内
  `.venv`、已有 requirements、`npm ci` 和前端生产构建。
- start 必须先 doctor，再以前台方式复用现有 launcher；默认仅监听
  `127.0.0.1`，只接受调用脚本时用户显式 `APP_HOST` 覆盖，不创建常驻服务。
- `backend/app/macos_deploy.py` 用标准库实现一致 SQLite backup、图片普通
  文件复制、v1 manifest/hash、权限收紧、恢复 dry-run、迁移/完整性门禁、
  服务运行拒绝、同文件系统 staging、原子替换与失败自动回滚。
- 正式备份会清空 `session_tokens` 以及主模型/优化模型
  `encrypted_api_key`，执行 `VACUUM` 后再哈希；不包含 logs、`.env`、
  API Key、Keychain/DPAPI 内容或引用。恢复后会话和凭据不恢复，目标机必须
  重新登录并填写 API Key。

验证：

- macOS 部署专项：`20 passed`。
- 全后端在隔离临时 `DATA_DIR`：`348 passed, 1 skipped, 1 warning`。
- 五个脚本 `bash -n`、Python `compileall`、
  `install.sh --check/--dry-run`（从仓库外 cwd）与 `git diff --check`
  通过。
- 全部新增测试仅使用 `tmp_path` 和明显假数据；未部署、未访问外网、未读取
  真实 Keychain、未调用真实模型，未改前端源码，因此未重跑浏览器验收。

明确未完成：

- 目标 MacBook 的真实安装、启动、登录、页面保存 Keychain 凭据、公司内网
  可达性与备份恢复演练未执行。
- 真实 API Key、真实模型、真实数据、XLSX/图片冻结执行器、Gold 形成与发布
  均未完成。
- Windows 研发机部署与真实 DPAPI 回归仍未完成。

## 最新完成：macOS Keychain 凭据安全层（2026-07-28）

> 基线提交：`9c67118 feat: add P0-E canary orchestration workspace`，成果
> 已进入提交 `b7d1574 feat: secure macOS credentials with Keychain`；
> 决策见 ADR-0012。

已完成：

- `backend/app/security.py` 新增无第三方依赖的 macOS Security.framework
  generic password 封装；通过 `ctypes` 直接调用
  `SecItemAdd/CopyMatching/Update/Delete`，所有函数均设置
  `argtypes/restype`，CoreFoundation 创建/复制对象在所有路径释放。
- macOS 数据库只保存 `keychain:v1:model-config` 或
  `keychain:v1:optimizer-config`；两个稳定 account 分离。真实密钥只进入
  当前登录用户 Keychain，同 account 再次保存原位覆盖。
- Windows 新写入增加 `dpapi:v1:` 前缀，仍兼容既有无前缀 DPAPI 密文；
  未知引用、错平台引用和不支持平台全部 fail-closed。
- 配置请求用秘密类型承载 API Key；空密钥拒绝，响应、异常和 DTO 表示不
  回显明文。未新增依赖、数据库迁移、前端或模型行为。
- 安全专项：`15 passed, 1 skipped`；macOS 真实隔离 Keychain 测试执行了
  新增、读取、原位覆盖、读取新值、`finally` 删除与删除后不存在检查。
- 全后端：`328 passed, 1 skipped, 1 warning`。Windows 真实 DPAPI 测试在
  macOS 由用例自身跳过，不再由父级命令手工排除。
- Python `compileall` 与 `git diff --check`：通过。

明确未完成：

- 目标 MacBook 的安装、启动、登录、页面保存凭据与运行部署验收未完成。
- 未使用真实 API Key，未触发真实模型连接、评测或提示词优化。
- Windows 研发机真实 DPAPI 回归与部署、真实数据联调仍未完成。

## 最新完成：P0-E E3 金丝雀前端编排工作区（2026-07-28）

> 基线提交：`85f41d8 feat: persist authenticated P0-E canary runs`。本次按任务
> 边界保留为未提交工作树，未提交、未推送、未部署。

已完成：

- 主导航新增 09“金丝雀运行”，路由 `/canary-runs` 采用 lazy 加载；
  页面接入已认证的 E3 创建、列表、详情、五个顺序转换、取消和失败 API。
- 创建计划固定 `domain=3D`，目标数量 30～50（默认 40），支持 seed 和
  可选显示名称；列表与详情支持手动刷新和选择。
- 六态门禁以清晰时间线展示；每个非终止状态只出现唯一下一门禁的结构化
  表单。所有布尔审批项默认 False，字段映射必须按
  `源字段 => 目标字段` 逐行人工确认。
- 所有推进与终止请求发送当前 `snapshot_fingerprint`；409 明确提示
  “快照已变化，请刷新后重试”，422 优先展示稳定的 code、message、
  current_state、attempted_transition 和 retryable。
- 非终止态提供带原因的二次确认区域，可明确取消或标记失败；成功终止态、
  失败态和取消态只读，不提供恢复入口。
- 详情展示运行标识、创建信息、时间、计划、累积证据摘要、可折叠指纹和
  五项安全不变量；任一不变量不是明确 False 时显示危险告警。证据只以
  纯文本摘要呈现，不生成可点击 URL。
- `frontend/src/lib/api.ts` 的 `ApiError` 向后兼容字符串 detail，并安全
  支持结构化 detail，不再把对象隐式渲染成 `[object Object]`。

明确边界：

- 当前页只是“安全编排与证据登记层”，不会上传 XLSX、下载图片、调用
  模型、写入 `Asset`/`EvaluationResult`、形成 Gold 或发布。
- 真实 XLSX 导入器、图片冻结/下载执行器、模型执行器、业务库接线、
  Gold/发布流程尚未接入。
- macOS Keychain 代码与隔离测试已在后续安全层阶段完成；Windows 研发
  部署、MacBook 安装部署、真实数据与真实模型联调仍未完成。当前没有把
  表单登记描述为这些能力已经发生。

验证：

- 前端 `npm run lint`：通过。
- 前端 `npm run build`：通过；`canary-runs-page` 生成独立 lazy chunk。
- 使用隔离 SQLite、临时测试账号与真实 headless Chrome 完成认证浏览器验收：
  登录 → 09 导航 → 创建 → 预检 → 审批 → 冻结 → 候选 → 人工审核就绪
  → 刷新恢复；终止态保持只读，五项安全不变量全部为 `false`。
- 首轮浏览器验收发现新建运行后列表刷新可能短暂回选旧运行；已通过缓存预置
  和 pending selection 保护修复，修复后完整链路复验通过。

## 最新完成：P0-E E3 金丝雀持久化与认证 API（2026-07-28）

> 本段仅为持久化与 API 接线，**未真实下载、未调用模型、未写
> Asset/EvaluationResult、未形成 Gold、未发布；该后端提交当时尚未完成
> 前端，前端工作区现已在上节完成，部署仍未完成**。
> 决策记录见 ADR-0011。

新增：

- `CanaryRun` 模型与迁移 17：唯一 `run_id`、可选显示名称、当前状态、
  规范 JSON 计划/累积证据/当前快照、快照指纹、创建者与时间；数据库
  约束状态和 JSON，读取边界复核指纹、完整快照与五项 False 不变量。
- `backend/app/p0e_canary_api.py`：认证后的创建、列表、详情、五个顺序
  转换、取消和失败 API；所有推进调用 E2 纯函数。
- 相同计划幂等复用 `run_id`；计划或显示名称漂移返回 409；转换使用
  `expected_snapshot_fingerprint` 条件更新，陈旧请求返回 409，不允许
  last-write-wins；相同规范证据可幂等重放，不同证据冲突。
- 请求 DTO 禁止提交额外的状态、指纹或运行级不变量；E2
  `CanaryRunError` 以稳定五字段映射为 422；不存在运行返回明确 404。
- 证据 URL 含 query、fragment 或 userinfo 时 fail-closed；响应固定返回
  五项 False 不变量、时间和创建者，并执行读取边界脱敏。
- `backend/tests/test_p0e_canary_api.py`：17 项测试覆盖迁移 17 旧库升级、
  全端点认证、创建幂等与漂移、精确 `3D`、完整顺序、跳级/回退/终止态、
  缺证据、URL 安全、陈旧冲突、重复转换、取消/失败、列表详情、规范 JSON
  和无 Asset/EvaluationResult 副作用。

验证：

- E3 API 专项：`17 passed`。
- E0/E1/E2/E3 指定组合：`124 passed`。
- 迁移专项：`17 passed`；既有迁移权威清单及最高版本断言已同步至 17。
- 全后端（排除 Windows-only DPAPI）：`313 passed, 1 deselected`。
- Python `compileall`、`git diff --check`、E3 OpenAPI 路径核验：通过。
- 前端生产构建：通过；本阶段未新增或接线 E3 页面。

## 最新完成：P0-E E2 金丝雀运行计划与状态机（2026-07-28）

> 独立隔离仓库，**未真实联网、未调用模型、未写业务数据库、未形成 Gold、未发布**。决策记录见 ADR-0010。
> P0-E E0/E1 代码（`p0e_safe_import.py`、`p0e_image_freeze.py`、`p0e_candidate_package.py` 和 `test_p0e_safe_import.py`）已由上游 OpenClaw 控制器提交为 `16cd2c75a5c39ddf94157e388860075cfaffcd4c`，并独立验证 39 项针对性测试及 228 项通用后端测试（macOS 上 1 项 Windows-only DPAPI 测试取消选择）。

新增：

- `backend/app/p0e_canary_run.py`：纯函数编排层，实现六态单调状态机（`draft → preflight_ready → approvals_ready → freeze_ready → candidate_ready → human_review_ready`）加终止态 `failed`/`cancelled`；所有转换为纯函数（无 I/O、无数据库、无模型），fail-closed，证据 URL 安全扫描，幂等 SHA-256 指纹，机器可读 `CanaryRunError`；五项不变量（`writes_business_database=False`、`downloads_performed=False`、`model_runs_performed=False`、`forms_gold=False`、`publishes_release=False`）在所有快照中显式记录。
- `backend/tests/test_p0e_canary_run.py`：确定性测试，覆盖完整快乐路径、所有跳跃门控、回退禁止、终止态无法继续、双重终止拒绝、所有缺失/无效审批场景、静默映射尝试、空白名单、固定 IP 未证明、不完整/不匹配 manifest、不足候选预览、候选声称 Gold/下载/模型调用、证据 URL 含 query/fragment/userinfo 拒绝、幂等性、机器可读错误、不变量全链路验证。
- `docs/decisions/0010-p0e-e2-canary-run-state-machine.md`：ADR-0010。

## 最新完成：P0-E 安全离线导入基础（E0/E1）

> E0/E1 工程基础，**未真实下载图片、未形成 Gold、未跑模型、未写业务数据库**。决策记录见 ADR-0009。已由上游 OpenClaw 控制器提交并验证（提交 `16cd2c75a5c39ddf94157e388860075cfaffcd4c`）。

- `backend/app/p0e_safe_import.py`：XLSX 只读预检。仅接收 `.xlsx`，受限 ZIP/XML 解析，拒绝公式/宏/外部关系/异常 ZIP/超限/不安全 XML；重复表头生成稳定内部名（如 `status__col_3`）并原样保留 RAW；`farmat → format` 仅作需人工确认的候选映射，从不静默应用；按文件字节 SHA-256 生成幂等批次键；Gold 目标锁状态缺失/已锁即 fail-closed；`writes_business_database=False`。
- `backend/app/p0e_image_freeze.py`：受控图片获取与确定性冻结。默认无白名单即拒绝任意 URL；仅允许显式精确域名 + HTTPS + 443 + 无 userinfo；每跳全量校验 A/AAAA 公网属性并逐跳重校验重定向；默认传输返回 `DNS_PINNING_UNAVAILABLE`（防 DNS rebinding，fail-closed），需固定 IP 契约的受控适配器才能真实获取；三重校验 Content-Type + 魔数 + Pillow 解码；流式临时落盘 + SHA-256 去重 + 原子替换 + 中断清理；来源 URL 仅保留 scheme/host/port/path，不落 query/userinfo。
- `backend/app/p0e_candidate_package.py`：30～50 张离线、固定 seed 的确定性分层预览。排除非 3D、缺人工等级/分类（含 3Dreason 缺真值）、重复 URL 和冲突样本并保留可机读原因；始终返回 `forms_gold=false`、`downloads_performed=false`、`model_runs_performed=false`。
- `backend/tests/test_p0e_safe_import.py`：覆盖重复表头、RAW 保真、farmat/format 预览与冲突、公式/扩展名/ZIP/宏拒绝、幂等批次键与 Gold-lock、全部 SSRF（IPv4/IPv6 参数化）、重定向重校验与固定 IP、DNS 变更、长度/MIME 欺骗、坏图、中断临时清理、SHA 去重、manifest 原子确定性与不完整判定、固定 seed 复现、697 3Dreason/重复/冲突排除、可机读错误。
- `backend/tests/conftest.py`：仅把 `backend/` 加入 `sys.path`，使测试在仓库根与 `backend/` 两种工作目录下都可导入 `app`；从 `backend/` 运行时为无操作。

## 最新完成：人工纠正与单提示词兼容

- 人工纠正改为单维度切换编辑；每个维度的分数、原因和说明独立保留，切换后不会丢失。
- 取消人工直接选择最终等级；维度纠正保存时由服务端评分引擎自动计算最终分数和 L1～L5，并持久化审计结果。
- 审核状态只显示待审核、暂缓审核、已确认、已纠正和已退回；范围外与结果不完整回归美感结果列。
- 任务创建新增单提示词模式；一次调用返回完整结构，同时保留原有 A/B 两阶段模式和历史数据。
- 验证：后端 `44 passed`；TypeScript + Vite 正式构建通过；真实 SQLite 已新增 `human_reviews.corrected_score`；服务健康检查通过。

## 最新完成：随拍图与画质受损等级封顶

- 服务端评分引擎升级为 `engine-v2.5.0`：随拍图或 `slight` 及以上画质受损时最高 L2；严重/不可用画质满足证据阈值时仍最高 L1。
- 人工纠正预览与保存后的服务端自动重算使用相同等级上限。
- 新增 `space_aesthetic_dimensions_v1.4-lite.2` / `space-rubric-v1.4` 提示词候选，不覆盖旧提示词版本。
- 决策记录见 ADR-0008。
- 验证：后端 `47 passed`；TypeScript + Vite 正式构建通过；本地登录页加载正常且无控制台错误。因仓库不保存管理员明文密码，登录后的纠正表单目视验收待使用现有账号完成。

## 已完成：核心列表时间信息

- 提示词版本、评测任务和评测结果新增 `updated_at`，旧 SQLite 数据启动时以创建时间安全回填。
- 提示词发布/归档、任务状态与进度变化、评测结果人工审核都会刷新最新更新时间。
- 提示词版本列表、任务列表和评测结果列表显示“最新更新时间”；素材列表将原“上传时间”明确为“创建时间”。
- 后端接口和前端类型已同步，新增时间字段契约测试。
- 验证：后端 `43 passed`；TypeScript + Vite 正式构建通过；真实数据库迁移与 `/api/health` 通过。
- 浏览器已验证服务登录页可达；因仓库不保存管理员明文密码，登录后四个列表的最终目视验收待使用现有账号完成。
- GitHub CLI 未安装，但不影响现有 Git 操作。
- 已验证：带本机凭据的 `git ls-remote origin HEAD` 成功，GitHub 官方 API 匿名访问返回 404，仓库存在且未公开暴露。

## 最新完成：智能抽样策略配置 v1.1

提交：`b809996 feat: add configurable review sampling policy`

已实现：

- 新增全局 `SamplingPolicy`；
- 可配置常规抽样比例、低/中置信度阈值、冷启动必审数量和高等级必审起点；
- 新增 `/api/sampling-policy` GET/PUT；
- 每次保存增加策略修订号；
- 评测结果返回 `smart-sampling-v1.1/policy-N`；
- “模型配置”页新增智能抽样策略表单。

验证状态：

- 后端测试：`43 passed`，有 1 条 TestClient/httpx 弃用警告；
- 前端 TypeScript + Vite 正式构建：通过；
- 本次文档治理过程中未重新执行该功能的真实浏览器验收。

建议下一步：重启服务，检查旧数据库能否自动创建 `sampling_policies` 表，并在浏览器验证保存、修订号和评测列表同步。

## 已完成能力

| 模块 | 当前状态 | 说明 |
|---|---|---|
| 登录 | 已完成 Demo | 单管理员 Cookie 会话，无角色权限 |
| 素材上传 | 已完成 | JPG/PNG/WebP、SHA-256 去重、本地永久保存 |
| 素材与结果分离 | 已完成 | 一张素材可产生多条模型/提示词评测记录 |
| 任务队列 | 已完成 | 创建、暂停、继续、取消全部任务，展示提示词版本 |
| 豆包配置 | 安全层已完成 | macOS Keychain、Windows DPAPI 与稳定引用已接线；目标机真实连接待验收 |
| A/B 两阶段评测 | 已完成 | A 做预检，B 做美感；范围外不调用 B |
| 服务端评分 | 已完成 | 固定权重、等级上限、版本快照 |
| 高风险自动复核 | 已完成 | 只能保持或降级，不抬高结果 |
| 评测结果列表 | 已完成 | 多条件筛选、版本信息、列表进入大图审核 |
| 逐维人工纠错 | 已完成 | 维度、人工分、原因码、说明和最终等级 |
| 普通/黄金样本集 | 已完成 | 真值、锁定、修订历史和历次评测历史 |
| 提示词版本 | 已完成 | A/B、草稿/发布/归档、禁止覆盖历史 |
| AI 提示词草案 | 已完成 | 简单豆包改写和独立 SOL/高能力模型优化任务 |
| 黄金回归 | 已完成基础流程 | 全量运行与比较；发布前硬门禁尚未完成 |
| 模型迁移 | 已完成基础流程 | 旧结果基线、新模型重跑、差异/抽检人工判断 |
| 智能抽样 v1 | 已完成 | 必审/抽审/暂缓/已审、稳定 10% 抽样、原因和优先级 |
| 抽样策略配置 v1.1 | 已提交 | 自动测试和构建通过，待补一次真实浏览器验收 |
| P0-E 金丝雀前端编排 | 工作树已完成 | 已接 E3 认证 API；只登记门禁证据，不执行导入、下载、模型、Gold 或发布 |
| macOS 部署生命周期 | 离线能力已完成 | 安装/诊断/前台启动/脱敏备份恢复已测试；目标 MacBook 尚未实际部署 |

## 本地数据快照

最近一次页面验收时：

- 素材：17 张；
- 评测结果：69 条；
- 智能抽样：63 必审、3 抽审、0 暂缓、3 已审核。

这是本机临时 Demo 数据，会随上传、评测和审核改变；不得把这些数字写成产品固定指标。

## 当前最高优先级

### P0

1. 在目标 MacBook 按 ADR-0013 做首次安装、启动、登录、Keychain 页面保存
   和脱敏备份恢复演练，再接公司内网真实模型；同时继续推进 Windows 部署。
2. 为 P0-E 接入真实且受控的 XLSX/冻结执行器证据来源；继续保持下载、模型、
   Gold 和发布的独立门禁。
3. 补做抽样策略配置 v1.1 的真实浏览器验收。
4. 让审核队列按当前模型×提示词组合、任务批次和时间范围组织，避免全部历史结果成为今日待办。
5. 增加审核任务认领、占用状态和乐观锁，避免多人覆盖同一结果。
6. 把提示词发布改为真正的回归门禁：先回归通过，再允许 published。
7. 建立一等的模型×A×B×Rubric×Engine 发布组合实体。

### P1

1. 将八维定义、权重和等级限制做成可版本化 Schema。
2. 将评测列表改为服务端分页、筛选与聚合；当前前端最多加载 1000 条。
3. 智能抽样决策改为增量计算或持久化，并记录每次审核采用的策略快照。
4. 增加准确率、八维误差、L4/L5 精确率、画质/摄影误判率、空字段率和人工介入率报表。

## 当前暂不做

- 角色权限；
- OSS 或其他对象存储；
- 现有素材库接入；
- 图片自动清理；
- 搜索/推荐同步；
- 中英文双语。

## 最近验证基线

2026-07-28：

```text
macOS 部署专项：20 passed
后端 pytest：348 passed, 1 skipped, 1 warning（隔离临时 DATA_DIR）
shell bash -n：通过
Python compileall：通过
install.sh --check/--dry-run：通过，未联网
git diff --check：通过
```

本阶段未修改前端源码，未部署或启动服务，未进行浏览器、真实 Keychain 页面、
真实模型、XLSX 执行器或 Gold 验收。

标准命令：

```powershell
cd D:\3d66-label-system\backend
..\.venv\Scripts\python.exe -X utf8 -m pytest tests -q

cd D:\3d66-label-system\frontend
npm.cmd run build
```

## 每次状态更新要求

完成一个里程碑后更新：

1. 当前提交哈希和远程同步状态；
2. 工作树中进行中的文件；
3. 已完成功能与仍缺失的闭环；
4. 最近测试、构建和浏览器验收结果；
5. 下一步 P0/P1；
6. 新产生的长期架构决策链接。
