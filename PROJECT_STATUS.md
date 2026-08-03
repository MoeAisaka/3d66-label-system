# 3d66 标签系统｜当前项目状态

> 最后更新：2026-08-03
> 本文件只记录“现在做到哪里”；长期原则见 `PRODUCT.md` 和 `AGENTS.md`，历史背景见 `CODEX_HANDOFF.md`。

## 最新完成：提示词版本管理与基准回归草稿选择（2026-08-03）

- `PromptVersion` 增加流水线归属：完整流水线专用、基准回归专用、两条流水线共用；
  每个版本同时绑定类目，服务端和 Worker 均执行归属门禁。
- 提示词管理器支持按类目新建 A/B 版本、编辑草稿当前内容、另存为新草稿、发布归属和软归档；
  已发布或已被任务冻结的版本不可原地修改，活动类目仍绑定的已发布版本不可归档。
- 基准回归选择器只显示基准回归专用/共用版本，草稿可以手动加入全量回归；完整流水线专用版本
  会被排除。完整流水线反向只接受完整流水线专用/共用版本。
- 基准回归结果缺少合法等级或服务端权威分数时，Job 与 BaselineItem 均进入失败状态并保存
  `missing_level`、`no_authoritative_score` 等原因，不再显示“运行完成但无有效分数”。
- 公司实例 Run #6 已只读定位：Prompt A #14 返回旧扁平字段，没有
  `classification.scope_status`，Worker 因无法判断评测范围而跳过 Prompt B #16；最终
  `aesthetic/score/level` 均为空。新版本会额外返回 `missing_precheck_scope_status`、
  `missing_prompt_b_response`、`missing_aesthetic_result` 并在页面翻译为可读失败原因。
- 兼容修复会为已到 v50 的旧 SQLite 数据补充 `pipeline_scope`，不改历史 migration ledger；
  策略快照也冻结类目和流水线归属。

当前验证：后端 `718 passed, 1 skipped`；前端 `npm run lint`、`npm run contract:dimensions`、
`npm run build`、Python `compileall`、`git diff --check` 通过。公司正式实例
`http://192.168.1.35:8081/` 健康检查已通过，但尚未用本候选完成部署或修复后真实回归验证。

## 最新完成：Prompt 类目隔离与非空间类目真实回归（2026-08-03）

- 数据库迁移升级到 v49，`PromptVersion` 正式持久化 `category_key`。Prompt、基准集、
  回归任务、Worker、评测包与自动优化均执行服务端跨类目门禁，前端筛选不作为唯一
  安全层；已归档但被类目冻结绑定的 Prompt 仍可用于历史回归，不允许跨类目复用。
- PDF 文本与材质图在没有专属已发布维度方案时，默认采用单提示词并关闭维度；不会
  借用空间图片八维，也不会生成虚假的逐维结论。v49 真实迁移后 PDF Prompt #13、
  材质 Prompt #14 及两个类目 Profile 的归属和模式均正确，外键检查为空。
- 使用当前机器的 Edge 登录态和隔离验收库完成真实浏览器端到端操作：PDF 基准集 #6、
  Run #6 完成 PDF 多模态总结与单提示词评测，结果 L3/68、期望 L3；材质基准集 #7、
  Run #7 完成单提示词评测，结果 L4/82、期望 L4。两条流水线总体和相邻等级准确率
  均为 100%，均冻结 `dimension_mode=none`；材质结果明确标记
  `formal=false, experimental=true`。
- API 强制跨类目探针通过：PDF Prompt 创建材质 Run、空间 Schema 创建材质 Run 均
  返回 422；`/api/prompts` 与 `/api/baseline-sets` 的三类目查询结果完全隔离。
- Edge 390×844 复验无文档级横向溢出，侧栏收起后不遮挡主内容；混淆矩阵只在自身
  容器内横向滚动。截图与可复跑 CDP 脚本位于
  `artifacts/browser-validation-20260803/`。

当前验证：

- 后端全量：`711 passed, 1 skipped`。
- 前端：`npm run lint`、`npm run contract:dimensions`、`npm run build` 通过。
- Windows、macOS、Docker 专项：`58 passed`。
- Docker 镜像构建、`docker compose config -q`、容器启动、数据库 v49 迁移、外键检查
  与健康检查通过；容器状态为 `running|healthy|0`。
- `git diff --check` 通过。

仍待完成：

- 公司 Windows 正式实例尚未部署本候选，不能把上述隔离环境、原生 Windows 和 Docker
  验收表述为公司线上已部署。

## 最新完成：类目维度管理与仅提示词实验（2026-08-02）

- 维度管理器从只读升级为管理员工作台：按类目独立支持全部维度、选择部分维度、关闭
  维度三种模式；关闭后进入单提示词直接评级实验，不生成或伪造逐维分数。
- 维度方案支持复制草稿、增加、删除、重排、编辑权重与等级锚点、保存、发布和绑定；
  已发布/已停用版本保持不可变，任务入队冻结类目、Schema 与有效维度选择。
- Worker、结果、回归、重算和评测包均读取冻结维度选择；仅提示词结果明确标记为实验
  结果，不进入依赖维度真值的正式纠偏链。
- 浏览器已验证部分维度保存、仅提示词保存、类目隔离和完整版本生命周期。移动端发现并
  修复 64 位 Schema 哈希导致的横向溢出，复验 `scrollWidth == viewport == 390`。
- 用户手册新增维度开关与版本管理两张真实页面截图；钉钉文档《标签实验台用户操作
  指南0731》已新增“三、维度管理与仅提示词实验”及 18-21 节，两张截图已上传；
  刷新后目录、章节和图片仍存在。

## 当前交付候选：以最终评测包为主线的审核与自动优化（2026-08-02）

当前分支：`feature/planned-iteration-20260801`；前端、后端、集成脚本与操作手册已在
同一候选中完成验收，并纯快进交付 Codeup `main`、`windows-deploy` 与功能分支。

已完成：

- 默认导航收敛为“开始评测 → 处理纠偏 → 二审评测包”。模型协议、预算、执行器、
  Bundle 等复杂选项进入管理员高级设置；素材页不再让一线审核员手工拼装 Prompt 入队。
- 评测包二审详情完整展示新版 Prompt 全文与版本、Rubric、维度开关/权重/等级锚点、
  黄金集角色和逐样本真值、回归门槛、基线/候选对照、失败项、阻塞、风险与下一步建议。
- Worker 持久化心跳、最近检查、readiness、活跃原因和可操作 blocker；未过期的
  processing lease 视为活跃，长模型调用不再被误报为 Worker 离线。
- 阻塞类目不会卡住其他队列；同时可运行类目按持久化最近服务时间确定性轮转。测试中
  同一时间戳连续六次 tick 为 3:3。
- 自动优化按 A/B Prompt、模型完整快照、Rubric 与维度合同解析并冻结类目基线；
  零匹配、多匹配、合同不完整、漂移或跨类目均 fail-closed。
- Bundle 工厂不再隐式绑定类目基线。二审批准不改变现役配置；只有显式发布评测包才
  在一个事务内提升所属类目的 Prompt、模型、Rubric、维度与 Bundle，并增加修订号。
- Prompt 发布改为类目级有效：发布空间图新包不会归档材质图仍在使用的 Prompt。
- 活动类目只允许已发布 Prompt；普通审核员无权设置基线 Bundle，管理员高级设置也
  必须通过完整合同校验。
- E2E seed 与故障矩阵写入显式基线和冻结维度合同，避免测试数据绕过生产门禁。

当前验证：

- 后端全量：`707 passed, 1 skipped, 6 warnings`；macOS 部署专项 `23 passed`，
  Windows 部署合同专项 `33 passed`。
- 自动化故障矩阵 7 项通过：超时、usage 缺失、优化密钥缺失、预算为零/耗尽、重复
  纠偏、跨类目隔离和并发 Worker。
- 前端 `npm run lint`、`npm run contract:dimensions`、`npm run build` 通过；保留
  既有入口 chunk 超过 500 kB 警告。
- Python `compileall`、集成脚本 `py_compile`、`git diff --check` 通过。
- 当前最终字节使用真实 `app.launcher`、API、Worker 和本地 OpenAI-compatible mock
  连续完成 3 轮主链：38 次 provider 请求、3 次配对回归均 `passed/pass`、3 个最终包
  进入二审；拒绝与批准不切换基线，显式发布才将 Bundle `1 → 5`；18 个结果均冻结
  `space_aesthetic@1.3.0`。
- Docker 镜像构建、健康检查、API+双 Worker、重启、卷持久化、文件 AEAD 密钥引用和
  同一套 3 轮真实 E2E 全通过；隔离容器与卷已清理。
- 桌面 1440×1000 与移动 390×844 对 7 个核心页面各跑 5 轮，共 70 次页面检查；
  无横向溢出、空白页或登录后控制台错误。真实二审详情验证 10,773 字长内容，移动端
  `scrollWidth=390`。截图与新版手册见 `docs/user-guide.md`。
- GIF、PDF、材质图与单提示词/A-B 基础实现的 61 项专项测试通过；入队新增稳定
  `prompt_mode_mismatch` 门禁，固定 single/ab 类目不能再绕过冻结模式。
- Windows 11 `10.0.26200`、PowerShell 5.1、Python 3.11.4 原生实机通过五脚本解析、
  install `-Check`、CurrentUser/LocalMachine doctor 九项与 DPAPI 回环。部署与安全
  全套为 `60 passed, 1 skipped`；正常 restore 与故障回滚 restore 各在独立进程连续
  复跑 5 次，合计 `10/10`，未再出现 `WinError 32` 或回滚失败。公司 Windows 正式
  实例尚未部署本候选，因此上述证据只表述为原生 Windows 验收，不表述为线上部署通过。

仍待完成（完成前不得宣称外部环境全部交付）：

- 公司 Windows 正式实例部署本候选后，仍需执行真实 API Key 保存、DPAPI 回环、
  PDF/GIF Worker 与完整评测包主链验收；restore 已在隔离 Windows 11 原生环境通过，
  但尚未对正式业务数据目录执行恢复演练。

长期决策见 [ADR-0030](docs/decisions/0030-package-review-and-category-baseline-promotion.md)。

## 规划功能迭代候选：多类目流水线、PDF 前处理、材质图与通用模型渠道（2026-08-01）

当前开发分支：`feature/planned-iteration-20260801`，基于 Codeup
`main@c0a31fa`。本节改动完成后再安全快进合入 `main` 与 `windows-deploy`。

已完成：

- 维度管理器 P2 已合入：路由策略、冻结 profile、解析快照、隔离维度校准持久化
  和管理页面均可用，数据库迁移已到 v30。
- 新增三个互相隔离的评测类目合同：`space_image`、`pdf_text`、`material_image`。
  素材包、评测 Job、Prompt/模型绑定和 MIME 校验均携带 `category_key`，重试 Job
  继承原类目，不会把 PDF 或材质图混入空间图片流水线。
- 评测 Job 在入队时冻结完整类目执行合同（MIME、前处理、提示词、模型非密参数、
  rubric 与维度标识），迁移已到 v35；worker 只从原模型配置记录读取当前系统凭据
  引用，API Key 不进入快照。类目或模型后续停用不影响已排队任务，重试继承同一快照。
- PDF 类目先做参数感知内容缓存的文本抽取、页图接触表渲染和可选 Tesseract OCR，
  再由冻结主模型完成独立多模态总结，之后才进入类目评测；原始 PDF 始终保留。
  A/B 两阶段都接收同一份冻结文档上下文。结果冻结页数、OCR 状态、文本字符数、
  2000 字摘录和多模态总结，人工可在审核页展开查看。
- GIF 动图沿用已交付的最多四帧接触表策略，原始动画仍用于页面展示。
- 主模型、提示词诊断模型和横评配置的 `provider` 均为通用渠道标识，不再锁死
  火山豆包或 SOL；迁移只把精确的旧默认名称改为通用名称，不覆盖用户自定义名称。
  类目页用明确的“跟随任务 / 单提示词 / A/B 两段”模式控制，独立选择主模型、
  Prompt、PDF 页数/文本上限和材质专项开关；非空间类目未绑定专属 Prompt 时拒绝入队。
- 基准回归已支持单提示词模式，并在结果页冻结完整维度、画质封顶证据、置信度、
  复核状态及模型/rubric/engine 版本，为人工判断提供依据。

验证：

- 后端：`586 passed, 1 skipped, 3 deselected`（Homebrew Python 3.14；三条
  Windows doctor CLI/完整安装测试因正式门禁仅允许 Python 3.11/3.12 而排除）。
- 专项：PDF/类目隔离/迁移/素材包与任务冻结合同通过；前端 `npm run lint`、
  `npm run build`、`git diff --check` 通过。

仍待完成：

- 在公司 Windows 实例部署本候选，真实验证非空 API Key 保存、DPAPI 引用和 PDF/GIF
  Worker；代码侧已有 Windows 5.1 修复，但线上实例尚未验收。
- 真实模型通道端到端跑一条 PDF 与材质图样本，确认 OCR/多模态上下文在目标渠道的
  输入限制下稳定工作。
- 完成 Codeup 远端历史核对后快进推送，并在云效/Windows 环境跑同一套回归门禁。

## 最新交付候选：基准回归单提示词模式与判断信息增强（2026-08-01）

当前工作树：`delivery/baseline-single-prompt-20260801`，基于 Codeup
`main@57abd865`。本节内容在完成远端历史核对后推送 Codeup。

已完成：

- 基准回归新增“单提示词（一次调用）”模式：可选择阶段 A 的任意提示词版本，
  创建时冻结 `prompt_b = NULL`，worker 复用现有单提示词执行链，不改变线上发布指针。
- 请求合同明确禁止单提示词与 A/B 同时提交；单提示词只能选择阶段 A，A/B 模式
  继续要求 A、B 成对且阶段匹配。审计事件记录 `prompt_mode`。
- 结果页补充完整八维（含 3 级中性维度）、画质严重度/置信度/证据、模型置信度、
  是否需要复核及模型/rubric/engine 版本，为人工判断等级偏差和封顶原因提供依据。
  老结果缺失字段只补空默认值，不重算历史结论。

验证：

- 后端全量：`512 passed, 1 skipped, 1 warning`。
- 前端：`npm run lint`、`npm run build`、`git diff --check` 通过。
- 单提示词专项覆盖：请求冲突校验、阶段校验、StrategyBundle 空 B、Job 空
  `prompt_b_id`、选择回显与失败收敛。

## 最新交付候选：动图评测入口（2026-08-01）

- 上传接口、ZIP 素材包和前端素材选择器均放行 GIF，并保留原始动图 MIME，页面预览
  仍展示原始动画。
- worker 对 GIF 生成按内容 SHA 缓存的最多四帧 PNG 接触表，再沿用现有图像评测链路
  发送给模型；这样不依赖上游是否支持 `image/gif`，且同一素材重复评测不会重复抽帧。
- 动图预览失败会让任务按现有错误链路失败，不静默回退为首帧或伪造成功。
- 已加入 GIF 上传和确定性多帧预览测试；待本候选提交并推送后，再做真实模型通道
  的端到端验收。

## 仓库状态

- Windows 正式服务器目标目录：`D:\3d66-label-system`；当前研发仓库为本文件
  所在的 MacBook 工作目录。
- 当前分支：`windows-deploy`
- 当前功能基线：以 `main` 分支最新提交为准，精确提交号请执行 `git log -1` 查看。
- 唯一迭代与推送目标：云效 Codeup
  `https://codeup.aliyun.com/3d66/tepeng/3d66.label-system.git`。公司发布机上
  该地址即 `origin`；历史开发机可能仍保留额外 GitHub 远程，仅作归档，不再作为
  交付目标。
- Codeup 推送与 MacBook 部署验收由研发侧负责；公司 Windows 实例由公司前端
  工程师按本分支自行部署。
- 分支关系：每次交付以 `git status -sb` 的实时结果为准。

## 共享测试环境发布入口（2026-07-31）

- 测试服务器：`192.168.1.35`。
- 测试访问地址：`http://192.168.1.35:8081`。
- 健康检查：`http://192.168.1.35:8081/api/health`。
- 发布目标固定为 Codeup `main`，发布前制作临时 Git bundle，通过 SSH 上传，
  服务器脚本构建并检查健康状态，失败自动回滚上一个提交。
- 当前 Windows 发布机已配置专用 SSH 密钥和服务器固定免密发布命令，发布过程不再
  反复提示服务器密码；私钥仅保存在本机 `~/.ssh/3d66_label_test_ed25519`，不入库。
- 仓库根目录不放置双击部署入口；Windows 和 macOS 入口单独分发，最终都调用
  仓库内同一个 `scripts/deploy-test.py`。
- 当前不需要 Jenkins。需要自动触发、审批或发布记录时，可在云效流水线中调用
  同一个脚本，继续复用服务器端的发布保护和回滚逻辑。
- 脚本默认要求人工输入 `DEPLOY` 确认；仅在已确认目标提交时才使用 `--yes`。

## 最新完成：可解释基准回归与连续优化工作流（2026-07-31）

> 本轮开发基线：`7331399`。已推送至 Codeup：主干功能与修复提交为
> `1a38114`、`0199890`、`df81fc1`、`0d6d69e`；本分支随主干同步，精确提交号
> 执行 `git log -1` 查看。两侧均为快进，未使用 force push。已在 MacBook 以
> 隔离 `DATA_DIR` 的 8099 金丝雀完成真实浏览器验收，1280/1440/1536/1920/390px
> 五个视口文档级横向溢出均为 0；生产 8080 仍为 `5c25bfa`，是否提升待操作员
> 决定。公司 Windows 实例由公司前端工程师后续按 `windows-deploy` 分支自行部署。

已完成：

- 需求范围从 Excel 图片 URL 获取调整为按上传图片名称预填等级。支持
  `L1/l1/好`、`L2/l2/中等`、`L3/l3/中差`、`L4/l4/极差`、
  `L5/l5/过滤`；无匹配或多等级冲突回退整批默认值，创建前可逐张修改。
- 基准集冻结每张最终期望等级及 `filename`、`batch_default`、
  `manual_override` 来源。Excel 解析、内网图片 URL 拉取和远端原图冻结不再
  属于本次需求。
- 基准回归结果升级为 `baseline-result-v2`，冻结最终等级、服务端分数、
  主要优势/短板、视觉证据、缺陷、等级限制和复核原因；历史缺失明确显示
  “历史结果未冻结评测理由”，不按当前规则伪造。
- 基准回归逐张结果原位支持确认、退回和逐维纠偏，复用正式 ReviewPanel
  并发与结算门禁；人工结论单独展示，不覆盖模型预测或冻结期望等级。
- “纠偏案例队列 → 自动优化编排”重构为“1 案例池 → 2 组批与优化 →
  3 候选验证”。页面显示当前下一步，提供一键安全试跑；配置项改用业务语言，
  P0～P3 即时触发含义可见，租约/退避/重试收进高级设置，运行记录逐条给出
  后续动作。安全试跑不调用模型、不计费、不发布。
- ADR-0024 固化文件名建议等级、回归理由、原位审核与连续优化流程合同。

验证：

- 基准回归与自动优化专项：`23 passed`。
- 后端排除真实 macOS Keychain 集成文件后的全量：
  `481 passed, 1 skipped, 1 warning`，退出码 0。
- 前端 `npm run lint`、正式生产构建和 `git diff --check` 通过。
- 使用隔离 `DATA_DIR`、隔离端口、临时测试账号和两张测试素材完成真实 Chrome
  验收：文件名 L1/L3 自动预填、L3 手改为 L2、创建冻结基准集、提示词取值
  入口、案例池下一步和组批配置说明均通过。测试未读取生产数据库、未调用模型。
- 更正：上条验收原记为“1440px 桌面与 390px 移动端均无横向溢出”，该结论
  覆盖不全。隔离环境未进入“已选中基准集且存在历史 run”状态，页面因此
  未渲染提示词/维度控制行与混淆矩阵结果区，两处栅格的溢出未被发现。后续
  在 MacBook 金丝雀以真实数据复测时暴露，见下方补充修复。

补充修复：基准回归页横向溢出（提交 `0199890`、`df81fc1`、`0d6d69e`）

- 根因一：提示词/维度控制行与“混淆矩阵 + 逐张对照”结果区使用 `xl:`
  视口断点搭配固定 px `minmax` 轨道，但两者都位于被 310px 侧栏挤窄的内容
  列内。1440px 视口下内容列仅 798px，而五轨栅格要求 1104px，故文档级溢出。
  断点判定的是视口宽度，不是组件可用宽度。
- 根因二：Tailwind v4 将任意断点（`min-[1750px]`）整块排在所有命名断点
  之前，因此 `xl:` 三轨规则在样式表中位置更靠后，在同等特异性下胜出，
  宽屏五轨分支从未生效。已将窄屏规则改为 `min-[1280px]:`，使两条规则落入
  同一排序组，宽屏分支按预期覆盖。后续在同一元素上混用命名断点与任意
  断点时需注意此排序行为。
- MacBook 金丝雀真实浏览器实测文档级横向溢出（修复后 / 修复前生产
  `5c25bfa`），均在“已选中基准集 + 存在历史 run”状态下测量：
  1280px `107px / 290px`、1440px `0 / 130px`、1536px `0 / 154px`、
  1920px `0`（五轨生效，实测 `150px 298px 298px 298px 154px`）。
- 根因三（提交 `0d6d69e`，同时更正上一条的归因）：1280px 残留的 107px
  原记为“素材表 `min-w-[720px]` 与创建按钮共同造成的既有缺陷、不在本次
  范围”，该归因有误。逐元素实测显示素材表位于合法的
  `max-h-[440px] overflow-auto` 滚动容器内，从不产生文档级溢出；唯一的
  文档级越界元素是“创建基准集”按钮，被“创建冻结基准集”表单行顶出视口
  108px。该行同样使用 `xl:` 视口断点搭配固定 px 轨道
  （`minmax(200px,1fr) minmax(220px,1fr) 160px auto`），1280px 下内容列仅
  638px 而四轨即使全部取最小值仍需 765px——与根因一完全同源，只是当时漏改
  这一处，并非独立的既有缺陷。已改为 `min-[1280px]:` 两条弹性轨道、
  `min-[1750px]:` 恢复四轨，全部使用 `minmax(0,...)` 以允许收缩。
- 修复后金丝雀实测（同一“已选中基准集 + 存在历史 run”状态，文档级横向
  溢出 / 创建行栅格轨道）：1280px `0` / `291px 291px`、1440px `0` /
  `371px 371px`、1536px `0` / `419px 419px`、1920px `0` /
  `446px 446px 160px 138px`（宽屏四轨生效）。五个视口的非滚动容器越界
  元素数均为 0。
- 390px 移动端复测：文档级横向溢出 `0`（`documentElement` 与 `body` 均为 0）。
  探测到的 7 个越界元素全部位于有意的横向滚动容器内（导航条、素材表、
  轮次条、混淆矩阵），属移动端预期行为，非缺陷。
- 复验：主干后端 `481 passed, 1 skipped`；`windows-deploy` 后端
  `512 passed, 1 skipped`；两侧前端 `npm run lint`、正式构建、
  `git diff --check` 通过；构建产物已确认 `@media (width>=1280px)` 先于
  `@media (width>=1750px)`。

仍待完成：

- 决定是否将金丝雀 `3d66-label-system.green-daa0188-20260731T101802Z`
  （现为 `ff29d7b`）提升为 8080 生产；提升涉及目录切换与重启携带真实数据的
  实例，属中高风险，需操作员显式确认。
- Windows 分支继续随主干同步，交由公司前端工程师部署；真实 Windows 必须
  通过 doctor 回环、非空 Key 保存和连接测试后才能宣称线上故障解决。

## 最新修复：Windows 非空 API Key 安全存储（2026-07-31）

> 主干修复提交：`7bd96be`；Windows 启动门禁提交：`0fa1e65`。故障目标是
> 公司内网 Windows 实例
> `http://192.168.1.35:8081/`；用户实际输入了非空 Key，服务端在 Windows
> 安全存储阶段返回“API Key 安全存储失败”。此前把该故障归因为空 Key 合同
> 是错误诊断，空 Key 兼容修复属于另一条独立问题。

已完成：

- Windows DPAPI 增加显式 `current-user` 与 `local-machine` 两种范围，分别
  使用 `dpapi:v1:` 与 `dpapi-machine:v1:` 引用；不做静默降级，历史无前缀
  密文继续兼容读取。
- 增加不落盘的随机哨兵 DPAPI 加密—解密回环，供 Windows `doctor` 在服务
  启动前证明安全存储可用。
- 运行时错误只记录业务 account、稳定原因和 Windows 数字错误码；响应不再
  把平台不支持、DPAPI 初始化、范围配置和加密失败混成同一条消息。API Key、
  密文和请求体均不进入日志或响应。
- ADR-0023 固化 Windows Server 启动入口默认机器范围、NTFS ACL 边界、真实
  回环门禁和禁止明文回退的约束。

验证：

- 安全专项：`26 passed, 1 skipped`；跳过项仅限原生 Windows 实机 DPAPI。
- 主干全后端：`479 passed, 1 skipped, 1 warning`。
- Windows 分支全后端：`510 passed, 1 skipped, 1 warning`。
- Windows 五个 PowerShell 脚本通过 PowerShell 7.6.4 官方解析器；
  前端 `npm run lint` 和正式 `npm run build` 通过。
  注意：该口径**只是语法解析且是 7.x**，不覆盖 5.1 运行期行为。
  2026-07-31 实机在 5.1 上发现四个运行期缺陷（均逃过了 5.1 与 7.x 两个 parser），
  已于 2026-08-01 全部修复并实机复验，见下节。
- Python `compileall` 与 `git diff --check`：通过。

### 原生 Windows 实机验收（2026-07-31，13600K）

验证机：原生 Windows 11 `10.0.26200`，PowerShell `5.1.26100.8115` Desktop，
Python 3.11.4，Node v24.15.0，npm 11.12.1。被测提交
`windows-deploy@f8d57893ca0faa679d50f4920faf9da9079b7a7c`。

已通过（均有实机证据）：

- `doctor.ps1` 全量门禁 **9/9 通过 ×3 轮**：`CurrentUser`、`LocalMachine`、
  以及默认 `DATA_DIR`（`%LOCALAPPDATA%\3d66-label-system`）。含真实
  「Windows DPAPI 内存回环」，两个范围各验一次。另确认 doctor **只读**——
  三轮跑完 `DATA_DIR` 仍不存在。
- ADR-0023 API Key 保护链闭环：非空 Key 保存后落库形态为
  `dpapi:v1:`（current-user，361 字节）/ `dpapi-machine:v1:`（local-machine，
  345 字节），**前缀随范围正确切换**；SQLite 全库字节扫描、GET 响应与
  `worker.log` 均无明文（接口只回 `api_key_mask`）；空 Key 重提交保留既有
  引用，不误删凭据。
- **解密正确性有字节级证据**：经本地回显端点比对 sha256，累计 4 次上游调用
  全部与原文哈希一致，且以 `Bearer` 正确送达。对真实模型仅超时只能证明
  「请求已发出」，故未采信。
- **切换 DPAPI 范围不会锁死既有凭据**：current-user 写入的引用在
  local-machine 运行时下照样解密出原文。`_decode_dpapi_reference` 同时接受
  两种前缀，且解密不传范围，依赖 Windows `CryptUnprotectData` 自解析。

未通过 / 边界（不得计入「已验证」）：

- ~~**`start.ps1` 在 PS 5.1 上无法启动服务**，`install.ps1` 同样必然失败；
  本次验收绕过了这两个脚本。~~
  **已于 2026-08-01 修复并实机复验通过，见下方「四个 PS 5.1 缺陷修复与复验」。**
- 两层 DPAPI 范围默认值相反：PS 脚本层 `-DpapiScope` 默认 `LocalMachine`，
- 两层 DPAPI 范围默认值相反：PS 脚本层 `-DpapiScope` 默认 `LocalMachine`，
  Python 层 `API_KEY_DPAPI_SCOPE` 默认 `current-user`；靠脚本注入环境变量才
  对齐，建议统一。
- 低危：非 ASCII API Key 在 PUT 阶段不校验（返回 200），此后每次使用都因
  HTTP header 限制失败；可通过重写合法 Key 恢复，且错误报文只暴露该字符与
  偏移、不含完整 Key、不入日志。建议在 `ModelConfigUpdate.api_key` 加 ASCII
  校验并停止回显底层异常原文。

仍待完成：

- 公司内网实例尚未部署本修复；线上故障是否解决仍须在该实例上以新 doctor 或
  保存接口的脱敏错误码确认。本次 13600K 验收证明的是**代码侧安全链成立**，
  不等于公司实例已修复。
- ~~操作员双击 `.cmd` 的路径当前走不通（缺陷 4）。~~
  **已于 2026-08-01 修复并实机复验通过，见下节。**

### 四个 PS 5.1 缺陷修复与复验（2026-08-01，13600K）

同一台 13600K，`PowerShell 5.1.26100.8115`，未安装 PS 7、未改动系统。
基线 `windows-deploy@42dbda5`，验证时**不绕过任何 wrapper**。

四条修法：

| 缺陷 | 位置 | 修法 |
| --- | --- | --- |
| 1 | `install.ps1` 三处 `Get-Command`（含 `node.exe`/`npm.cmd`） | 包数组后先判 `.Count` 再取 `[0]`；命中为空保留 `$null` 以保留友好报错 |
| 2 | `install.ps1` 两处版本探测 | 改 `sys.version.split()[0]`，去除字面双引号 |
| 3 | `install.ps1:68` `Invoke-NativeCapture` | 去 `2>&1` 改 `2>$null`；调用期间临时 `Continue`，`finally` 还原；只以退出码判定 |
| 4 | `start.ps1:56-64` | 数组 splat → **哈希表 splat** |

实机结果：

```text
install.ps1 -Check   → Python 3.11.4 / Node v24.15.0 / npm 11.12.1 门禁全过
install.ps1（完整）  → exit 0，65s；pip 31 包 + npm ci 113 包 + vite build ✓ 13.36s
                       .venv 与 frontend/dist 均生成；复检 exit 0
start.ps1（不带 -DataDir）  → 服务监听 18080，GET /openapi.json 200，GET / 200
start.ps1 -DataDir ... -DpapiScope CurrentUser → 同样两个 200
doctor 九项门禁：两个变体下均全部通过
```

参数绑定正确的决定性证据：不传 `-DpapiScope` 时 doctor 报 `local-machine`（默认），
传 `CurrentUser` 时报 `current-user`——修复前该参数会被错绑。且已核实仓库根、
`scripts\windows\`、`C:\` 三处都无名为 `-DpapiScope` 的残留目录。

附带一条更正：`DEFECTS-powershell-51.md` 原先给缺陷 1 建议的 `@(...)[0].Source`
**本身是错的**——`Set-StrictMode -Version Latest` 下对空数组取 `[0]` 会抛
`IndexOutOfRangeException`，反而掉包友好报错；已实测确认并改为先判 `Count`。

共享机未受影响：vLLM `pid 121044`（5001）与第三方 python `pid 14988`（8080）全程存活，
18080 在每个变体后都回到空闲。

仍建议补一条 5.1 冒烟测试（`-Check` + 一次 `start.ps1` 起停）；仅靠 parser 无法防同类回归。
`doctor.ps1`/`backup.ps1`/`restore.ps1` 里的数组 splat **不是缺陷**（splat 给原生 exe，
按位置传参本就正确），本轮未动。

## 最新完成：素材包主链与基准回归整包闭环（2026-07-31）

## 2026-08-01 自动优化闭环、类目隔离与素材管理增量

本轮把产品合同从“人工完成纠偏后还要手工检查队列”推进为常驻 Worker 自动链路：

```text
最终纠偏
→ 同一事务写入对应类目系统黄金集与纠偏案例
→ Worker 按 category_key + prompt_version 隔离组批
→ 自动分析并生成候选提示词
→ 自动创建配对回归
→ 等待同一 run 的全部候选回归进入终态
→ 汇总证据并进入提示词/维度方案人工二审
```

- `refresh_regression_run()` 现在按自动 run 冻结的 `regression_ids` 汇总，第一条回归完成不会提前把父 run 放入二审。
- 类目黄金集使用 `系统黄金集·{category_key}`，最终人工纠偏自动写入；PDF、空间图片、材质图的案例、黄金样本和回归绑定互不混用。
- 生产反馈 `category_key` 改为必填，禁止把 PDF/材质图反馈静默归入空间图片。
- 素材上传继续要求选择类目；素材列表支持后续手动切换所属通道，运行中/排队任务禁止切换。
- 新增素材批量软删除和素材包软删除；包状态进入 `deleted` 后从活动列表隐藏，历史包条目、原文件、评测和审计仍保留。迁移版本升至 v38。
- 自动优化页面文案改为 Worker 自动处理，手动按钮仅保留为管理员重试入口；安全门、日预算和优化模型配置仍是平台级一次性治理，不再作为每批人工步骤。

验证：后端功能全量（排除环境专属 Windows CLI）`562 passed, 1 skipped`；自动闭环、迁移、类目隔离和素材管理专项 `59 passed`；前端 `npm run lint`、正式构建和 `git diff --check` 通过。Windows 部署专项其余 `28 passed`；剩余 2 条 CLI 测试仅因当前 Mac 运行解释器为 Python 3.14，而 Windows doctor 合同要求 3.11/3.12，保留为环境限制，不代表 Windows 实机验收。

> 发布分支：`release/material-package-baseline-fix`；起始基线：
> `83f5e47`。代码与隔离浏览器验收已完成，正在执行主干/Windows 同步、
> GitHub 推送与 MacBook 生产发布门。

已完成：

- 修复基准回归上传清空原生 `FileList` 后请求缺少文件、后端返回
  `Field required` 的问题。
- 一个批量图片请求、一个文件夹或一个 ZIP 自动形成一个不可变素材包；同内容
  复用 `Asset`，但保留每次导入的包条目。支持从现有素材手工另建素材包。
- 素材删除改为可恢复逻辑删除：活动列表、任务和新基准集默认排除，图片文件、
  历史评测、真值、包来源和审计继续保留；再次上传相同内容时恢复。
- “素材选择”并入“素材包”页，旧 `/assets` 重定向到统一入口。
- 基准回归可选择素材包并给整包声明统一 L1～L5 真值。服务端展开包内有效
  唯一素材，不受前端预览上限影响；仍保留逐张混合等级模式。
- 回归报告继续显示精确/相邻命中率、混淆矩阵和逐张偏差；偏差样本可选择后
  批量加入提示词找补队列。
- 生产回流机器 token 增加仓库外秘密文件加载方式；环境变量仍为最高优先级，
  未配置时继续关闭写入。
- ADR-0021 固化素材包生命周期、逻辑删除和整包基准集不可变约束。

验证：

- 后端全量：`468 passed, 1 skipped, 2 warnings`；素材包专项 `7 passed`，
  含失败原子回滚和 ZIP 路径穿越拒绝。
- 前端 `npm run lint`、正式 `npm run build`、`git diff --check`：通过。
- 隔离浏览器完成批量图片、文件夹、ZIP、手工整理、逻辑删除、上传恢复、
  整包 L1 基准集、真实任务入队、准确率/混淆矩阵/逐张偏差与找补入队。
- 页面异常和非预期 HTTP 4xx/5xx 为 0；唯一 401 是登录前认证探针。
- 证据见 `121-验收工件-标签实验台素材包与基准回归-20260731/`。

发布门：

- 合入 `main`、同步 `windows-deploy`、GitHub 推送和 MacBook 蓝绿部署完成前，
  本节不得宣称已在生产启用。
## 已集成候选：维度管理器 P2 路由与校准持久化（2026-08-01）

> 已从 `dim-p2-s1-route-foundation` 至 `dim-p2-s4-calibration-persistence`
> 串行合入当前交付分支。候选策略仍保持 `calibration_only`，生产 Worker
> 对 strategy-bundle-v3 fail-closed；本节不宣称生产启用。

已实现：

- 新增版本化 `DimensionRoutePolicy`，使用规范 JSON 与 SHA-256 标识；
  已发布策略由 ORM 和数据库双层拒绝原地更新/删除。
- 物化 L0 三核心维 `common_core@1.0.0`，从现役空间包逐键提取
  呈现完整性、视觉层次、灵感参考，保留稳定键和锚点。
- 物化 `product_aesthetic@0.1.0-candidate` 单品候选包：L0 三维加
  形态比例、材质工艺、功能表达、场景搭配四个扩展维。
- 候选路由策略覆盖空间、单品、平面、意向以及未知/冲突输入。未知、冲突、
  低置信输入回落 L0 并要求人工复核；只有 `quality_severity=unusable`
  当前允许返回受控不可评。
- 单品候选包设置最少 50、目标 100 张校准门，并要求目标错例、稳定对照、
  盲测留出三类证据。校准未完成且缺少单品 B 提示词合同，因此生产解析
  fail-closed。
- 新增只读路由策略 API、冻结路由解析快照和“优化与回归 → 维度管理器”页面，展示候选版本、
  维度定义、校准进度、阻塞原因和各素材族路由去向。
- ADR-0020 已接受，明确固定核心维、可变扩展维、冻结时序、受控回落和
  P1～P4 分期路线。

已通过的定向验证：

- 维度路由、不可变合同、旧库迁移和 API：`15 passed`。
- DimensionSchema 与全迁移：`30 passed`。
- 后端全量：`474 passed, 1 skipped, 2 warnings`。
- 冻结空间零漂移、Schema、路由与全迁移专项：`61 passed`。
- 前端 TypeScript lint 与生产 build：通过，生成独立
  `dimension-manager-page` chunk。
- 隔离数据库上的登录、路由策略列表/详情、单品候选包详情和前端 SPA 深链
  均为 HTTP 200；候选包实测为 7 维，发布门为 0/50，且
  `calibration_only` 与两项阻塞原因均按定义返回。
- P2 专项测试覆盖路由、冻结 profile、解析快照和隔离校准持久化；合入后将以
  当前主线全量测试和真实浏览器门禁重新验收，不能沿用旧分支数字冒充本次结果。

下一步：

- 真实浏览器需确认候选/阻塞/回落信息可见且无失败请求；在此之前不部署生产。

## 最新完成：维度管理器 P1 执行链与前端合同（2026-07-30）

> 隔离分支按 `dim-p1-s1 → dim-p1-s2 → dim-p1-s3 →
> dim-p1-s4-consumers → dim-p1-s5-frontend →
> dim-p1-s6-zero-drift` 串行推进；S5 提交为 `941011b`，S6 与浏览器
> 验收收口提交基线为 `513ed67`。2026-07-31 已受控合入
> `main@83f5e47`、同步 `windows-deploy@32872cd` 并部署到 MacBook。

已完成：

- `DimensionSchema` 已从只读注册表接入 StrategyBundle 与每次评测结果快照；
  结果、人工纠偏、风险复核、智能抽样、配对回归和提示词优化均从结果绑定的
  Schema 读取维度键、标签、权重和聚合合同。不同 Schema 默认不可比较，
  损坏或身份不完整的快照 fail-closed。
- 现役空间八维以兼容修订保持原键、原权重和原评分行为；同时已有三维测试
  Schema 证明评分、纠偏、复核、抽样、回归与优化器不依赖“恰好八维”。
- 评测 API 现在向前端返回完整、可验证的 `dimension_schema` 合同；旧
  strategy-bundle-v1 结果以明确的 `legacy_derived=true` 兼容显示，损坏合同
  返回 `status=invalid`，不因单条历史数据异常拖垮列表接口。
- 审核详情、审核列表、逐维纠偏和样本标准页已按结果/真值绑定的定义动态显示
  维度数量、顺序和中文标签。前端纠偏预览从 Schema 读取等级换算、权重、
  L1～L5 阈值和已冻结等级限制；合同异常时禁止逐维纠偏，但保留整条确认或
  退回入口。
- 新建立的样本真值冻结完整 Schema 身份与定义；历史未绑定真值只读显示并
  标记“规则未绑定”，不会被误算为完整、可跨规则比较的 Gold。
- 新增零依赖三维前端合同脚本，验证动态键/标签、加权评分与 L2 封顶，不引入
  新测试框架或第三方依赖。
- 新增由旧引擎 `2cbb594` 生成并绑定来源提交的冻结空间回放集。15 个场景覆盖
  历史/现役权重、媒介与画质封顶、非正式结果、证据门禁、坍缩复核、效果图
  特殊检查、专业摄影冲突、提示词硬门槛与人工纠偏；完整结果规范 JSON 的
  SHA-256 逐项一致，因此分数、等级、维度明细、封顶与复核原因均为零漂移。
- 冻结回放首轮发现旧空间包的两条兼容文案在参数化后发生漂移。现仅对原八维
  兼容合同恢复旧文案，非八维合同继续输出由 Schema 参数生成的动态文案，并
  以三维正反向测试锁定两条分支。

验证：

- 后端全量：`459 passed, 1 skipped, 2 warnings`；其中冻结空间零漂移与
  Schema 评分专项 `25 passed`。
- 前端非八维合同：通过；`npm run lint`：通过；`npm run build`：通过，
  共转换 4641 modules，并生成独立 `dimension-schema` chunk。
- `py_compile`、`git diff --check`、新增内容密钥模式扫描：通过；业务前端
  已无空间八维稳定键、`dimensions === 8` 或“八维”显示硬编码。
- 真实浏览器关键路径：通过。隔离 SQLite 与临时账号下，三维评审显示 3 个
  动态维度及 15 个纠偏控件；无效合同显示“维度规则无法解析”且纠偏控件为
  0；已绑定三维真值可按定义编辑；历史未绑定真值标记“规则未绑定，仅供查看”
  且维度字段只读。页面异常、控制台 error、失败请求及 HTTP 4xx/5xx 均为 0。
  截图与 SHA-256 报告存放于
  `119-验收工件-标签实验台维度P1浏览器-20260730/`。

当前状态：

- P1-S1～S6、主干集成、Windows 同步、迁移 25→27、生产数据库/图片完整性
  和真实浏览器复验均已完成。
- 当前生产已使用动态维度 Schema；P2 已进入当前交付候选，尚未进入生产
  Worker 或正式实例。

## 最新完成：维度管理器 P1 第一棒数据层（2026-07-30）

> 隔离工作树：
> `/Volumes/OC-PRIMARY-4T/OpenClawProjects/wt-dim-p1-s1-20260730`；
> 分支：`dim-p1-s1`；起始基线：`9a7898b`。交付提交见本次最终
> `__RESULT__`。本棒未修改评分、Worker、复核、抽样、回归、优化器、
> 前端、A/B 调用时序、提示词、部署或正式数据。

已完成：

- 新增一等、版本化 `DimensionSchema` 实体，包含稳定业务键/版本、类型与
  素材族、父包/L0/候选来源引用、完整定义 JSON、规范哈希和创建/发布/停用
  审计；数据库约束覆盖枚举、JSON 对象、哈希格式、发布审计、自引用和唯一性，
  并建立注册表查询索引。
- 已发布或已停用 Schema 的 UPDATE/DELETE 同时由 SQLAlchemy 持久化事件和
  SQLite 触发器拒绝；草稿/候选仍可正常创建、修改和删除，变更发布内容只能
  新建版本。
- 物化空间包 v1 的“历史默认修订”与“现役 v1.3 修订”。八个稳定键、两套
  权重、1～5 级换算、L1～L5 阈值、Engine 与 risk-review 版本/键/排序常量
  均以静态规范 JSON 和 SHA-256 身份保存；测试逐项对照当前
  `scoring.py`/`risk_review.py`，未接入任何现役评分读取路径。
- 新增只读认证 API：`GET /api/dimension-schemas` 支持按业务键、类型、素材族
  和状态过滤；`GET /api/dimension-schemas/{schema_key}/versions/{version}`
  返回指定版本的完整定义。
- 新增 schema migration version 26。完整库对候选来源建立真实外键；缺少
  中间父表的历史分叉库保留可迁移字段，避免 SQLite 对不存在父表的 NULL
  写入失败。迁移末尾执行 `PRAGMA foreign_key_check`，新增旧库升级、真实
  父子 Schema INSERT 和二次 FK 检查冒烟。
- 按中枢增补授权，仅机械更新既有迁移测试的迁移名清单和最高版本断言；
  未修改该文件其他断言或任何其他既有测试。

验证：

- DimensionSchema 与全迁移定向：`30 passed, 2 warnings`。
- 全量 backend pytest 最终复跑：`417 passed, 1 skipped, 2 warnings`；
  两条 warning 为既有 TestClient/httpx 弃用提示和 baseline regression
  的 SQLAlchemy NULL identity 提示。
- Python `py_compile` 与 `git diff --check`：通过。
- 本棒无前端与执行路径行为变化，按合同未执行前端构建或浏览器验收。

当前进行中与下一步：

- 本棒范围内无待实现项；后续棒再按 ADR-0020 接入执行路径、结果/真值快照
  和前端维度管理器。本棒新增注册表保持只读且不被现役评分链读取。
## 最新完成：审核身份自动取当前登录账号（2026-07-30）

> 隔离工作树：`/Users/yukina/OpenClaw/labellab-ux`；分支：
> `ux-single-reviewer`；指定基线：`f057aaf013bdda85fca5df0430fb3f086b85af60`。
> 本次未推送、未部署，也未修改正式数据、生产目录或其他工作树。

已实现：

- 评测审核、模型迁移和配对回归三个实际可编辑入口改为只读展示
  `/api/auth/me` 返回的当前登录账号，不再使用独立审核姓名或
  `localStorage` 缓存。基线中“例如：小陈”精确 placeholder 实际只有
  `frontend/src/pages/review-page.tsx` 一处，不是历史盘点的四处；另外两处
  分别使用“填写后可提交”和无 placeholder。
- 历史二审/仲裁、初审盲审、主审裁决、配对回归审批和模型迁移复核全部从
  `current_user.username` 写入审核身份。旧 `reviewer_name` /
  `lead_reviewer_name` 字段继续解析，但不参与查重、落库或幂等判断。
- 初审组详情的旧 `reviewer_name` 查询参数不再决定“我的票”，防止通过
  客户端姓名读取其他审核员尚未公开的盲审票。长期决策见 ADR-0019。
- 多人投票测试改用不同认证账号；ReviewPanel 仍收齐全部冻结席位后按既有
  B 口径结算。

已通过验证：

- 审核身份与相关工作流专项：`28 passed, 1 warning`。
- MacBook 控制面使用临时 `DATA_DIR` 执行后端全量：
  `405 passed, 1 skipped, 1 warning`，退出码 0。
- 前端 `npm run lint`：退出码 0；`npm run build`：退出码 0。
- `git diff --check`：通过；
  `git grep -n 'placeholder="例如：小陈"' -- frontend/src`：0 命中。

## 最新完成：ADR-0016 真实执行器与生产回流对接件（2026-07-30）

> 隔离工作树：`/Users/yukina/OpenClaw/labellab-adr16`；分支：
> `adr16-real-executors`；起始基线：`a8018a9`。本次未推送、未部署、未读取
> 真实密钥、未调用真实模型，也未访问并行开发工作树。

已完成：

- 自动优化接入现有 `OptimizerConfig + DoubaoClient` 诊断/合成链路，按真实
  input/output/total usage 计费。新增原子日预算预留/结算、租约竞争、过期
  恢复、最大尝试、指数退避和脱敏错误码；候选只创建新草稿，并原子绑定锁定
  黄金样本的目标错例/稳定对照/盲测三角色配对回归，绝不自动发布。
- 横评新增管理员显式 `real` 模式，三变体绑定服务端模型配置。创建时冻结图片
  SHA-256、人工真值、Prompt 全文、StrategyBundle、AgentPlan、非秘密模型设置、
  transport fingerprint、计价和单轮上限；运行前按质量门优先顺序重新验证全部
  冻结证据。每次 A/B 调用后立即检查 usage 和累计成本，缺失时停止扩大调用。
- 模型配置增加输入上限和双向计价；独立横评模型使用严格编号的现有
  Keychain/DPAPI account。密钥仍只保存系统凭据引用，不进入 API、日志、错误
  或冻结快照。
- 生产回流 POST 改用独立 Bearer token，未配置 `503`、缺失/错误 `401`，使用
  恒定时间比较；同一 `event_id` 完整载荷可重放，任一字段变化返回 `409`。
- 前端自动优化、生产回流、三模型横评和模型配置页展示真实状态、预算、usage、
  成本、失败/重试与机器鉴权状态。危险配置只对管理员显示，并要求质量门、预算
  和真实费用二次确认；主导航仍保持六个工作域。
- 新增生产回流集成说明、默认 dry-run 的标准库发送脚本，以及 ADR-0018 分阶段
  启用、停机、回滚、监控、租约恢复和密钥轮换 runbook。

安全默认值保持：

- `enabled=false`
- `dry_run=true`
- `daily_budget_micros=0`
- 横评 `execution_mode=test`

验证结果将在本次交付最终提交前以独立 `.venv` 全量后端 pytest、前端 lint/build、
真实浏览器关键路径和 `git diff --check` 的最终结果更新；测试全程使用假执行器，
禁止真实模型网络调用。

## 最新完成：Windows 公司服务器受控部署生命周期（2026-07-30）

> 基线提交：`a8018a9f53c7614c7e02745e587ea86a026e6c29`；开发分支：
> `windows-deploy`。只修改当前研发仓库，未部署、未 push、未访问生产目录、
> 生产数据、真实 DPAPI 凭据或真实模型。决策见 ADR-0017。

已完成：

- 新增 `scripts/windows/install.ps1`、`doctor.ps1`、`start.ps1`、
  `backup.ps1`、`restore.ps1`。五脚本使用严格模式、UTF-8/CJK 参数数组、
  脚本位置解析仓库、显式原生退出码和仓库内 Python `-X utf8`；不提权、
  不安装系统软件、不修改注册表/服务/防火墙/计划任务。
- 安装门禁固定 Python 3.11/3.12、Node 20～26、npm 10/11；只创建仓库内
  `.venv`、安装既有 requirements、执行 `npm ci` 和生产构建，不写业务
  数据或启动服务。`-Check`/`-DryRun` 不安装、不构建、不联网。
- doctor 按“显式参数 → 进程 `DATA_DIR` → 仓库 `.env` →
  `%LOCALAPPDATA%\3d66-label-system`”解析，只读检查仓库、运行时、构建、
  现有父目录、SQLite 和 Windows 凭据引用；拒绝相对路径、仓库内路径及
  symlink/junction/reparse point，不调用 DPAPI。
- start 必须先通过 doctor，默认进程未显式设置 `APP_HOST` 时强制
  `127.0.0.1`，前台运行现有 launcher，不注册服务或后台任务。
- 新增 `backend/app/windows_deploy.py`：正式备份通过 SQLite backup API
  生成一致副本，清空会话和主/优化模型凭据，排除 logs/环境文件，生成独立
  Windows v1 manifest、大小和 SHA-256；拒绝 NTFS ADS、设备名、大小写重复、
  未声明文件、额外根入口、未来迁移、篡改和全部 reparse point。
- restore 自动先 dry-run，拒绝服务端口占用，在目标同一父目录建立 staging
  与 rollback snapshot，分入口原子替换 database/images，失败自动补偿；
  不恢复或迁移 DPAPI/Keychain/API Key。
- `start-3d66.cmd`、`启动3d66标签系统.cmd` 和审计中发现的
  `首次安装.cmd` 已收敛为参数/退出码透传壳，不再保留 `npm install` 或直接
  launcher 等第二套逻辑。
- DPAPI 底层构造器与工厂增加 Windows 双层平台守卫；非 Windows 测试证明在
  加载 `crypt32`/`kernel32` 前拒绝，真实 DPAPI 回环继续只在 Windows 执行。
- ADR-0016 默认安全值未修改：自动消费者仍默认关闭、dry-run、零预算，部署
  脚本不触发真实自动化、模型调用或发布。

验证：

- Windows 生命周期专项：`29 passed`。
- 安全层专项：`15 passed, 2 skipped`；跳过项分别是 Windows-only 真实
  DPAPI，以及当前 macOS 执行沙箱返回 `OSStatus -50` 的真实登录 Keychain。
- 全后端隔离临时 `DATA_DIR`：`420 passed, 2 skipped, 1 warning`；warning
  为既有 Starlette TestClient/httpx 弃用提示。
- Python 3.12 `py_compile`、脚本危险命令/严格模式/UTF-8/参数和退出码静态
  回归、`git diff --check`：通过。
- 当前 MacBook 未安装 `pwsh`，未做 PowerShell parser 语法机检；没有安装
  PowerShell，待真实 Windows/获批 pwsh 环境验收。
  已部分解除：2026-07-31 在 13600K 原生 Windows（PowerShell 5.1）完成
  doctor 门禁与 ADR-0023 端到端实机验收，详见前文「原生 Windows 实机验收」。

明确未完成：

- ~~本分支的 `install.ps1` 与 `start.ps1` 在 PowerShell 5.1 上实测不可用。~~
  已解除：四个运行期缺陷已于 2026-08-01 修复，并在 13600K 用**真实两个脚本**
  完成安装与启动复验（install exit 0；start 两种调用均 HTTP 200）。
- 13600K 实机已覆盖：doctor 九项门禁（两个 DPAPI 范围 + 默认 DATA_DIR）、
  DPAPI 真实回环、非空/空 Key 保存、解密正确性、跨范围解密兼容。
  仍未覆盖：管理员全新安装、Python/Node/npm 版本矩阵、中文/空格路径、
  四级 DATA_DIR、Ctrl+C、活跃 WAL 备份、restore 故障回滚、
  junction/reparse point 的完整 ADR-0017 清单，以及 Windows Server 环境。
- 公司脚本签名/ExecutionPolicy、正式备份盘和 NTFS ACL、局域网暴露/TLS/
  防火墙方案仍为待决项；当前代码不绕过或擅自配置。
- 当前沙箱禁止写 `.git`，尚未形成用户要求的三个逻辑提交；工作树内容与测试
  已完成，需在具备 Git 写权限的同一 `windows-deploy` 分支完成提交核验。
## 最新完成：部署前置第三棒收口（2026-07-29）

> 起始基线：`cc3d254`。本棒完整保留 Phase A/B 与前两棒工作树，不推送、
> 不部署、不连接生产数据，也未调用真实模型。

- ADR-0015 与本状态文档已统一为 Owner 确认的结算口径：初审组必须收齐
  面板创建时冻结的全部审核席位后才结算；部分字段提前达到严格多数不能
  提前关闭面板。当前代码与测试本来就是该行为，本棒未改结算逻辑。
- “候选提示词”与“小样本配对回归”不再共用同一个页面。候选节点负责
  生成、编辑、物化不可变候选并交接回归；配对回归节点独立展示任务、
  冻结样本证据、系统建议和人工结论。待发布决策可按回归 ID 深链到对应
  证据，六个一级工作域与所有 `/legacy` 历史深链保持不变。
- 新增迁移 23，为 `material_packages` 和 `material_package_items` 同时建立
  UPDATE/DELETE 拒绝触发器。既有数据库即使已经应用迁移 20～22，也会在
  前向升级时补齐数据库级不可变保护。
- 新增四路数据库专项测试，分别覆盖素材包、素材包条目的 UPDATE 和 DELETE
  被拒，并确认失败事务后原记录仍完整保留。

验证：

- 迁移专项：`23 passed`。
- 全后端：`391 passed, 1 skipped, 1 warning`；唯一 warning 为既有
  Starlette TestClient/httpx 弃用提示。
- `tests/test_review_panel_concurrency.py` 独立连跑 3 次，每次均为
  `2 passed, 1 warning`。
- 前端 `npm run lint`：0 error；`npm run build`：成功，生成独立
  `paired-regression-page` lazy chunk。
- `git diff --check`：通过。
- ADR-0016 默认安全值保持
  `enabled=false / dry_run=true / daily_budget_micros=0`。

本棒未补冻结指标入口的前端单元测试：仓库当前没有前端测试框架，且任务禁止
新增第三方依赖；该入口已有上一棒真实浏览器验收，本次未为最低优先项扩依赖。

三项产品语义已由 Owner 定稿（2026-07-30 晨，决定：维持现状，不再视为待决项）：

1. 最后一票产生“投票修订 + 结算修订”两次 revision——保留现状；
2. 冻结指标来源使用手输批次键或评测 ID，不新增目录选择器——保留现状；
3. 相同冻结哈希使用不同业务键时返回 409——保留现状。

## 最新完成：ReviewPanel CAS 并发与冻结版本指标入口（2026-07-29）

> 基线提交：`cc3d254`。本棒继续保留 Phase A/B 与前一棒全部未提交工作树；
> 未提交、未推送、未部署，也未调用真实模型。

- `ReviewPanel.revision` 的投票、最终结算和主审裁决已改为数据库条件更新：
  `UPDATE ... WHERE id=:panel_id AND revision=:expected`。受影响行数不是 1
  时回滚当前事务并返回 `409 STALE_REVIEW_PANEL`，不再依赖 ORM
  “先读后 `+= 1`”。
- 普通投票先 CAS 占用投票修订；收齐席位后形成最终真值时，再以已占用修订
  执行一次 CAS。最终审核记录、面板完成态、评测结果修订和纠偏优化案例在
  同一事务内落库；主审并发只有一个赢家，纠偏案例只入队一次。
- 结算触发已收敛到 `panel_is_ready_to_settle()`。Owner 已确认采用
  “收齐全部冻结席位后结算”：即使部分字段提前达到严格多数，也必须等待
  全部冻结席位完成独立盲审；当前代码与测试已是该语义，无行为改动。
- 新增真实双会话并发测试：用文件 SQLite、两套独立 HTTP 客户端和同步屏障
  强制两个请求在同一旧修订上竞争，覆盖并发盲审票、失败方刷新重试、并发
  主审、最终真值及优化案例恰好一次。
- “版本指标”页已开始接入冻结快照选择：可选择提示词版本，并按任务批次键
  或明确评测结果 ID 任务集保存不可变指标快照；实时全量聚合明确降为
  “非冻结口径”的运营参考。

验证：

- 全后端：`387 passed, 1 skipped, 1 warning`。
- 前端 `npm run lint`、TypeScript/Vite 正式 `build`、`git diff --check`：
  通过。
- 使用隔离 SQLite、临时账号、`127.0.0.1:18127` 和 OpenClaw managed
  Chromium 的独立无痕 BrowserContext 完成认证验收：新初审请求顺序为
  `POST review-panel/open` 后 `POST review-panel/votes`；历史
  `/legacy/review/secondary`、`/legacy/review/arbitration` 深链可读；
  主导航严格为六个一级工作域且没有旧样本二审/仲裁入口；冻结批次快照创建
  成功，浏览器控制台无非预期错误。
- 浏览器证据：
  `/Users/yukina-macmini/OpenClaw/112-验收工件-标签实验台ReviewPanelCAS-20260729/`。
- 隔离验收库中的 ADR-0016 默认值仍为
  `enabled=0 / dry_run=1 / daily_budget_micros=0`。

## 最新完成：ADR-0015 新初审入口收口（2026-07-29）

> 基线提交：`cc3d254`。本次继续保留 Phase A/B 既有未提交工作树；未提交、
> 未推送、未部署，也未调用真实模型。

- 新图片初审不再回落到历史 `/api/evaluations/{id}/review` 状态机。前端在
  结果尚无面板时先按全局策略创建 `ReviewPanel`，随后提交盲审投票。
- 历史审核接口已在 OpenAPI 标记为 deprecated，只允许已有
  `secondary / arbitration` 链继续收尾；初审请求返回
  `409 REVIEW_PANEL_REQUIRED`，缺少历史初审/二审证据的兼容仲裁也会拒绝。
- 单人初审形成最终真值后，样本集继续读取服务端重算后的人工等级；同时修正
  SQLite 同一会话内审核时间出现 naive/aware 混合时的稳定排序。

验证：

- 初审/历史兼容/样本集专项：`12 passed`。
- 全后端：`385 passed, 1 skipped, 1 warning`。
- 前端 `npm run lint`、TypeScript/Vite 正式 `build`、`git diff --check`：
  通过。

## 最新完成：Pipeline v2 Phase B 安全自动化、生产回流与三模型横评框架（2026-07-29）

> 基线提交：`cc3d254`。本阶段在 Phase A 未提交工作树上继续开发，未提交、
> 未推送、未部署；安全边界见 ADR-0016。

已完成：

- 新增自动优化消费者策略、运行记录与队列租约/CAS。消费者默认关闭且默认
  `dry-run`，支持纠偏样本阈值、P0/P1 即时触发、日预算、冷却、候选上限、
  最大尝试次数、指数退避、过期租约恢复和只追加审计。
- 即使显式开启，未配置执行器时也只会形成待执行计划；确定性测试替身仅供
  隔离测试。自动链路最多推进到“等待提示词发布二审”，永不自动发布提示词。
- 新增不可变生产反馈事件入口。事件执行字段校验、内容哈希和幂等冲突检查，
  只映射到实验台优化案例队列，不连接或写入生产数据库。
- 新增 Sol/Terra/Luna 横评实验和结果框架。实验冻结 cohort、Prompt、Rubric、
  Engine、AgentPlan、策略包和模型定价；当前只允许 `disabled` 或确定性
  `test` 执行模式，不会发起真实模型调用。
- 横评指标覆盖质量、P0/P1、低置信度率、人工介入率、p50/p95、模型成本、
  含人工与重试的总成本和稳定性。生产候选先经过质量硬门槛，再比较
  Pareto/综合成本；最终仍必须人工决定。
- 前端新增“自动优化编排”“生产案例回流”“Sol/Terra/Luna 横评”和“系统审计”
  深链，明确展示默认关闭、dry-run、真实执行器未配置和永不自动发布等边界。

验证：

- Phase B 专项与迁移：`27 passed`；相关组合专项：`34 passed`。
- 全后端：`384 passed, 1 skipped, 1 warning`；唯一 warning 为既有
  Starlette TestClient/httpx 弃用提示。
- Python `py_compile/compileall`、前端 `npm run lint`、TypeScript/Vite
  正式 `build`、`git diff --check`：通过。
- 使用隔离临时 SQLite、临时验收账号和本机 Chrome 完成认证浏览器验收：
  默认关闭的安全检查、生产案例回流、三模型横评、系统审计页面均可达；
  页面诚实显示未配置真实执行器，应用控制台 `0 error`。

明确未完成：

- 未接真实 Sol/Terra/Luna 或提示词优化执行器；未运行真实横评、真实模型、
  正式 Gold、自动金丝雀、自动回滚或生产发布。
- 未连接或修改正式数据库、凭据、Keychain/DPAPI、Gateway 或系统配置。
  生产反馈当前是实验台侧不可变入口合同，不代表生产系统已完成对接。

## 最新完成：Pipeline v2 Phase A 后端收口与前端信息架构重构（2026-07-29）

> 基线提交：`cc3d254`。本阶段继续保留既有未提交工作树，未提交、未推送、
> 未部署；产品语义遵循 ADR-0015。

已完成：

- 素材上传继续生成不可变 `MaterialPackage`；素材包列表可按当前单提示词或
  A/B 策略汇总未评测、仅旧版、当前版完成、排队、运行与失败，素材选择页
  默认排除当前策略已完成图片并允许显式关闭后复测。
- 初审人数继续支持 1/3/5/7/9 人冻结快照。多人组收齐票后改为调用统一的
  逐字段严格多数计算，同时覆盖八维和关键字段；无多数时
  `ReviewPanel.status=lead_adjudication`，`EvaluationResult.review_stage`
  仍保持 `initial`，主审最终事件也记录为 `initial`，不再把新任务写入历史
  `arbitration` 语义。旧 `secondary/arbitration` 数据仍可从兼容深链读取。
- 新增审核面板列表、纠偏案例队列读取、提示词冻结任务集指标快照 API。
  指标同时返回样本准确率、纠偏数、逐维准确率、等级准确率、审核覆盖率和
  N；未完成初审的结果不计为正确。
- 提示词发布会保存上一已发布版本的回滚指针；新增显式人工回滚 API。
  金丝雀字段只展示骨架状态，未实现自动运行、自动回滚或生产写入。
- 前端主导航收束为六个一级工作域，并实现 ADR-0015 的全部二级深链：
  素材与任务、初评与初审、优化与回归、二审与版本、模型实验、系统治理。
  旧平铺路径重定向兼容，不再作为主导航。
- 素材包/素材选择、低置信度初审组、组内共识/主审裁决、纠偏案例、
  候选提示词/配对回归、待发布决策、版本指标、版本历史与回滚均接现有真实
  API。Sol/Terra/Luna 横评、生产候选和完整审计页明确显示“执行器未接通”，
  没有伪造模型调用、自动回流或生产实装。

验证：

- 初审/素材包/冻结指标专项：`7 passed`。
- 全后端：`376 passed, 1 skipped, 1 warning`。
- 前端 `npm run lint` 与 TypeScript/Vite 正式 `build`：通过。
- `git diff --check`：通过。
- 使用隔离临时 SQLite、临时验收账号和本机 Chrome 完成认证浏览器验收：
  六个一级工作域、主审裁决深链、版本指标字段、版本历史与回滚、模型横评/
  审计的诚实未接通状态、旧 `/assets` 重定向均正确；控制台无错误。
- 以 960px 可用宽度模拟 200% 缩放后的等效布局，页面无文档级横向溢出，
  移动头部可见。

明确未完成：

- 未调用真实豆包、Sol、Terra 或 Luna；未接生产自动回流、自动优化消费者、
  横评执行器、完整审计事件流或生产发布执行器。
- 未修改正式数据库、凭据、Keychain/DPAPI、Gateway 或系统配置；未创建
  正式 Gold，未发布提示词，未触发真实金丝雀或人工回滚动作。

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
| 初审人数弹性机制 | 当前工作树已完成 | 初期默认 1 人即时定案；支持切换 3/5/7/9 人，收齐全部冻结席位后计算多数共识；面板创建时冻结人数 |
| P0-E 金丝雀前端编排 | 工作树已完成 | 已接 E3 认证 API；只登记门禁证据，不执行导入、下载、模型、Gold 或发布 |
| macOS 部署生命周期 | 离线能力已完成 | 安装/诊断/前台启动/脱敏备份恢复已测试；目标 MacBook 尚未实际部署 |
| Windows 部署生命周期 | 实机验收通过（含 wrapper 脚本） | doctor 门禁 9/9（两个 DPAPI 范围）与 ADR-0023 API Key 链已在 13600K 原生 Windows 实机验收；`install.ps1`/`start.ps1` 的四个 PS 5.1 缺陷已于 2026-08-01 修复并用真实脚本复验（install exit 0；start 两种调用均 HTTP 200）；公司内网实例尚未部署 |

## 本地数据快照

最近一次页面验收时：

- 素材：17 张；
- 评测结果：69 条；
- 智能抽样：63 必审、3 抽审、0 暂缓、3 已审核。

这是本机临时 Demo 数据，会随上传、评测和审核改变；不得把这些数字写成产品固定指标。

## 当前最高优先级

### P0

1. 按 ADR-0017 在公司 Windows 服务器完成无管理员安装、doctor、启动、
   DPAPI、活跃 SQLite 备份和故障恢复全清单；按 ADR-0013 在目标 MacBook
   完成对应 Keychain 与灾备演练，再接公司内网真实模型。
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

2026-07-31（`windows-deploy@f8d5789`，**13600K 原生 Windows 11 实机**，
PowerShell 5.1.26100.8115 Desktop）：

```text
doctor.ps1 全量门禁：9/9 通过 ×3 轮（CurrentUser / LocalMachine / 默认 DATA_DIR）
Windows DPAPI 真实内存回环：两个范围均通过
doctor 只读校验：三轮后 DATA_DIR 仍不存在
API Key 落库形态：dpapi:v1: / dpapi-machine:v1:（前缀随范围切换）
明文泄漏扫描：SQLite 全库 / GET 响应 / worker.log 均无命中
解密正确性：4 次上游调用 sha256 全部字节级匹配
跨范围兼容：current-user 引用在 local-machine 运行时下正常解密
空 Key 重提交：保留既有引用，不误删凭据
manual install：venv 10s / pip 27s / npm ci 34s / vite build 17s
install.ps1：PowerShell 5.1 下必然失败（当时已绕过；后续已修复，见下方 08-01）
start.ps1：PowerShell 5.1 下无法启动服务（缺陷 4；后续已修复，见下方 08-01）
```

2026-08-01（同一台 13600K，PowerShell 5.1.26100.8115，基线 `windows-deploy@42dbda5`
叠四个缺陷修复，**不绕过任何 wrapper**）：

```text
install.ps1 -Check：Python 3.11.4 / Node v24.15.0 / npm 11.12.1 门禁全过
install.ps1（完整安装）：exit 0，65s；pip 31 包 / npm ci 113 包 / vite build ✓ 13.36s
                        .venv 与 frontend/dist 均生成；-Check 复检 exit 0
start.ps1（不带 -DataDir）：监听 18080，GET /openapi.json 200，GET / 200
start.ps1 -DataDir ... -DpapiScope CurrentUser：同样两个 200
doctor 九项门禁：两个变体下均全过（scope 随入参正确切换）
无 -DpapiScope 残留目录；共享服务 pid 121044 / 14988 全程存活
```

2026-07-30（`windows-deploy` Windows 生命周期工作树，未在 Windows 实机运行）：

```text
Windows 生命周期专项：29 passed
安全层专项：15 passed, 2 skipped
全后端回归：420 passed, 2 skipped, 1 warning（隔离临时 DATA_DIR）
Python 3.12 py_compile：通过
脚本静态安全/参数/退出码回归：通过
git diff --check：通过
PowerShell parser：当前 MacBook 无 pwsh，未机检
```

2026-07-29（当前双流水线重构工作树，尚未提交或部署）：

```text
初审工作流专项：5 passed
迁移专项：19 passed
全后端回归：374 passed, 1 skipped
前端 lint 与正式 build：通过
初审人数：1/3/5/7/9 可配置，偶数由 API/数据库拒绝
单人模式：首票在 initial 阶段形成最终真值
多人模式：收齐全部冻结席位后逐字段计算严格多数，无多数仍进入初审主审裁决
历史稳定性：面板人数创建时冻结，全局配置只影响新面板
```

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

## 账号权限与模型管理系统（2026-08-01）

已完成多人账号 RBAC（admin/manager/reviewer/analyst/viewer）、核心写接口权限收口、会话即时失效和最后管理员保护。模型配置升级为统一注册表，支持 OpenAI Chat/Responses、Anthropic Messages 与受控 OpenAI-compatible JSON 协议，节点模型绑定在入队时冻结非密快照。Docker/ Linux 使用 `/data/secrets/master.key` 的 AES-GCM 主密钥文件，数据库使用命名卷持久化；主密钥必须纳入备份。Mac mini 已安装 Docker/Colima 并完成真实容器构建、密钥密文、命名卷重启与容器重建读回金丝雀。

## 管理员模块化类目流水线（2026-08-01）

已移除类目表固定三值约束。管理员可创建草稿类目，并配置输入 MIME/后缀、有序前处理模块、跟随/单提示词/A-B 模式、类目附加指令、现役八维指标重点范围、各节点模型和自动化策略；启用后素材上传、ZIP 过滤、任务排队与 Worker 执行均读取类目合同。新任务冻结 `evaluation-category-profile-v2`，旧 v1 快照继续兼容。处理器、指标和模型节点均为服务端受控注册表，未知模块、未知指标和错误顺序 fail-closed，不开放任意代码插件。Docker v39→v40 迁移、动态类目创建、非法合同拒绝、容器重启读回及 1440/390 响应式验收通过。架构决策见 ADR-0028。
## 最新完成：统一标签生产与治理平台基础接口（2026-08-01）

- 新增本地一等内容投影、上游增量事件、标签发布变更集、版本化正式标签、Outbox
  变更事件和下游消费者 checkpoint。外部系统未连接前，接口可在本地按合同验收。
- 上游仅接收 `content.created`、`content.updated`、`content.deleted`；同一 `event_id`
  可安全重放，载荷漂移返回冲突，乱序事件保留审计但不覆盖新状态。没有本地素材的
  内容明确停在 `awaiting_material`，不会伪造评测成功。
- 人工完成初审的 `approved`/`corrected` 真值才可建立发布请求；管理员二审通过后才
  生成 `PublishedLabel` 与 Outbox。消费者仅能读取发布层，不能读取候选、评测原始响应
  或人工过程数据。
- 回滚会引用历史标签创建新的发布版本和 `rolled_back` 事件，不删除或重写历史。
  当前不调用下游 webhook、不直连下游数据库，保持 `external_writes_enabled=false`。
- 上游和下游使用独立凭据：`CONTENT_INGRESS_TOKEN` / `content-ingress.token` 与
  `LABEL_CONSUMER_TOKEN` / `label-consumer.token`，避免单侧凭据泄露扩大权限。
- ADR-0029 固化了三层标签、事件幂等、发布门禁、cursor 对账和最终一致性合同。
- 发布工作台现已提供正式标签手动导出：当前生效/全部历史版本、类目和发布时间筛选，支持 XLSX、CSV、JSON。导出只读取 `PublishedLabel`，要求 `releases:read`，单次最多 10,000 条并写入只追加审计；CSV 已防公式注入。
