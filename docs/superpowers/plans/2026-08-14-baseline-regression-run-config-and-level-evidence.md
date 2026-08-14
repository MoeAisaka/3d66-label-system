# Baseline Regression Run Config and Level Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复桌面端基准回归运行配置抽屉的挤压问题，支持同类目 V3 现役/候选版本选择及 A/B 联动，并在主结果区恢复完整 L1–L5 五档矩阵与单档 Precision/Recall。

**Architecture:** 保持现有后端权威状态机和无迁移边界不变；前端通过独立纯函数筛选候选 revision、构造启动 payload 和计算矩阵派生指标。运行配置抽屉改为约 820px 的纵向分区并用固定底栏承载摘要与启动动作，历史 run 继续读取冻结快照。

**Tech Stack:** React 18 + TypeScript + TanStack Query + Tailwind CSS；FastAPI/Pydantic + SQLAlchemy；Vitest/前端合同脚本；pytest。

## Global Constraints

- 不新增数据库表、字段或迁移。
- 不改变 V3 候选创建、激活、退役、发布、回退或后端最终校验。
- 不自动激活候选、不发布标签事实、不触发真实模型调用、自动组批、存量重跑或部署。
- 仅面向电脑桌面验收，不扩展移动端。
- active 请求不传 `candidate_revision_id`；candidate 请求传所选 revision ID 并冻结完整合同。
- 五档矩阵固定展示 25 格；分母为零显示 `—`；三档聚合继续作为附加指标。

---

### Task 1: Revision types, API adapter, and eligibility/linkage pure functions

**Files:**
- Modify: `frontend/src/lib/types.ts` (add V3 revision list/detail and frozen revision metadata types; extend `BaselineDimensionSelection.v3_contract`)
- Modify: `frontend/src/lib/api.ts` (add `baselineRegressionApi.listV3Revisions(categoryKey)` and `candidate_revision_id` to `createRun` payload)
- Modify: `frontend/src/features/baseline-regression/baseline-regression-contract.ts` (add pure candidate eligibility, A/B binding, and payload helpers)
- Create: `frontend/scripts/baseline-v3-run-config-contract.ts`

**Interfaces:**
- `V3RevisionOption`: `{id, category_key, display_name, status, revision, parent_revision_id, contract_hash, contract, created_at, updated_at}`.
- `V3RevisionListResponse`: `{projected_revision_id, candidate_count, items: V3RevisionOption[]}`.
- `isSelectableV3Candidate(revision, revisions, projectedRevisionId): boolean` follows the current active ancestor chain and only accepts `status === "candidate"`.
- `v3RevisionGroup(revision, projectedRevisionId)` returns `active | candidate | history` for display.
- `resolvePromptBinding(candidate, stage)` reads `candidate.contract.prompt_bindings.call_a_version/call_b_version`.
- `buildBaselineRunPayload(selection)` omits `candidate_revision_id` in active mode and includes it in candidate mode.

- [ ] **Step 1: Write failing pure-contract assertions**

```ts
assert(isSelectableV3Candidate(candidateChild, revisions, active.id))
assert(!isSelectableV3Candidate(retired, revisions, active.id))
assert(!isSelectableV3Candidate(orphanCandidate, revisions, active.id))
assert.deepEqual(buildBaselineRunPayload({mode: "active", promptMode: "published"}), {})
assert.deepEqual(buildBaselineRunPayload({mode: "candidate", candidateRevisionId: 9, promptAId: 2, promptBId: 3}), {candidate_revision_id: 9, prompt_a_id: 2, prompt_b_id: 3})
```

- [ ] **Step 2: Run the contract script and confirm failure**

Run: `npx tsx scripts/baseline-v3-run-config-contract.ts`

Expected: FAIL because revision types/helpers are not exported yet.

- [ ] **Step 3: Implement types, API adapter, and pure helpers**

Keep helper logic side-effect free so the page can use it without duplicating ancestor-chain rules. The API adapter calls `GET /api/category-evaluation-v3-config/{category_key}/revisions` and returns the typed response.

- [ ] **Step 4: Re-run the contract script**

Run: `npx tsx scripts/baseline-v3-run-config-contract.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/baseline-regression/baseline-regression-contract.ts frontend/scripts/baseline-v3-run-config-contract.ts
git commit -m "feat: expose baseline v3 revision selection helpers"
```

### Task 2: Desktop run-config drawer layout and V3/A/B interaction

**Files:**
- Modify: `frontend/src/components/workspace-page.tsx` (add opt-in `size="wide"`/`className` to `SecondaryDrawer`; preserve the default 680px behavior for all callers)
- Modify: `frontend/src/features/baseline-regression/run-config-drawer.tsx` (use wide drawer, vertical sections, scrollable body, fixed footer)
- Modify: `frontend/src/pages/baseline-regression-page.tsx` (load revisions after category selection; active/candidate selection; A/B auto-binding and mismatch guard; pass payload to createRun)
- Modify: `frontend/scripts/check-baseline-level-metrics.ts` or create a focused JSX contract script if needed

**Interfaces:**
- `SecondaryDrawer` accepts optional `size?: "default" | "wide"` and `className?: string`; no global default change.
- `RunConfigDrawer` receives `canStart`, `startLabel`, and `onStart` through its footer caller without owning mutation state.
- Page query key: `["baseline-v3-revisions", selectedCategoryKey]`; loading/error states disable start and expose retry.

- [ ] **Step 1: Add failing layout/interaction assertions**

Assert source contains `size="wide"`, vertical section labels `提示词配置` / `V3 合同配置` / `执行方式`, fixed footer styling, candidate status grouping, and a disabled-start message containing the mismatched A/B versions.

- [ ] **Step 2: Run the focused contract**

Run: `npx tsx scripts/check-baseline-v3-run-config.ts`

Expected: FAIL before implementation.

- [ ] **Step 3: Implement opt-in wide drawer and page interaction**

Use container-safe layout (`grid-cols-1`/`lg:grid-cols-2` inside the 820px drawer, never viewport `min-[1280px]` columns). Active is the default; candidates are selectable only when the pure helper returns true. Selecting a candidate switches prompt mode to manual and preselects bound A/B; mismatch renders a concrete error and disables the fixed-footer start button.

- [ ] **Step 4: Verify focused contract and typecheck**

Run: `npx tsx scripts/check-baseline-v3-run-config.ts` and `npm run build` from `frontend/`.

Expected: PASS and a production bundle with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/workspace-page.tsx frontend/src/features/baseline-regression/run-config-drawer.tsx frontend/src/pages/baseline-regression-page.tsx frontend/scripts/check-baseline-v3-run-config.ts
git commit -m "fix: make baseline run config wide and v3 revision aware"
```

### Task 3: Restore five-level matrix and per-level Precision/Recall

**Files:**
- Modify: `frontend/src/features/baseline-regression/level-performance-summary.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx` (render summary in the primary results panel before secondary field evidence)
- Create: `frontend/scripts/baseline-level-matrix-contract.ts`

**Interfaces:**
- `BaselineLevelMatrixCell`: `{expected, predicted, count}`.
- `computeBaselineLevelMatrixMetrics(metrics)` returns fixed `levels`, row totals, column totals, `recallByLevel`, and `precisionByLevel`; null for zero denominators.
- The component renders `data-testid="baseline-level-matrix"`, 25 `data-testid="baseline-level-cell-Lx-Ly"` cells, and per-level `data-testid` attributes for precision/recall.

- [ ] **Step 1: Write failing matrix tests**

Cover diagonal hits, cross-level errors, empty rows/columns, and an all-zero matrix. Assert L1–L5 order, row recall, column precision, and `null` for zero denominators.

- [ ] **Step 2: Run matrix contract and confirm failure**

Run: `npx tsx scripts/baseline-level-matrix-contract.ts`

Expected: FAIL because the pure function and matrix markup are absent.

- [ ] **Step 3: Implement pure calculations and matrix markup**

Render rows as human truth and columns as model prediction. Keep zero cells visible, highlight diagonal hits without color-only meaning, and retain exact/adjacent totals plus recommended (L1/L2), regular (L3/L4), and filtered (L5) cards.

- [ ] **Step 4: Run matrix contract and frontend build**

Run: `npx tsx scripts/baseline-level-matrix-contract.ts && npm run build` from `frontend/`.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/baseline-regression/level-performance-summary.tsx frontend/src/pages/baseline-regression-page.tsx frontend/scripts/baseline-level-matrix-contract.ts
git commit -m "feat: restore baseline five-level matrix evidence"
```

### Task 4: Freeze revision metadata in historical selection and regression tests

**Files:**
- Modify: `backend/app/main.py` (`_frozen_v3_dimension_summary` and `_baseline_run_selection` only)
- Modify: `frontend/src/lib/types.ts` (typed historical V3 metadata)
- Modify: `backend/tests/test_baseline_regression.py`
- Modify: `frontend/scripts/baseline-v3-run-config-contract.ts`

**Interfaces:**
- Historical `selection.dimension.v3_contract` adds `revision`, `revision_id`, `candidate_revision_id`, and `contract_hash` when present; missing legacy metadata remains `null`/"历史 run 未记录".

- [ ] **Step 1: Add backend regression assertions**

Build a run with a candidate bundle and assert `_baseline_run_selection(run)["dimension"]["v3_contract"]` preserves revision ID/hash; build a legacy run and assert the field remains absent without breaking the response.

- [ ] **Step 2: Run the focused backend tests and confirm failure**

Run: `pytest backend/tests/test_baseline_regression.py -k "selection or candidate" -q`

Expected: the new metadata assertion fails before the serializer change.

- [ ] **Step 3: Implement serializer metadata only**

Read values from the frozen `v3_authoritative_bundle`; do not query current active state while serializing historical runs.

- [ ] **Step 4: Run focused backend tests**

Run: `pytest backend/tests/test_baseline_regression.py -k "selection or candidate" -q`.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_baseline_regression.py frontend/src/lib/types.ts frontend/scripts/baseline-v3-run-config-contract.ts
git commit -m "fix: expose frozen v3 metadata in baseline history"
```

### Task 5: Full verification and project status handoff

**Files:**
- Modify: `PROJECT_STATUS.md` (record implementation, tests, non-deployment state, and remaining Edge acceptance)

- [ ] **Step 1: Run backend full suite**

Run: `pytest -q` from repository root.

Expected: all existing tests pass with only baseline skips/warnings.

- [ ] **Step 2: Run frontend checks**

Run: `npm run lint && npm run build` from `frontend/`, plus the focused baseline contract scripts.

- [ ] **Step 3: Inspect diff and verify scope**

Run: `git diff --check`, `git status --short`, and confirm no migration, deployment, or unrelated page changes.

- [ ] **Step 4: Update PROJECT_STATUS.md**

Record commit SHAs, exact test output, and that push/merge/deploy remain pending separate authorization.

- [ ] **Step 5: Commit status documentation**

```bash
git add PROJECT_STATUS.md
git commit -m "docs: record baseline run config and matrix delivery"
```

## Self-review checklist

- Spec coverage: Tasks 1–2 cover active/candidate V3 selection, A/B binding, layout and payload; Task 3 covers all 25 matrix cells and per-level metrics; Task 4 covers historical freeze; Task 5 covers Edge-ready verification and handoff.
- Placeholder scan: no TODO/TBD/“implement later” steps; each code step names concrete files, interfaces, tests, and commands.
- Type consistency: `V3RevisionOption`/`V3RevisionListResponse` feed the query and pure eligibility helper; `BaselineDimensionSelection.v3_contract` receives the serializer fields added in Task 4; matrix helper consumes existing `BaselineLevelMetrics`.

## Execution handoff

用户已确认按当前会话分批执行，因此采用 inline execution，按 Task 1→5 逐批实现并在每批结束运行确定性验证；本阶段不推送、不合并、不部署。
