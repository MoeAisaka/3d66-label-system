# Label System Reconstruction Proposal Receipt

**Date:** 2026-08-17

**Branch:** `codex/3d-shadow-dry-run-prep-20260816`

**Base at current documentation update:** `2bdcd553793453678193ad2e043a4ae2d3b8d54d`

**Execution scope:** local documentation and deterministic rehearsal artifacts only.

## Delivered artifacts

| Artifact | Purpose | SHA-256 |
|---|---|---|
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.md` | Editable authoritative business briefing v2.0 and 45-day/Q4 Roadmap | `dc05675e867dccf29c960ea9d5fe8240048222727be8480fd218b982527ffbbb` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.docx` | 13-page visually verified Word handout | `c8e00d435ca19825a1e96ec7a8c23cf2dcc3125a0b33109c4c4dd31fac35ac98` |
| `docs/proposals/2026-08-16-tpeng-label-system-reconstruction-business-brief.pdf` | 13-page business handout PDF | `0acb221fbcd2b5d9c307df2bc289384559523099a1d832e5de32389d9ba210e3` |
| `docs/handoff/2026-08-17-label-system-human-backend-handoff.md` | Human-backend takeover, repository, code map, productionization and release handoff | `55a8658b5b6a7f9c9e7962d4533cad9db93fe33b7de7127d459dbfac176465dd` |
| `docs/handoff/2026-08-17-unpublished-package-ledger.md` | Unpublished branch/worktree inventory, migration conflicts and recommended combined-baseline order | `e5079dab7e7f0ad5e1a9af07bf79bc7ff98be92507fb79da51d80b4d8e12515f` |
| `docs/contracts/2026-08-17-kg-four-batch-target-table-request-v1.md` | Knowledge-graph four-batch and two-target-table DDL request contract | `b4c039ab23e01a80ee96217e470cd5c750daff7b6d81021bf88f18aea66cb9f0` |
| `docs/contracts/2026-08-16-label-system-owner-signoff-register.csv` | Six-role, 12-item owner signoff register | `a275a584ce67465a84c4d7dd7e648e3c8847a29ecf5f85f5ea3b588aab19d808` |
| `docs/contracts/2026-08-16-3d-su-september-closure-rehearsal.md` | September 3D/SU deterministic rehearsal gates | `25f86e9765503474cb1322a345c98b50ad2e97d3c67eef74169d5677d150ee27` |
| `docs/contracts/2026-08-16-downstream-field-projection-contract-v1.md` | Published-fact wide-table/small-table projection boundary | `44750202354f6452d6a9281cece8930b2057029b9589f217b399b8540d791300` |

## Content and boundary checks

- The v2.0 Markdown briefing follows the business narrative order **背景与现状 → 当前痛点 → 解决方案总览 → 方案细节**. It preserves the closed loop, Canonical fact ownership, dual human gates, dual release axes, one wide table plus responsibility small tables, and the Precision >= 80% / Recall >= 70% default field gate.
- The Roadmap is explicit: the 45-day MVP runs from 2026-08-17 through 2026-09-30 and prioritizes the knowledge-graph domestic/overseas whole/single four-batch production-consumption loop; Q4 phase 1 productionizes the platform and completes human-backend takeover; Q4 phase 2 expands modular category routing and downstream Badcase governance.
- `docs/handoff/2026-08-17-label-system-human-backend-handoff.md` identifies the sole Codeup repository, backend/frontend code map, database and migration rules, Git/MR/release rules, 45-day MVP gaps, takeover acceptance, and the first engineering ticket.
- `docs/handoff/2026-08-17-unpublished-package-ledger.md` records every local branch/worktree that still has unique or uncommitted work, including full SHAs, files, migration versions, test evidence, absorption status, conflicts and the recommended combined-baseline order.
- The proposal explicitly excludes Query×asset relevance, ranking weights, recall fusion, online experiments and graph-internal relations from Canonical asset facts.
- The owner signoff register has 12 rows; the Owner confirmed 10 product/data/algorithm/review/platform items as `OWNER_CONFIRMED_PENDING_EVIDENCE`; the two consumer items are `PENDING_DOWNSTREAM_SYNC` pending downstream coordination.
- Placeholder/stale-claim scan found one `生产已就绪` match only in the negated rehearsal safety statement: “不代表生产已就绪”. No `TBD`, `TODO`, “仅评测工具”, “两套独立项目”, or unsupported completed-production claim was found.
- Nowledge claim review examined 20 claims. Three markers point to superseded 2026-07/08 historical notes, not a conflict with the current frozen unified-LabelLab positioning; no historic implementation result is used as evidence that real ingress is ready.

## Visual QA

- The v2.0 PDF was rendered and inspected across all 13 Letter pages. Chinese text, background/current-state and pain-point sections, Roadmap tables, knowledge-graph four-batch routes, hierarchy and long identifiers are legible; no clipping, overlap, missing glyph or blank page was observed.
- The original text DOCX route was rejected after headless LibreOffice rendered Chinese as blank/square glyphs despite explicit CJK font settings. The final DOCX is therefore a visual handout containing one reviewed PDF page render per Word page. It has 13 inline images and rendered back to 13 clean pages at `/private/tmp/labellab-proposal-qa.IePCS2/` on 2026-08-17.
- Markdown is the editable source of truth for future content changes; regenerate the PDF and visual Word handout after a Markdown change. This renderer workaround does not alter scope, authority, or product requirements.

## Regression and repository checks

- `git diff --check`: passed.
- `DATA_DIR=$(mktemp -d) PYTHONPATH=. uv run --with-requirements backend/requirements.txt python -m pytest -q backend/tests/test_three_d_readiness.py backend/tests/test_source_identity_probe.py`: `15 passed in 0.11s`.
- Fresh focused 3D/SU readiness, source identity, semantic contracts/mapping, Profile, Shadow projection, workflow fixture and migration suite: `126 passed, 1 warning in 18.04s`.
- Fresh backend full suite with an isolated temporary `DATA_DIR`: `1514 passed, 1 skipped, 6 warnings in 128.58s`; warnings are existing dependency deprecations.
- Fresh frontend `contract:three-d-dry-run`, `contract:three-d-readiness` and `contract:tag-demand`: passed. The readiness contract first failed on the superseded `res_type=1` assertion, then passed after aligning it to the frozen domestic `res_type in (1,6)` source contract.
- Fresh frontend TypeScript lint: passed.
- Fresh Vite production build: passed; only the existing main-chunk size advisory remains.
- Proposal Python builders compiled successfully and the Node print helper passed `node --check`.
- This Mac's `git ls-remote` attempt returned `Permission denied (publickey)`; the combined coordinator subsequently confirmed online on 2026-08-17 that Codeup `main` remains `50e5b1572dd3ea5b65a7641ca50ae32fd850df07`, matching the local `origin/main` snapshot.

## Explicitly not performed

- No real source or business database connection.
- No DataWorks/ODPS query or DML.
- No real model call, token access, permission request, external contact, push, merge, or deployment.
- No automatic candidate activation, label-fact publishing, stock overwrite, or change to the frozen 3D/SU readiness contract.

## Remaining owner action before a real 3D/SU run

The Owner has confirmed the non-consumer scope. Remaining actions are to archive the 10 confirmation evidence packages, synchronize the two downstream consumer items, and separately freeze any real source access, model calls, target-table DML, gray release and rollback. Until those gates are complete, retain `pending_external_signoff` and do not interpret this package as real-ingress authorization.
