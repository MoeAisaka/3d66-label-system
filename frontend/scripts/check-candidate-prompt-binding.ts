import assert from "node:assert/strict"

import {
  resolveCandidatePromptBinding,
} from "../src/features/baseline-regression/baseline-regression-contract.ts"

const prompts = [
  {
    id: 10,
    stage: "A" as const,
    version: "candidate-a-v3",
    status: "published" as const,
    pipeline_scope: "shared" as const,
  },
  {
    id: 11,
    stage: "B" as const,
    version: "candidate-b-v3",
    status: "archived" as const,
    pipeline_scope: "shared" as const,
  },
  {
    id: 12,
    stage: "B" as const,
    version: "full-pipeline-b-v3",
    status: "published" as const,
    pipeline_scope: "full_pipeline" as const,
  },
]

assert.deepEqual(
  resolveCandidatePromptBinding(prompts, "A", "candidate-a-v3"),
  { status: "available", promptId: 10, requestedVersion: "candidate-a-v3" },
)
assert.deepEqual(
  resolveCandidatePromptBinding(prompts, "B", "candidate-b-v3"),
  { status: "unavailable", promptId: 11, requestedVersion: "candidate-b-v3", reason: "archived" },
)
assert.deepEqual(
  resolveCandidatePromptBinding(prompts, "B", "full-pipeline-b-v3"),
  { status: "unavailable", promptId: 12, requestedVersion: "full-pipeline-b-v3", reason: "pipeline_scope" },
)
assert.deepEqual(
  resolveCandidatePromptBinding(prompts, "A", "missing-a-v3"),
  { status: "unavailable", promptId: null, requestedVersion: "missing-a-v3", reason: "missing" },
)

console.log("candidate prompt binding contract: passed")
