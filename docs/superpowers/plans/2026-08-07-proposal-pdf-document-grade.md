# Proposal PDF Document-Level Grade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Fix PDF A cross-batch conflicts/invalid outputs and make `proposal_text_pdf` produce a document-level score and grade from the original PDF.

**Architecture:** Keep deterministic PDF page rendering and representative-page B input. Replace the A batch reducer with a document-level reducer that preserves conflicts as audit metadata, add bounded recursive recovery for invalid batches, and route PDF results through a dedicated UI status branch. No page export or schema migration.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, React/TypeScript, Vite.

---

### Task 1: Freeze the v2 document-level contract

**Files:**
- Modify: `backend/app/proposal_text_contract.py`
- Modify: `backend/app/proposal_text_seed.py`
- Modify: `backend/app/proposal_text_assets/v3_contract_proposal_text_v1.json`
- Create: `backend/app/proposal_text_assets/call_a_proposal_text_v2.txt`
- Create: `backend/app/proposal_text_assets/call_b_proposal_text_v2.txt`
- Test: `backend/tests/test_proposal_text_contract.py`
- Test: `backend/tests/test_proposal_text_integration.py`

- [x] Write a failing contract test asserting v2 version identifiers and `information_merge=document_first_seen_with_audit`; run the focused test and confirm it fails against v1.
- [x] Update the frozen contract constants and seed bindings; add the v2 prompt copies with explicit document-level scoring language.
- [x] Run contract and seed tests; confirm old v1 fixtures remain rejected only where version-specific behavior is expected and historical fixture validation is still covered through its dedicated tests.

### Task 2: Implement document-level A aggregation

**Files:**
- Modify: `backend/app/proposal_text_pdf_channel.py`
- Test: `backend/tests/test_proposal_text_pdf_channel.py`

- [x] Add failing tests for ordinary project-name conflicts, classification majority/tie, completeness precedence, and redline union.
- [x] Implement field-aware merging: first non-empty scalar plus audit conflicts, page-weighted majority classification, conservative completeness, summed image counts, and document-level coverage metadata.
- [x] Run the focused channel test module and verify the new behavior is green.

### Task 3: Add bounded recovery for invalid A batches

**Files:**
- Modify: `backend/app/proposal_text_pdf_channel.py`
- Test: `backend/tests/test_proposal_text_pdf_channel.py`
- Test: `backend/tests/test_proposal_text_integration.py`

- [x] Add failing tests where the first 16-page batch fails twice but 8-page child batches recover, and where a one-page child still fails and the document remains manual review.
- [x] Implement deterministic binary splitting with a maximum depth of four for a 16-page batch, preserve every provider response in audit output, and continue only when all pages are covered or a real redline terminates scanning.
- [x] Run focused tests and the Worker integration test; verify B receives a merged document precheck and the scoring path can produce a level.

### Task 4: Fix PDF-specific review status rendering

**Files:**
- Modify: `frontend/src/pages/review-list.tsx`
- Modify: `frontend/src/lib/types.ts`
- Test: `backend/tests/test_main.py` or existing frontend contract test location
- Create/Modify: `frontend/scripts/check-proposal-pdf-review.ts`

- [x] Add a focused UI contract assertion for a `proposal_text_pdf` evaluation with `dimension_mode=none` and a valid v3 score; verify it is classified as scored rather than invalid contract.
- [x] Implement a dedicated PDF document-result branch showing level/score or “PDF 文档级人工复核” with the stored reason; keep generic image dimension validation unchanged.
- [x] Run the TypeScript/Vite build and the focused frontend contract check.

### Task 5: Verify and document delivery

**Files:**
- Modify: `PROJECT_STATUS.md`
- Create: `docs/decisions/0038-proposal-pdf-document-level-grading.md`

- [x] Run backend focused tests, backend full suite, frontend typecheck/build, `compileall`, and `git diff --check`.
- [x] Update project status and add ADR-0038 recording the v2 contract and compatibility boundary.
- [x] Inspect `git status --short` and report exact evidence; do not deploy to production or claim the 100-item regression ran without real model/data evidence.
