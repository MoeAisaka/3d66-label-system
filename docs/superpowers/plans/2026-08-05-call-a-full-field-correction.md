# 调用A全字段纠偏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有节点纠偏工作台中可视化编辑调用A全部12字段，并保证分数/等级联动、字段级历史、旧记录安全降级。

**Architecture:** 新增统一 `call_a_field` 节点类型，前端以 `call_a.<field>` 路径展示12个固定节点；后端将文案、分类、标签、红线和媒介字段映射到 `precheck_json.production_fields`，将 `score/grade` 映射到权威结果列及 `scoring_json`。供应商 `raw_response_a` 保持不可变，人工修正由规范化结果与追加式 `correction_history_json` 表达。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy、pytest、React、TypeScript、TanStack Query。

---

### Task 1: 后端调用A字段纠偏合同

**Files:**
- Modify: `backend/tests/test_node_correction.py`
- Modify: `backend/app/node_correction_api.py`

- [ ] **Step 1: Write the failing tests**

新增真实数据库/API测试，分别断言：文案/分类/tags/trait落入 `precheck_json.production_fields`；score改动按 `81/61/41/21/0` 映射等级；grade直改保留score；每次历史含字段路径、前后值、纠偏人、原因、UTC时间；缺字段返回冲突而不报500。

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest -q backend/tests/test_node_correction.py -k call_a`
Expected: FAIL，因为 `call_a_field` 尚未被请求模型允许。

- [ ] **Step 3: Implement minimal backend behavior**

在 `CorrectNodeRequest` 增加 `call_a_field`；限定路径为 `call_a.<12字段之一>`；复用 `validate_production_correction` 校验11个 production 字段；score写入权威列并确定性派生grade，grade写入人工覆盖标记且不改score，其他字段不触发算分；统一追加历史。

- [ ] **Step 4: Run targeted tests**

Run: `backend/.venv/bin/pytest -q backend/tests/test_node_correction.py`
Expected: 全部通过。

### Task 2: 前端12字段节点与专用控件

**Files:**
- Modify: `frontend/scripts/check-node-correction-editor.ts`
- Modify: `frontend/src/lib/node-correction.ts`
- Modify: `frontend/src/pages/node-correction-editor.tsx`

- [ ] **Step 1: Write the failing frontend contract**

固定断言 `call_a.score/grade/title/seotitle/category/style/tags/cons/design/reason/image_defects/trait` 共12个节点、四个中文分组、缺字段只读提示和控件元数据。

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix frontend run check:node-correction`
Expected: FAIL，当前只有零散 `precheck_field` 节点。

- [ ] **Step 3: Implement minimal UI**

建立12字段元数据；score为0-100整数、grade为L1-L5、title/seotitle显示长度提示、category拆成一级下拉和二级文本、tags支持标签增删、cons/design多行、reason多选、image_defects与trait枚举；缺字段保持节点可见但只读。

- [ ] **Step 4: Run frontend contract**

Run: `npm --prefix frontend run check:node-correction`
Expected: 输出 `node correction editor frontend contract: ok`。

### Task 3: 全量验证、提交、部署与真机验收

**Files:**
- Create: `/Users/Shared/OpenClaw/121-验收-调用A全字段纠偏-20260805/验收报告.md`
- Create: `/Users/Shared/OpenClaw/121-验收-调用A全字段纠偏-20260805/验证证据/*`
- Create: `/Users/Shared/OpenClaw/121-验收-调用A全字段纠偏-20260805/截图/*`

- [ ] **Step 1: Run backend and frontend gates**

Run: `backend/.venv/bin/pytest -q`、`npm --prefix frontend run lint`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`。

- [ ] **Step 2: Commit and publish hub main**

确认只含本轮文件，提交新commit并推送 `origin/main`。

- [ ] **Step 3: Deploy from MacBook-Company**

在 MacBook 创建目标commit bundle，经固定 `deploy-3d66-label-test` 发布到 `192.168.1.35:8081`，核对健康200、容器commit及 `inspiration_image` active rev3。

- [ ] **Step 4: Edge CDP 18800 acceptance**

在基准回归页打开评测，截图12字段界面；实际修改title/tags/trait及score，确认grade联动和历史逐条可见；保存截图与API/数据库证据。

- [ ] **Step 5: Final evidence review**

逐项核对12字段、联动、历史、旧记录、全量回归、Docker、部署和真机证据；任何缺口必须在报告中标为未完成，禁止推断通过。
