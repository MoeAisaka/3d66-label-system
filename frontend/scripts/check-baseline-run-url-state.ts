import assert from "node:assert/strict"

import { baselineRunIdAfterSetLoad } from "../src/features/baseline-regression/baseline-regression-contract.ts"

assert.equal(
  baselineRunIdAfterSetLoad(29, [{ id: 30 }], 29),
  29,
  "a run pinned by the URL must survive loading an unrelated default baseline set",
)
assert.equal(
  baselineRunIdAfterSetLoad(0, [{ id: 30 }], 29),
  29,
  "the URL run must win when the default baseline set loads first",
)

console.log("baseline run URL state contract: passed")
