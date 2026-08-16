# Label System Reconstruction Proposal Receipt

**Date:** 2026-08-16

**Branch:** `codex/3d-shadow-dry-run-prep-20260816`

**Base at start:** `31a5dbeb291704196fc75b3deb3c18ac860d7fd3`

**Execution scope:** local documentation and deterministic rehearsal artifacts only.

## Delivered artifacts

| Artifact | Purpose | SHA-256 |
|---|---|---|
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md` | Editable authoritative business briefing v1.1 | `85e4aeec5290da48caecc1c00919082953b5e7f4cc2024c32e7b5fa94eab93b8` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx` | Eight-page visually verified Word handout | `daf7eda7ef56b2c16b72681323cf8dd9d7dfce30dd5dfb3dc24ec5317c8d6e27` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.pdf` | Eight-page business handout PDF | `f658f050210b45b134990cb3d52ede654fce962c9e28481513b911d8eae0f7ce` |
| `docs/contracts/2026-08-16-label-system-owner-signoff-register.csv` | Six-role, 12-item owner signoff register | `a275a584ce67465a84c4d7dd7e648e3c8847a29ecf5f85f5ea3b588aab19d808` |
| `docs/contracts/2026-08-16-3d-su-september-closure-rehearsal.md` | September 3D/SU deterministic rehearsal gates | `25f86e9765503474cb1322a345c98b50ad2e97d3c67eef74169d5677d150ee27` |
| `docs/contracts/2026-08-16-downstream-field-projection-contract-v1.md` | Published-fact wide-table/small-table projection boundary | `44750202354f6452d6a9281cece8930b2057029b9589f217b399b8540d791300` |

## Content and boundary checks

- The v1.1 Markdown briefing now follows the business narrative order **背景与现状 → 核心痛点 → 解决方案总览 → 方案细节**. It has 40 headings and one Mermaid architecture diagram, and preserves the closed loop, Canonical fact ownership, dual human gates, dual release axes, the 3D/SU September vertical slice, one wide table plus responsibility small tables, and the Precision >= 80% / Recall >= 70% default field gate.
- The proposal explicitly excludes Query×asset relevance, ranking weights, recall fusion, online experiments and graph-internal relations from Canonical asset facts.
- The owner signoff register has 12 rows; the Owner confirmed 10 product/data/algorithm/review/platform items as `OWNER_CONFIRMED_PENDING_EVIDENCE`; the two consumer items are `PENDING_DOWNSTREAM_SYNC` pending downstream coordination.
- Placeholder/stale-claim scan found one `生产已就绪` match only in the negated rehearsal safety statement: “不代表生产已就绪”. No `TBD`, `TODO`, “仅评测工具”, “两套独立项目”, or unsupported completed-production claim was found.
- Nowledge claim review examined 20 claims. Three markers point to superseded 2026-07/08 historical notes, not a conflict with the current frozen unified-LabelLab positioning; no historic implementation result is used as evidence that real ingress is ready.

## Visual QA

- The v1.1 PDF was rendered and inspected across all eight Letter pages. Chinese text, the new background/current-state and pain-point sections, tables, hierarchy, footer/page numbers and long-path wrapping are legible; no clipping, overlap or missing glyph was observed.
- The original text DOCX route was rejected after headless LibreOffice rendered Chinese as blank/square glyphs despite explicit CJK font settings. The final DOCX is therefore a visual handout containing one reviewed PDF page render per Word page. It has eight inline images, no tables, and rendered back to eight clean pages at `/tmp/tpeng-proposal-docx-v11b.l6KeD8/`.
- Markdown is the editable source of truth for future content changes; regenerate the PDF and visual Word handout after a Markdown change. This renderer workaround does not alter scope, authority, or product requirements.

## Regression and repository checks

- `git diff --check`: passed.
- `DATA_DIR=$(mktemp -d) PYTHONPATH=. uv run --with-requirements backend/requirements.txt python -m pytest -q backend/tests/test_three_d_readiness.py backend/tests/test_source_identity_probe.py`: `15 passed in 0.11s`.

## Explicitly not performed

- No real source or business database connection.
- No DataWorks/ODPS query or DML.
- No real model call, token access, permission request, external contact, push, merge, or deployment.
- No automatic candidate activation, label-fact publishing, stock overwrite, or change to the frozen 3D/SU readiness contract.

## Remaining owner action before a real 3D/SU run

The Owner has confirmed the non-consumer scope. Remaining actions are to archive the 10 confirmation evidence packages, synchronize the two downstream consumer items, and separately freeze any real source access, model calls, target-table DML, gray release and rollback. Until those gates are complete, retain `pending_external_signoff` and do not interpret this package as real-ingress authorization.
