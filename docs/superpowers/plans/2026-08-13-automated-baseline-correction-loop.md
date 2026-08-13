# Automated Baseline Correction Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the correction-analysis dead end with a persisted automatic pipeline that creates one immutable mechanism candidate, validates it with a candidate-bound regression, and stops only at the final human approve/reject gate.

**Architecture:** `BaselineCorrectionRun` becomes the orchestration aggregate. A focused service generates validated `CategoryEvaluationV3Revision` artifacts through an injectable adapter, creates a candidate-bound baseline regression using the worker's existing frozen `v3_authoritative_bundle`, refreshes the orchestration when regression results arrive, and exposes one admin-only final decision transaction. The active projection remains unchanged until human approval.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite migrations, Pydantic, pytest, React, TypeScript, TanStack Query.

## Global Constraints

- Do not call a real tuning model or run real regression jobs during tests or acceptance.
- Do not auto-approve or auto-publish a candidate.
- Keep the mechanism release axis independent from the label-fact release axis.
- Preserve the separate uncommitted run-config drawer layout fix.
- Desktop-only UI; primary color remains `#CCED46`.

---

### Task 1: Persist the automatic correction state machine

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations/runner.py`
- Modify: `backend/tests/test_migration.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Produces `BaselineCorrectionRun.status in {processing,awaiting_decision,approved,rejected,failed}`.
- Produces `stage`, `candidate_revision_id`, `regression_run_id`, `orchestration_json`, and final decision audit columns.

- [ ] Add failing model and migration tests that reject the old terminal state and prove legacy rows become retryable failures.
- [ ] Run the focused tests and confirm RED on missing columns/statuses.
- [ ] Add migration 64 that rebuilds `baseline_correction_runs`, preserves JSON/report evidence, maps `awaiting_confirmation` to `failed`, and installs the new constraints.
- [ ] Update the SQLAlchemy model to match the migrated table.
- [ ] Run the focused tests and confirm GREEN.

### Task 2: Generate and freeze one unified candidate revision

**Files:**
- Create: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/baseline_regression.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Consumes `CorrectionMechanismGenerator.generate(db, correction, active_revision, report) -> GeneratedMechanismCandidate`.
- Produces `advance_correction_run(db, correction, generator) -> None` and idempotently binds `candidate_revision_id`.

- [ ] Write a failing API test with a deterministic generator returning a changed prompt/level-scale/rule artifact; assert one candidate revision, unchanged active projection, no human intermediate blocker, and persisted stage transitions.
- [ ] Run the test and confirm RED because the run still ends in `awaiting_confirmation`.
- [ ] Implement frozen input/report preparation, active revision resolution, generator protocol, validator reuse, idempotent candidate creation, and coded stage failures.
- [ ] Run the focused test and confirm GREEN.

### Task 3: Bind regression execution to the candidate revision

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/tests/test_baseline_regression.py`
- Modify: `backend/tests/test_worker_v3_authoritative.py`

**Interfaces:**
- Produces a new `BaselineRegressionRun` whose jobs freeze `revision_bundle(candidate)` in `category_profile_snapshot_json.v3_authoritative_bundle`.
- Produces `refresh_correction_run(db, correction)` that enters `awaiting_decision` only after regression terminal metrics exist.

- [ ] Add failing tests proving candidate jobs use the candidate revision while ordinary jobs still use the active projection.
- [ ] Add a failing orchestration test proving an incomplete regression remains `processing/regression` and a completed one becomes `awaiting_decision` with comparison and recommendation.
- [ ] Implement candidate-bound run/job creation and refresh logic with idempotent bindings.
- [ ] Run focused worker and baseline tests and confirm GREEN.

### Task 4: Add the final human decision transaction

**Files:**
- Modify: `backend/app/category_evaluation_v3_revisions.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_category_evaluation_v3_revisions.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Produces `activate_candidate_revision(db, projected, candidate, actor)`.
- Produces `POST /api/baseline-corrections/{id}/decision` with `{decision: approved|rejected, note: string}`.

- [ ] Add failing tests for approval before regression, failed recommendation, projection drift, successful atomic activation, rejection, and idempotent repeated decisions.
- [ ] Run focused tests and confirm RED.
- [ ] Implement activation validation, projection copy, revision lifecycle updates, audit append, and conflict-safe decision persistence.
- [ ] Run focused tests and confirm GREEN.

### Task 5: Replace the blocking UI with automatic progress and final actions

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/scripts/check-information-architecture-contract.ts`

**Interfaces:**
- Consumes the new correction status/stage/candidate/regression/decision payload.
- Produces final approve/reject calls and no intermediate configuration UI.

- [ ] Extend the source contract to reject `awaiting_confirmation`, “另行创建候选版本”, and “当前阻塞”, and require the automatic stage labels plus final actions.
- [ ] Run `npm --prefix frontend run contract:information-architecture` and confirm RED.
- [ ] Update types/API, replace blocking copy with the automatic pipeline progress, show actionable failure/retry, and render approve/reject only in `awaiting_decision`.
- [ ] Run the frontend contract and confirm GREEN.

### Task 6: Verify, document, commit, and push the isolated branch

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/superpowers/plans/2026-08-13-automated-baseline-correction-loop.md`

- [ ] Run focused backend migration, correction, revision, and worker tests.
- [ ] Run the complete frontend information-architecture contract, lint, and SHA-stamped build.
- [ ] Run `git diff --check` and inspect the complete diff for accidental layout-fix overlap or production calls.
- [ ] Record exact evidence and remaining deployment gates in `PROJECT_STATUS.md`.
- [ ] Commit to `codex/automated-correction-loop-v1` and push with the existing Codeup SSH identity; do not merge or deploy until review evidence is complete.
