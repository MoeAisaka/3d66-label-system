# 纠偏工作台连续处理与修订持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在标签实验台基准回归中实现连续纠偏、左图右编辑、人工结果可修订持久化，并补齐 V3 等级撮合器纠偏入口。

**Architecture:** 服务端在现有唯一 `ReviewPanel` 上增加审核轮次号，`HumanReview` 记录所属轮次；重开接口只推进轮次和评测修订，不删除历史。前端用纯函数选择下一条未完成素材，纠偏成功只同步刷新当前回归，其他查询后台刷新；已完成结果通过重开接口生成新轮次并以旧纠偏作为编辑初值。

**Tech Stack:** FastAPI、SQLAlchemy、SQLite 迁移、React、TypeScript、TanStack Query、Node 合同脚本、Pytest

**Spec:** `docs/superpowers/specs/2026-08-18-correction-workbench-ux.md`

## Global Constraints

- 不修改现役模型、提示词、V3 合同或人工真值内容。
- 审核历史只追加，重开不得删除或覆盖旧轮次。
- 避开 3D/SU 未提交的维度类型、维度合同与新模块文件。
- 所有生产代码先有失败测试，再实现最小修复。

---

### Task 1: 审核轮次持久化与重开接口

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_review_workflow_policy.py`
- Test: `backend/tests/test_migration.py`

**Interfaces:**
- Produces: `POST /api/evaluations/{evaluation_id}/review-panel/reopen`
- Produces: `ReviewPanel.review_round: int`、`HumanReview.review_round: int`
- Consumes: `expected_review_revision` 乐观锁、现有 `ReviewPanel.revision`

- [ ] **Step 1: 写失败测试**

```python
def test_completed_panel_can_reopen_without_mixing_previous_votes(client, result):
    first = complete_single_reviewer_panel(client, result.id)
    reopened = client.post(
        f"/api/evaluations/{result.id}/review-panel/reopen",
        json={"expected_review_revision": first["evaluation_review_revision"]},
    )
    assert reopened.status_code == 200
    assert reopened.json()["review_round"] == 2
    assert reopened.json()["submitted_count"] == 0
```

- [ ] **Step 2: 运行失败测试**

Run: `backend/.venv312/bin/python -m pytest backend/tests/test_review_workflow_policy.py -q`
Expected: FAIL，重开接口或 `review_round` 尚不存在。

- [ ] **Step 3: 增加 v74 迁移和模型字段**

```python
def _migration_074_add_review_rounds(connection: Connection) -> None:
    # review_panels.review_round 与 human_reviews.review_round 均默认 1，旧数据归入第一轮。
```

- [ ] **Step 4: 实现重开与当前轮次隔离**

```python
@app.post("/api/evaluations/{evaluation_id}/review-panel/reopen")
def reopen_review_panel(...):
    # 校验 completed + expected_review_revision；推进 review_round；清空当前轮次终态指针；保留旧 HumanReview。
```

- [ ] **Step 5: 运行后端专项和迁移测试**

Run: `backend/.venv312/bin/python -m pytest backend/tests/test_review_workflow_policy.py backend/tests/test_review_panel_concurrency.py backend/tests/test_migration.py -q`
Expected: PASS。

### Task 2: 人工结果回显与再次修改

**Files:**
- Modify: `frontend/src/pages/review-correction-form.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/scripts/check-baseline-correction-workbench.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: `review_panel.final_truth.corrections`
- Consumes: `baselineRegressionApi.reopenReview(evaluationId, expectedRevision)`
- Produces: `initialCorrections?: ReviewCorrection[]`

- [ ] **Step 1: 写失败合同脚本**

```ts
assert.match(correctionForm, /initialCorrections/)
assert.match(baselinePage, /再次修改/)
assert.match(apiSource, /review-panel\/reopen/)
```

- [ ] **Step 2: 运行合同脚本并确认失败**

Run: `npm run contract:baseline-correction`
Expected: FAIL，接口和属性尚不存在。

- [ ] **Step 3: 实现旧纠偏初值与历史摘要**

```ts
export function ReviewCorrectionForm({ initialCorrections = [], ...props }) {
  // 将上一轮完整 corrections 转成 drafts/keyFieldDrafts，允许继续修改或撤回某项。
}
```

- [ ] **Step 4: 实现“再次修改”按钮**

```ts
await baselineRegressionApi.reopenReview(evaluation.id, evaluation.review_revision)
```

- [ ] **Step 5: 运行合同与类型检查**

Run: `npm run contract:baseline-correction && npm run lint`
Expected: PASS。

### Task 3: 连续下一条与左图右编辑

**Files:**
- Create: `frontend/src/features/baseline-regression/correction-navigation.ts`
- Modify: `frontend/src/features/baseline-regression/correction-workbench.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Test: `frontend/scripts/check-baseline-correction-workbench.ts`

**Interfaces:**
- Produces: `nextPendingCorrectionId(items, currentId): number | null`
- Produces: `onNext`、`hasNext`、`autoAdvance` 纠偏工作台属性

- [ ] **Step 1: 写失败导航测试**

```ts
assert.equal(nextPendingCorrectionId(items, 1), 3)
assert.equal(nextPendingCorrectionId(items, 3), null)
```

- [ ] **Step 2: 运行合同脚本并确认失败**

Run: `npm run contract:baseline-correction`
Expected: FAIL，导航纯函数尚不存在。

- [ ] **Step 3: 实现最小导航纯函数和固定布局**

```ts
export function nextPendingCorrectionId(items, currentId) {
  return items.slice(currentIndex + 1).find(isPending)?.id ?? null
}
```

- [ ] **Step 4: 提交成功后自动进入下一条**

```ts
await queryClient.invalidateQueries({ queryKey: ["baseline-regression", run.id] })
nextId ? onOpenCorrection(nextId) : onCloseCorrection()
void queryClient.invalidateQueries({ queryKey: ["evaluations"] })
```

- [ ] **Step 5: 验证合同、类型和生产构建**

Run: `npm run contract:baseline-correction && npm run lint && npm run build`
Expected: PASS。

### Task 4: V3 等级撮合器纠偏入口

**Files:**
- Modify: `frontend/src/features/baseline-regression/correction-workbench.tsx`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/src/lib/node-correction.ts`
- Test: `frontend/scripts/check-node-correction-editor.ts`
- Test: `frontend/scripts/check-baseline-correction-workbench.ts`
- Test: `backend/tests/test_node_correction.py`

**Interfaces:**
- Consumes: `evaluation.scoring.v3_context`
- Consumes: `POST /api/evaluation-results/{id}/correct-node`
- Produces: 所有存在冻结 V3 上下文的基准回归结果均显示节点纠偏工作台。

- [ ] **Step 1: 写失败合同断言**

```ts
assert.match(workbenchSource, /scoring\?\.v3_context/)
assert.equal(finalNode.label, "V3 最终等级")
```

- [ ] **Step 2: 运行失败测试**

Run: `npm run contract:node-correction && npm run contract:baseline-correction`
Expected: FAIL，当前仍依赖 `dimension_scoring_mode` 且标签不明确。

- [ ] **Step 3: 修改展示条件和中文说明**

```tsx
{evaluation && evaluation.scoring?.v3_context && <NodeCorrectionEditor ... />}
```

- [ ] **Step 4: 运行 V3 前后端专项测试**

Run: `npm run contract:node-correction && backend/.venv312/bin/python -m pytest backend/tests/test_node_correction.py -q`
Expected: PASS。

### Task 5: 完整验证与合流准备

**Files:**
- Modify: `PROJECT_STATUS.md`（仅追加本分支本地状态，合流前再决定是否保留）

- [ ] **Step 1: 后端完整相关验证**

Run: `backend/.venv312/bin/python -m pytest backend/tests/test_review_workflow_policy.py backend/tests/test_review_panel_concurrency.py backend/tests/test_node_correction.py backend/tests/test_migration.py -q`

- [ ] **Step 2: 前端完整验证**

Run: `npm run contract:baseline-correction && npm run contract:node-correction && npm run lint && npm run build`

- [ ] **Step 3: 浏览器只读验收**

在本地服务验证：桌面端左图右编辑、再次修改、手动下一条；用测试数据库完成一次重开与新轮次提交，不触碰测试服真实运营数据。

- [ ] **Step 4: 检查并发冲突**

Run: `git diff --check && git diff --name-only origin/main...HEAD`

逐段对比 3D/SU 工作树对 `main.py`、`models.py` 的改动，仅保留本任务审核轮次区块。

- [ ] **Step 5: 本地提交并等待推送/合并/部署授权**

```bash
git add <本任务文件>
git commit -m "feat: streamline correction workbench revisions"
```
