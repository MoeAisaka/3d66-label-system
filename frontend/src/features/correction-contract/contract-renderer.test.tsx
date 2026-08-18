import {
  correctionNodeOptions,
  correctionNodeValueType,
  groupCorrectionNodes,
} from "./contract-renderer"
import type { CorrectionContractNode } from "./types"

function equal<T>(actual: T, expected: T, message: string) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`)
  }
}

function truthy(value: unknown, message: string) {
  if (!value) throw new Error(message)
}

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
equal(grouped.A.map((node) => node.node_key), ["call_a.title"], "A 节点分组")
equal(grouped.B.map((node) => node.node_key), ["call_b.composition.subject_offset"], "B 节点分组")
equal(grouped.V3.map((node) => node.node_key), ["v3.level_thresholds"], "V3 节点分组")
equal(correctionNodeValueType(nodes[0]), "text", "文本类型")
equal(correctionNodeValueType(nodes[1]), "json", "规则类型")
equal(correctionNodeOptions({ ...nodes[0], type: "enum", options: ["L1", "L2"] }), ["L1", "L2"], "枚举选项")
equal(correctionNodeOptions(nodes[2]), [], "只读节点没有选项")
truthy(true, "renderer contract")

console.log("correction contract renderer contract: red")
