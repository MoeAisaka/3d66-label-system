# TPENG 标签实验台 3D/SU Shadow 与确定性闭环预备批次回执

## 范围与边界

- 工作分支：codex/3d-shadow-dry-run-prep-20260816
- 基线：main@9943b8c7ae14dd70a54b7c08197be58c9b8131c2
- 目标类目：model_3d_su，复用 TPENG 标签实验台平台通用能力，不复制第二套类目底座。
- 运行环境：本地 dry_run，只使用 deterministic fixture；未连接真实上游、真实模型、DataWorks、统一大维表/小表或业务数据库。
- 发布边界：不执行正式标签发布、候选机制启用、存量覆盖、真实 DML、Codeup 推送、MR 合并或 192.168.1.35:8081 部署。
- 既有 codex/3d-shadow-consumption-mvp-v1@7b4ebce 分支保持原状，未删除、未推送、未合并。

## 已交付能力

| 能力 | 结果 |
| --- | --- |
| 来源与字段合同 | 只读来源合同、确定性分页游标、schema fingerprint、来源身份与素材版本证据；来源适配器无法证明只读时 fail-closed |
| Shadow 投影 | 影子目标/合同/运行/租约、重试、熔断、回滚、行数与 payload hash 对账；仅允许 environment=shadow 与 shadow_only=true |
| 3D/SU Profile | Profile Registry 只注册 model_3d_su，未知或未启用 profile 禁止执行 |
| Canonical 事实边界 | Shadow manifest 仅读取 PublishedLabel.status=published，并携带资产版本/来源身份/SHA-256、机制、模型和质量溯源；候选、原始响应、人工过程不进入正式投影 |
| 运行时迁移 | migration 72 增量、幂等；兼容旧 projection_contracts 的 local/test 约束升级和不完整 SQLite trigger 历史库，不改写历史事实 |
| 确定性闭环 | 7 步串行 DAG：来源接入 → 评测/标注 → 人工纠偏门 → 标签事实发布门 → Shadow 投影 → 对账 → Badcase 回流 |
| 双人工门 | 运行先暂停在 human_correction_gate，越序审批被拒；人工放行后再次暂停在 label_fact_gate，第二次人工放行后才继续投影与回流 |
| 桌面证据 | 运行中心一级摘要仅保留类目、工作流、当前关口、状态、检查点和阻塞原因；详细快照、投影和回流证据进入二级抽屉 |

## 验证证据

- 后端全量（Python 3.12、全新临时 DATA_DIR）：1503 passed, 1 skipped, 6 warnings；warning 仅为既有 FastAPI/httpx 与 PDF SWIG 弃用提示。
- 3D/SU Shadow/来源/Profile/迁移联合专项：先前已验证 85 passed；受影响模块回归先前已验证 92 passed。
- 本轮人工双门专项：3 passed，覆盖越序审批拒绝、两次独立人工放行、失败重试、检查点恢复和幂等运行。
- 前端：npm run test:lightbox、npm run test:baseline-level-metrics、npm run lint、npm run build 均通过；构建仅保留既有 Vite 配置与大 chunk warning。
- Claude Code 只读审查尝试无输出并执行错误；随后按用户授权在当前会话完成 Inline Review，未发现 Critical/Important 级问题，未修改审查以外文件。
- git diff --check 通过；本地实现提交为 52ca8dc，origin 仍为 Codeup SSH 地址，未发生远端写入。

## 后续门禁

真实上游接入、真实模型调用、DataWorks/业务数据库投影、自动组批、正式标签发布、存量重跑、推送、合并和测试服部署必须由 Owner 重新冻结范围并单独授权。本回执不把本地 fixture 结果解释为生产闭环已上线。
