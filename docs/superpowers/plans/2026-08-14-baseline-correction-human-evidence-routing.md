# Baseline Correction Human Evidence Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed frozen human node corrections and review evidence into automatic baseline correction, then enforce A/B prompt-stage routing from that evidence before candidate creation.

**Architecture:** Extend the existing correction snapshot and deterministic report with normalized, source-tagged per-sample evidence. Derive a narrow routing contract from human evidence only, pass it through the registered tuning-model text payload, and validate the generated prompt stage before any draft prompt, candidate revision, or regression is created.

**Tech Stack:** Python 3.12, SQLAlchemy relationships, FastAPI service functions, pytest.

## Global Constraints

- Modify only `backend/app/baseline_regression.py`, `backend/app/baseline_correction_orchestration.py`, focused tests, and these design/plan documents.
- Do not modify `models.py`, `main.py`, migrations, frontend, deployment, active mechanisms, prompts, mappings, or human truth.
- Do not treat automatic corrections as human evidence.
- Do not add image or multimodal input to the tuning-model call.
- Preserve backward compatibility for v1 frozen correction inputs.

---

### Task 1: Freeze normalized human correction evidence

**Files:**
- Modify: `backend/app/baseline_regression.py`
- Create: `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- Produces `correction_context` per selected item with source-tagged node corrections, visible human reviews, evidence counts, and affected layers.

- [x] Write a failing test with one human call-A node correction, one automatic node correction, and one human key-field review; assert only human evidence affects routing.
- [x] Run the focused test and confirm it fails because `correction_context` is absent.
- [x] Add strict JSON normalization, completed-panel visibility handling, source classification, and affected-layer mapping.
- [x] Run the focused test and confirm it passes.

### Task 2: Report evidence coverage and routing constraints

**Files:**
- Modify: `backend/app/baseline_regression.py`
- Modify: `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- Produces `evidence_summary`, `sample_evidence`, and `candidate_routing` in `baseline-correction-report-v2`.

- [x] Add failing table-driven tests for pure A, pure B, V3-only, mixed, and no-human-evidence reports.
- [x] Confirm RED on missing report fields.
- [x] Implement deterministic aggregation and routing without changing existing accuracy calculations.
- [x] Confirm GREEN for all routing cases.

### Task 3: Enforce routing and pass it to the registered tuner

**Files:**
- Modify: `backend/app/baseline_correction_orchestration.py`
- Modify: `backend/tests/test_baseline_correction_human_evidence.py`

**Interfaces:**
- Produces a stable `CORRECTION_PROMPT_STAGE_MISMATCH` failure before candidate persistence.
- Produces `baseline-correction-generator-input-v2` with explicit `routing_constraints` and the evidence-bearing report.

- [x] Add failing tests proving pure A rejects B, pure B rejects A, and mixed/V3 accepts either legal stage.
- [x] Add a failing registered-generator test that captures the text JSON input and asserts evidence plus routing constraints are present.
- [x] Implement one route validator used by both split-phase and direct orchestration paths, and extend the registered tuner payload.
- [x] Run focused tests and confirm GREEN.

### Task 4: Regression verification and delivery

**Files:**
- Review all changed files; no new production files.

- [x] Run the new focused test file.
- [x] Run `backend/tests/test_baseline_regression.py`, `backend/tests/test_node_correction.py`, and relevant correction orchestration tests.
- [x] Run the complete backend suite with an isolated `DATA_DIR`.
- [x] Run `git diff --check`, inspect `git diff --name-only`, and confirm no overlap with the 3D shadow branch file list.
- [x] Commit only the scoped files on `codex/correction-evidence-routing-20260814`; do not push, merge, or deploy.

## Verification Evidence

- TDD red checks: missing correction context, missing report evidence/routing, and missing candidate-stage enforcement all failed for the intended reason before implementation.
- New focused suite: `11 passed`.
- Related correction suites: `53 passed, 1 warning`.
- Complete backend suite with isolated `DATA_DIR`: `1301 passed, 1 skipped, 6 warnings`.
- Concurrent branch owner confirmed no edits to the two production files owned by this plan; shared `main.py`, `models.py`, migrations, frontend, and deployment files remain untouched.
