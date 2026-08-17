# 3D/SU Grade Scoring Regression Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the frozen 3D/SU five-dimension `grade + evidence` call-B contract so valid grades deterministically produce L1-L4 scores and malformed output becomes manual review instead of 100/L1.

**Architecture:** Append a system-owned v2 prompt/rubric/spec revision while retaining every v1 prompt and projected revision for historical replay. The v2 track dimensions use linear grade points (`1=0, 2=25, 3=50, 4=75, 5=100`) and no deduction rules, so the worker executes the frozen static B prompt; an explicit grade-output contract makes the worker bypass only the obsolete v1 preliminary scorer and validate the exact five grades/evidence before the existing v3 grade bridge aggregates them.

**Tech Stack:** Python 3.12, SQLAlchemy 2, existing v3 category evaluation engine, pytest, React/TypeScript/Vite verification.

## Global Constraints

- Keep `category_key=model_3d_su`; do not touch the parallel `three_d` profile.
- Keep L1 `80-100`, L2 `61-79`, L3 `41-60`, L4 `0-40`; L5 remains disabled.
- Preserve Run #27 and all historical frozen results; never rewrite v1 prompts, v1 projected revisions, jobs, results, or baseline items.
- Upgrade only known system-owned v1/v2 `model_3d_su` profile/config rows; preserve operator-edited descriptions and fail closed for unknown ownership.
- Invalid, incomplete, extra-key, out-of-range, or evidence-free v2 B output must persist `score=None`, `level=None`, `needs_review=True` with a stable v3 error code.
- Work locally only. Do not push, merge, deploy, write production data, call a real model, or rerun a real baseline.
- Do not commit without a separate user request.

---

### Task 1: Prove the regression through the full worker

**Files:**
- Modify: `backend/tests/test_category_worker_pipeline.py`
- Modify: `backend/tests/test_model_3d_su_seed.py`

**Interfaces:**
- Consumes: `seed_model_3d_su(db, settings)`, `_category_execution_snapshot(...)`, `worker.evaluate_job(job_id)`.
- Produces: integration coverage for static B routing, grade scoring, malformed-output review, and v1-to-v2 seed upgrade.

- [x] **Step 1: Add a valid grade-3 worker regression**

  Seed `model_3d_su`, freeze its active profile and v3 bundle into a job, return a `家装` in-scope precheck followed by the five exact v2 dimensions at grade 3 with non-empty Chinese evidence, and assert:

  ```python
  assert len(calls) == 2
  assert calls[1][0] == prompt_b.system_prompt
  assert "hit_rules" not in calls[1][0]
  assert "hit_rules" not in calls[1][1]
  assert result.score == 50
  assert result.level == "L3"
  assert scoring["dimension_scoring_mode"] == "grade_fallback"
  assert result.prompt_b_version == MODEL_3D_SU_CALL_B_VERSION
  ```

- [x] **Step 2: Add a malformed B worker regression**

  Return only four of the five dimensions and assert the worker still persists a result with:

  ```python
  assert result.score is None
  assert result.level is None
  assert result.needs_review is True
  assert scoring["scoring_mode"] == "v3_authoritative_failed"
  assert scoring["v3_error_code"] == "grade_output_invalid"
  ```

- [x] **Step 3: Add an idempotent v1-to-v2 seed upgrade regression**

  Materialize the old system v1 prompt/profile/config identities, invoke the new seed twice, and assert four immutable prompts remain, the profile points to v2 A/B while its edited description is unchanged, the old projected revision is retired, a v2 child revision is active, and the second seed does not append another revision.

- [x] **Step 4: Run RED**

  Run:

  ```bash
  backend/.venv/bin/python -X utf8 -m pytest \
    backend/tests/test_category_worker_pipeline.py \
    backend/tests/test_model_3d_su_seed.py -q
  ```

  Expected current failures: seeded track dimensions still contain `deduction_rules`, the dynamic `hit_rules` prompt is used, and no v2 identities/upgrade exist.

### Task 2: Append the v2 grade-scored 3D/SU mechanism

**Files:**
- Modify: `backend/app/model_3d_su_category_seed.py`
- Create: `backend/prompts/model_3d_su_call_a_v2.txt`
- Create: `backend/prompts/model_3d_su_call_b_v2.txt`
- Test: `backend/tests/test_model_3d_su_seed.py`

**Interfaces:**
- Produces: `model-3d-su-v2-grade-scoring-20260817`, `model-3d-su-rubric-v2`, v2 A/B prompt identities, and `grade_output_contract={"format_version":"dimension-grade-output-v1","require_exact_keys":true,"evidence_required":true}` on each track.
- Consumes: existing `sync_projected_revision(...)` append-only projection mechanism.

- [x] **Step 1: Version all active mechanism identities**

  Keep explicit v1 constants for ownership/upgrade recognition and make the exported active constants point to v2. Use a v2 created-by identity and new prompt filenames; never update rows whose version is v1.

- [x] **Step 2: Replace rule deductions with linear grade points**

  Build each dimension as:

  ```python
  {
      "key": key,
      "label": label,
      "weight": weight,
      "grade_points": {"1": 0.0, "2": 25.0, "3": 50.0, "4": 75.0, "5": 100.0},
  }
  ```

  Do not include `deduction_rules`. Add the strict grade-output contract at track level and persist an empty `dimension_deduction_rules_json` mirror.

- [x] **Step 3: Add anchored v2 prompts**

  Keep A's output schema unchanged but give it a new immutable identity. In B, retain the exact five `grade + evidence` keys and add calibration anchors: all 5 maps to L1, all 4 maps to L2, all 3 maps to L3, and all 1/2 maps to L4 after weighting; B still cannot output a score or level.

- [x] **Step 4: Upgrade only recognized system-owned rows**

  For a known v1 profile, update A/B pointers, rubric, and system ownership while preserving description and unrelated operator fields. For a known v1 config, call `sync_projected_revision` so the v1 revision is retired and a v2 child is appended; unknown owners or rubric/spec identities raise without mutation.

- [x] **Step 5: Run seed tests GREEN**

  Run:

  ```bash
  backend/.venv/bin/python -X utf8 -m pytest backend/tests/test_model_3d_su_seed.py -q
  ```

### Task 3: Route and validate static grade B output

**Files:**
- Modify: `backend/app/worker_v3_authoritative.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/test_worker_v3_authoritative.py`
- Test: `backend/tests/test_category_worker_pipeline.py`

**Interfaces:**
- Produces: `v3_uses_static_grade_output(v3_bundle, precheck) -> bool` and strict grade-output validation before `evaluate_one(...)`.
- Consumes: existing `_common_grades_from_aesthetic(...)`, `compose_deductions(...)`, and `build_v3_authoritative_error_scoring(...)`.

- [x] **Step 1: Detect only explicitly contracted static-grade tracks**

  Resolve the active track using the same redline/classification path as authoritative scoring. Presence of `grade_output_contract` bypasses the unrelated legacy preliminary scorer; the authoritative evaluator then requires its exact `dimension-grade-output-v1` shape, so a malformed contract also becomes manual review instead of an uncaught legacy-schema error.

- [x] **Step 2: Validate the exact B payload before grade extraction**

  When the explicit grade-output contract is present, require `dimensions` to contain exactly the common and specific keys, every grade to be an integer 1-5, and every evidence value to be a non-empty list of non-empty strings. Raise `V3AuthoritativeError("grade_output_invalid", ...)` for every violation.

- [x] **Step 3: Bypass only legacy preliminary scoring**

  In `worker.evaluate_job`, keep the static B call enabled, but include the explicit static-grade flag wherever rule mode currently bypasses `_apply_dimension_selection`, legacy score calculation, and legacy risk review. The final v3 call still receives the untouched five-dimension aesthetic payload.

- [x] **Step 4: Run authoritative and full-worker tests GREEN**

  Run:

  ```bash
  backend/.venv/bin/python -X utf8 -m pytest \
    backend/tests/test_worker_v3_authoritative.py \
    backend/tests/test_category_worker_pipeline.py \
    backend/tests/test_model_3d_su_seed.py -q
  ```

### Task 4: Align decisions, status, and verification evidence

**Files:**
- Modify: `docs/decisions/0046-model-3d-su-evaluation-mechanism.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Produces: durable explanation of why v1 rule-hit scoring was replaced by v2 grade scoring, and exact local verification evidence.

- [x] **Step 1: Amend ADR-0046**

  Record that v1's simultaneous static grade prompt and dynamic rule configuration caused the worker to bypass the frozen prompt and inflate results. State the v2 linear grade points, strict output failure behavior, append-only identity upgrade, and historical replay boundary.

- [x] **Step 2: Update current status**

  Add a top section to `PROJECT_STATUS.md` with the local branch, exact verification counts, unchanged Run #27/history, and the explicit no-push/no-deploy/no-rerun boundary.

- [x] **Step 3: Run focused and broad backend verification**

  Run the focused suite first, then:

  ```bash
  PYTHONPATH=.:backend backend/.venv/bin/python -X utf8 -m pytest backend/tests -q
  ```

- [x] **Step 4: Run frontend and repository checks**

  Run:

  ```bash
  npm --prefix frontend run lint
  LABEL_LAB_BUILD_SHA=$(git rev-parse --short HEAD) npm --prefix frontend run build
  git diff --check
  git status --short --branch
  ```

- [x] **Step 5: Review the frozen contract against the diff**

  Confirm there are no database migrations, no historical row rewrites, no changes under `three_d`, no secrets or generated artifacts, and no external actions.

## Confirmed Release Contract Amendment (2026-08-17)

The user subsequently authorized this completed local fix to enter the release
stage. This amendment supersedes only the earlier no-commit/no-push/no-deploy
boundary; all implementation, history-preservation, and no-real-rerun
constraints above remain frozen.

- Run the full backend suite, frontend lint/build, and repository checks again
  immediately before integration.
- Commit only the ten reviewed files on
  `codex/model-3d-su-grade-fix-20260817`.
- Push the feature branch, create a Codeup merge request into `main`, merge with
  a merge node, and retain the source branch.
- Deploy only the exact merged Codeup `main` through
  `/usr/local/sbin/deploy-3d66-label-test` to the shared test server
  `192.168.1.35:8081`.
- The deployment necessarily includes the previously merged but not yet
  deployed `850508a3..9943b8c7` main delta (MR #14, including additive
  migrations 70 and 71) before this fix.
- Immediately before deployment, create a consistent SQLite backup under the
  existing `predeploy-snapshots` directory and verify its SHA-256,
  `integrity_check`, `foreign_key_check`, and migration version.
- Do not rewrite Run #27 and do not start a real evaluation, baseline
  regression, correction, stock rerun, optimization, or model call.
- Stop on test failure, remote-main drift, active work, snapshot/integrity
  failure, or inability to prove Codeup main = server HEAD = static build SHA.
- The protected script owns automatic code rollback. Any live database restore
  still requires a separate explicit confirmation.
