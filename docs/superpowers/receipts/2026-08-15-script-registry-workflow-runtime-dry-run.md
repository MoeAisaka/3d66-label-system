# TPENG 标签实验台受控脚本与工作流运行时 dry-run 验收回执

## 范围与基线

- 工作分支：`codex/tpeng-label-reconstruction-kickoff-20260815`
- 代码能力基线：`6da0e1942e287ffc93aa289710a64c4158bfb713`
- 数据库迁移：71
- 环境：本地 `dry_run`
- 产品边界：标签实验台是标签体系重构统一产品载体；本批只交付受控脚本注册、工作流版本、通用运行时、五队列适配和桌面运行证据。

本批未连接真实 DataWorks、真实模型、真实上游、真实大维表/小表或其他外部数据库；未启动机制发布、标签事实发布、存量覆盖或真实 DML；未推送、未创建/合并 MR、未部署测试服或生产环境。

## 能力验收

| 验收项 | 结果 |
| --- | --- |
| 脚本注册 | 定义与不可变版本、受控生命周期、Schema/哈希/权限/重试校验通过；任意代码与命令字段 fail-closed |
| 工作流注册 | DAG、唯一入口/终点、依赖兼容、受限 JSON 条件路由、canonical hash 和冻结快照通过 |
| 调度 | 复用 `DeterministicQueueScheduler` 与 `QueueSchedulerState`；仅使用 validation、interactive、production_batch、canary、recovery 五队列 |
| 运行时 | 幂等创建、有限租约、心跳、超时、重试、检查点、暂停、取消、恢复和追加式审计通过 |
| 历史边界 | 未重写或回填 `EvaluationProductionRun`、历史评测、纠偏、机制发布或标签发布记录 |
| 执行器边界 | 首批只允许 `deterministic_fixture`；页面/API 不提供源码、SQL、Shell 或命令入口 |

## 两步故障注入证据

确定性工作流为 `identity -> fail_once`：

1. 首个步骤成功并写入检查点。
2. 第二个步骤首次执行按夹具合同失败，进入 `recovery` 队列。
3. Worker 重新领取后从检查点恢复，第二次执行成功。
4. 已成功的 `identity` 步骤没有重跑。
5. 相同幂等键重复请求返回同一运行，没有新增 `ProductionRun`。
6. 链路没有创建 `EvaluationJob`、`LabelRelease` 或其他正式发布副作用。

专项端到端测试结果：`1 passed`。

## 自动化验证

后端在仓库根目录使用 Python 3.12 环境执行：

```text
PYTHONPATH=.:backend <python-3.12> -m pytest -q backend/tests
1460 passed, 1 skipped, 6 warnings in 135.16s
```

warning 仅为既有 FastAPI/httpx 和 PDF SWIG 弃用提示。

前端依次通过：

- 全部合同脚本：维度、V3-only、节点纠偏、Proposal PDF、100 张均衡基准、等级/思考控制、模型中心、信息架构、机制编辑器、双工作区、标签需求、内容身份和运行中心。
- Lightbox 浏览器合同与五档回归指标合同。
- `npm run lint`。
- `npm run build`；仅保留既有 Vite 配置提示和主 chunk `524.41 kB` warning。

## Microsoft Edge 桌面验收

本地夹具：`http://127.0.0.1:4176/scripts/runtime-center-test.html`。夹具渲染真实 `OperationsCenterPage`，API 全部为内存 mock，不连接测试服、数据库或外部网络。

验收确认：

- 一级页展示验证、交互、生产批量、金丝雀、恢复五队列及配额/阻塞摘要。
- 通用运行列表展示工作流版本、当前步骤、最后检查点、责任人、阻塞原因、状态与队列。
- 点击“查看证据”后，二级抽屉展示步骤时间线、脚本版本、输入哈希、输出哈希、`checkpoint_hash`、租约责任和完整冻结快照。
- 抽屉保留取消与暂停动作，主页面仍可识别当前运行主线；桌面宽度下未出现单字竖排或主操作区横向挤压。
- React 页面执行无错误。DevTools 中仅有本地夹具未提供 `favicon.ico` 的 404，以及已安装浏览器扩展自身的 warning；均不影响页面能力。

## 停止条件与后续门禁

本批到本地 dry-run 验收即停止。任何真实执行器、DataWorks、模型调用、外部数据库、自动发布、Codeup 推送/MR 或 `192.168.1.35:8081` 部署，都必须由 Owner 重新冻结范围并明确授权。
