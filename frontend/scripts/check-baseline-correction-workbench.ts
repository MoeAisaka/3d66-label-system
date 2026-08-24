import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
  dimensionGradeOptions,
  levelForMinimumScore,
} from "../src/lib/level-thresholds.ts"
import {
  nextPendingCorrectionId,
  previousCorrectionId,
} from "../src/features/baseline-regression/correction-navigation.ts"

const apiSource = readFileSync("src/lib/api.ts", "utf8")
const formSource = readFileSync("src/pages/review-correction-form.tsx", "utf8")
// 2026-08-24：基准回归页从 3250 行拆成 baseline-regression-page.tsx + features/baseline-regression/* 若干模块。
// 契约要守的是「基准回归这块界面整体」符合合同，而不是内容挤在同一个文件里，
// 所以这里把页面与抽出的模块拼起来一起校验：断言语义不变，拆分也不会让合同失效。
const pageSource = [
  readFileSync("src/pages/baseline-regression-page.tsx", "utf8"),
  ...[
  "regression-page-shared.tsx",
  "regression-results.tsx",
  "correction-analysis-panel.tsx",
  "level-explanation.tsx",
  "field-metrics-evidence.tsx",
  "form-selects.tsx",
  "correction-stage-meta.tsx",
  ].map((f) => readFileSync(`src/features/baseline-regression/${f}`, "utf8")),
].join("\n")
const workbenchSource = readFileSync("src/features/baseline-regression/correction-workbench.tsx", "utf8")
const imageLightboxSource = readFileSync("src/components/image-lightbox.tsx", "utf8")

assert.match(apiSource, /review-panel\/reopen/)
assert.match(formSource, /initialCorrections/)
assert.match(formSource, /initialNote/)
assert.match(formSource, /setOverallNote\(initialNote\)/)
assert.match(formSource, /initialDimensionCorrections/)
assert.match(formSource, /initialKeyFieldCorrections/)
assert.match(formSource, /correctionChanged/)
assert.match(pageSource, /再次修改/)
assert.match(pageSource, /上一条/)
assert.match(pageSource, /previousCorrectionId/)
assert.match(pageSource, /reopenSeeds.*corrections/)
assert.match(pageSource, /reopenSeeds.*note/)
assert.doesNotMatch(pageSource, /const nextId = nextPendingCorrectionId\(items, variables\.item\.id\)/)
assert.match(workbenchSource, /xl:grid-cols-\[minmax\(280px,360px\)_minmax\(0,1fr\)\]/)
assert.match(workbenchSource, /ImagePreviewButton/)
assert.match(workbenchSource, /ImageReferenceDock/)
assert.match(workbenchSource, /上一条/)
assert.match(workbenchSource, /下一条/)
assert.match(workbenchSource, /window\.confirm/)
assert.match(workbenchSource, /requestNavigation\(onPrevious\)/)
assert.match(workbenchSource, /requestNavigation\(onNext\)/)
assert.match(workbenchSource, /referenceOpen/)
assert.match(imageLightboxSource, /原图参考浮窗/)
assert.match(workbenchSource, /max-w-full/)
assert.match(workbenchSource, /onPreview=\{openReference\}/)
assert.doesNotMatch(workbenchSource, /<img src=\{item\.image_url\} alt=\{item\.asset\.name\} className=\"max-h-\[72vh\] w-full object-contain\" \/>/)
assert.match(pageSource, /<div className=\"grid grid-cols-1 gap-4\">\s*<LevelExplanation/)
assert.doesNotMatch(pageSource, /<div className=\"grid gap-4 md:grid-cols-2\">\s*<LevelExplanation/)
assert.match(workbenchSource, /v3_context/)
assert.match(workbenchSource, /NodeCorrectionEditor[\s\S]*children/)
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
  previousCorrectionId([
    { id: 1, review_stage: "initial" },
    { id: 2, review_stage: "completed" },
    { id: 3, review_stage: "initial" },
  ], 3),
  2,
)
assert.equal(
  previousCorrectionId([
    { id: 1, review_stage: "initial" },
    { id: 2, review_stage: "initial" },
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
