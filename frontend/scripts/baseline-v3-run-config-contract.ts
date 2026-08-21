import assert from "node:assert/strict"

import {
  buildBaselineRunPayload,
  isSelectableV3Candidate,
  resolveV3PromptBinding,
  v3CandidateLineage,
  v3RevisionGroup,
} from "../src/features/baseline-regression/baseline-regression-contract.ts"

const active = {
  id: 10,
  category_key: "inspiration_image",
  display_name: "现役合同",
  status: "active",
  revision: 3,
  parent_revision_id: null,
  contract_hash: "a".repeat(64),
  contract: {},
} as const
const candidate = {
  ...active,
  id: 11,
  status: "candidate",
  revision: 4,
  parent_revision_id: active.id,
  contract: { prompt_bindings: { call_a_version: "A-v7", call_b_version: "B-v4" } },
} as const
const retired = { ...candidate, id: 12, status: "retired" } as const
const orphan = { ...candidate, id: 13, parent_revision_id: 999 } as const
const revisions = [candidate, active, retired, orphan]

// Selectability depends on category + lifecycle status only; lineage drift must
// never disable an operator's candidate choice.
assert.equal(isSelectableV3Candidate(candidate, revisions, active.id), true)
assert.equal(isSelectableV3Candidate(retired, revisions, active.id), false)
assert.equal(isSelectableV3Candidate(orphan, revisions, active.id), true)

// Revisions link child -> parent, so a newly activated revision sits at the tip
// of the chain. Candidates branching off the active revision's ancestry stay
// "on chain"; unrelated parents are reported as diverged but remain selectable.
assert.equal(v3CandidateLineage(candidate, revisions, active.id), "on_active_chain")
assert.equal(v3CandidateLineage(orphan, revisions, active.id), "diverged")

// Regression guard for the real model_3d_su shape: V8 was activated from V5, so
// sibling candidates hanging off V5/V2 must stay selectable.
const v2 = { ...active, id: 10, status: "retired", revision: 2, parent_revision_id: null } as const
const v5 = { ...active, id: 15, status: "retired", revision: 5, parent_revision_id: v2.id } as const
const v8Active = { ...active, id: 23, status: "active", revision: 8, parent_revision_id: v5.id } as const
const v6Candidate = { ...candidate, id: 21, revision: 6, parent_revision_id: v5.id } as const
const v7Candidate = { ...candidate, id: 22, revision: 7, parent_revision_id: v6Candidate.id } as const
const v4Candidate = { ...candidate, id: 13, revision: 4, parent_revision_id: v2.id } as const
const realChain = [v2, v5, v8Active, v6Candidate, v7Candidate, v4Candidate]
for (const item of [v6Candidate, v7Candidate, v4Candidate]) {
  assert.equal(isSelectableV3Candidate(item, realChain, v8Active.id), true)
  assert.equal(v3CandidateLineage(item, realChain, v8Active.id), "on_active_chain")
}
assert.equal(v3RevisionGroup(active, active.id), "active")
assert.equal(v3RevisionGroup(candidate, active.id), "candidate")
assert.equal(v3RevisionGroup(retired, active.id), "history")
assert.deepEqual(resolveV3PromptBinding(candidate, "A"), "A-v7")
assert.deepEqual(resolveV3PromptBinding(candidate, "B"), "B-v4")
assert.deepEqual(
  buildBaselineRunPayload({ mode: "active", promptMode: "published" }),
  {},
)
assert.deepEqual(
  buildBaselineRunPayload({
    mode: "candidate",
    candidateRevisionId: candidate.id,
    promptMode: "manual",
    promptAId: 2,
    promptBId: 3,
    categoryKey: "inspiration_image",
  }),
  {
    candidate_revision_id: candidate.id,
    prompt_a_id: 2,
    prompt_b_id: 3,
    category_context: {
      source: "baseline_set",
      category_key: "inspiration_image",
    },
  },
)

console.log("baseline v3 run config contract: ok")
