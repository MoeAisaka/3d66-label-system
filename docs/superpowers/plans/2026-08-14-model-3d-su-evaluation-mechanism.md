# 3D & SU 模型美感评测机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the independently versioned `model_3d_su` category mechanism to LabelLab so operators can upload samples and run regressions without duplicating the parallel `three_d` production-consumption work.

**Architecture:** Keep category behavior in a focused pure seed module that builds and validates a v3 contract, classification map, and rule-deduction dimensions. Seed independent A/B prompts plus an active `EvaluationCategoryProfile` and active `CategoryEvaluationV3Config`; extend only the shared dynamic category catalog with parallel `model_3d_su` entries.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic validators, pytest, React/TypeScript dynamic category APIs.

## Global Constraints

- Use `category_key=model_3d_su`; never modify or reuse the parallel `three_d` profile.
- Use L1 80–100, L2 61–79, L3 41–60, L4 0–40; explicitly disable L5.
- Keep redline disabled; white background and QR code are record-only signals.
- Preserve the shared `production_fields` contract and place class-specific markers in `model_3d_su_fields`.
- Use deterministic rule deductions 20/50/80 for document ranges 15–30/40–60/70–100.
- Do not import NAS/Excel samples, call real models, deploy, or change production data.
- Seed idempotently; refuse unknown operator-owned conflicts instead of overwriting.

---

### Task 1: Freeze the mechanism design and boundary documents

**Files:**
- Create: `docs/superpowers/specs/2026-08-14-model-3d-su-evaluation-mechanism-design.md`
- Create: `docs/superpowers/plans/2026-08-14-model-3d-su-evaluation-mechanism.md`

- [x] Record the approved contract, the `three_d` isolation boundary, output fields, scoring weights, and non-goals.
- [x] Self-review for conflicting category keys, missing L5 semantics, and undocumented external effects.

### Task 2: Add failing tests for builders and persistence

**Files:**
- Create: `backend/tests/test_model_3d_su_seed.py`
- Test: `backend/app/model_3d_su_category_seed.py` (expected missing import in RED)

**Interfaces:**
- `build_model_3d_su_contract() -> dict[str, Any]`
- `build_model_3d_su_classification_map() -> dict[str, Any]`
- `build_model_3d_su_subcategory_dimensions() -> dict[str, dict[str, Any]]`
- `seed_model_3d_su(db: Session, settings: Settings) -> None`

- [ ] **Step 1: Write the failing tests**

  Test contract identity, L1-L4 scale with disabled L5, disabled redline, three tracks, exact weight sums, 20/50/80 rule deductions, and output field registry. Add a persistence test that calls the seed twice and asserts one profile, two prompts, and one v3 config without revision drift.

- [ ] **Step 2: Run RED**

  Run `backend/.venv/bin/python -X utf8 -m pytest backend/tests/test_model_3d_su_seed.py -q` from the repository root. Expected failure: `ModuleNotFoundError` for the new seed module.

### Task 3: Implement the pure v3 contract and rule dimensions

**Files:**
- Create: `backend/app/model_3d_su_category_seed.py`

- [ ] **Step 1: Add constants and prompt/version identities**
- [ ] **Step 2: Build a contract accepted by `validate_category_evaluation_contract`**
- [ ] **Step 3: Build classification mappings for space/building, soft furnishing, and functional-model terms**
- [ ] **Step 4: Build one common dimension group per track with the document weights and 20/50/80 deduction rules**
- [ ] **Step 5: Run the focused seed tests and verify GREEN**

### Task 4: Add prompts and idempotent persistence

**Files:**
- Create: `backend/prompts/model_3d_su_call_a_v1.txt`
- Create: `backend/prompts/model_3d_su_call_b_v1.txt`
- Modify: `backend/app/model_3d_su_category_seed.py`
- Modify: `backend/app/seed.py`

- [ ] **Step 1: Add A prompt with required common fields, classification, and `model_3d_su_fields` markers**
- [ ] **Step 2: Add B prompt with five dimensions, 1–5 grades/evidence, and no final level/score**
- [ ] **Step 3: Seed published A/B prompts, active profile, and active v3 config without overwriting operator edits**
- [ ] **Step 4: Run persistence and prompt-content tests**

### Task 5: Register the category in shared dynamic catalog

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/category_pipeline.py`
- Modify: `backend/tests/test_model_3d_su_seed.py`

- [ ] **Step 1: Add `model_3d_su` to `CATEGORY_PROFILE_DEFAULTS`, `CATEGORY_KEYS`, and `DEFAULT_PIPELINES` without touching `three_d`**
- [ ] **Step 2: Assert `/api/evaluation-categories` includes the active category and its image pipeline**
- [ ] **Step 3: Run targeted API/category tests**

### Task 6: Verification and handoff

**Files:**
- No new production files; update only if verification exposes a scoped regression.

- [ ] **Step 1: Run focused seed/contract/worker tests**
- [ ] **Step 2: Run the relevant existing category, baseline, v3, and migration tests**
- [ ] **Step 3: Run frontend typecheck/build because category navigation is dynamic**
- [ ] **Step 4: Inspect `git diff --check`, `git status`, and exact changed-file list**
- [ ] **Step 5: Report evidence, remaining non-goals, and merge/deploy boundary**
