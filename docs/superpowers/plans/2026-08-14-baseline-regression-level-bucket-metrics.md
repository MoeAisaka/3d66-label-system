# Baseline Regression Level Bucket Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show exact and adjacent level accuracy plus recommendation, regular, and filter bucket precision/recall for the currently selected baseline regression while preserving the full L1–L5 matrix.

**Architecture:** Keep the backend and persisted run schema unchanged. A focused frontend module derives three bucket metrics from the existing `BaselineLevelMetrics.confusion_matrix` and renders them above the existing field-quality strip; the current five-level evidence drawer remains untouched.

**Tech Stack:** React 19, TypeScript 7, Vite 8, existing browser-contract harness, Node assert.

## Global Constraints

- Recommendation bucket is L1 and L2.
- Regular bucket is L3 and L4.
- Filter bucket is L5.
- Exact and adjacent accuracy remain visible.
- The existing L1–L5 confusion matrix and per-level evidence remain visible and unchanged.
- Metrics are derived from the selected run and must never be hard-coded.
- A zero prediction or truth denominator renders `—`, not `0%`.
- Do not modify backend APIs, database schema, evaluation contracts, release gates, or downstream consumption.

---

### Task 1: Add a failing browser contract for the full metric display

**Files:**
- Create: `frontend/scripts/baseline-level-metrics-test.html`
- Create: `frontend/scripts/baseline-level-metrics-test.tsx`
- Create: `frontend/scripts/check-baseline-level-metrics.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: `LevelPerformanceSummary` and `computeBaselineLevelBucketMetrics` from `@/features/baseline-regression/level-performance-summary`.
- Produces: `npm run test:baseline-level-metrics`, a browser contract that verifies the required labels, values, and zero-denominator behavior.

- [ ] **Step 1: Create the browser fixture and assertions**

Use a 100-sample L1–L5 matrix whose exact accuracy is 42%, adjacent accuracy is 87%, recommendation precision is 32/47 = 68.09%, recommendation recall is 32/40 = 80%, regular precision is 23/30 = 76.67%, regular recall is 23/40 = 57.5%, filter precision is 14/23 = 60.87%, and filter recall is 14/20 = 70%:

```tsx
const metrics: BaselineLevelMetrics = {
  schema_version: "baseline-level-metrics-v2",
  levels: ["L1", "L2", "L3", "L4", "L5"],
  total: 100,
  completed: 100,
  pending: 0,
  denominator: 100,
  valid_predictions: 100,
  failed: 0,
  exact_hits: 42,
  adjacent_hits: 87,
  deviations: 58,
  exact_accuracy: 0.42,
  adjacent_accuracy: 0.87,
  confusion_matrix: {
    L1: { L1: 8, L2: 8, L3: 0, L4: 2, L5: 2 },
    L2: { L1: 8, L2: 8, L3: 3, L4: 0, L5: 1 },
    L3: { L1: 0, L2: 8, L3: 6, L4: 5, L5: 1 },
    L4: { L1: 3, L2: 0, L3: 6, L4: 6, L5: 5 },
    L5: { L1: 4, L2: 0, L3: 0, L4: 2, L5: 14 },
  },
}
```

The harness must render `LevelPerformanceSummary`, assert visible text for all eight values, call `computeBaselineLevelBucketMetrics` with an all-zero matrix, and fail unless zero denominators return `null`.

- [ ] **Step 2: Add the runnable contract command**

Add to `frontend/package.json`:

```json
"test:baseline-level-metrics": "node --experimental-strip-types scripts/check-baseline-level-metrics.ts"
```

`check-baseline-level-metrics.ts` must start Vite on an unused strict port, launch headless Chrome/Chromium, require `data-test-status="passed"`, stop Vite, and remove its temporary browser profile.

- [ ] **Step 3: Run the contract to verify RED**

Run: `npm run test:baseline-level-metrics`

Expected: FAIL because `level-performance-summary.tsx` does not exist yet.

---

### Task 2: Implement bucket derivation and rendering

**Files:**
- Create: `frontend/src/features/baseline-regression/level-performance-summary.tsx`
- Test: `frontend/scripts/baseline-level-metrics-test.tsx`

**Interfaces:**
- Consumes: `BaselineLevelMetrics` and `BaselineLevel` from `@/lib/types`.
- Produces:
  - `computeBaselineLevelBucketMetrics(metrics: BaselineLevelMetrics): BaselineLevelBucketMetric[]`
  - `LevelPerformanceSummary({ metrics }: { metrics: BaselineLevelMetrics }): ReactElement`

- [ ] **Step 1: Implement the exact bucket definitions and calculator**

```tsx
export type BaselineLevelBucketMetric = {
  key: "recommended" | "regular" | "filtered"
  label: "推荐档" | "常规档" | "过滤档"
  levels: readonly BaselineLevel[]
  truePositive: number
  predicted: number
  expected: number
  precision: number | null
  recall: number | null
}

type BucketDefinition = Pick<
  BaselineLevelBucketMetric,
  "key" | "label" | "levels"
>

const BUCKETS: readonly BucketDefinition[] = [
  { key: "recommended", label: "推荐档", levels: ["L1", "L2"] },
  { key: "regular", label: "常规档", levels: ["L3", "L4"] },
  { key: "filtered", label: "过滤档", levels: ["L5"] },
]

export function computeBaselineLevelBucketMetrics(
  metrics: BaselineLevelMetrics,
): BaselineLevelBucketMetric[] {
  return BUCKETS.map((bucket) => {
    const bucketLevels = new Set<BaselineLevel>(bucket.levels)
    let truePositive = 0
    let predicted = 0
    let expected = 0
    for (const expectedLevel of metrics.levels) {
      for (const predictedLevel of metrics.levels) {
        const count = metrics.confusion_matrix[expectedLevel]?.[predictedLevel] ?? 0
        if (bucketLevels.has(predictedLevel)) predicted += count
        if (bucketLevels.has(expectedLevel)) expected += count
        if (bucketLevels.has(expectedLevel) && bucketLevels.has(predictedLevel)) {
          truePositive += count
        }
      }
    }
    return {
      ...bucket,
      truePositive,
      predicted,
      expected,
      precision: predicted ? truePositive / predicted : null,
      recall: expected ? truePositive / expected : null,
    }
  })
}
```

- [ ] **Step 2: Render global and bucket metrics without hiding old evidence**

Render a section labelled `等级表现` with:

```tsx
<MetricCard label="精确等级准确率" value={formatPercent(metrics.exact_accuracy)} />
<MetricCard label="相邻等级准确率" value={formatPercent(metrics.adjacent_accuracy)} />
```

Then render one card per bucket with both `精确率` and `召回率`. Use `Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 2 })` so the acceptance fixture displays `68.09%` and whole values display `42%`, `80%`, and `70%`. Return `—` for `null`.

- [ ] **Step 3: Run the browser contract to verify GREEN**

Run: `npm run test:baseline-level-metrics`

Expected: PASS with a message confirming exact, adjacent, recommendation, regular, filter, and zero-denominator behavior.

- [ ] **Step 4: Mutation-check the calculator**

Temporarily change the recommendation bucket to `L1` only and rerun the contract.

Expected: FAIL because recommendation precision/recall no longer match 68.09%/80%.

Restore `L1`, `L2` and rerun.

Expected: PASS.

---

### Task 3: Integrate the metric module into the baseline results main area

**Files:**
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/scripts/check-information-architecture-contract.ts`

**Interfaces:**
- Consumes: `LevelPerformanceSummary` from Task 2 and the current selected run's `metrics` object.
- Produces: A main-area metric section that updates when `selectedRunId` changes while leaving `FieldMetricsEvidence` and its five-level matrix intact.

- [ ] **Step 1: Add a failing integration contract**

Extend `check-information-architecture-contract.ts` to require the page to render:

```tsx
<LevelPerformanceSummary metrics={metrics} />
```

Also retain the existing `MetricsDrawer` assertion and assert that `FieldMetricsEvidence` continues to consume `levelMetrics={summary.metrics}`.

- [ ] **Step 2: Run the integration contract to verify RED**

Run: `npm run contract:information-architecture`

Expected: FAIL because the page does not yet import or render `LevelPerformanceSummary`.

- [ ] **Step 3: Add the component to the results panel**

Import the component and render it immediately before the existing field-quality/status metric strip:

```tsx
<LevelPerformanceSummary metrics={metrics} />
```

Do not remove or rename the existing field macro accuracy, field macro recall, human gate, next-step strip, `MetricsDrawer`, or `FieldMetricsEvidence`.

- [ ] **Step 4: Run both contracts to verify GREEN**

Run:

```bash
npm run test:baseline-level-metrics
npm run contract:information-architecture
```

Expected: both PASS.

- [ ] **Step 5: Commit the feature**

```bash
git add frontend/package.json frontend/scripts/baseline-level-metrics-test.html frontend/scripts/baseline-level-metrics-test.tsx frontend/scripts/check-baseline-level-metrics.ts frontend/scripts/check-information-architecture-contract.ts frontend/src/features/baseline-regression/level-performance-summary.tsx frontend/src/pages/baseline-regression-page.tsx docs/superpowers/plans/2026-08-14-baseline-regression-level-bucket-metrics.md
git commit -m "feat: show baseline level bucket metrics"
```

---

### Task 4: Verify the combined release scope

**Files:**
- Verify only; no additional implementation files expected.

**Interfaces:**
- Consumes: the metric feature commit plus the pending Lightbox and projected-revision repairs.
- Produces: evidence that the combined branch is safe to submit to Codeup and deploy to the shared test server.

- [ ] **Step 1: Run frontend verification**

Run TypeScript lint, production build, every existing frontend contract, `test:lightbox`, and `test:baseline-level-metrics`.

Expected: all commands exit 0; the existing Vite chunk-size warning may remain.

- [ ] **Step 2: Run backend verification**

Run the focused projected-revision tests and then:

```bash
PYTHONPATH=. /Volumes/WorkSSD/Codex/2026-08-11/labellab/work/frontend-information-architecture-v1/.venv312/bin/pytest -q backend/tests
```

Expected: no failures; the current baseline is 1333 passed, 1 skipped, 6 warnings.

- [ ] **Step 3: Review scope and release through the existing protected path**

Confirm `git diff --check`, inspect every changed file, push only `codex/lightbox-projection-repair-20260814`, create and merge one Codeup MR, then deploy the final merged `main` once through the protected main-only script.

- [ ] **Step 4: Perform Edge acceptance**

In the logged-in Edge session verify:

- Current-run exact and adjacent accuracy are visible.
- Recommendation, regular, and filter precision/recall are visible and change when selecting another historical run.
- The L1–L5 matrix remains available in the field evidence drawer.
- Historical baseline runs remain visible.
- Lightbox uses the checkerboard background, visible image border, and uncropped `contain` rendering.
- Category contract list and `model_3d_su` revision history load without `projected_revision_missing`.
