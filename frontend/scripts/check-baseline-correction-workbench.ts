import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
  dimensionGradeOptions,
  levelForMinimumScore,
} from "../src/lib/level-thresholds.ts"
import { nextPendingCorrectionId } from "../src/features/baseline-regression/correction-navigation.ts"

const apiSource = readFileSync("src/lib/api.ts", "utf8")
const formSource = readFileSync("src/pages/review-correction-form.tsx", "utf8")
const pageSource = readFileSync("src/pages/baseline-regression-page.tsx", "utf8")
const workbenchSource = readFileSync("src/features/baseline-regression/correction-workbench.tsx", "utf8")

assert.match(apiSource, /review-panel\/reopen/)
assert.match(formSource, /initialCorrections/)
assert.match(pageSource, /再次修改/)
assert.match(pageSource, /下一个/)
assert.match(workbenchSource, /xl:grid-cols-\[minmax\(0,1fr\)_420px\]/)
assert.match(workbenchSource, /ImagePreviewButton/)
assert.match(workbenchSource, /max-w-full/)
assert.match(workbenchSource, /onPreview=\{onPreview\}/)
assert.doesNotMatch(workbenchSource, /<img src=\{item\.image_url\} alt=\{item\.asset\.name\} className=\"max-h-\[72vh\] w-full object-contain\" \/>/)
assert.match(pageSource, /<div className=\"grid grid-cols-1 gap-4\">\s*<LevelExplanation/)
assert.doesNotMatch(pageSource, /<div className=\"grid gap-4 md:grid-cols-2\">\s*<LevelExplanation/)
assert.match(workbenchSource, /v3_context/)
assert.equal(
  nextPendingCorrectionId([
    { id: 1, review_stage: "initial" },
    { id: 2, review_stage: "completed" },
    { id: 3, review_stage: "secondary" },
  ], 1),
  3,
)
assert.equal(
  nextPendingCorrectionId([
    { id: 1, review_stage: "initial" },
    { id: 2, review_stage: "completed" },
  ], 1),
  null,
)
assert.equal(
  levelForMinimumScore(90.58, { L1: 90, L2: 80, L3: 76, L4: 60, L5: 0 }),
  "L1",
)
assert.equal(
  levelForMinimumScore(83.56, { L1: 90, L2: 80, L3: 76, L4: 60, L5: 0 }),
  "L2",
)
assert.deepEqual(
  dimensionGradeOptions.map((option) => option.grade),
  [5, 4, 3, 2, 1],
)

console.log("baseline correction workbench contract: passed")
