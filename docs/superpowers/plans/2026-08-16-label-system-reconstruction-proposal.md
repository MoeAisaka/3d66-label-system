# TPENG 标签体系重构宣讲方案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出一份面向业务方宣讲、同时可供研发拆解的 TPENG 标签体系重构方案，并配套负责人签认、9 月 3D/SU 闭环演练和下游字段合同。

**Architecture:** 以 TPENG 标签实验台（LabelLab）作为标签体系重构的统一产品载体和标签/内容中台通用底座。方案按“下游字段需求合同 → 素材接入 → 标注路径/任务 → 自动与人工标注 → 纠偏验收 → 版本发布 → 下游引用/对账 → Badcase 回流”展开，明确 Canonical 事实与搜索/知识图谱/向量/数据库投影的边界。业务类目只通过 profile、字段适用性、机制、提示词、规则、门槛和专用视图扩展，不复制平台通用能力。

**Tech Stack:** Markdown、CSV、Python 3.12、python-docx、LibreOffice headless、仓库既有 ADR/PROJECT_STATUS/Gap 清单。

## Global Constraints

- 产品命名统一为 TPENG 标签实验台（LabelLab）；“标签体系重构”描述目标，不再作为并列项目线。
- `semantic.*`、`quality.*`、`governance.*`、人工真值、证据、来源、机制/模型/规则版本、审核和发布状态属于 Canonical 事实。
- 搜索索引、知识图谱、向量索引和下游数据库表都是可重建消费投影，不得成为事实主库或反向覆盖 Canonical。
- 机制发布轴和标签事实发布轴独立；AI 生成候选和回归证据，人工决定启用/拒绝与正式发布。
- 首个真实验收纵切是 3D/SU，目标为 2026 年 9 月跑通增量接入—标注—纠偏—发布—消费—回流。
- 当前只生成本地方案、模板和 dry-run 清单；不连接真实上游、真实模型、DataWorks/ODPS 或业务数据库，不申请权限，不执行 DML，不推送/合并/部署。
- 方案不得把本地 fixture、readiness manifest 或 dry-run 结果表述为生产上线事实。

**Document delivery note (2026-08-16):** Markdown remains the editable authoritative source. The paired Word output is a visually verified handout assembled from the validated PDF page renders because the current headless LibreOffice renderer cannot reliably render CJK text from OOXML font runs. This is a renderer workaround, not a change to product scope or an assertion that the Word file is the editable source.

---

### Task 1: Build the business briefing source document

**Files:**
- Create: `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md`
- Reference: `docs/decisions/0042-unified-labellab-product-carrier.md`
- Reference: `docs/decisions/0043-canonical-facts-and-semantic-projection-boundaries.md`
- Reference: `docs/decisions/0045-dual-workspaces-and-table-projection-contract.md`
- Reference: `docs/decisions/0047-platform-semantic-tag-demand-contract.md`
- Reference: `docs/discussion/tpeng-platform-capability-map-and-gaps-20260811.md`
- Reference: `docs/discussion/tpeng-labellab-gap-register-20260813.md`

**Interfaces:**
- Produces one authoritative narrative that both business stakeholders and engineers can use.
- Uses current-state labels: `已实现`、`本批冻结`、`待负责人签认`、`下一阶段候选`。

- [x] Write the document sections: executive summary; why now; unified positioning; current pain points; target business loop; architecture layers; Canonical fact ownership; platform modules; incremental/stock workflows; dual human gates and dual release axes; model/mechanism management; 3D/SU September vertical slice; downstream big-table/small-table consumption; KPI and acceptance; RACI; roadmap; risks; decision requests; non-goals.
- [x] Include one architecture diagram in Mermaid and one compact capability matrix.
- [x] Explicitly state that query relevance, ranking weights and graph-internal relations remain downstream strategy and are not asset facts.
- [x] Explicitly state the first success standard: 3D/SU closed-loop production evidence plus field Precision/Recall and downstream reconciliation; do not use platform construction volume as a substitute.
- [x] Run a placeholder and stale-claim scan for `TBD`, `TODO`, `仅评测工具`, `两套独立项目`, `已生产`, and unsupported production metrics.

### Task 2: Build stakeholder execution artifacts

**Files:**
- Create: `docs/contracts/2026-08-16-label-system-owner-signoff-register.csv`
- Create: `docs/contracts/2026-08-16-3d-su-september-closure-rehearsal.md`
- Create: `docs/contracts/2026-08-16-downstream-field-projection-contract-v1.md`

**Interfaces:**
- The signoff register captures role, decision, evidence, expiry and status without inventing owner names.
- The rehearsal checklist maps each September gate to a local deterministic rehearsal and a future real-evidence dependency.
- The downstream contract maps one unified wide table and several small tables to formal published facts only.

- [x] Create CSV rows for Product/Label, Data, Algorithm, Platform, Reviewer, and Consumer owners with `UNASSIGNED` placeholders and explicit evidence fields.
- [x] Define the 3D/SU rehearsal sequence, failure injections, stop conditions, and evidence expected at each gate.
- [x] Define downstream table responsibilities, primary keys, Canonical source namespaces, version columns, idempotency keys, reconciliation fields and Badcase return fields.
- [x] Mark all real permissions, real source access, real model calls and physical DML as future separately frozen dependencies.

### Task 3: Generate the Word briefing artifact

**Files:**
- Create: `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx`
- Create: local builder under `/tmp` or a task-local non-deliverable path.
- QA output: temporary rendered PNGs outside the repository.

**Interfaces:**
- DOCX follows the `standard_business_brief` preset with `memo_masthead` first-page header.
- Body and headings use explicit Calibri/spacing tokens; tables use fixed 9360 DXA width and 120 DXA indent.
- The Word document content must match the Markdown source; no unsupported numbers or private credentials.

- [x] Load workspace dependencies and mark the artifact operation exactly once with `mark_artifact_operation_started.mjs`.
- [x] Build the DOCX as the visual handout described in the delivery note; its reviewed pages match the paired Markdown/PDF content without depending on the failing CJK OOXML rendering path.
- [x] Render with `render_docx.py` to PNGs and inspect every page at 100% zoom.
- [x] Run structural checks for headings, page count, placeholder absence and metadata privacy.

### Task 4: Verify, receipt and local handoff

**Files:**
- Create: `docs/superpowers/receipts/2026-08-16-label-system-reconstruction-proposal.md`
- Modify: `PROJECT_STATUS.md`

- [x] Run Markdown/CSV structural checks and `git diff --check`.
- [x] Run proposal content scan and confirm no stale naming or unsupported production claims.
- [x] Verify DOCX render output and record page count, hash and QA result.
- [x] Run the existing readiness focused tests to ensure the document-only batch did not alter the frozen readiness contract.
- [x] Commit locally only; do not push, merge or deploy.
- [x] Return the owner signoff checklist and decision requests to the 标签体系 reconstruction discussion.

## Stop Conditions

Stop immediately if the deliverable requires real credentials, real source access, model calls, external database writes, production deployment, automatic candidate activation, automatic label publishing, or any change to the frozen 3D/SU readiness contract.
