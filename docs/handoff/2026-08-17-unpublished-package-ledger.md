# LabelLab 未发布包台账

> 盘点日期：2026-08-17
>
> 盘点范围：`/Volumes/WorkSSD/Codex/2026-08-11/labellab` 本地仓库与 linked worktree
>
> 对比基线：本地远端跟踪引用 `origin/main@50e5b1572dd3ea5b65a7641ca50ae32fd850df07`
>
> 发布边界：本次只读盘点与文档记录，不合流、不删除旧包、不推送、不部署

## 1. 结论

当前真正需要后续处理的未发布内容有三类：

1. `codex/3d-shadow-dry-run-prep-20260816` 的 11 个独有提交，以及同一工作树内尚未提交的最终方案、Roadmap、真人研发交接和四批两表合同；
2. `codex/3d-shadow-consumption-mvp-v1` 的 10 个独有提交，但该包与当前 `origin/main` 在 migration 68～70 发生编号冲突，且与 dry-run 包大量重叠，禁止整包直接合并；
3. `codex/frontend-information-architecture-v1` 工作树的 3 个未提交旧版运行配置布局修改。该分支提交已被 `origin/main` 吸收，未提交修改也已被主线更完整的宽抽屉实现覆盖，不建议合流，仅保留待人工审计。

除上述内容外，其余已登记本地分支没有独有提交；它们的提交已被当前本地 `origin/main` 包含或等价吸收。

## 2. 基线确认与本机限制

本机执行 `git ls-remote origin refs/heads/main` 时，Codeup SSH 仍返回 `Permission denied (publickey)`；但 combined 协调任务已在 2026-08-17 在线确认 Codeup `main` 仍为 `50e5b1572dd3ea5b65a7641ca50ae32fd850df07`，与本地 `origin/main` 一致。

后续正式合流从该完整 SHA 创建唯一 combined 集成分支。本机 SSH 鉴权问题不影响本次本地提交，但在需要本机直接 fetch/push 前仍需单独修复。

## 3. 包 A：3D/SU Shadow dry-run 与闭环预备

### 3.1 身份与目的

- 分支：`codex/3d-shadow-dry-run-prep-20260816`
- 完整 SHA：`2bdcd553793453678193ad2e043a4ae2d3b8d54d`
- 相对本地 `origin/main`：ahead 11、behind 2
- 业务目的：补齐 3D/SU 来源身份、字段合同、Shadow 投影、readiness 门禁、确定性工作流和双人工门预备能力，为九月真实标签闭环提供可测试底座。
- 是否被 `origin/main` 等价吸收：否。`git cherry origin/main` 显示 11 个 `+` 独有提交。

### 3.2 独有提交

| 完整 SHA | 说明 |
|---|---|
| `02789b542a2d3f8e6d415df3afab10b5411e45fd` | 3D Shadow dry-run 设计 |
| `2b21449f04fbb2ce1527c5b9c679db009108ee5b` | 3D Shadow 领域合同测试 |
| `6e5ed6614e9ff7596ec8ca32a388218606bcf161` | 3D/SU Shadow dry-run 基础能力 |
| `175fe9c5eb51cca6773704da2d5022e56852b440` | 3D/SU readiness manifest |
| `3c37d500272e59d34179979f01af453f1ada4ee4` | 3D/SU 接入 readiness 合同 |
| `31a5dbeb291704196fc75b3deb3c18ac860d7fd3` | readiness 验收回执 |
| `1fe96e297c661fa87a40bfb62e1ce2a692ac755b` | 标签体系重构方案初稿 |
| `8cd3a678841457e90b86265c7a9915b65c9b0894` | Owner 签认范围 |
| `694f169de9452bff578a7dc794ef964f407bb251` | 方案叙事重构 |
| `7187f197ed975ef538f8e1a917631c1e9b4c1c55` | 知识图谱目标表和相对重要性语义 |
| `2bdcd553793453678193ad2e043a4ae2d3b8d54d` | 国内/海外来源绑定拆分 |

### 3.3 已提交修改文件

```text
PROJECT_STATUS.md
backend/app/category_evaluation_v3_config_api.py
backend/app/category_evaluation_v3_revisions.py
backend/app/field_demand_contracts.py
backend/app/label_governance.py
backend/app/main.py
backend/app/mechanism_profiles.py
backend/app/migrations/runner.py
backend/app/model_3d_su_category_seed.py
backend/app/models.py
backend/app/production_feedback.py
backend/app/projection_contracts.py
backend/app/readonly_sources.py
backend/app/semantic_tag_contracts.py
backend/app/semantic_tag_mapping.py
backend/app/shadow_projection.py
backend/app/three_d_profile.py
backend/app/three_d_readiness.py
backend/app/three_d_workflow_fixture.py
backend/app/worker.py
backend/app/worker_v3_authoritative.py
backend/tests/test_field_demand_contracts.py
backend/tests/test_mechanism_profile_boundaries.py
backend/tests/test_migration.py
backend/tests/test_readonly_sources.py
backend/tests/test_semantic_tag_contracts.py
backend/tests/test_semantic_tag_mapping.py
backend/tests/test_shadow_projection.py
backend/tests/test_three_d_profile.py
backend/tests/test_three_d_readiness.py
backend/tests/test_three_d_shadow_consumption_flow.py
backend/tests/test_three_d_workflow_fixture.py
docs/contracts/2026-08-16-3d-su-september-closure-rehearsal.md
docs/contracts/2026-08-16-downstream-field-projection-contract-v1.md
docs/contracts/2026-08-16-label-system-owner-signoff-register.csv
docs/contracts/2026-08-17-kg-four-batch-target-table-request-v1.md
docs/contracts/3d-su-field-signoff-template-v1.csv
docs/contracts/3d-su-field-supply-v1.md
docs/contracts/3d-su-golden-set-plan-v1.md
docs/contracts/3d-su-permission-raci-v1.md
docs/contracts/3d-su-readiness-freeze-v1.md
docs/contracts/3d-su-source-identity-v1.md
docs/decisions/0047-platform-semantic-tag-demand-contract.md
docs/discussion/tpeng-labellab-gap-register-20260813.md
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.pdf
docs/superpowers/plans/2026-08-14-kg-semantic-tag-demand-contract.md
docs/superpowers/plans/2026-08-16-3d-shadow-dry-run-prep.md
docs/superpowers/plans/2026-08-16-3d-su-readiness-freeze.md
docs/superpowers/plans/2026-08-16-label-system-reconstruction-proposal.md
docs/superpowers/receipts/2026-08-16-3d-shadow-dry-run-prep.md
docs/superpowers/receipts/2026-08-16-3d-su-readiness-freeze.md
docs/superpowers/receipts/2026-08-16-label-system-reconstruction-proposal.md
docs/superpowers/specs/2026-08-14-kg-semantic-tag-demand-design.md
docs/superpowers/specs/2026-08-16-3d-shadow-dry-run-prep-design.md
frontend/package.json
frontend/scripts/check-three-d-dry-run-contract.ts
frontend/scripts/check-three-d-readiness-contract.ts
frontend/src/pages/operations-center-page.tsx
```

### 3.4 迁移、验证和冲突

- 迁移版本：新增 migration 72 `add_3d_shadow_dry_run_contracts`；本地 `origin/main` 当前最高为 migration 71。
- 最新验证：后端全量 `1514 passed, 1 skipped, 6 warnings`；前端 TypeScript lint 通过；Vite production build 通过，仅保留主 chunk 大于 500 kB 的提示；方案 Word 手册重新渲染为 13 页并逐页检查通过。
- 依赖：当前分支缺少主线上的 `d85eb8676372df61341b224f362183f83c53288b` 3D/SU 等级修复及其 merge commit。
- 冲突：与包 B 在 `main.py`、`models.py`、`migrations/runner.py`、来源、字段合同、Shadow 投影、Worker、3D Profile 和多份测试中大量重叠。
- 建议：先基于在线确认后的 Codeup `main` 重建 combined 分支，再按提交顺序移植本包；只有确认主线最高迁移仍为 71 时才保留 migration 72 编号。

## 4. 包 A2：最终方案、Roadmap 与真人研发交接工作树

### 4.1 身份与目的

- 所在分支：`codex/3d-shadow-dry-run-prep-20260816`
- 基础 SHA：`2bdcd553793453678193ad2e043a4ae2d3b8d54d`
- 状态：`WORKTREE-DIRTY`，尚无提交 SHA
- 业务目的：形成“背景与现状 → 当前痛点 → 解决方案”的最终宣讲方案，补齐 45 天 MVP、Q4 一期/二期 Roadmap、知识图谱四批两表合同和真人后端研发交接。
- 是否被 `origin/main` 等价吸收：否；这些修改只存在于当前本地工作树。

### 4.2 未提交修改文件

```text
PROJECT_STATUS.md
README.md
docs/contracts/2026-08-17-kg-four-batch-target-table-request-v1.md
docs/handoff/2026-08-17-label-system-human-backend-handoff.md
docs/handoff/2026-08-17-unpublished-package-ledger.md
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md
docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.pdf
docs/superpowers/receipts/2026-08-16-label-system-reconstruction-proposal.md
frontend/scripts/check-three-d-readiness-contract.ts
scripts/build_tpeng_proposal_doc.py
scripts/build_tpeng_proposal_handout.py
scripts/print_tpeng_proposal_pdf.mjs
```

### 4.3 迁移、验证和建议

- 迁移版本：无新增数据库迁移；依赖包 A 的 migration 72 说明。
- 最新验证：与包 A 共用后端全量、前端 lint/build 和 13 页 Word 渲染证据；`contract:three-d-readiness` 已把旧 `res_type=1` 断言修正为国内冻结口径 `res_type in (1,6)` 并通过；Markdown 为唯一可编辑权威源。
- 建议：在包 A 完成 combined 基线移植后，将本包作为独立文档提交，重新生成 PDF/DOCX 并再次逐页检查，避免把二进制手册冲突混入后端能力移植提交。

## 5. 包 B：3D Shadow consumption MVP

### 5.1 身份与目的

- 分支：`codex/3d-shadow-consumption-mvp-v1`
- 完整 SHA：`7b4ebce4adf554cd052f413105086fb5473f5f18`
- 相对本地 `origin/main`：ahead 10、behind 69
- 业务目的：提供图片 Lightbox 边缘检查、字段与素材版本合同、只读来源轮询、受控 3D Profile、Shadow 投影 Worker、对账抽屉和消费证据。
- 是否被 `origin/main` 等价吸收：未被 Git patch 等价吸收。`git cherry origin/main` 显示 10 个 `+`；但其中多项能力已在主线或包 A 以不同实现继续演进，必须做能力级语义对比。

### 5.2 独有提交

| 完整 SHA | 说明 |
|---|---|
| `4a794fd9aff03d3d8c06ddf6819269c634b6ddf5` | 3D Shadow consumption MVP 定义 |
| `84c2b9c6c258e4b9b22ea98445f58df5b2b8d2ec` | 实施计划 |
| `072ca6a97d55654d0fa667715028319b5c99e7b0` | Lightbox 明暗棋盘格和图片边缘轮廓 |
| `aab182df9060ddfe2abefbc5b505764be4d07acc` | 字段与素材版本合同 |
| `dc3d0f13589a99f1a3e5d0130475b6614372e95a` | 只读来源轮询 |
| `71cc244bd1a8485c1d3b24bda64761cb57312df5` | 受控 3D Profile |
| `40f7d7e3d773c00ef449abfa501e1f9ba79dab81` | Shadow 投影 Worker |
| `4447da58e5092de30b6746e5640f2103e178c3c5` | 3D Shadow 消费证据 |
| `b5ac1f5e215c8a8589b18d4aac49b2e11d34535b` | 边界记录 |
| `7b4ebce4adf554cd052f413105086fb5473f5f18` | 验收记录 |

### 5.3 修改文件

```text
PRODUCT.md
PROJECT_STATUS.md
backend/app/category_evaluation_v3_config_api.py
backend/app/category_evaluation_v3_revisions.py
backend/app/field_demand_contracts.py
backend/app/label_governance.py
backend/app/main.py
backend/app/mechanism_profiles.py
backend/app/migrations/runner.py
backend/app/models.py
backend/app/production_feedback.py
backend/app/projection_contracts.py
backend/app/readonly_sources.py
backend/app/shadow_projection.py
backend/app/three_d_profile.py
backend/app/worker.py
backend/app/worker_v3_authoritative.py
backend/tests/test_field_demand_contracts.py
backend/tests/test_mechanism_profile_boundaries.py
backend/tests/test_migration.py
backend/tests/test_readonly_sources.py
backend/tests/test_shadow_projection.py
backend/tests/test_three_d_profile.py
backend/tests/test_three_d_shadow_consumption_flow.py
docs/decisions/0046-3d-readonly-source-and-shadow-consumption.md
docs/decisions/README.md
docs/discussion/tpeng-labellab-gap-register-20260813.md
docs/superpowers/plans/2026-08-14-3d-shadow-consumption-mvp.md
docs/superpowers/plans/2026-08-14-image-lightbox-inspection-background.md
docs/superpowers/receipts/2026-08-14-3d-shadow-consumption-local-verification.md
docs/superpowers/specs/2026-08-14-3d-shadow-consumption-mvp-design.md
docs/superpowers/specs/2026-08-14-image-lightbox-inspection-background-design.md
docs/user-guide.md
frontend/scripts/check-baseline-lightbox.ts
frontend/scripts/check-dual-workspaces-contract.ts
frontend/scripts/check-mechanism-editor-contract.ts
frontend/scripts/lightbox-test.tsx
frontend/src/components/image-lightbox.tsx
frontend/src/components/projection-reconciliation-drawer.tsx
frontend/src/components/shadow-projection-run-drawer.tsx
frontend/src/features/mechanism-config/profile-capability-summary.tsx
frontend/src/features/mechanism-config/registry.ts
frontend/src/features/mechanism-config/three-d-editor.tsx
frontend/src/lib/api.ts
frontend/src/lib/types.ts
frontend/src/pages/projection-governance-page.tsx
```

### 5.4 迁移、验证和冲突

- 迁移版本：该包定义 migration 68 `add_field_and_asset_version_contracts`、69 `add_readonly_source_contracts`、70 `add_shadow_projection_worker`。
- 硬冲突：当前本地 `origin/main` 已使用 68 `add_semantic_tag_contract_registry`、69 `harden_semantic_tag_fact_provenance`、70 `add_source_identity_verification`。禁止直接合并或保留原编号。
- 历史测试证据：聚焦后端 `88 passed, 1 warning`；后端全量 `1325 passed, 1 skipped, 6 warnings`；Lightbox、前端合同、TypeScript lint 和 Vite production build 通过。该证据来自分支验收回执，本轮未重新在该旧分支执行。
- 建议：包 A 移植完成后，再逐能力比较包 B。只把 combined 基线中仍缺失的能力拆成新提交，迁移从当时主线下一个可用版本开始编号；Lightbox 若主线已有等价实现，只保留验收测试和设计证据，不重复改 UI。

## 6. 包 C：存量回归运行配置抽屉旧版修复

### 6.1 身份与目的

- 分支：`codex/frontend-information-architecture-v1`
- 分支完整 SHA：`2591e92001246dd9541edac4500204daa4d278c7`
- 状态：分支提交相对 `origin/main` ahead 0、behind 98；工作树另有 3 个未提交文件
- 业务目的：把存量回归运行配置从错误的宽屏六列布局改为两列，并把运行按钮移入抽屉底栏。
- 是否被 `origin/main` 等价吸收：分支提交已完全吸收；未提交布局修改已被主线更完整的约 820px 宽抽屉、分段配置和固定底栏方案覆盖，属于已被语义上替代的旧实现。

### 6.2 未提交修改文件

```text
PROJECT_STATUS.md
frontend/scripts/check-information-architecture-contract.ts
frontend/src/pages/baseline-regression-page.tsx
```

### 6.3 迁移、验证和建议

- 迁移版本：无。
- 旧证据：工作树记录了信息架构合同、TypeScript lint、Vite build，以及 Edge `1440×900`、`1280×720` 验收通过；本轮未重新执行该旧工作树验证。
- 建议：不要把这 3 个文件合入 combined 基线，也不要删除工作树。保留到 Owner 完成审计；若需再次确认，只做与当前主线页面的行为对比。

## 7. 已被本地 origin/main 吸收的干净分支

| 分支 | 完整 SHA | 相对 `origin/main` | 处理建议 |
|---|---|---:|---|
| `codex/baseline-v3-run-config-metrics-20260814` | `818a0e3d06064b845ac7990e3890bed412cf0004` | ahead 0 / behind 27 | 不再重复合流 |
| `codex/c39-f753-combined-20260814` | `efcc556e7498b4e34e1a7de6babc74b28d914b49` | ahead 0 / behind 61 | 不再重复合流 |
| `codex/inspiration-accuracy-baseline-20260811` | `1d82ac9cfe7c458f3e85c2b497abeb6b1261d250` | ahead 0 / behind 122 | 不再重复合流 |
| `codex/label-mechanism-v1` | `3ac167bb5b3ced5b9e9e680956c528a2c23b8b72` | ahead 0 / behind 66 | `outputs/` 为空目录，不清理 |
| `codex/legacy-correction-blocker-cleanup-v1` | `3cb919d8eb48241f2225e5dc12f916c50d44ce05` | ahead 0 / behind 89 | 不再重复合流 |
| `codex/legacy-correction-deployment-receipt-v1` | `7575e6415ce89ece4a66a155672146a44de6b8ff` | ahead 0 / behind 82 | 不再重复合流 |
| `codex/lightbox-projection-repair-20260814` | `4513c4c1c18d5fb6be1060750264248e3b50a271` | ahead 0 / behind 56 | 不再重复合流 |
| `codex/model-3d-su-grade-fix-20260817` | `d85eb8676372df61341b224f362183f83c53288b` | ahead 0 / behind 1 | 已由主线 merge commit 包含 |
| `codex/tpeng-label-reconstruction-kickoff-20260815` | `9dd0cd802fbc55e6dc4ae3013a4126afd6fd58a0` | ahead 0 / behind 3 | 不再重复合流 |
| `codex/global-auto-batching-mechanism-20260817` | `50e5b1572dd3ea5b65a7641ca50ae32fd850df07` | ahead 0 / behind 0 | 与本地 `origin/main` 相同 |

## 8. 建议合流顺序

1. 以已在线确认的 Codeup `main@50e5b1572dd3ea5b65a7641ca50ae32fd850df07` 创建唯一 combined 集成分支。
2. 先移植包 A 的 11 个独有提交，吸收主线 3D/SU 等级修复，重新确认 migration 72 是否仍可用。
3. 再提交包 A2 的方案、Roadmap、交接、四批两表合同和重建脚本；重新生成 PDF/DOCX。
4. 对包 B 做能力级 Gap 对比，只移植 combined 基线仍缺失的实现；禁止整包 merge，禁止复用 migration 68～70。
5. 包 C 不合流，保留工作树等待人工审计。
6. 合流完成后统一运行后端全量、受影响专项、前端合同、lint、build、迁移升级/全新库测试和 `git diff --check`。
7. 只有 Codeup MR 合并后形成唯一 combined `main`，且另获部署授权，才进入共享测试服部署；本台账不构成推送、合并或部署授权。
