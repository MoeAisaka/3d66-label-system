# Inspiration Hard-Defect Recall Rev4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task.

**Goal:** Publish an isolated inspiration-image rev4 that recalls auditable decisive Call-A signals, applies monotonic versioned hard-defect severity caps, preserves rev3 replay, and proves the effect on baseline set 6.

**Architecture:** Keep rev3 behavior behind common-modifiers-v1 and add common-modifiers-v2 only to the new inspiration contract. The adapter normalizes a minimum authoritative precheck and marks missing, uncertain, or contradictory decisive signals for fail-closed handling. Prompt and contract seeding append immutable versions; sibling categories keep their existing capability set and revision.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic, pytest, React/TypeScript/Vite, guarded Git-bundle deployment.

---

### Task 1: Freeze run-14 evidence and prove RED

**Files:**
- Create: backend/tests/fixtures/inspiration_run14_hard_defects.json
- Modify: backend/tests/test_dimension_deduction_aggregator.py
- Modify: backend/tests/test_inspiration_call_a_adapter.py
- Modify: backend/tests/test_prompt_loader.py
- Modify: backend/tests/test_worker_calibration.py

- [ ] Add 7987, 8000, 8001, and 8003 raw A plus normalized B fixtures.
- [ ] Assert rev3 remains 72/L2, 79/L2, 79/L2, 79/L2.
- [ ] Assert rev4 maps their Tier-A defects to 20/L5 and is monotonic around 79/80.
- [ ] Assert three Tier-B hits escalate, one Tier-B caps at 60, corner watermark records only, and large/subject-obscuring watermarks cap at 20.
- [ ] Assert reason/image_defects/evidence normalize, conflicts or missing fields need review, and hard_defects are preserved.
- [ ] Assert prompt mandatory fields and the minimum worker contract.
- [ ] Run selected tests and record expected missing-feature failures.

Run: /Users/yukina/OpenClaw/labellab-adr33-framework/.venv/bin/python -X utf8 -m pytest backend/tests/test_dimension_deduction_aggregator.py backend/tests/test_inspiration_call_a_adapter.py backend/tests/test_prompt_loader.py backend/tests/test_worker_calibration.py -q

### Task 2: Versioned severity and monotonic aggregation

**Files:**
- Modify: backend/app/category_evaluation_contract.py
- Modify: backend/app/inspiration_category_seed.py
- Modify: backend/app/category_evaluation_aggregator.py

- [ ] Preserve a rev3 builder using common-modifiers-v1.
- [ ] Emit rev4 with common-modifiers-v2 tiers/actions, source-qualified rules, known-real-photo modifier, and Tier-B escalation.
- [ ] Validate both schemas fail-closed.
- [ ] Resolve hard_defects/image_defects through the frozen table and apply min(current_score, cap_to).
- [ ] Record rules, tier, escalation, modifier, and action; run scoring tests GREEN.

### Task 3: Append Call-A rev4 and fail closed on drift

**Files:**
- Create: prompts/inspiration_image_call_a_rev3.txt
- Modify: prompts/inspiration_image_call_a.txt
- Modify: backend/app/schema_adapter.py
- Modify: backend/app/worker.py
- Modify: backend/app/worker_v3_authoritative.py
- Modify: frontend/src/lib/types.ts

- [ ] Keep four booleans and hard_defects; add reason, three watermark types, evidence, decision_status, and uncertain_fields.
- [ ] Normalize without overwriting hard_defects; enforce reason/boolean and hit/evidence consistency.
- [ ] Store decisive_signal_validation; rev4 fails closed when invalid while rev3 remains compatible.
- [ ] Inject only the minimum authoritative contract for inspiration baselines.
- [ ] Add frontend types without changing lightbox.

### Task 4: Freeze actual dynamic Call-B identity

**Files:**
- Modify: backend/app/dimension_deduction_bridge.py
- Modify: backend/tests/test_multimodal_deduction_bridge.py
- Modify: backend/tests/test_worker_v3_authoritative.py
- Modify: backend/tests/test_baseline_regression.py

- [ ] Persist a template version and SHA-256 for generated system/user prompts in success and fallback.
- [ ] Verify fallback warning/needs_review and normal path identity.
- [ ] Verify snapshots retain raw A/B, normalized structures, prompt identity, and versions.

### Task 5: Seed rev4 without rewriting history or siblings

**Files:**
- Modify: backend/app/seed.py
- Modify: backend/tests/test_inspiration_seed_persistence.py
- Modify: backend/tests/v3_contract_fixtures.py

- [ ] Append a shared Call-A PromptVersion; never update prior rows.
- [ ] Increment inspiration config once and keep it active.
- [ ] Seed sibling clones from legacy capability; existing sibling rows remain unchanged.
- [ ] Assert byte-stable rev3 replay and immutable old prompt content.

### Task 6: Verify and commit

- [ ] Run related/full backend, compileall, diff check, credential scan.
- [ ] Run three frontend contracts, lint, and build.
- [ ] Update PROJECT_STATUS.md and ADR-0034.
- [ ] Commit only task files on feat/v3-only-category-contracts.

### Task 7: Guarded deploy, fixed baseline, report

- [ ] Verify/upload a single-ref bundle and invoke the guarded sudo deploy command.
- [ ] Verify health 200, healthy container, one parent plus eight workers.
- [ ] Create a structured API run for baseline_set_id=6 with new A and unchanged B.
- [ ] Compare run 14/new run metrics, matrices, 15 severe errors, guardrail, and detection rates.
- [ ] Prove rev3 replay and sibling revisions unchanged.
- [ ] Write /Users/Shared/OpenClaw/125-实现-标签实验台硬伤分级与召回修复-20260805/README.md.
