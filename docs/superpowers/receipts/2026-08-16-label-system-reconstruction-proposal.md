# Label System Reconstruction Proposal Receipt

**Date:** 2026-08-16

**Branch:** `codex/3d-shadow-dry-run-prep-20260816`

**Base at start:** `31a5dbeb291704196fc75b3deb3c18ac860d7fd3`

**Execution scope:** local documentation and deterministic rehearsal artifacts only.

## Delivered artifacts

| Artifact | Purpose | SHA-256 |
|---|---|---|
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md` | Editable authoritative business briefing | `b689e304eae6ebff5d96948806791801544b4c86dc2272b83cfda0bdb5c8e1a3` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx` | Eight-page visually verified Word handout | `85741171606b7876654dd9984dcdbcd5f9c51669b11efa9e899ea0503e30fe11` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.pdf` | Eight-page business handout PDF | `478043e4193e8fa10687893d0e79d7b5a0a2261b85d05661a2588f7580520778` |
| `docs/contracts/2026-08-16-label-system-owner-signoff-register.csv` | Six-role, 12-item owner signoff register | `1617194a71f3cb7bae772453ca37a888c463832331dff3636bf1268be5a81c1a` |
| `docs/contracts/2026-08-16-3d-su-september-closure-rehearsal.md` | September 3D/SU deterministic rehearsal gates | `25f86e9765503474cb1322a345c98b50ad2e97d3c67eef74169d5677d150ee27` |
| `docs/contracts/2026-08-16-downstream-field-projection-contract-v1.md` | Published-fact wide-table/small-table projection boundary | `44750202354f6452d6a9281cece8930b2057029b9589f217b399b8540d791300` |

## Content and boundary checks

- The Markdown briefing has 32 headings and one Mermaid architecture diagram. It explicitly preserves the closed loop, Canonical fact ownership, dual human gates, dual release axes, the 3D/SU September vertical slice, one wide table plus responsibility small tables, and the Precision >= 80% / Recall >= 70% default field gate.
- The proposal explicitly excludes Query×asset relevance, ranking weights, recall fusion, online experiments and graph-internal relations from Canonical asset facts.
- The owner signoff register has 12 rows; role groups are exactly `product_label`, `data`, `algorithm`, `review`, `platform` and `consumer`; every entry is `UNASSIGNED`.
- Placeholder/stale-claim scan found one `生产已就绪` match only in the negated rehearsal safety statement: “不代表生产已就绪”. No `TBD`, `TODO`, “仅评测工具”, “两套独立项目”, or unsupported completed-production claim was found.
- Nowledge claim review examined 20 claims. Three markers point to superseded 2026-07/08 historical notes, not a conflict with the current frozen unified-LabelLab positioning; no historic implementation result is used as evidence that real ingress is ready.

## Visual QA

- The PDF was rendered and inspected across all eight Letter pages. Chinese text, tables, hierarchy, footer/page numbers and long-path wrapping are legible; no clipping, overlap or missing glyph was observed.
- The original text DOCX route was rejected after headless LibreOffice rendered Chinese as blank/square glyphs despite explicit CJK font settings. The final DOCX is therefore a visual handout containing one reviewed PDF page render per Word page. It has eight inline images, no tables, and rendered back to eight clean pages at `/tmp/tpeng-proposal-docx.4O3rMc/`.
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

Assign and complete the six owner groups’ signoff for: platform/category fields, source window and identity probe, model/mechanism and quality thresholds, golden truth and review rules, queue/projection/recovery rehearsal, and downstream table mapping/reconciliation/Badcase return. Until then, retain `pending_external_signoff` and do not interpret the local package as real-ingress authorization.
