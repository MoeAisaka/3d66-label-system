# Contract-Driven Redline Prefilter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make V3 business enum values contract-owned and terminate confirmed Call-A redlines as L5 before any Call-B request.

**Architecture:** Redline policies remain structurally validated but no longer consult a platform business-value allowlist. The worker computes one evidence-aware redline prefilter decision from the frozen V3 bundle after Call A, uses that decision both to route Call B and to keep unconfirmed redline values from being re-applied by the final scorer. Correction metadata derives reason options from the frozen redline rules.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLAlchemy, pytest, React/TypeScript contract metadata.

**Spec:** User-confirmed execution contract in this task (2026-08-20), ADR-0033, and ADR-0049.

## Global Constraints

- Preserve the existing 3D/SU worktree unchanged.
- Work only in this detached local worktree; do not connect to test servers or modify live data.
- Do not commit, push, merge, deploy, approve candidates, or rerun historical regressions.
- Business strings may change per frozen contract; type, structure, uniqueness, hash, and engine-capability validation remain fail-closed.
- Historical runs continue to evaluate against their frozen V3 bundles.

---

### Task 1: Contract-owned redline values

**Files:**
- Modify: `backend/app/redline_policy.py`
- Modify: `backend/app/schema_adapter.py`
- Modify: `backend/app/inspiration_category_seed.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_redline_policy.py`
- Test: `backend/tests/test_schema_adapter.py`

**Interfaces:**
- Consumes: `redline_policy.rules[*].match_any` from the frozen V3 contract.
- Produces: structural validation for arbitrary non-empty business values and normalized non-empty reason arrays without a platform enum lookup.

- [x] Add failing tests proving a custom reason such as `透明棋盘格` is accepted by policy validation, precheck normalization, and human-value validation while malformed/non-string values are rejected.
- [x] Run the focused tests and confirm failure is caused by `PRODUCTION_REASON_VALUES` membership checks.
- [x] Replace business-value membership checks with non-empty-string/list shape validation; retain the legacy constant only as compatibility metadata.
- [x] Generate the regression-only Call-A schema appendix from the frozen redline policy instead of the old six-value platform list.
- [x] Run the focused tests and existing redline/schema-adapter suites to green.

### Task 2: Dynamic evidence audit and pre-B terminal decision

**Files:**
- Modify: `backend/app/schema_adapter.py`
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_v3_authoritative.py`
- Test: `backend/tests/test_category_worker_pipeline.py`
- Test: `backend/tests/test_schema_adapter.py`

**Interfaces:**
- Produces: `evaluate_v3_redline_prefilter(v3_bundle, precheck) -> dict[str, Any]` with confirmed hit, raw hit, matched rules, and routing reason.
- Consumes: frozen `redline_policy`, Call-A `redline_triggered`, and `decisive_evidence.redline_triggered`.
- Produces: a single frozen routing decision used by both Call-B orchestration and authoritative scoring.

- [x] Add failing adapter tests showing a newly declared rule key is audited without code changes and missing hit evidence remains unconfirmed.
- [x] Add failing worker tests proving a confirmed hit produces L5 with exactly one provider call, while the same raw reason without evidence performs Call B and is not later reclassified as L5.
- [x] Run each focused test and confirm the expected wrong branch is exercised.
- [x] Pass the frozen redline policy into Call-A adaptation, compute the prefilter once after A, short-circuit B only for confirmed hits, and suppress only unconfirmed rule matches in the scoring copy while preserving raw Call-A output for audit.
- [x] Cover mixed confirmed/unconfirmed hits, including shared `match_any` values, so final scoring retains only the evidence-confirmed rules through detached precheck and contract copies.
- [x] Run adapter, authoritative scorer, aesthetic-foundation, and worker integration suites to green.

### Task 3: Frozen correction metadata and documentation

**Files:**
- Modify: `backend/app/correction_contract.py`
- Test: `backend/tests/test_correction_contract.py`
- Modify: `frontend/src/lib/node-correction.ts`
- Modify: `frontend/src/features/mechanism-config/image-rule-editor.tsx`
- Test: `frontend/scripts/check-node-correction-editor.ts`
- Create: `docs/decisions/0050-contract-owned-redline-values-and-prefilter.md`
- Modify: `docs/decisions/README.md`
- Modify: `docs/decisions/0033-category-custom-evaluation-base-and-redline.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: frozen V3 `redline_policy.rules[*].match_any`.
- Produces: `call_a.reason` correction-node options derived from the frozen contract rather than `_PRODUCTION_FIELD_SPECS` when rules declare values.

- [x] Add a failing correction-contract test showing a custom reason appears in the frozen node metadata and undeclared platform defaults do not override it.
- [x] Derive reason options deterministically from enabled and disabled frozen rules, preserving rule order and removing duplicates.
- [x] Make the legacy node-correction UI derive reason choices from the result's frozen V3 contract and update the mechanism-editor guidance.
- [x] Document contract-owned business values, evidence-aware A-stage termination, history compatibility, and the no-B guarantee for confirmed hits.
- [x] Run correction-contract backend tests and the affected frontend node-correction contract script.

### Task 4: Completion verification

**Files:**
- Inspect all changed files only.

**Interfaces:**
- Consumes: all task outputs.
- Produces: deterministic evidence for handoff; no external side effects.

- [x] Run the combined affected backend suites with a fresh temporary data directory.
- [x] Run frontend TypeScript/build and the contract scripts that exercise dynamic options; record the existing Vite chunk-size warning separately.
- [x] Run Python compilation, `git diff --check`, `git status --short`, and inspect the complete diff for secrets or unrelated changes.
- [x] Confirm the original 3D/SU worktree still has exactly its pre-existing modifications and no files from this task.
