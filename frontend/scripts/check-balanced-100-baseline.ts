import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8")
const pageSource = readFileSync(new URL("../src/pages/baseline-regression-page.tsx", import.meta.url), "utf8")
const rebuildSource = readFileSync(
  new URL("../src/features/baseline-regression/balanced-rebuild-drawer.tsx", import.meta.url),
  "utf8",
)

assert.match(apiSource, /inspiration-balanced-100/)
assert.match(pageSource, /L1-L5 各 20 张/)
assert.match(pageSource, /selectedCategoryKey === "inspiration_image"/)

// 这个接口是幂等的：集合冻结过一次之后，再点只切换选中、不新建任何东西。按钮
// 曾经叫「生成 100 张均衡基准集」，运营点了看不到变化就以为功能坏了。文案必须
// 说「切换」，并且在幂等分支里明说本次没有新建。
assert.doesNotMatch(pageSource, /生成 100 张均衡基准集/)
assert.match(pageSource, /切换到均衡基准集/)
assert.match(pageSource, /已切换到均衡基准集/)
assert.match(pageSource, /本次没有新建/)

// 冻结样本不能原地改写（历史回归引用着它），所以纳入新标注素材只能重建成新集合。
assert.match(apiSource, /inspiration-balanced-sample\/rebuild-survey/)
assert.match(apiSource, /inspiration-balanced-sample\/rebuild/)
assert.match(apiSource, /surveyBalancedRebuild/)
assert.match(apiSource, /rebuildBalancedSample/)
assert.match(pageSource, /重建均衡样本/)
assert.match(pageSource, /BalancedRebuildDrawer/)
assert.match(pageSource, /BalancedRebuildForm/)

// 重建前必须先让运营看见能抽多少，以及现有样本漏掉了什么，否则无法判断值不值得
// 新建一个集合。
assert.match(rebuildSource, /max_per_level/)
assert.match(rebuildSource, /selectable_distribution/)
assert.match(rebuildSource, /已跑过/)
assert.match(rebuildSource, /永远进不了这份样本/)

// 三种抽样方式都要在界面上说清各自口径；默认必须是全局均匀抽样，因为原样本正是
// 按 asset_id 升序抽的，只覆盖最早那批上传。
assert.match(rebuildSource, /stable_hash/)
assert.match(rebuildSource, /newest/)
assert.match(rebuildSource, /oldest/)
assert.match(pageSource, /useState<BalancedRebuildStrategy>\("stable_hash"\)/)

// 种子只对全局均匀抽样有效；按时间取的两种方式完全由上传顺序决定，那时不该显示
// 一个改了也没用的输入框。
assert.match(rebuildSource, /strategy === "stable_hash" && <label>/)

console.log("balanced baseline sample contract ok")
