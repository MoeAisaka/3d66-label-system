import type {
  CorrectionContractNode,
  CorrectionLayer,
  CorrectionNodeGroup,
  CorrectionNodeValue,
} from "./types"

const layerOrder: readonly CorrectionLayer[] = ["A", "B", "V3"]

export function groupCorrectionNodes(
  nodes: readonly CorrectionContractNode[],
): CorrectionNodeGroup {
  const grouped: CorrectionNodeGroup = { A: [], B: [], V3: [] }
  nodes.forEach((node, index) => {
    if (!grouped[node.layer]) return
    grouped[node.layer].push({ ...node, order: node.order ?? index })
  })
  for (const layer of layerOrder) {
    grouped[layer].sort((left, right) => (left.order ?? 0) - (right.order ?? 0))
  }
  return grouped
}

export function correctionNodeOptions(node: CorrectionContractNode): unknown[] {
  const options = node.options ?? node.allowed_values ?? node.values
  return Array.isArray(options) ? options : []
}

export function correctionNodeValueType(
  node: Pick<CorrectionContractNode, "type">,
): "text" | "number" | "boolean" | "json" | "enum" {
  const type = String(node.type || "text").toLowerCase()
  if (type === "enum" || type === "enumeration") return "enum"
  if (type === "boolean" || type === "bool") return "boolean"
  if (
    type === "integer"
    || type === "int"
    || type === "number"
    || type === "float"
    || type === "decimal"
    || type.startsWith("integer_")
    || type.startsWith("number_")
    || type.startsWith("float_")
  ) return "number"
  if (type === "list" || type === "array" || type === "object" || type === "json_object") return "json"
  if (type === "rule_hit" || type === "rule_judgement" || type === "rule_judgment") return "json"
  if (type === "text" || type === "string") return "text"
  return "json"
}

export function correctionNodeEditable(node: CorrectionContractNode): boolean {
  return node.editable !== false && node.metadata?.editable !== false && !node.read_only
}

export function correctionNodeDisplayValue(
  node: CorrectionContractNode,
  value: CorrectionNodeValue,
): CorrectionNodeValue {
  if (value !== undefined && value !== null) return value
  if (node.metadata && Object.prototype.hasOwnProperty.call(node.metadata, "frozen_value")) {
    return node.metadata.frozen_value
  }
  return value
}

export function correctionNodeInputValue(
  node: CorrectionContractNode,
  value: CorrectionNodeValue,
): string {
  const displayValue = correctionNodeDisplayValue(node, value)
  if (displayValue === undefined || displayValue === null) return ""
  if (typeof displayValue === "string") return displayValue
  if (typeof displayValue === "number" || typeof displayValue === "boolean") return String(displayValue)
  return JSON.stringify(displayValue, null, 2)
}

export function parseCorrectionNodeInput(
  node: CorrectionContractNode,
  rawValue: string,
): CorrectionNodeValue {
  const valueType = correctionNodeValueType(node)
  if (valueType === "text" || valueType === "enum") return rawValue
  if (valueType === "number") {
    const value = Number(rawValue)
    return Number.isFinite(value) ? value : rawValue
  }
  if (valueType === "boolean") return rawValue === "true"
  try {
    return JSON.parse(rawValue)
  } catch {
    return rawValue
  }
}
