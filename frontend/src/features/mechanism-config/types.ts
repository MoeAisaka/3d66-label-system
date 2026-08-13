import type { ComponentType } from "react"

export type JsonObject = Record<string, any>

export type MechanismProfileDescription = {
  profile_type: string | null
  source: "explicit" | "legacy_image_shape" | "unresolved"
  supported: boolean
  editable: boolean
  reason: string | null
  version: string | null
  capabilities: string[]
  editor_route: string | null
  read_only_fallback: boolean
  can_execute: boolean
}

export type MechanismProfileCatalogItem = {
  profile_type: string
  version: string
  capabilities: string[]
  editor_route: string | null
  read_only_fallback: boolean
  editable: boolean
  can_execute: boolean
}

export type ConfigSummary = {
  id: number
  category_key: string
  display_name: string
  status: string
  revision: number
  contract_hash: string
  projected_revision_id: number
  candidate_count: number
  media_penalty_enabled: boolean
  updated_at: string
}

export type ConfigDetail = ConfigSummary & {
  mechanism_profile: MechanismProfileDescription
  contract: JsonObject
  classification_map: JsonObject
  subcategory_dimensions: Record<string, JsonObject>
  dimension_deduction_rules: Record<string, JsonObject>
  created_by: string
  created_at: string
}

export type ConfigRevision = {
  id: number
  category_key: string
  display_name: string
  status: "draft" | "candidate" | "active" | "retired"
  revision: number
  parent_revision_id: number | null
  contract_hash: string
  mechanism_profile: MechanismProfileDescription
  contract: JsonObject
  classification_map: JsonObject
  subcategory_dimensions: Record<string, JsonObject>
  dimension_deduction_rules: Record<string, JsonObject>
  media_penalty_enabled: boolean
  created_by: string
  created_at: string
  updated_at: string
}

export type Editable = {
  category_key: string
  display_name: string
  contract: JsonObject
  classification_map: JsonObject
  subcategory_dimensions: Record<string, JsonObject>
}

export type ValidationErrorItem = {
  target: string
  code: string
  message: string
}

export type MechanismEditorProps = {
  workflowKind: "incremental" | "stock"
  draft: Editable
  runtimeRevision: ConfigRevision | null
  selectedRevision: ConfigRevision | null
  busy: boolean
  banner: string | null
  errors: ValidationErrorItem[]
  onPatch: (mutator: (next: Editable) => void) => void
  onValidate: () => void
  onCreateCandidate: () => void
}

export type MechanismEditorPlugin = {
  profileType: string
  canEdit: boolean
  Editor: ComponentType<MechanismEditorProps>
  buildSummary: (revision: ConfigRevision | null) => string
  prepareForSave?: (draft: Editable) => Editable
}

export function cloneEditable<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function revisionToEditable(revision: ConfigRevision): Editable {
  return {
    category_key: revision.category_key,
    display_name: revision.display_name,
    contract: cloneEditable(revision.contract),
    classification_map: cloneEditable(revision.classification_map),
    subcategory_dimensions: cloneEditable(revision.subcategory_dimensions),
  }
}

export function isNewMechanismDraft(
  runtimeRevision: ConfigRevision | null,
  selectedRevision: ConfigRevision | null,
): boolean {
  return runtimeRevision === null && selectedRevision === null
}
