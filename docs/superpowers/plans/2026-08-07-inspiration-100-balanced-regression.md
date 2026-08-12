# Inspiration 100 Balanced Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the inspiration-image regression to an immutable 100-item baseline with 20 human-labelled items per L1-L5 band, while preventing technical retries from bypassing the ABORT gate.

**Architecture:** Keep the existing immutable baseline model and filename-truth parser. Add a named 100-item selector that reuses the existing creator with a new distribution and rejects duplicate SHA-256 values before persistence. Add a retry scheduling gate inside the same transaction that transitions a failed parent to `retrying`; a paused/cancelled control or an `ABORT-NOTICE.txt` file fails closed and creates no recovery child.

**Tech Stack:** Python 3, FastAPI/SQLAlchemy, SQLite migrations, pytest, existing guarded test-server deployment script.

---

### Task 1: Add retry ABORT gate tests

**Files:**
- Create: `backend/tests/test_retry_abort_gate.py`

- [ ] **Step 1: Write failing tests** for an ABORT notice and paused control, asserting the parent becomes failed and no recovery child is created.
- [ ] **Step 2: Run `pytest backend/tests/test_retry_abort_gate.py -q` and confirm the tests fail because the gate does not exist.

### Task 2: Implement retry gate

**Files:**
- Modify: `backend/app/worker.py`

- [ ] **Step 1: Add a fail-closed gate that reads `ABORT_NOTICE_PATH` (defaulting to the repository/workdir `ABORT-NOTICE.txt`), the persisted `EvaluationControl`, and the current parent status before creating a recovery job.
- [ ] **Step 2: On a blocked retry, persist `technical:retry_aborted`, set stage `retry_aborted`, fail any bound regression item, and return without adding a child.
- [ ] **Step 3: Run the targeted tests and the existing queue scheduler retry tests.

### Task 3: Add the 100-item balanced selector

**Files:**
- Modify: `backend/app/inspiration_auto_correction.py`
- Modify: `scripts/inspiration_golden_workflow.py`
- Create: `backend/tests/test_inspiration_balanced_golden_set.py`

- [ ] **Step 1: Add constants for a distinct 2026-08-07 balanced set and `{"好":20,"中等":20,"中差":20,"极差":20,"过滤":20}`.
- [ ] **Step 2: Add a creator entry point that preserves the old selector, rejects duplicate SHA-256 values in the selected 100, and returns the exact manifest/fingerprint/report.
- [ ] **Step 3: Add a CLI command `create-balanced-100` and unit tests for distribution, deterministic order, duplicate rejection, and idempotence.

### Task 4: Verify and deploy only to the test server

**Files:**
- Create: `/Users/Shared/OpenClaw/148-验证-标签实验台100张均衡扩样-20260807/` reports and logs outside Git.

- [ ] **Step 1: Run targeted tests, full backend pytest, compileall, frontend build, and `git diff --check`.
- [ ] **Step 2: Check the independent label148 ABORT gate and test-server reachability; abort if the notice exists, active jobs are nonzero, or the server is unreachable.
- [ ] **Step 3: Use only the protected test deployment helper, then create the immutable set and verify 20 items per band before starting a run.
- [ ] **Step 4: Run the 100-item regression with provider-call ceiling 400 and record exact calls, tokens, failures, valid predictions, overall accuracy, and per-band recall.
- [ ] **Step 5: Apply fail-closed acceptance, preserve production zero-touch evidence, and write the report, trace, manifest, rollback note, and bundle hashes.
