import assert from "node:assert/strict"

import {
  buildBaselineRunPayload,
  isSelectableV3Candidate,
  resolveV3PromptBinding,
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

assert.equal(isSelectableV3Candidate(candidate, revisions, active.id), true)
assert.equal(isSelectableV3Candidate(retired, revisions, active.id), false)
assert.equal(isSelectableV3Candidate(orphan, revisions, active.id), false)
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
  }),
  { candidate_revision_id: candidate.id, prompt_a_id: 2, prompt_b_id: 3 },
)

console.log("baseline v3 run config contract: ok")
