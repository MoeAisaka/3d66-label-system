# 基准回归页节点纠偏集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在基准回归逐张审核中复用 v3 节点纠偏工作台，并让新旧维度规则路径和旧引擎结果安全共存。

**Architecture:** rule-deduction 结果统一使用 `NodeCorrectionEditor` 和冻结 `scoring.v3_context`，非规则模式继续保留旧 `ReviewCorrectionForm`。解析层同时识别 `schema_definition.dimensions` 与旧 `group.dimensions`；维度键或规则 ID 不对齐时只将对应维度标为只读，不影响确认、退回和最终等级能力。

**Tech Stack:** React 19、TypeScript、TanStack Query、FastAPI 既有 correct-node API、Node contract scripts、pytest、Vite、Docker。

---

### Task 1: 锁定解析与页面集成回归

**Files:**
- Modify: `frontend/scripts/check-node-correction-editor.ts`

- [ ] **Step 1: 写失败测试**

新增三组断言：旧 `common_group.dimensions` 能生成维度节点；缺少或不匹配的历史维度节点带 `readOnly` 和中文兼容提示；baseline 页面源码导入并渲染 `NodeCorrectionEditor`。

- [ ] **Step 2: 验证 RED**

Run: `cd frontend && npm run contract:node-correction`
Expected: FAIL，旧路径无维度节点或 baseline 尚未渲染节点编辑器。

### Task 2: 解析器兼容与逐维降级

**Files:**
- Modify: `frontend/src/lib/node-correction.ts`
- Modify: `frontend/src/pages/node-correction-editor.tsx`

- [ ] **Step 1: 实现最小解析修复**

在 `definitionsForTrack()` 中优先读取 `group.schema_definition.dimensions`，缺失时回退 `group.dimensions`，两条路径都覆盖 `common_group` 和 `specific_group`。

- [ ] **Step 2: 实现维度对齐状态**

将配置维度与 `aesthetic.dimensions` 按 key 对齐；缺少结果维度、结果仅有旧维度或命中未知 rule ID 时生成只读节点，并附“该结果由旧引擎产出，维度规则版本不一致，建议用新引擎重跑后再逐维纠偏”。可对齐节点继续开放规则勾选、中文置信度与证据编辑。

- [ ] **Step 3: 编辑器显示局部降级**

只读节点在导航和详情中明确标记“只读”，不渲染提交控件；页面顶部显示非红色兼容说明。其他节点和纠偏历史保持可用。

- [ ] **Step 4: 验证 GREEN**

Run: `cd frontend && npm run contract:node-correction`
Expected: `node correction editor frontend contract: ok`

### Task 3: baseline 审核页复用节点工作台

**Files:**
- Modify: `frontend/src/pages/baseline-regression-page.tsx`

- [ ] **Step 1: 接入共享组件**

导入 `NodeCorrectionEditor`。当 `evaluation.scoring.dimension_scoring_mode === "rule_deduction"` 时渲染它，传入当前登录用户名；否则继续渲染 `ReviewCorrectionForm`。

- [ ] **Step 2: 刷新结果与历史**

`onCorrected` 重新拉取当前 baseline run，并失效 evaluations/dashboard 查询，使分数、等级和纠偏历史立即更新；保留现有确认结果、退回复核按钮。

- [ ] **Step 3: 前端专项验证**

Run: `cd frontend && npm run contract:node-correction && npm run lint && npm run build`
Expected: 全部 exit 0。

### Task 4: 回归、部署和真实浏览器验收

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create: `/Users/Shared/OpenClaw/120-验收-基准回归页节点纠偏集成修复-20260805/验收报告.md`

- [ ] **Step 1: 完整验证**

Run: `backend/.venv/bin/python -m pytest backend/tests -q`（按仓库可用 venv 调整绝对路径）；`cd frontend && npm run lint && npm run build`；Docker build + health。
Expected: pytest 0 failed；前端和镜像 exit 0；健康 200。

- [ ] **Step 2: 提交与推送**

Run: `git diff --check && git status --short && git commit -m "fix: integrate node corrections into baseline review" && git push origin HEAD:main`
Expected: hub main 指向新提交；只包含本任务文件。

- [ ] **Step 3: 固定发布**

从 MacBook 生成 bundle，经 `deploy-3d66-label-test <bundle> <commit>` 发布；核对 `/api/health`、服务器 commit 和 active config rev=3。

- [ ] **Step 4: Edge 真机验证**

打开 Run #10，展开一条旧引擎结果：确认调用 A 与五阶段节点可见；可对齐维度显示规则、中文置信度和证据；不对齐维度仅友好只读；截图并检查控制台。

- [ ] **Step 5: 收尾**

将根因、RED/GREEN、pytest、构建、Docker、部署和浏览器证据写入验收目录；清理本任务临时脚本、bundle、镜像和 worktree，不动用户原工作树的未跟踪文件。
