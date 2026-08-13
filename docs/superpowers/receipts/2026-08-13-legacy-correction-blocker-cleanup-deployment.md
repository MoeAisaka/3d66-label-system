# LabelLab 历史纠偏人工确认阻塞清理部署回执

## 范围

- 功能提交：`3cb919d8eb48241f2225e5dc12f916c50d44ce05`
- Codeup 合并提交：`8b9e5c4cff418196e93f63852ff39b9ed8f412e7`
- Codeup MR：#4 `fix: 清理历史纠偏人工确认阻塞`
- 测试环境：`http://192.168.1.35:8081`
- 范围仅为历史 `LEGACY_CORRECTION_INCOMPLETE` 纠偏记录移除废止的
  `human_confirmation_required` blocker；不修改自动纠偏五阶段、最终人工启用/拒绝、
  双发布轴或存量重跑合同。

## 合并与部署

- 2026-08-13 在 Codeup 通过 MR #4 评审，以“创建一个合并节点”合入 `main`；源分支
  `codex/legacy-correction-blocker-cleanup-v1` 已保留。
- 采用完整 `origin/main` Git bundle，通过受保护脚本
  `/usr/local/sbin/deploy-3d66-label-test` 发布。脚本记录并可在构建或健康检查失败时回滚到
  `e570c4e4779ca426b48fe280e16ae7affa23e5eb`。
- 本机保留 bundle：
  `outputs/deployments/2026-08-13-legacy-correction-blocker-v1/codeup-main-8b9e5c4cff418196e93f63852ff39b9ed8f412e7.bundle`；
  SHA-256 为
  `e6ea440fcca4f728e61cc43b8ab6d14fd074e628e8e04e0efad57bcfaff14be5`。
- 部署前通过 SQLite online backup 保留服务器快照：
  `/data/database/predeploy-snapshots/app-predeploy-e570c4e4-before-8b9e5c4c-20260813T081434Z.db`；
  SHA-256 为
  `a659c4a17ce99799e8b74b34486ccb08eb30b80cf728f3f2a10d5f6e287fb3b3`。

## 部署前闸门

| 检查项 | 结果 |
| --- | --- |
| SQLite integrity / FK | `ok` / 0 |
| Schema migration | 64 |
| 活跃评测、基准回归、processing 纠偏、存量重跑 | 均为 0 |
| 目标历史记录 | 3 条，均包含废止 blocker，适合作为精确迁移对象 |
| 容器 | `running/healthy`，restart count=0 |

## 部署后验收

| 检查项 | 结果 |
| --- | --- |
| Codeup main / server HEAD | 均为 `8b9e5c4cff418196e93f63852ff39b9ed8f412e7` |
| 容器与健康检查 | `running/healthy`、restart count=0；容器内与外部 `/api/health` HTTP 200 |
| Schema migration | 65 |
| SQLite integrity / FK | `ok` / 0 |
| 活跃任务 | 活跃评测、基准回归、processing 纠偏、存量重跑均为 0 |
| 目标历史记录 | 仍为 `failed` + `LEGACY_CORRECTION_INCOMPLETE`，保留原失败说明，`blockers_json=[]` |
| 前端版本 | `LabelLab v0.2.0 · build 8b9e5c4` |
| Edge 桌面验收 | 基准回归和 Proposal PDF V3 合同配置在 `1440x900`、`1280x720` 无白屏、文档横向溢出、控制台错误 |

历史纠偏失败卡片现在说明“旧版纠偏任务未创建候选或回归，请重新执行”及“重新执行会沿用
本次冻结样本，不需要补充任何配置”；废止的“提示词或维度调整必须由人工确认后另行创建
候选版本”未再显示。验收过程中没有启动纠偏分析、重新执行、真实模型调用或存量重跑。
