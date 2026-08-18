import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
  correctionNodeOptions,
  correctionNodeValueType,
  groupCorrectionNodes,
} from "../src/features/correction-contract/contract-renderer.ts"
import type { CorrectionContractNode } from "../src/features/correction-contract/types.ts"

const rendererSource = readFileSync("src/features/correction-contract/contract-renderer.tsx", "utf8")
assert.match(rendererSource, /onSubmit\?:/)
assert.match(rendererSource, /保存合同纠偏/)

const nodes: CorrectionContractNode[] = [
  {
    node_key: "call_a.title",
    layer: "A",
    path: "call_a.title",
    label: "素材标题",
    description: "冻结素材标题",
    type: "text",
    semantic_version: "1",
    compatibility_key: "title",
    required: true,
    evidence: { description: "需要图片证据", required: true },
  },
  {
    node_key: "call_b.composition.subject_offset",
    layer: "B",
    path: "dimension.composition.hit_rules.subject_offset",
    label: "构图秩序：主体偏移",
    description: "主体明显偏移",
    type: "rule_hit",
    semantic_version: "1",
    compatibility_key: "subject-offset",
    required: false,
    evidence: { description: "需要规则证据" },
  },
  {
    node_key: "v3.level_thresholds",
    layer: "V3",
    path: "scoring.level_thresholds",
    label: "等级分数阈值",
    description: "冻结阈值",
    type: "list",
    semantic_version: "1",
    compatibility_key: "thresholds",
    required: true,
    evidence: { description: "仅供查看" },
    metadata: { editable: false, frozen_value: [{ min_score: 90, level: "L1" }] },
  },
]

const grouped = groupCorrectionNodes(nodes)
assert.deepEqual(grouped.A.map((node) => node.node_key), ["call_a.title"])
assert.deepEqual(grouped.B.map((node) => node.node_key), ["call_b.composition.subject_offset"])
assert.deepEqual(grouped.V3.map((node) => node.node_key), ["v3.level_thresholds"])
assert.equal(correctionNodeValueType(nodes[0]), "text")
assert.equal(correctionNodeValueType(nodes[1]), "json")
assert.deepEqual(correctionNodeOptions({ ...nodes[0], type: "enum", options: ["L1", "L2"] }), ["L1", "L2"])
assert.deepEqual(correctionNodeOptions(nodes[2]), [])

console.log("correction contract renderer contract: passed")
