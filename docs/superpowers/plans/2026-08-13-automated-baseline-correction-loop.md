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

- [x] Add failing model and migration tests that reject the old terminal state and prove legacy rows become retryable failures.
- [x] Run the focused tests and confirm RED on missing columns/statuses.
- [x] Add migration 64 that rebuilds `baseline_correction_runs`, preserves JSON/report evidence, maps `awaiting_confirmation` to `failed`, and installs the new constraints.
- [x] Update the SQLAlchemy model to match the migrated table.
- [x] Run the focused tests and confirm GREEN.

### Task 2: Generate and freeze one unified candidate revision

**Files:**
- Create: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/baseline_regression.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Consumes `CorrectionMechanismGenerator.generate(db, correction, active_revision, report) -> GeneratedMechanismCandidate`.
- Produces `advance_correction_run(db, correction, generator) -> None` and idempotently binds `candidate_revision_id`.

- [x] Write a failing API test with a deterministic generator returning a changed prompt/level-scale/rule artifact; assert one candidate revision, unchanged active projection, no human intermediate blocker, and persisted stage transitions.
- [x] Run the test and confirm RED because the run still ends in `awaiting_confirmation`.
- [x] Implement frozen input/report preparation, active revision resolution, generator protocol, validator reuse, idempotent candidate creation, and coded stage failures.
- [x] Run the focused test and confirm GREEN.

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

- [x] Add failing tests proving candidate jobs use the candidate revision while ordinary jobs still use the active projection.
- [x] Add a failing orchestration test proving an incomplete regression remains `processing/regression` and a completed one becomes `awaiting_decision` with comparison and recommendation.
- [x] Implement candidate-bound run/job creation and refresh logic with idempotent bindings.
- [x] Run focused worker and baseline tests and confirm GREEN.

### Task 4: Add the final human decision transaction

**Files:**
- Modify: `backend/app/category_evaluation_v3_revisions.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_category_evaluation_v3_revisions.py`
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Produces `activate_candidate_revision(db, projected, candidate, actor)`.
- Produces `POST /api/baseline-corrections/{id}/decision` with `{decision: approved|rejected, note: string}`.

- [x] Add failing tests for approval before regression, failed recommendation, projection drift, successful atomic activation, rejection, and idempotent repeated decisions.
- [x] Run focused tests and confirm RED.
- [x] Implement activation validation, projection copy, revision lifecycle updates, audit append, and conflict-safe decision persistence.
- [x] Run focused tests and confirm GREEN.

### Task 5: Replace the blocking UI with automatic progress and final actions

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/scripts/check-information-architecture-contract.ts`

**Interfaces:**
- Consumes the new correction status/stage/candidate/regression/decision payload.
- Produces final approve/reject calls and no intermediate configuration UI.

- [x] Extend the source contract to reject `awaiting_confirmation`, “另行创建候选版本”, and “当前阻塞”, and require the automatic stage labels plus final actions.
- [x] Run `npm --prefix frontend run contract:information-architecture` and confirm RED.
- [x] Update types/API, replace blocking copy with the automatic pipeline progress, show actionable failure/retry, and render approve/reject only in `awaiting_decision`.
- [x] Run the frontend contract and confirm GREEN.

### Task 6: Verify, document, commit, and push the isolated branch

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/superpowers/plans/2026-08-13-automated-baseline-correction-loop.md`

- [x] Run focused backend migration, correction, revision, and worker tests.
- [x] Run the complete frontend information-architecture contract, lint, and SHA-stamped build.
- [x] Run `git diff --check` and inspect the complete diff for accidental layout-fix overlap or production calls.
- [x] Record exact evidence and remaining deployment gates in `PROJECT_STATUS.md`.
- [x] Commit to `codex/automated-correction-loop-v1` and push with the existing Codeup SSH identity; do not merge or deploy until review evidence is complete.

## Verification Evidence

- Backend focused suites: `92 passed, 1 warning`.
- Frontend information architecture and workspace component contracts: passed.
- TypeScript lint: passed.
- SHA-stamped Vite production build: passed; existing main chunk size warning only.
- `git diff --check`: passed.
- Edge `151.0.4129.72`: passed at `1440×900` and `1280×720` using a temporary file database and deterministic generator; approve confirmation was cancelled and API state remained `awaiting_decision` with no decision.
- No real tuning-model call, real batch regression, production data access, merge, or deployment was performed during tests or acceptance.
- Codeup branch `codex/automated-correction-loop-v1` was created and read back at feature commit `3eb2ec4df6176a370b36b082f5c6e376ffdedf7c` before this documentation closeout.
