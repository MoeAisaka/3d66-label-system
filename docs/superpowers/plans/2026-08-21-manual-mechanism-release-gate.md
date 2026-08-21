# Manual Mechanism Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the manual v3 mechanism lifecycle so an operator can edit a candidate, run a frozen candidate regression, obtain a quality-gated admin approval, and atomically activate the candidate without bypassing tag publication.

**Architecture:** Put candidate-regression quality evaluation in a small backend helper shared by the existing automatic-correction decision path and the new manual activation endpoint. Extend the isolated v3-config router with an admin-only activation action that performs CAS, ancestry, snapshot, prompt-binding, and metric checks before one transaction updates the runtime projection and prompt bindings. Add typed API/UI links that carry a candidate revision into the existing baseline regression workspace and expose activation only after a passing run.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, React/TypeScript, TanStack Query, Vite.

**Spec:** `docs/superpowers/specs/2026-08-21-manual-mechanism-release-gate-design.md`

## Global Constraints

- Candidate revisions and historical snapshots remain append-only; no legacy PUT path is reopened.
- Activation requires administrator identity, current-projection CAS, a completed comparable regression, and non-regression evidence.
- Activation never publishes tag facts, starts a stock rerun, calls a real model, or mutates historical results.
- Existing automatic correction keeps its admin decision gate and must use the same quality-gate logic.
- All image categories use the Call-B `aesthetic_score` as the runtime matcher's initial score; `base_score`/`grade_points` are historical-only and never a fallback for new runs.
- Invalid or incomplete Call-B aesthetic output fails closed with `score=null` and `level=null`; `proposal_text_pdf` retains its documented additive component exception.
- All behavior changes require focused backend tests and frontend contract/build verification.
- Preserve unrelated worktree changes and use `apply_patch` for edits.

### Task 0: Unify image scoring around Call-B aesthetic foundation

**Files:**
- Modify: `backend/app/category_evaluation_contract.py`
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/app/evaluation_v3_pipeline.py`
- Modify: `backend/app/category_evaluation_aggregator.py`
- Modify: `backend/app/inspiration_aesthetic_foundation.py`
- Modify: `backend/app/model_3d_su_category_seed.py`
- Modify: `backend/app/inspiration_category_seed.py`
- Modify: `backend/app/seed.py`
- Modify: `backend/prompts/model_3d_su_call_b_v3.txt`
- Modify: `backend/tests/test_category_evaluation_aggregator.py`
- Modify: `backend/tests/test_worker_v3_authoritative.py`
- Modify: `backend/tests/test_model_3d_su_seed.py`
- Create: `backend/tests/test_b_aesthetic_foundation_scoring.py`

**Interfaces:**
- Add a normalized `b_aesthetic_foundation_v1` payload with `aesthetic_score: int`, evidence, and source metadata for every image v3 evaluation.
- `aggregate_category_evaluation(..., initial_score=...)` starts from the frozen Call-B score and applies only contract-owned deductions/caps.
- Invalid Call-B foundation raises a coded error consumed by the worker's existing fail-closed path.

- [ ] **Step 1: Write failing score-source tests.** Assert that a score of 88 enters the matcher as 88 before a rule deduction, that `base_score` is ignored for a new image run, that 3D/SU output contains a scalar `aesthetic_score`, and that malformed/missing B output produces a coded fail-closed result.
- [ ] **Step 2: Run the focused tests and verify they fail because the matcher still derives the starting score from track/grade values.**
- [ ] **Step 3: Add the shared B foundation validator and update image Call-B contracts/prompts.** Keep proposal-text validation unchanged.
- [ ] **Step 4: Thread the normalized foundation through authoritative worker and aggregator.** Preserve the A redline evidence-aware short circuit; never call B when redline evidence is complete.
- [ ] **Step 5: Remove runtime use of `base_score`/`grade_points` for new image scoring and retain them only in historical replay adapters.**
- [ ] **Step 6: Run focused image, 3D/SU, and redline tests; confirm exact initial-score and fail-closed behavior.**

### Task 1: Add the failing release-gate contract tests

**Files:**
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`
- Modify: `backend/tests/test_baseline_regression.py`
- Create: `backend/tests/test_mechanism_release_gate.py`

**Interfaces:**
- Tests will target the new router action `POST /api/category-evaluation/v3-config/{category_key}/revisions/{revision}/activate`.
- Tests will target a pure gate evaluator that returns a release report or raises a stable coded error.

- [ ] **Step 1: Write the failing pure-gate tests.** Cover a completed candidate run with a matching candidate revision, a missing baseline comparison, a failed item, an exact/adjacent regression, and a candidate snapshot/contract mismatch. Assert stable codes and that the valid case returns the metric deltas and `approval_allowed=True`.

- [ ] **Step 2: Run the focused pure-gate tests and verify the expected failure.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_mechanism_release_gate.py -q`

Expected: collection or assertion failure because the gate module and evaluator do not exist yet.

- [ ] **Step 3: Write the failing API tests.** Extend the existing v3 router fixture to provide an admin dependency. Create an active config, append a candidate, create a completed candidate regression snapshot and its previous baseline, then assert:
  - a non-admin receives `403`;
  - a passing admin activation returns the candidate and refresh payload;
  - stale revision/hash, wrong category/run, incomplete run, and failed quality gate return coded `409` without changing projected or candidate status;
  - a repeated activation is idempotent only for the same approved candidate and otherwise rejected.

- [ ] **Step 4: Run the API tests and verify they fail for the missing route/behavior.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_category_evaluation_v3_config_api.py -k "activate or release" -q`

Expected: `404`/missing route or assertion failures, not fixture errors.

- [ ] **Step 5: Add an automatic-correction reuse assertion.** In `backend/tests/test_baseline_regression.py`, construct a candidate correction whose regression report passes and assert that its decision path produces the same gate report shape and still updates the existing prompt/profile and v3 projection only after admin approval.

- [ ] **Step 6: Run the new regression assertion to verify it fails only because the shared helper is absent.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_baseline_regression.py -k "release_gate or approved" -q`

Expected: failure at the new shared-gate assertion while the existing correction tests remain runnable.

### Task 2: Implement the shared candidate release gate

**Files:**
- Create: `backend/app/mechanism_release_gate.py`
- Modify: `backend/app/baseline_correction_orchestration.py:999-1118`
- Modify: `backend/app/main.py:12030-12284`

**Interfaces:**
- Produce `CandidateReleaseGateError(code: str, message: str)`.
- Produce `evaluate_candidate_release_gate(db, *, category_key, projected, candidate, regression_run, expected_projected_revision, expected_projected_contract_hash) -> dict[str, Any]`.
- The returned report contains `schema_version`, baseline/candidate metrics, field metrics, deltas, regression list, `recommendation`, and `approval_allowed`.

- [ ] **Step 1: Implement snapshot and ancestry checks in the helper.** Load the candidate v3 bundle from `regression_run.execution_snapshot_json`; require the candidate id, category, contract hash, and candidate prompt bindings to match persisted rows. Require a current active projection and a direct candidate-chain descendant. Return coded errors for missing projection, wrong category, invalid status, broken ancestry, missing candidate snapshot, and stale CAS.

- [ ] **Step 2: Implement regression evidence checks.** Require `regression_run.status == "completed"`, a non-null `previous_run_id`, matching baseline-set fingerprint, positive denominators, zero failed items, and no exact/adjacent/field metric regression compared with the previous run. Reuse `build_baseline_field_metrics` and `field_metric_release_regressions`; do not recompute or mutate run metrics.

- [ ] **Step 3: Run the pure-gate tests and make them pass.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_mechanism_release_gate.py -q`

Expected: all focused gate tests pass.

- [ ] **Step 4: Refactor `refresh_correction_run` to call the shared evaluator.** Preserve its existing report schema and status transitions, but remove duplicated metric comparison logic. Ensure automatic correction still reaches `awaiting_decision` and its report remains byte-compatible where the existing tests assert it.

- [ ] **Step 5: Run the automatic-correction tests.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_baseline_regression.py tests/test_baseline_correction_human_evidence.py -q`

Expected: all existing correction and human-evidence tests pass.

### Task 3: Add the admin-only manual activation API

**Files:**
- Modify: `backend/app/category_evaluation_v3_config_api.py:120-180,448-735`
- Modify: `backend/app/main.py:298-304,1679-1686`
- Modify: `backend/tests/test_category_evaluation_v3_config_api.py`

**Interfaces:**
- Add `V3RevisionActivationRequest` with `regression_run_id`, `expected_projected_revision`, `expected_projected_contract_hash`, and bounded `note`.
- Add a response model containing the activated revision, projected revision, regression evidence, mechanism refresh, and audit event key.
- Extend `build_category_evaluation_v3_config_router(require_user, require_admin=None)`; production passes `admin_user`, isolated test fixtures pass an explicit admin dependency.

- [ ] **Step 1: Implement the route after revision detail routes.** Resolve the revision by category and revision number, load the active projected config and regression run, invoke the shared gate, resolve candidate A/B prompt rows from the candidate contract bindings, and verify the current legacy profile still points to the candidate's recorded parent prompt ids.

- [ ] **Step 2: Activate atomically.** Within one database transaction call `activate_candidate_revision`, retire/publish the candidate prompt rows, update the category profile prompt ids, append an audit event with the note and gate report, and commit. On any coded error, rollback and leave all statuses and pointers unchanged.

- [ ] **Step 3: Make repeated activation safe and stale activation fail closed.** A currently active candidate returns the same refresh payload only when the same regression and CAS values are presented; a different candidate, retired candidate, or drifted projection returns a coded conflict.

- [ ] **Step 4: Run the API tests and make them pass.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_category_evaluation_v3_config_api.py -k "activate or release" -q`

Expected: all manual activation, authorization, CAS, snapshot, quality, and no-mutation assertions pass.

### Task 4: Connect the manual configuration page to candidate regression and activation

**Files:**
- Modify: `frontend/src/lib/api.ts:210-298`
- Modify: `frontend/src/lib/types.ts:614-665`
- Modify: `frontend/src/pages/category-evaluation-v3-config-page.tsx:1-477`
- Modify: `frontend/src/pages/baseline-regression-page.tsx:94-361,478-502,1000-1150`
- Modify: `frontend/src/features/baseline-regression/baseline-regression-contract.ts`
- Modify: `frontend/scripts/check-mechanism-editor-contract.ts`
- Create/modify: `frontend/src/features/baseline-regression/manual-candidate-release.test.ts`

**Interfaces:**
- Add typed `activateV3Revision` and `candidateRegressionContext` helpers.
- Carry `category_key`, `candidate_revision_id`, and optional `baseline_set_id` through the baseline regression URL without changing existing run/correction URLs.

- [ ] **Step 1: Write failing frontend contract tests.** Assert that a candidate revision renders a “create candidate regression” link containing its category and revision, that activation is only rendered for an attached completed passing run and admin identity, and that success invalidates config, prompt, revision, baseline-set, and run queries.

- [ ] **Step 2: Run the frontend contract test and verify it fails.**

Run: `cd frontend && npm run test:contracts -- manual-candidate-release`

Expected: missing helper/text or assertion failure because the link and activation action do not exist.

- [ ] **Step 3: Add API/types and URL prefill support.** Extend `baselineRegressionApi` with the activation request and response types. In the baseline regression page, parse candidate context from search params, select the candidate revision, bind its A/B prompts, and keep the existing manual run safeguards.

- [ ] **Step 4: Add configuration-page actions.** Show a candidate-only link to the baseline regression workspace and a clear “回归通过后启用” state. Do not show activation for active/history/draft/unknown profiles.

- [ ] **Step 5: Add the activation action to the candidate regression view.** Use the typed endpoint with current projected revision/hash and the selected completed run, require an admin confirmation note, show coded conflicts, and invalidate all affected queries after success.

- [ ] **Step 6: Run frontend contract tests and build.**

Run: `cd frontend && npm run test:contracts && npm run typecheck && npm run build`

Expected: all contract scripts, TypeScript, and Vite production build pass.

### Task 5: Update documentation and perform full verification

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `docs/decisions/README.md`
- Create: `docs/decisions/0052-manual-mechanism-release-gate.md`

- [ ] **Step 1: Add ADR-0052.** Record that manual mechanism candidates and automatic correction candidates share one release gate, that regression evidence is mandatory, and that tag-fact publishing remains independent.

- [ ] **Step 2: Update project status.** Record the implementation commit, endpoint, test totals, and the remaining non-goals (real model/batch deployment not performed in this change).

- [ ] **Step 3: Run backend focused and full verification.**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_mechanism_release_gate.py tests/test_category_evaluation_v3_config_api.py tests/test_baseline_regression.py tests/test_baseline_correction_human_evidence.py -q`

Then run the full backend suite in a fresh temporary data directory using the repository's standard command.

Expected: zero failures; only previously documented dependency warnings may remain.

- [ ] **Step 4: Run frontend verification and diff checks.**

Run: `cd frontend && npm run test:contracts && npm run typecheck && npm run build`

Then run `git diff --check` and `git status --short`.

- [ ] **Step 5: Review the final diff before any commit or deployment.** Confirm only the design, plan, release-gate implementation, focused tests, frontend contract changes, ADR, and project status are included; do not push or deploy without a separate user authorization.
