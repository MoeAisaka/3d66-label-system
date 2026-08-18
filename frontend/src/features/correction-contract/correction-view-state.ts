import type {
  CorrectionDraft,
  CorrectionDraftNode,
  CorrectionNodeValue,
  CorrectionSubmissionRequest,
  CorrectionView,
} from "./types"

function valueFromNode(node: CorrectionView["nodes"][number]): CorrectionNodeValue {
  if (node.human_value !== undefined && node.human_value !== null) return node.human_value
  if (node.current_value !== undefined && node.current_value !== null) return node.current_value
  return node.model_value
}

function evidenceFromNode(node: CorrectionView["nodes"][number]): Array<Record<string, unknown>> {
  if (Array.isArray(node.evidence_entries)) return node.evidence_entries.map((item) => ({ ...item }))
  const raw = (node as CorrectionView["nodes"][number] & { evidence?: unknown }).evidence
  return Array.isArray(raw)
    ? raw.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")).map((item) => ({ ...item }))
    : []
}

export function correctionDraftFromView(view: CorrectionView): CorrectionDraft {
  return Object.fromEntries(view.nodes.map((node) => [
    node.node_key,
    {
      value: valueFromNode(node),
      reason: node.reason ?? "",
      evidence: evidenceFromNode(node),
      dirty: false,
    } satisfies CorrectionDraftNode,
  ]))
}

export function updateCorrectionDraft(
  draft: CorrectionDraft,
  nodeKey: string,
  patch: Partial<CorrectionDraftNode>,
): CorrectionDraft {
  const current = draft[nodeKey] ?? { value: undefined, reason: "", evidence: [], dirty: false }
  return {
    ...draft,
    [nodeKey]: {
      ...current,
      ...patch,
      dirty: true,
    },
  }
}

export function mergeCorrectionResponse(
  _previous: CorrectionDraft,
  response: CorrectionView,
): CorrectionDraft {
  return correctionDraftFromView(response)
}

export function correctionSubmissionPayload(
  draft: CorrectionDraft,
  view: CorrectionView,
  idempotencyKey: string,
): CorrectionSubmissionRequest {
  if (!view.contract) {
    throw new Error("当前运行没有可提交的纠偏合同")
  }
  return {
    contract_hash: view.contract.contract_hash,
    review_revision: view.review_revision,
    idempotency_key: idempotencyKey,
    nodes: view.nodes
      .filter((node) => draft[node.node_key]?.dirty && node.editable !== false && !node.read_only)
      .map((node) => {
        const nodeDraft = draft[node.node_key]
        if (!nodeDraft) throw new Error(`缺少节点 ${node.node_key} 的草稿`)
        return {
          node_key: node.node_key,
          human_value: nodeDraft.value,
          reason: nodeDraft.reason.trim(),
          evidence: nodeDraft.evidence,
        }
      }),
  }
}
