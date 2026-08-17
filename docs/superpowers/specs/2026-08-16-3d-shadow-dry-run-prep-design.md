# 3D/SU Shadow 与确定性闭环预备批次设计

## 目标

在当前 `main@9943b8c7ae14dd70a54b7c08197be58c9b8131c2` 基础上，形成可供 9 月真实 3D/SU 标签闭环复用的本地工程底座：

1. 将未合入的 `codex/3d-shadow-consumption-mvp-v1@7b4ebce` 能力移植到当前 main，而不是直接合并旧分叉。
2. 用受控 deterministic fixture 串起 3D/SU 的接入、身份、评测、人工门、正式事实门、投影、对账和 Badcase 回流状态。
3. 证明五队列、版本冻结、租约、重试、检查点、幂等和故障恢复可以承载该类目。

## 冻结范围

### 包含

- 只读来源合同、分页游标、schema fingerprint、身份验证和增量接入证据。
- 3D/SU 类目 Profile、字段需求合同引用、受控 shadow projection target/contract/worker、影子对账和回退证据。
- 3D/SU deterministic workflow fixture：标准步骤类型使用既有工作流注册与运行时，不新增队列和调度器。
- 至少覆盖成功链路、人工门阻塞/放行、投影失败恢复、Badcase 回流和重复幂等请求。
- 后端专项测试、迁移幂等测试、前端运行中心/投影证据抽屉合同、全量回归和本地 Edge 验收。

### 不包含

- 不连接真实上游、真实 DataWorks、真实模型、真实大维表/小表或任何外部数据库。
- 不执行正式标签发布、候选机制自动启用、存量覆盖、真实 DML 或生产部署。
- 不冻结知识图谱/搜索的最终字段、词表、排序和 Query×素材关系。
- 不改变双人工门、机制发布轴与标签事实发布轴，不把 shadow 结果当正式事实。
- 不删除旧 `codex/3d-shadow-consumption-mvp-v1` 工作树或分支，不推送、不合并、不部署。

## 架构与数据流

```text
FixtureReadOnlySource
  -> source contract + cursor + SourceIdentityVerification
  -> 3D/SU Profile / field contract snapshot
  -> deterministic evaluation evidence
  -> human_correction_gate (paused until explicit fixture decision)
  -> label_fact_gate (independent second gate)
  -> published-fact-only shadow manifest
  -> FixtureShadowProjectionAdapter
  -> row-count/hash/version reconciliation
  -> feedback fixture -> optimization case queue
```

所有运行都创建 `ProductionRun` 和 `ProductionStepAttempt`，在创建时冻结 workflow、script、profile、字段合同、机制/模型占位版本、输入资产和 queue policy 的 snapshot hash。流程只通过现有 `DeterministicQueueScheduler` 的五类队列 dispatch；步骤不持有长事务。

人工门在 fixture 中表现为可审计的 `blocked`/`paused` 状态；只有显式的测试决策才能继续，不能由 worker 自动跳过。shadow projection 只能消费正式发布事实，目标必须 `environment=shadow`、`shadow_only=true` 且通过最小权限和 schema fingerprint 检查。

## 兼容移植策略

旧 shadow 分支与当前 main 的共同祖先为 `8a2c173`，当前 main 已额外前进 67 个提交。因此不做整分支 merge，采用“测试/文档先行、按能力移植”的方式：

1. 先移植并运行 shadow 分支专项测试，确认缺失模块和冲突边界。
2. 按 `field_demand_contracts`、`readonly_sources`、`three_d_profile`、`shadow_projection`、API/迁移/模型和前端证据抽屉的职责分批移植。
3. 对 `main` 已存在的 `models.py`、`migrations/runner.py`、`main.py`、`projection_contracts.py`、`label_governance.py` 和 `production_feedback.py` 只做最小补丁，不覆盖当前 main 的运行时实现。
4. 每个能力完成独立测试后再进入下一能力；冲突无法证明安全时停在本地，不强行合入。

## 验收与停止条件

- 旧主线基线保持：后端全量 1460 passed/1 skipped，前端合同、Lightbox、等级指标、lint、build 全部通过。
- 新增专项覆盖：只读来源拒绝可写连接；shadow 目标拒绝越权；正式事实过滤候选/过程字段；投影失败/重试/回退/对账；人工双门；Badcase 幂等回流；工作流运行重启与检查点恢复。
- 迁移在空库、当前库、重复执行场景幂等；不改写历史评测、纠偏、机制或标签发布记录。
- 所有本地运行均使用 dry-run/fixture；无真实模型调用、外部网络 DML 或测试服写入。
- 任一发现候选/人工过程进入正式投影、自动绕过人工门、外部写入、版本漂移无法对账，立即停止并保留当前工作树作为回退点。

## 回退

本批所有变更仅存在于隔离工作树 `codex/3d-shadow-dry-run-prep-20260816`。回退采用删除未合并本地提交或直接保留工作树，不触碰 Codeup main、测试服和旧 shadow 分支。

