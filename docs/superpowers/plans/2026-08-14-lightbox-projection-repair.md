# Lightbox and Category Projection Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Deliver the requested Lightbox inspection background and repair the `model_3d_su` runtime revision projection so category contracts and revision history load again.

**Architecture:** Rebuild the release branch from final Codeup `main=f614565f`, apply only the three-file Lightbox patch and the idempotent 3D/SU seed repair. The seed repair creates or attaches the immutable revision projection during startup; it does not perform manual database DML. Historical baseline-run APIs are verified separately and are not changed unless a reproducible independent failure is found.

**Tech Stack:** React, TypeScript, Vite, FastAPI, SQLAlchemy, SQLite migrations, pytest.

## Global Constraints

- Base is final `main=f614565f42b838889597b6016eb87accaeb21334`.
- Do not include the local 3D shadow branch, read-only source adapter, projection worker, or its documentation.
- Lightbox must not crop, filter, or mutate the source image; use `object-fit: contain` with a checkerboard inspection background and visible border.
- Do not execute manual database DML, evaluation, correction, stock reruns, or real model calls.
- Deployment target is only the shared test server `192.168.1.35:8081` through the protected main-only deployment path.
- Stop on any unexpected file scope, failed deterministic verification, SHA mismatch, health/readiness failure, or database integrity/FK failure.

### Task 1: Reproduce the two runtime failures with tests

**Files:**
- Modify: `backend/tests/test_model_3d_su_seed.py`
- Modify: `frontend/scripts/lightbox-test.tsx`
- Modify: `frontend/scripts/check-baseline-lightbox.ts`

- [ ] Add a test proving a fresh `model_3d_su` seed leaves a non-null `projected_revision_id`.
- [ ] Add a test proving an existing same-spec row with a null projection is repaired idempotently.
- [ ] Copy the Lightbox checkerboard, `contain`, and border assertions from `072ca6a` before changing the component.
- [ ] Run the focused backend and Lightbox checks and record the expected RED failures.

### Task 2: Implement the minimum fixes

**Files:**
- Modify: `backend/app/model_3d_su_category_seed.py`
- Modify: `frontend/src/components/image-lightbox.tsx`

- [ ] Import the existing revision projection helper.
- [ ] Ensure new and same-spec `model_3d_su` rows attach a revision before returning; keep startup idempotent and preserve operator-owned rows.
- [ ] Apply the three-file `072ca6a` behavior without changing image source, crop, filter, or modal semantics.
- [ ] Run the focused tests to GREEN.

### Task 3: Verify historical regression visibility and release safety

- [ ] Run the baseline-regression API/page smoke checks independently from the category-contract list.
- [ ] Run all relevant backend tests, Lightbox and frontend contracts, TypeScript, and production build.
- [ ] Confirm only the five scoped runtime/test files plus this plan changed.
- [ ] Commit, create the Codeup MR, merge to `main`, deploy once through the protected script, and verify exact SHA, health/readiness, SQLite integrity/FK, no active work, and Edge desktop behavior.
