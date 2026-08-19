# Correction Candidate, NAS Reference, and Failed-Item Rerun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make correction candidate generation resilient to partial AI output, merge the existing read-only NAS reference package into current main, and rerun only the 87 failed items from baseline run 30.

**Architecture:** Correction generation accepts an RFC 7396-style partial revision over the frozen active revision, inherits unchanged prompt text, validates the composed candidate, and performs one bounded repair call with persisted diagnostics. NAS remains a read-only source reference added by migration 76. The failed-item rerun uses the existing baseline subset API and creates a new immutable run linked to run 30 without mutating run 30 or its 13 successful items.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite migrations, Pydantic, pytest, React/TypeScript/Vite, Docker Compose.

**Spec:** Frozen execution contract confirmed in the Codex task on 2026-08-19; NAS design from commit `3383251`.

## Global Constraints

- Product name is **特鹏标签中台（Label System）**.
- AI may create candidates and run regression, but may not activate a mechanism without the existing human decision gate.
- Do not mutate or delete baseline run 30, its 13 successful items, or any historic failure evidence.
- NAS is read-only; the application must not write, delete, overwrite, crop, filter, or copy source originals.
- Test server only; production is out of scope.
- Code merge base is Codeup `origin/main@8f9d0370cf850e01dd8be80cdefe3e90e40ecac9`; deployment rollback remains `8ca837aa5a51cbfadda5f966ddbf9ae487265ac4` until the first new deployment succeeds.

---

### Task 1: Adaptive correction candidate composition and repair

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_baseline_correction_human_evidence.py`
- Test: `backend/tests/test_baseline_regression.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: frozen `CategoryEvaluationV3Revision`, active A/B `PromptVersion` rows, and correction report.
- Produces: one complete `GeneratedMechanismCandidate`, composed from AI patch plus frozen active values; `generation_trace` evidence for each model attempt.

- [x] Write failing tests proving omitted `classification_map` and `subcategory_dimensions` inherit from the active revision, while explicitly invalid replacements still fail validation.
- [x] Run the focused tests and confirm the current full-output-only normalizer fails them.
- [x] Implement deterministic JSON merge-patch composition and prompt inheritance without weakening final candidate validation.
- [x] Write a failing test proving the registered tuner performs exactly one repair call after invalid structured output and records both attempts.
- [x] Implement the bounded repair call and persist bounded raw output, validation code/message, request correlation ID, usage, and attempt status in correction orchestration for success and terminal failure.
- [x] Run focused correction tests and confirm both partial-candidate and repair paths pass.
- [ ] Commit Task 1 independently.

### Task 2: Rebase the NAS read-only reference package

**Files:**
- Replay and resolve the 26 files from commit `3383251`.
- Preserve current-main behavior in `backend/app/inspiration_aesthetic_foundation.py`, `backend/app/main.py`, and `scripts/deploy-test-server.sh`.
- Keep migration `76 add_nas_asset_source_references` as the only new migration.

**Interfaces:**
- Consumes: configured NAS root and stored relative source path.
- Produces: authenticated read-only asset content and metadata APIs with legacy `/api/assets/{id}/file` compatibility.

- [ ] Cherry-pick `3383251` after Task 1 and resolve conflicts against current main without dropping the v2 aesthetic contract fix or correction display changes.
- [ ] Run NAS storage/API/migration tests, automation history isolation tests, and the frontend NAS contract.
- [ ] Run an old-database migration upgrade check and verify migration 76 applies once.
- [ ] Commit any conflict-resolution delta independently if the cherry-pick is not clean.

### Task 3: Controlled failed-item rerun of run 30

**Files:**
- No production-code change expected; use `POST /api/baseline-sets/{id}/runs` with `baseline_item_ids` and explicit `category_context`.
- Record operational evidence in `PROJECT_STATUS.md` or a deployment receipt without credentials or private model payloads.

**Interfaces:**
- Consumes: exactly the 87 `BaselineRegressionItem` IDs in run 30 whose status is `failed`.
- Produces: one new immutable baseline run with `previous_run_id=30`, `total=87`, and the same frozen model/prompt/mechanism selection unless the existing endpoint requires explicit equivalent IDs.

- [ ] Before calling the API, query and record run 30 counts: total 100, completed 13, valid 13, failed 87.
- [ ] Resolve the 87 failed baseline-set item IDs and assert they do not overlap the 13 successful IDs.
- [ ] Create one subset run with an idempotent operational record and assert the new run has exactly 87 items and `previous_run_id=30`.
- [ ] Let the existing worker execute the 87 jobs; do not retry or recreate the 13 successes.
- [ ] Record final counts and preserve run 30 unchanged.

### Task 4: Verification, review, Codeup merge, and test-server deployment

**Files:**
- Modify: `PROJECT_STATUS.md`
- Use: `scripts/deploy-test-server.sh`
- Use: `scripts/verify-nas-test-server.sh`

**Interfaces:**
- Produces: Codeup merged main and test server build SHA with deterministic health/data evidence.

- [ ] Run focused backend tests, full backend tests in a fresh data directory, frontend contracts, lint, build, `git diff --check`, and migration tests.
- [ ] Review the complete diff for scope, security, read-only NAS enforcement, correction retry bounds, and immutable-run preservation.
- [ ] Push and merge correction changes first; deploy and verify health, build SHA, migration, SQLite integrity/FK, and correction behavior.
- [ ] Push and merge NAS changes second; deploy from final Codeup main and verify read-only NAS plus legacy-image fallback.
- [ ] Retry correction 6 with the real tuning model; accept only automatic progression to regression/decision or a persisted bounded retry failure.
- [ ] Create and execute the 87-item subset run, then verify run 30 and its 13 successful items remain unchanged.
- [ ] Stop immediately on failed health/readiness, build mismatch, migration/integrity/FK failure, unexpected active jobs, write-capable NAS behavior, or any attempt to mutate run 30.
