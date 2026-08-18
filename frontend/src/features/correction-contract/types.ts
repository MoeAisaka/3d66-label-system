export type CorrectionLayer = "A" | "B" | "V3"

export type CorrectionNodeValue = unknown

export type CorrectionContractNode = {
  node_key: string
  layer: CorrectionLayer
  path: string
  order?: number
  label: string
  description: string
  type: string
  semantic_version: string
  compatibility_key: string
  required: boolean
  evidence: {
    description: string
    required?: boolean
    [key: string]: unknown
  }
  options?: unknown[]
  allowed_values?: unknown[]
  values?: unknown[]
  min?: number
  max?: number
  minimum?: number
  maximum?: number
  recompute_ref?: string
  metadata?: Record<string, unknown>
  model_value?: CorrectionNodeValue
  current_value?: CorrectionNodeValue
  human_value?: CorrectionNodeValue
  reason?: string
  evidence_value?: unknown
  evidence_entries?: Array<Record<string, unknown>>
  inheritance?: {
    status: "inherited" | "new" | "changed" | "current" | string
    [key: string]: unknown
  }
  editable?: boolean
  read_only?: boolean
  read_only_reason?: string
  steps?: string[]
  [key: string]: unknown
}

export type CorrectionContractIdentity = {
  contract_version: string | null
  contract_hash: string
  category_key: string
}

export type CorrectionView = {
  schema_version: string
  lane?: "baseline" | "incremental" | "candidate" | string
  run_id: number | null
  item_id: number | null
  evaluation_id: number | null
  category_key: string | null
  snapshot_status: "frozen" | "legacy_read_only" | string
  snapshot_source?: string
  read_only: boolean
  unavailable_reason?: string | null
  unavailable_nodes?: string[]
  contract: CorrectionContractIdentity | null
  review_revision: number
  nodes: CorrectionContractNode[]
  idempotent_replay?: boolean
}

export type CorrectionDraftNode = {
  value: CorrectionNodeValue
  reason: string
  evidence: Array<Record<string, unknown>>
  dirty?: boolean
}

export type CorrectionDraft = Record<string, CorrectionDraftNode>

export type CorrectionSubmissionRequest = {
  contract_hash: string
  review_revision: number
  idempotency_key: string
  nodes: Array<{
    node_key: string
    human_value: CorrectionNodeValue
    reason: string
    evidence: Array<Record<string, unknown>>
  }>
}

export type CorrectionNodeGroup = Record<CorrectionLayer, CorrectionContractNode[]>
