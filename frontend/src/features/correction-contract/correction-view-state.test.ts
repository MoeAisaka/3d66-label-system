import type { CorrectionView } from "./types"
import {
  correctionDraftFromView,
  correctionSubmissionPayload,
  mergeCorrectionResponse,
  updateCorrectionDraft,
} from "./correction-view-state.ts"

function equal<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`)
  }
}

const view: CorrectionView = {
  schema_version: "correction-view-v1",
  lane: "baseline",
  run_id: 11,
  item_id: 22,
  evaluation_id: 33,
  category_key: "inspiration_image",
  snapshot_status: "frozen",
  read_only: false,
  contract: {
    contract_version: "2026.08.18.1",
    contract_hash: "hash-1",
    category_key: "inspiration_image",
  },
  review_revision: 4,
  nodes: [
    {
      node_key: "call_a.title",
      layer: "A",
      path: "call_a.title",
      label: "标题",
      description: "标题字段",
      type: "text",
      semantic_version: "1",
      compatibility_key: "title",
      required: true,
      evidence: { description: "图片证据", required: true },
      current_value: "旧标题",
      human_value: "人工标题",
      reason: "已有人工判断",
      evidence_entries: [{ text: "画面可见" }],
    },
    {
      node_key: "v3.final_level",
      layer: "V3",
      path: "final_level",
      label: "最终等级",
      description: "服务端重算",
      type: "enum",
      options: ["L1", "L2", "L3", "L4", "L5"],
      semantic_version: "1",
      compatibility_key: "final-level",
      required: true,
      evidence: { description: "等级依据", required: true },
      current_value: "L3",
      model_value: "L3",
    },
  ],
}

const draft = correctionDraftFromView(view)
equal(draft["call_a.title"]?.value, "人工标题", "优先回填人工值")
equal(draft["call_a.title"]?.reason, "已有人工判断", "回填人工理由")
equal(draft["call_a.title"]?.evidence, [{ text: "画面可见" }], "回填人工证据")
equal(draft["call_a.title"]?.dirty, false, "历史值初始不脏")

const changed = updateCorrectionDraft(draft, "v3.final_level", {
  value: "L2",
  reason: "边界证据",
  evidence: [{ text: "等级边界可见" }],
})
equal(changed["v3.final_level"]?.dirty, true, "修改节点标记为脏")
const payload = correctionSubmissionPayload(changed, view, "idem-1")
equal(payload.contract_hash, "hash-1", "提交沿用合同哈希")
equal(payload.review_revision, 4, "提交沿用修订号")
equal(payload.idempotency_key, "idem-1", "提交携带幂等键")
equal(payload.nodes.map((node) => node.node_key), ["v3.final_level"], "只提交被修改节点")

const response: CorrectionView = {
  ...view,
  review_revision: 5,
  nodes: view.nodes.map((node) => node.node_key === "v3.final_level"
    ? { ...node, human_value: "L2", reason: "边界证据", evidence_entries: [{ text: "等级边界可见" }] }
    : node),
}
const merged = mergeCorrectionResponse(changed, response)
equal(merged["v3.final_level"]?.value, "L2", "保存后读取服务端人工值")
equal(merged["v3.final_level"]?.reason, "边界证据", "保存后读取服务端理由")
equal(merged["v3.final_level"]?.dirty, false, "保存响应清除脏状态")

console.log("correction view state contract: passed")
