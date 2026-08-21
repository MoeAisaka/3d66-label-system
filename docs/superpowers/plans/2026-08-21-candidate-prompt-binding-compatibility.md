# Candidate Prompt Binding Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make candidate V3 regression runs resolve their contract-bound A/B Prompt versions exactly, fail clearly when a bound Prompt is unavailable, and never silently fall back to the current published pair.

**Architecture:** Keep the backend fail-closed contract validator unchanged as the execution safety boundary. Expand the baseline-regression Prompt query to include historical rows, add a small pure resolver for availability, and make candidate selection expose a deterministic unavailable state instead of substituting current Prompt IDs. Preserve historical candidate contract bindings; do not rewrite them to newer Prompt versions.

**Tech Stack:** FastAPI, SQLAlchemy, React, TypeScript, Vitest/Jest-compatible frontend tests, pytest, Vite.

**Spec:** User-confirmed execution contract in the current Codex task: repair the candidate Revision 7 A/B binding compatibility path, test it, commit, push to Codeup, and deploy once to test server `192.168.1.35` with health/database verification.

## Global Constraints

- Do not weaken `validate_category_evaluation_prompt_bindings()` or execute a candidate with a different A/B pair.
- Do not rewrite historical candidate contracts or historical evaluation snapshots.
- Only touch the current repository branch and the authorized test server; do not touch production.
- Preserve a deployment rollback point before the single deployment.
- Stop on failed tests, dirty/unexpected worktree, failed health/readiness, container unhealthy, restart count change, or database integrity failure.

### Task 1: Add failing resolver and API-contract tests

**Files:**
- Create: `frontend/src/features/baseline-regression/baseline-regression-contract.test.ts` (or the repository's existing colocated test file if present)
- Modify: `backend/tests/test_baseline_regression.py`

**Interfaces:**
- The pure frontend resolver must distinguish `available`, `unavailable`, and `unbound` states rather than returning a fallback ID.
- Existing backend coverage proves a mismatched candidate-bound Prompt is rejected before any run/job is created, while an exact executable pair still passes the existing validator.

- [ ] Write the failing frontend test for an absent candidate binding: current published IDs must not be returned as the candidate's effective IDs.
- [ ] Write the failing backend test for archived/wrong-scope candidate-bound Prompt availability and assert a structured error with the exact stage/version.
- [ ] Run the focused tests and confirm they fail for the current fallback behavior.

### Task 2: Implement exact candidate Prompt resolution

**Files:**
- Modify: `frontend/src/features/baseline-regression/baseline-regression-contract.ts`
- Modify: `frontend/src/pages/baseline-regression-page.tsx`
- No backend production change; the existing fail-closed run validator remains authoritative.

**Interfaces:**
- Candidate selection consumes the exact `call_a_version`/`call_b_version` values from the revision contract.
- UI state exposes a clear unavailable binding message and disables run start; it never substitutes `publishedPromptA/B` for a candidate-bound version.
- Backend remains the final gate and reports stage, requested version, and reason (`missing`, `archived`, or `pipeline_scope_invalid`) when applicable.

- [ ] Implement the minimal pure resolver and make effective IDs nullable/invalid when the exact binding is unavailable.
- [ ] Update the candidate auto-bind effect and mismatch message to distinguish “not found/unavailable” from “selected different version.”
- [ ] Keep backend candidate binding validation unchanged and cover its existing rejection path with the focused regression suite.
- [ ] Run focused frontend/backend tests and confirm they pass.

### Task 3: Run full verification

**Files:**
- No new production files; update tests only if a deterministic regression case is required.

- [ ] Run the relevant backend pytest modules.
- [ ] Run frontend lint/type checks and production build.
- [ ] Run repository deployment-script dry-run/syntax checks.
- [ ] Confirm `git diff --check` and a clean pre-commit worktree except intentional changes.

### Task 4: Commit and push

**Files:**
- Commit the intentional source/test/plan changes only.

- [ ] Create one focused commit with the candidate Prompt binding compatibility fix.
- [ ] Push the current branch to Codeup `origin`.
- [ ] Record commit SHA and remote ref; do not merge unrelated branches.

### Task 5: Single controlled test deployment

**Files:**
- No repository changes during deployment unless the guarded script requires an already-reviewed artifact.

- [ ] Verify the remote target and current deployment state before writing.
- [ ] Run the guarded deployment once, preserving the pre-deploy snapshot.
- [ ] Verify server HEAD/build SHA, `/api/health`, `/api/health/ready`, container running/healthy/restart count, migration, SQLite integrity, and foreign-key checks.
- [ ] If any gate fails, stop and report the rollback point without retrying deployment.
