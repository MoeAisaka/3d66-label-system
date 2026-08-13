# 回归纠偏并发恢复与决策入口设计

## 背景与真实故障

2026-08-13，内网测试服在执行基准回归自动纠偏时出现两类相连故障：

1. `POST /api/baseline-regressions/20/corrections` 在同一个 SQLite 写事务内调用调优模型，
   持锁约 7 分 44 秒。同期人工纠偏创建审核面板连续返回
   `sqlite3.OperationalError: database is locked`。
2. 调优模型最终返回 HTTP 200，但候选 JSON 不符合统一机制合同，任务 #4 以
   `CORRECTION_GENERATOR_OUTPUT_INVALID` 失败。现有错误只说明“字段无效”，无法定位缺失字段。

锁等待期间 8 个评测 worker 在 `BEGIN IMMEDIATE` 处抛出异常并退出；原健康检查只确认
Web 服务存活，无法识别后台 worker 已全部退出。

## 已冻结目标

- 自动纠偏的模型调用不得持有数据库写事务，也不得阻塞人工纠偏等其他写操作。
- 启动接口先持久化冻结输入和 `processing` 状态，再由后台任务自动生成候选、校验和创建候选回归。
- SQLite 短暂锁冲突只让 worker 有限退避重试，不得导致 worker 进程退出。
- 调优模型候选允许安全解包常见的 `candidate` / `mechanism_candidate` 包装层，但完整合同字段仍必须严格校验。
- 候选结构失败必须列出缺失或无效字段，不能只返回笼统错误。
- 用户始终在“存量回归 → 基准回归处理纠偏”当前区域查看分析、候选、回归和人工结论；不新增第二个结果页面。
- 候选回归完成后，同一区域显示“等待人工决策”。只有系统管理员看到并可点击“启用候选”或“拒绝候选”。

## 非目标

- 不改变 AI 建议的业务规则、评分合同或候选启用门槛。
- 不自动启用候选，不发布标签事实，不触发存量标签覆盖。
- 不修改既有人工真值、历史纠偏记录或失败任务证据。
- 不引入 PostgreSQL、Redis、外部任务队列或新的部署拓扑。
- 不处理移动端。

## 后端设计

### 自动纠偏短事务

启动与重试接口使用三个边界：

1. **准备事务**：冻结输入、写入 `processing/candidate_generation`、保存确定性分析和现役机制快照后提交。
2. **无事务模型调用**：使用已冻结的纯数据请求调用调优模型。该阶段不持有 SQLAlchemy Session 或 SQLite 锁。
3. **落库事务**：重新加载纠偏任务，校验当前状态与冻结基线未漂移，写入完整候选、候选 revision 和候选回归；失败则写入可操作错误。

FastAPI `BackgroundTasks` 在响应发送后执行第 2、3 阶段。接口立即返回已持久化的
`processing` 任务，前端沿用 2.5 秒轮询。相同纠偏任务只允许一个后台执行租约；重试沿用冻结输入。

### 候选合同诊断

候选归一化支持顶层直接返回，以及单层 `candidate`、`mechanism_candidate`、
`unified_candidate` 包装。校验错误输出稳定字段路径，例如：

`调优模型返回的统一机制候选缺少或无效字段：prompt.system_prompt、revision.contract`

任何缺失完整 contract、classification map 或 subcategory dimensions 的返回仍失败，不进行猜测补全。

### worker 锁退避与健康

`process_one` 捕获 SQLite `database is locked`，按短间隔有限退避后重新轮询；该异常不进入任务技术失败，也不退出进程。非锁类数据库异常继续抛出，避免掩盖数据错误。

`/api/health` 保持启动探针兼容，同时返回 worker 活跃数和期望数。新增 readiness 路径供 Docker
健康检查使用：主服务正常但 worker 全失活时返回 503。部署脚本启动门仍使用基础存活探针，避免启动环。

## 前端设计

当前纠偏区域标题下固定展示：

- “分析结果就在当前区域查看”；
- “候选回归完成后，本区域进入等待人工决策”；
- “系统管理员在此启用或拒绝候选，其他账号只读等待”。

失败卡展示阶段、错误码、具体字段问题和“沿用冻结样本重新执行”。成功后仍在右侧显示指标、
AI 建议、候选 revision、候选回归与风险；决策按钮不跳转页面。

## 风险与回退

- 后台任务在进程重启时可能中断；启动恢复逻辑把超时的 `processing/candidate_generation`
  标记为可重试失败，避免永久卡住。
- 部署前创建 SQLite 在线快照；代码可回滚到部署前 Codeup `main`，数据库新增状态仅使用现有列。
- 若专项并发测试、全量测试、构建、worker readiness 或 Edge 当前页验收任一失败，停止合并部署。

## 验收

- 慢调优模型调用期间，人工审核面板可正常写入，不返回 500。
- 启动纠偏接口快速返回 `processing`，模型调用完成后自动进入 `regression` 或带具体字段的 `failed`。
- SQLite 锁冲突不会杀死 worker；readiness 能发现 worker 全失活。
- 当前页面明确说明结果位置和决策位置；管理员可在同一区域启用/拒绝，普通账号只读。
- 后端专项与全量测试、前端合同/lint/build、`git diff --check`、服务器数据库完整性、worker
  数量、Codeup SHA、静态 build SHA 与 Edge 当前路径验收全部通过。
