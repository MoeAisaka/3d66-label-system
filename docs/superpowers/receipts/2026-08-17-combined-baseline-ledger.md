# LabelLab 全局自动组批 combined 基线回执

> 日期：2026-08-17
>
> 分支：`codex/global-auto-batching-mechanism-20260817`
>
> 状态：本地 combined 基线已验证；未推送、未创建合并请求、未合并主线、未部署

## 1. 基线与合流结论

- 唯一远端基线：`origin/main@50e5b1572dd3ea5b65a7641ca50ae32fd850df07`。
- 已保留主线 3D/SU 等级评分修复 `d85eb8676372df61341b224f362183f83c53288b`。
- 已按原提交顺序选择性移植 `codex/3d-shadow-dry-run-prep-20260816` 的 11 个能力提交。
- 已追加移植 A2 最终方案、Roadmap、真人研发交接和合同校验提交 `6c8b6b1c7798772f69cbb55fadc62b505475cf06`，combined 对应提交为 `afd8bef`。
- 未整支合并 `codex/3d-shadow-consumption-mvp-v1`，未引入其冲突的 migration 68～70。
- 未吸收已被主线替代的旧运行配置抽屉工作树修改。

## 2. 提交映射

| 原提交 | combined 提交 | 内容 |
|---|---|---|
| `02789b5` | `0da43c2` | 3D Shadow dry-run 设计 |
| `2b21449` | `af5704d` | 3D Shadow 领域合同测试 |
| `6e5ed66` | `b460f7f` | 3D/SU Shadow dry-run 基础能力 |
| `175fe9c` | `0c143de` | 3D/SU readiness manifest |
| `3c37d50` | `039176f` | 3D/SU 接入 readiness 合同 |
| `31a5dbe` | `c90e3e9` | readiness 验收回执 |
| `1fe96e2` | `9282219` | 标签体系重构方案初稿 |
| `8cd3a67` | `8f0907b` | Owner 签认范围 |
| `694f169` | `214fd36` | 方案叙事重构 |
| `7187f19` | `0bc8b33` | 知识图谱目标表和相对重要性语义 |
| `2bdcd55` | `24452f9` | 国内/海外来源绑定拆分 |
| `6c8b6b1` | `afd8bef` | 最终方案、Roadmap、交接与合同校验收口 |

全局自动组批设计与实施计划在合流前已独立提交：

- `3e12baa`：全类目自动组批与机制迭代设计；
- `224678d`：全类目自动组批与机制迭代实施计划。

## 3. 迁移与兼容性

- combined 最高迁移为 migration 72 `add_3d_shadow_dry_run_contracts`。
- migration 68～71 继续沿用主线既有定义，未发生编号覆盖。
- A2 不新增数据库迁移。
- `backend/app/model_3d_su_category_seed.py`、`backend/app/worker.py`、`backend/app/worker_v3_authoritative.py` 的主线等级修复和 dry-run 能力同时保留。
- `PROJECT_STATUS.md` 保留主线历史状态与 3D/SU、标签体系方案新增状态，没有删除任一侧原记录。

## 4. 最新验证

### 后端

- combined 全量：`1524 passed, 1 skipped, 6 warnings in 126.11s`。
- 3D/SU 来源、Shadow、消费流、readiness 与迁移专项：`79 passed, 1 warning`。
- warning 均为既有依赖弃用提示，没有业务测试失败。

### 前端

- `contract:three-d-dry-run`：通过。
- `contract:three-d-readiness`：通过；已按冻结来源合同校验 `res_type in (1,6)`。
- `contract:tag-demand`：通过。
- TypeScript lint：通过。
- Vite production build：通过；仅保留既有配置提示和主 chunk `524.41 kB` 提示。

### 仓库门禁

- `git diff --check`：通过。
- A2 原工作树在提交 `6c8b6b1` 后干净。
- combined 工作树没有未提交的跟踪文件修改。

## 5. 停止条件与下一步

- 当前不推送、不创建合并请求、不合并主线、不部署、不调用真实模型、不启用真实自动组批。
- 前置门禁已关闭；下一步从实施计划 Task 1 开始，使用测试驱动方式实现版本化类目泳道与资格快照数据合同。
- 后续功能迁移预留 migration 73；若主线在正式合流前新增迁移，必须重新编号并重跑迁移测试。
