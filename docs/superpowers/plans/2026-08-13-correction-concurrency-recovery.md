# Correction Concurrency Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep human correction writable during automatic correction analysis, make tuning-output failures actionable, preserve worker availability, and make the result/decision location explicit on the current regression page.

**Architecture:** Persist and commit correction preparation before scheduling the tuning call as a FastAPI background task. Execute the model call without a database session, then open a short transaction to validate and persist the candidate or failure. Retry transient SQLite claim locks inside workers and expose worker readiness separately from the startup liveness probe.

**Tech Stack:** FastAPI BackgroundTasks, SQLAlchemy, SQLite, pytest, React, TypeScript, TanStack Query, Docker health checks.

## Global Constraints

- Do not modify existing human truth or manually rewrite server correction rows.
- Do not auto-approve candidates or publish label facts.
- Keep mechanism and label-fact release axes independent.
- Reuse frozen correction samples on retry; require no intermediate human configuration.
- Desktop-only UI; primary color remains `#CCED46`.
- Preserve worktrees, branches, bundles, snapshots and SSH material.

---

### Task 1: Split correction generation from SQLite write transactions

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Produces `prepare_correction_generation(db, correction) -> PreparedCorrectionGeneration`.
- Produces `generate_correction_candidate(prepared, generator) -> GeneratedMechanismCandidate` without a Session.
- Produces background `run_baseline_correction(correction_id) -> None` using short preparation/finalization transactions.

- [ ] Write a failing file-backed SQLite test that blocks the deterministic generator and proves a second session can create a review panel while generation is pending.
- [ ] Run the focused test and confirm RED with `database is locked` or a blocked correction response.
- [ ] Implement persisted preparation, background scheduling, no-session generation and short finalization.
- [ ] Run the focused correction suite and confirm GREEN.

### Task 2: Normalize and diagnose tuning candidate output

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Test: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- Consumes direct or one-layer wrapped candidate mappings.
- Produces `CORRECTION_GENERATOR_OUTPUT_INVALID` with exact missing field paths.

- [ ] Write failing tests for a wrapped valid candidate and an invalid candidate missing prompt/revision fields.
- [ ] Run the tests and confirm RED on wrapper rejection and generic error text.
- [ ] Implement safe unwrapping and deterministic field-path diagnostics.
- [ ] Run the tests and confirm GREEN.

### Task 3: Keep workers alive across transient SQLite claim locks

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_queue_scheduler.py`

**Interfaces:**
- Produces `is_sqlite_lock_error(exc) -> bool`.
- `process_one()` returns `False` after bounded retry exhaustion instead of terminating the worker.

- [ ] Write a failing test where `claim_next_job` raises SQLite lock errors and then succeeds/returns idle.
- [ ] Run the test and confirm RED because the exception escapes.
- [ ] Add bounded lock retry with short injectable sleep; keep non-lock errors fail-fast.
- [ ] Run queue scheduler tests and confirm GREEN.

### Task 4: Add worker readiness evidence

**Files:**
- Modify: `backend/app/main.py`
- Modify: Docker/deployment health configuration selected by repository inspection.
- Test: focused health/launcher tests.

**Interfaces:**
- `/api/health` remains HTTP 200 and reports worker counts.
- `/api/health/ready` returns 503 when expected workers are absent after startup grace.

- [ ] Write failing health tests for active and stale/missing worker states.
- [ ] Run them and confirm RED.
- [ ] Implement liveness/readiness payloads and update container health configuration without creating a startup loop.
- [ ] Run health, launcher and deployment tests and confirm GREEN.

### Task 5: Make result and decision location explicit

**Files:**
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- Modify: `frontend/scripts/check-information-architecture-contract.ts`

**Interfaces:**
- Current correction panel states where results appear and where admins decide.
- Failed generation displays exact backend diagnostics and retry action.

- [ ] Extend the source contract to require “结果在当前区域查看”, “系统管理员在当前区域”, and no separate result-page navigation.
- [ ] Run the information architecture contract and confirm RED.
- [ ] Add concise current-page guidance and preserve the existing inline decision controls.
- [ ] Run contract, lint and production build and confirm GREEN.

### Task 6: Verify, review, merge and deploy

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create: deployment receipt under `docs/superpowers/receipts/`.

- [ ] Run focused backend tests, then full backend tests with an isolated `DATA_DIR`.
- [ ] Run all frontend contracts, lightbox test, lint and SHA-stamped build.
- [ ] Run `git diff --check`, inspect the full diff and perform a self-review because this task is explicitly restricted to the current session without subagent delegation.
- [ ] Commit and push a dedicated Codeup branch, create/merge the MR into `main`, and verify remote SHA.
- [ ] Gate deployment on zero active jobs/runs, create a database snapshot and Git bundle, then deploy through the protected script.
- [ ] Verify database integrity/FK/migration, worker readiness, server/static SHA and Edge current-page workflow without submitting a real human decision.
