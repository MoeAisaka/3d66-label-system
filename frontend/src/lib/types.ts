import type { NodeCorrectionHistoryItem } from "@/lib/node-correction"

export type User = {
  id: number
  username: string
  display_name: string
  is_admin: boolean
  is_active?: boolean
  role?: "admin" | "manager" | "reviewer" | "analyst" | "viewer"
  role_label?: string
  permissions?: string[]
  created_at?: string
  last_login_at?: string | null
}

export type CanaryRunState =
  | "draft"
  | "preflight_ready"
  | "approvals_ready"
  | "freeze_ready"
  | "candidate_ready"
  | "human_review_ready"
  | "failed"
  | "cancelled"

export type CanaryRun = {
  run_id: string
  display_name: string | null
  state: CanaryRunState
  plan: {
    plan_version: string
    domain: "3D"
    target_size: number
    seed: string
  }
  evidence: Record<string, unknown>
  snapshot_fingerprint: string
  writes_business_database: boolean
  downloads_performed: boolean
  model_runs_performed: boolean
  forms_gold: boolean
  publishes_release: boolean
  created_by: string
  created_at: string
  updated_at: string
}

export type Dashboard = {
  asset_count: number
  queued: number
  processing: number
  needs_review: number
  levels: Record<string, number>
  model: { name: string; model_id: string; has_api_key: boolean }
}

export type ReviewStage = "initial" | "secondary" | "arbitration" | "completed"

export type HumanReview = {
  id: number
  stage: Exclude<ReviewStage, "completed">
  reviewer_name: string
  decision: "approved" | "corrected" | "rejected"
  corrected_level: string | null
  corrected_score: number | null
  note: string
  corrections: ReviewCorrection[]
  created_at: string
}

export type DimensionDefinition = {
  key: string
  label: string
  description?: string
  display_order?: number
  weight?: number
  grade_points?: Record<string, number>
  aggregation_role?: string
  layer?: string
  anchors?: Record<string, string>
}

export type DimensionSchemaDefinition = {
  format_version?: string
  package_key?: string
  package_version?: string
  compatibility_revision?: string
  dimensions: DimensionDefinition[]
  aggregation?: {
    engine_version?: string
    grade_points?: Record<string, number>
    level_thresholds?: Record<string, number>
    score_round_digits?: number
    collapse_rule?: Record<string, unknown>
    high_evidence_rule?: Record<string, unknown>
    top_level_rule?: Record<string, unknown>
    decision_rule_policy?: Record<string, unknown>
  }
  output_contract?: {
    dimension_output_keys?: string[]
    unknown_key_policy?: string
  }
  core_dimension_keys?: string[]
  family_dimension_keys?: string[]
  risk_review?: {
    dimension_keys?: string[]
    [key: string]: unknown
  }
  [key: string]: unknown
  prompt_contract?: {
    status?: string
    required_stage?: string
    publishing_blocked?: boolean
  }
  release_gate?: {
    minimum_calibration_samples?: number
    target_calibration_samples?: number
    completed_calibration_samples?: number
    status?: string
    publishing_blocked?: boolean
    blocked_reasons?: string[]
  }
}

export type DimensionSchemaRegistryItem = {
  id: number
  schema_key: string
  version: string
  schema_type: "core" | "family_pack" | "extension"
  family_key: "space" | "product" | "graphic" | "intent" | "common"
  display_name: string
  status: "draft" | "candidate" | "published" | "retired"
  parent_schema_id: number | null
  core_schema_id: number | null
  canonical_hash: string
  created_by: string
  created_at: string
  published_by: string | null
  published_at: string | null
  retired_at: string | null
  definition?: DimensionSchemaDefinition
}

export type DimensionRoutePolicyDefinition = {
  format_version: string
  policy_key: string
  policy_version: string
  activation_scope: "calibration_only" | "production"
  category_family_map: Record<string, string>
  family_routes: Record<string, {
    mode: "family_pack" | "core_fallback"
    schema_ref: {
      schema_key: string
      version: string
      family_key: string
      status: string
      canonical_hash: string
    }
  }>
  unknown_family_policy: string
  conflict_policy: string
}

export type DimensionRoutePolicyRegistryItem = {
  id: number
  policy_key: string
  version: string
  display_name: string
  status: "draft" | "candidate" | "published" | "retired"
  canonical_hash: string
  created_by: string
  created_at: string
  published_by: string | null
  published_at: string | null
  retired_at: string | null
  definition?: DimensionRoutePolicyDefinition
}

export type EvaluationDimensionSchema = {
  status: "resolved" | "invalid"
  schema_id: number | null
  schema_key: string | null
  version: string | null
  canonical_hash: string | null
  legacy_derived: boolean
  dimension_keys: string[]
  definition: DimensionSchemaDefinition | null
  error: string | null
}

export type InspirationHardDefect =
  | "blurry_grayish"
  | "careless_composition"
  | "garish_color"
  | "large_dead_black"
  | "distorted_viewpoint"
  | "fake_material"
  | "fisheye_distortion"
  | "invalid_black_border"
  | "severe_color_cast"
  | "known_real_photo_defect"

export type InspirationImageDefect =
  | "corner_small_watermark"
  | "subject_obscuring_watermark"
  | "large_area_watermark"

export type InspirationAuthoritativePrecheck = {
  redline_triggered: Record<
    "screenshot" | "casual_photo" | "text_heavy" | "qr_code_heavy",
    boolean
  >
  reason: string[]
  hard_defects: InspirationHardDefect[]
  image_defects: InspirationImageDefect[]
  decisive_evidence: {
    redline_triggered: Record<string, string[]>
    hard_defects: Array<{ key: InspirationHardDefect; evidence: string }>
    image_defects: Array<{ key: InspirationImageDefect; evidence: string }>
  }
  decision_status: "complete" | "uncertain"
  uncertain_fields: string[]
  decisive_signal_validation?: { status: "valid" | "needs_review"; reasons: string[] }
  needs_review?: boolean
}

export type Evaluation = {
  id: number
  asset_id: number
  job_id: number
  prompt_id: number | null
  prompt_a_id: number | null
  prompt_b_id: number | null
  preprocess: {
    schema_version: "evaluation-preprocess-v1"
    status: "completed"
    category_key: string
    source_mime_type: string
    model_mime_type: string
    config: Record<string, unknown>
    pdf?: {
      page_count?: number
      rendered_pages?: number
      text_extraction?: string
      ocr_status?: string
      text_chars?: number
    }
    pdf_input_channel?: {
      schema_version: "proposal-pdf-input-v1"
      evaluation_object?: "source_pdf_document"
      long_image_stitching: false
      metadata_page_count?: number | null
      actual_page_count?: number | null
      call_a?: {
        scanned_pages?: number[]
        attempted_pages?: number[]
        failed_pages?: number[]
        recovery_batches?: number[][]
        batch_count?: number
        stop_reason?: string
      }
      call_b?: {
        evaluation_object?: "source_pdf_document"
        representative_pages?: number[]
        sample_size?: number
      }
    }
    multimodal_summary?: {
      schema_version: "pdf-multimodal-summary-v1"
      status: "completed"
      document_type: string
      summary: string
      key_points: string[]
      visual_findings: string[]
      risks: string[]
      confidence: number
      model_id: string
    }
    text_excerpt?: string
  } | null
  precheck: Record<string, any> & Partial<InspirationAuthoritativePrecheck>
  aesthetic: Record<string, any> | null
  dimension_schema: EvaluationDimensionSchema
  scoring: Record<string, any>
  score: number | null
  inspiration_aesthetic_score: number | null
  level: string | null
  final_level: string | null
  final_score: number | null
  confidence: number | null
  needs_review: boolean
  review_stage: ReviewStage
  review_revision: number
  review_truth_status: "provisional" | "completed"
  review_panel: ReviewPanelSummary | null
  review_history: HumanReview[]
  correction_history?: NodeCorrectionHistoryItem[]
  human_review: HumanReview | null
  risk_review: {
    version: string
    triggered: boolean
    verdict: "keep" | "downgrade" | "uncertain" | "error"
    confidence?: number
    trigger_reasons?: string[]
    reasons?: string[]
    corrections?: Array<{ field: string; before: unknown; after: unknown }>
  } | null
  versions: Record<string, string | null>
  created_at: string
  updated_at: string
}

export type ReviewCorrection = {
  target_type: "dimension" | "key_field"
  field_key: string
  model_value: unknown
  human_value: unknown
  reason_codes: string[]
  note: string
}

export type Asset = {
  id: number
  name: string
  mime_type: string
  category_key: string
  size_bytes: number
  width: number | null
  height: number | null
  created_at: string
  image_url: string
  duplicate?: boolean
  restored?: boolean
  suggested_expected_level?: BaselineLevel | null
  level_suggestion?: {
    schema_version: "filename-level-suggestion-v1"
    status: "matched" | "conflict" | "unmatched"
    suggested_level: BaselineLevel | null
    matches: Array<{ level: BaselineLevel; token: string }>
  }
  evaluation_status?:
    | "not_evaluated"
    | "evaluated_old"
    | "evaluated_current"
    | "queued"
    | "running"
    | "failed"
}

export type BaselineLevel = "L1" | "L2" | "L3" | "L4" | "L5"

export type BaselineLevelMetrics = {
  schema_version: "baseline-level-metrics-v1" | "baseline-level-metrics-v2"
  levels: BaselineLevel[]
  total: number
  completed: number
  pending: number
  denominator: number
  valid_predictions: number
  unscored?: number
  manual_required?: number
  failed: number
  exact_hits: number
  adjacent_hits: number
  deviations: number
  exact_accuracy: number
  adjacent_accuracy: number
  confusion_matrix: Record<BaselineLevel, Record<BaselineLevel, number>>
}

export type BaselineFieldMetric = {
  field_key: string
  support: number
  tp: number
  fp: number
  fn: number
  accuracy: number
  recall: number
  confusion_matrix: Record<string, Record<string, number>>
  failure_sample_ids: number[]
}

export type BaselineFieldMetrics = {
  schema_version: "baseline-field-metrics-v1"
  run_id: number
  category_key: string
  field_metrics: BaselineFieldMetric[]
  aggregates: {
    macro: {
      field_count: number
      accuracy: number
      recall: number
    }
    micro: {
      support: number
      tp: number
      fp: number
      fn: number
      accuracy: number
      recall: number
    }
  }
  failure_sample_ids: number[]
  golden_failure_sample_ids: number[]
  versions: {
    model: string[]
    prompt: { a: string[]; b: string[] }
    mechanism: {
      spec_version: string | null
      rubric: string[]
      engine: string[]
      strategy_bundle_id: number
      strategy_canonical_id: string
    }
    asset: {
      baseline_set_fingerprint: string
      count: number
      payload_hash: string
    }
    truth: {
      locked_sample_set_ids: number[]
      revision_min: number
      revision_max: number
      matched_asset_count: number
    }
  }
  decision_policy: {
    evidence_only: true
    auto_activate_candidate: false
  }
}

export type BaselineSemanticFieldMetric = {
  field_key: string
  truth_count: number
  predicted_count: number
  true_positive_count: number
  precision: number | null
  recall: number | null
  mapping_coverage: number | null
  unmapped_rate: number | null
  conflict_rate: number | null
  null_semantics_accuracy: number | null
  correction_rate: number | null
  review_coverage: number | null
  bilingual_consistency: number | null
  reconciliation_rate: number | null
}

export type BaselineSemanticQualityMetrics = {
  schema_version: "semantic-quality-metrics-v1"
  run_id: number
  category_key: string
  fields: Record<string, BaselineSemanticFieldMetric>
  aggregates: {
    macro_precision: number | null
    macro_recall: number | null
    micro_precision: number | null
    micro_recall: number | null
  }
  reconciliation_rate: number | null
  evidence?: {
    status: "ready" | "unavailable_historical"
    truth_source: "frozen_run_snapshot" | "unavailable"
    truth_asset_count: number
    truth_revision_min: number | null
    truth_revision_max: number | null
    review_evidence_item_count: number
    reconciliation_evidence_item_count: number
  }
  contract?: {
    contract_id: number
    contract_key: string
    contract_version: number
    contract_hash: string
    site_scope: "domestic" | "overseas"
    asset_scope: "whole" | "single" | "other" | "unknown"
  } | null
}

export type BaselinePromptSelection = {
  id: number | null
  stage: "A" | "B"
  name: string | null
  version: string | null
  rubric_version: string | null
}

export type BaselineDimensionSelection = {
  mode: "strategy_snapshot" | "category_default" | "all" | "selected" | "none"
  manual_selection_supported: boolean
  enabled?: boolean
  selected_keys?: string[]
  effective_keys?: string[]
  dimension_schema_id?: number | null
  schema_key?: string | null
  version?: string | null
  display_name?: string | null
  prompt_only?: boolean
  route_policy_id?: string | null
  schemas?: Array<{
    schema_key: string | null
    version: string | null
    schema_type: string | null
    family_key: string | null
    canonical_hash: string | null
  }>
  source_schema?: {
    schema_key: string
    version: string
    canonical_hash: string
  } | null
  contract?: Record<string, unknown> | null
  v3_contract?: {
    spec_version: string
    revision?: number | null
    revision_id?: number | null
    candidate_revision_id?: number | null
    contract_hash?: string | null
    tracks: Array<{
      key: string
      label: string
      dimension_count: number
    }>
  } | null
}

export type BaselineV3Revision = {
  id: number
  category_key: string
  display_name: string
  status: "draft" | "active" | "candidate" | "retired" | string
  revision: number
  parent_revision_id: number | null
  contract_hash: string
  mechanism_profile?: Record<string, unknown>
  contract: Record<string, unknown>
  classification_map?: Record<string, unknown>
  subcategory_dimensions?: Record<string, unknown>
  dimension_deduction_rules?: Record<string, unknown>
  media_penalty_enabled?: boolean
  created_by?: string
  created_at?: string
  updated_at?: string
}

export type BaselineV3RevisionList = {
  projected_revision_id: number
  candidate_count: number
  items: BaselineV3Revision[]
}

export type BaselineRegressionRun = {
  id: number
  baseline_set_id: number
  category_key: string
  sequence_no: number
  previous_run_id: number | null
  strategy_bundle_id: number
  strategy_canonical_id: string
  status: "running" | "completed" | "partial_failed" | "failed"
  total: number
  completed: number
  valid_predictions: number
  failed: number
  metrics: BaselineLevelMetrics
  selection: {
    schema_version: "baseline-run-selection-v1" | "baseline-run-selection-v2"
    category_key?: string | null
    prompt_mode?: "single" | "dual" | "ab" | null
    execution_mode?: "freeform" | "structured"
    prompt_a: BaselinePromptSelection | null
    prompt_b: BaselinePromptSelection | null
    dimension: BaselineDimensionSelection
  }
  created_by: string
  created_at: string
  finished_at: string | null
}

export type BaselineSetSummary = {
  id: number
  name: string
  category_key: string
  description: string
  default_expected_level: BaselineLevel
  fingerprint: string
  item_count: number
  run_count: number
  latest_run: BaselineRegressionRun | null
  frozen: true
  created_by: string
  created_at: string
}

export type BaselineSetItem = {
  id: number
  asset_id: number
  source_package_id: number | null
  expected_level: BaselineLevel
  asset: {
    schema_version: "baseline-asset-v1"
    asset_id: number
    name: string
    sha256: string
    mime_type: string
    size_bytes: number
    width: number | null
    height: number | null
    source_package_id: number | null
    expected_level_source?: "filename" | "batch_default" | "manual_override"
    filename_level_suggestion?: Asset["level_suggestion"]
    created_at: string
  }
  image_url: string
  frozen: true
}

export type BaselineSetDetail = {
  summary: BaselineSetSummary
  items: BaselineSetItem[]
  pagination: { offset: number; limit: number; total: number }
  runs: BaselineRegressionRun[]
}

export type BaselineRegressionItem = {
  id: number
  baseline_set_item_id: number
  asset_id: number
  asset: BaselineSetItem["asset"]
  image_url: string
  expected_level: BaselineLevel
  predicted_level: BaselineLevel | null
  authoritative_score: number | null
  cap_reasons: Array<Record<string, unknown>>
  stage_a: Record<string, unknown>
  level_explanation: {
    schema_version: "baseline-level-explanation-v1"
    status: "available" | "out_of_scope" | "incomplete" | "unavailable_historical"
    predicted_level: BaselineLevel | null
    authoritative_score: number | null
    scope_status: string | null
    strong_dimensions: Array<{
      key: string
      grade: number
      evidence: string[]
      defects: string[]
    }>
    weak_dimensions: Array<{
      key: string
      grade: number
      evidence: string[]
      defects: string[]
    }>
    all_dimensions: Array<{
      key: string
      grade: number
      evidence: string[]
      defects: string[]
    }>
    image_quality: {
      status: "available" | "missing"
      severity: string | null
      severity_label: string
      confidence: number | null
      evidence: string[]
    }
    caps: Array<Record<string, unknown>>
    review_reasons: string[]
    message?: string
  }
  confidence: number | null
  needs_review: boolean | null
  versions: {
    model?: string | null
    prompt_a?: string | null
    prompt_b?: string | null
    rubric?: string | null
    engine?: string | null
  }
  interpretation?: {
    status: "scored" | "manual_required"
    execution_mode?: "freeform" | "structured"
    raw_text_a?: string | null
    raw_text_b?: string | null
    message?: string
  }
  status: "queued" | "completed" | "failed"
  deviation: boolean
  error_message: string
  evaluation_id: number | null
  evaluation: Evaluation | null
  job_id: number | null
  run_id: number
  optimization_case_id: number | null
  finished_at: string | null
}

export type BaselineRegressionDetail = {
  summary: BaselineRegressionRun
  baseline_set: BaselineSetSummary
  strategy: Record<string, unknown>
  comparison: {
    comparable: boolean
    previous_run_id: number | null
    current_sequence_no: number
    previous_sequence_no: number | null
    exact_accuracy_delta: number | null
    adjacent_accuracy_delta: number | null
    current: { total: number; valid_predictions: number; failed: number }
    previous: { total: number; valid_predictions: number; failed: number } | null
  }
  filter: { deviations_only: boolean }
  pagination: { offset: number; limit: number; total: number }
  items: BaselineRegressionItem[]
}

export type BaselineCorrectionStatus =
  | "processing"
  | "awaiting_decision"
  | "approved"
  | "rejected"
  | "failed"

export type BaselineCorrectionStage =
  | "analysis"
  | "candidate_generation"
  | "candidate_validation"
  | "regression"
  | "decision"

export type BaselineCorrectionRegressionReport = {
  schema_version: "baseline-correction-regression-v1"
  run_id: number
  status: BaselineRegressionRun["status"]
  comparable: boolean
  baseline_metrics: BaselineLevelMetrics
  candidate_metrics: BaselineLevelMetrics
  exact_accuracy_delta: number | null
  adjacent_accuracy_delta: number | null
  regressions: Array<{
    code: string
    message: string
    delta?: number
  }>
  recommendation: "approve" | "reject"
  approval_allowed: boolean
}

export type BaselineCorrectionReport = {
  schema_version: "baseline-correction-report-v1"
  status: "automatic_candidate_pipeline"
  category_key: string
  baseline_run_id: number
  selection: {
    policy: "explicit_completed_deviations"
    count: number
    item_ids: number[]
  }
  accuracy_report: {
    run_metrics: BaselineLevelMetrics
    selected_deviation_count: number
    average_level_distance: number
    direction_counts: Record<string, number>
    confusion_pairs: Array<{ pair: string; count: number }>
  }
  attribution: {
    dominant_direction: string
    prompt_only: boolean
    dimension_signal_count: number
  }
  prompt_suggestions: Array<{
    code: string
    priority: "high" | "medium" | "low"
    message: string
    supporting_samples: number
  }>
  dimension_suggestions: Array<{
    dimension_key: string
    priority: "high" | "medium" | "low"
    message: string
    signals: Record<string, number>
  }>
  confidence: "high" | "medium" | "low"
  risks: string[]
  publication: {
    allowed: false
    next_state: "automatic_candidate_regression"
    message: string
  }
  candidate_regression?: BaselineCorrectionRegressionReport
}

export type BaselineCorrectionOrchestration = {
  base_projection?: {
    config_id: number
    revision_id: number
    revision: number
    contract_hash: string
  }
  generated_candidate?: Record<string, unknown>
  candidate_prompt?: {
    id: number
    stage: "A" | "B"
    base_prompt_id: number
    version: string
  }
  candidate_revision?: {
    id: number
    revision: number
    contract_hash: string
  }
  candidate_summary?: Record<string, unknown>
  tuning_model?: Record<string, unknown>
  regression?: {
    run_id: number
    job_ids: number[]
    source_run_id: number
    baseline_set_fingerprint: string
  }
}

export type BaselineCorrectionRun = {
  id: number
  baseline_run_id: number
  category_key: string
  selected_item_ids: number[]
  status: BaselineCorrectionStatus
  stage: BaselineCorrectionStage
  progress: number
  attempt_count: number
  report: BaselineCorrectionReport | Record<string, never>
  blockers: Array<{ code: string; message: string; retryable: boolean }>
  candidate_revision_id: number | null
  regression_run_id: number | null
  orchestration: BaselineCorrectionOrchestration
  error: {
    code: string
    message: string
    retryable: boolean
  } | null
  decision: "approved" | "rejected" | null
  decided_by: string | null
  decided_at: string | null
  decision_note: string
  created_by: string
  created_at: string
  updated_at: string
  finished_at: string | null
}

export type MaterialPackage = {
  id: number
  package_key: string
  name: string
  source: "manual_upload" | "production_import" | "legacy_backfill"
  category_key: string
  item_count: number
  unique_asset_count: number
  active_asset_count: number
  removed_asset_count: number
  duplicate_count: number
  status_summary: Record<NonNullable<Asset["evaluation_status"]>, number>
  created_by: string
  created_at: string
}

export type UploadFileIssue = {
  filename: string
  reason: string
}

export type MaterialUploadResult = {
  items: Asset[]
  successful_files: string[]
  skipped_files: UploadFileIssue[]
  failed_files: UploadFileIssue[]
  summary: {
    success_count: number
    skipped_count: number
    failed_count: number
  }
  package: {
    id: number
    name: string
    item_count: number
    duplicate_count: number
    restored_count: number
    ignored_count: number
    failed_count: number
  }
}

export type EvaluationCategoryProfile = {
  id: number
  category_key: string
  display_name: string
  description: string
  status: "draft" | "active" | "retired"
  allowed_mime_types: string[]
  preprocess_config: Record<string, unknown>
  pipeline_config: CategoryPipelineConfig
  pipeline_revision: number
  prompt_a_id: number | null
  prompt_b_id: number | null
  model_config_id: number | null
  automation_config: Record<string, unknown>
  automation_revision: number
  rubric_version: string
  dimension_schema_key: string | null
  dimension_schema_version: string | null
  dimension_management?: {
    schema_version: string
    schema_status: string
    schema_immutable: boolean
    available_options: Array<{ key: string; label: string; display_order?: number; weight?: number }>
    selection: {
      mode: "all" | "selected" | "none"
      effective_keys: string[]
      prompt_only: boolean
    } | null
    error: { code: string; message: string } | null
  }
  updated_at: string
}

export type EvaluationProductionRunStatus =
  | "preparing"
  | "queued"
  | "evaluating"
  | "first_review"
  | "optimizing"
  | "regressing"
  | "awaiting_review"
  | "approved"
  | "rejected"
  | "published"
  | "blocked"
  | "failed"
  | "archived"

export type WorkflowKind = "incremental" | "stock"

export type EvaluationProductionProgress = {
  percent: number
  current_step: string
  completed_jobs: number
  total_jobs: number
}

export type EvaluationProductionFix = {
  label: string
  href: string
  api_path?: string | null
  api_method?: string | null
}

export type EvaluationProductionBlocker = {
  code: string
  title: string
  message: string
  fix: EvaluationProductionFix
}

export type EvaluationProductionTimelineStep = {
  key: string
  label: string
  status: "completed" | "current" | "pending" | "blocked" | "failed"
  completed_at: string | null
}

export type EvaluationProductionRun = {
  id: number
  idempotency_key: string
  status: EvaluationProductionRunStatus
  current_stage: string
  current_stage_label: string
  material_package_id: number
  material_package: {
    id: number
    name: string
    package_key: string
    active_asset_count: number
  }
  category_key: string
  workflow_kind: WorkflowKind
  category: { key: string; name: string; configuration_hash: string }
  job_ids: number[]
  job_counts: {
    total: number
    queued: number
    processing: number
    completed: number
    failed: number
  }
  pending_first_review_count: number
  progress: EvaluationProductionProgress
  automation_run_id: number | null
  automation: { id: number; status: string; dry_run: boolean; href: string } | null
  regression_run_id: number | null
  regression: {
    id: number
    status: string
    recommendation: string
    completed: number
    total: number
    href: string
  } | null
  evaluation_package_id: number | null
  evaluation_package: { id: number; status: string; href: string } | null
  blockers: EvaluationProductionBlocker[]
  fix_actions: EvaluationProductionFix[]
  ai_next_step: string
  timeline: EvaluationProductionTimelineStep[]
  error: { code: string; message: string } | null
  audit: { revision: number; last_reconciled_at: string | null }
  created_by: string
  created_at: string
  updated_at: string
  started_at: string
  finished_at: string | null
  archived_at: string | null
}

export type EvaluationPackageStatus =
  | "validating"
  | "awaiting_review"
  | "approved"
  | "rejected"
  | "published"
  | "archived"

export type EvaluationPackageSummary = {
  id: number
  package_key: string
  category_key: string
  status: EvaluationPackageStatus
  prompt_mode: "single" | "dual"
  prompt_a_id: number
  prompt_b_id: number | null
  dimension_schema_id: number | null
  dimension_route_policy_id: number | null
  sample_set_id: number
  baseline_strategy_bundle_id: number | null
  candidate_strategy_bundle_id: number
  regression_run_id: number
  automation_run_id: number | null
  metric_snapshot_id: number | null
  canonical_manifest_hash: string
  manifest_hash_valid: boolean
  ai_recommendation: string
  change_summary: string
  review: {
    revision: number
    decision: "approved" | "rejected" | null
    note: string
    reviewed_by: string | null
    reviewed_at: string | null
  }
  publish: {
    published_by: string | null
    published_at: string | null
    publishes_automatically: false
  }
  archive: {
    archived_by: string | null
    archived_at: string | null
    reason: string
  }
  created_by: string
  created_at: string
  updated_at: string
}

export type EvaluationPackagePromptSnapshot = {
  id: number
  stage: "A" | "B"
  name: string
  version: string
  rubric_version: string
  system_prompt: string
  user_prompt: string
  change_note?: string
  canonical_hash?: string
}

export type EvaluationPackagePrompts = {
  mode: "single" | "dual"
  a: EvaluationPackagePromptSnapshot
  b: EvaluationPackagePromptSnapshot | null
}

export type EvaluationPackageGoldenItem = {
  sample_item_id: number
  asset_id: number
  asset_name: string
  asset_sha256: string
  mime_type: string
  image_url: string
  expected_level: string | null
  expected_category: string | null
  role: string | null
  truth_revision: number
  truth: Record<string, unknown>
  source_evaluation_id: number | null
}

export type EvaluationPackageDetail = EvaluationPackageSummary & {
  canonical_manifest: Record<string, unknown>
  category: {
    category_key: string
    profile: Record<string, unknown> | null
  }
  prompts: EvaluationPackagePrompts
  dimensions: Record<string, unknown>
  golden_sample_set: {
    id: number
    name: string
    description: string
    kind: string
    status: string
    category_key: string
    item_count: number
    judgable_item_count: number
    items_manifest_hash: string
    items: EvaluationPackageGoldenItem[]
  }
  strategies: {
    baseline: Record<string, unknown> | null
    candidate: Record<string, unknown>
  }
  regression: {
    id: number
    name: string
    mode: string
    status: string
    terminal: boolean
    recommendation: string
    threshold: number
    total: number
    completed: number
    passed: number
    failed: number
    sample_set_version: string | null
    sample_manifest: Record<string, unknown>
    metric_rules_version: string | null
    metric_rules: Record<string, unknown>
    metrics: Record<string, unknown>
    summary: Record<string, unknown>
    items: Array<Record<string, unknown>>
    created_by: string
    created_at: string
    finished_at: string | null
  }
  automation: Record<string, unknown> | null
  metrics: Record<string, unknown>
  identity: Record<string, unknown>
  ai: {
    recommendation: string
    change_summary: string
    publishes_automatically: false
  }
}

export type CategoryPipelineProcessor = {
  module: string
  enabled: boolean
  config: Record<string, unknown>
}

export type CategoryDimensionConfig = {
  enabled: boolean
  mode: "all" | "selected" | "none"
  enabled_keys: string[]
  selected_keys?: string[]
}

export type CategoryPipelineConfig = {
  schema_version: "category-pipeline-v1"
  input_kind: "image" | "pdf"
  allowed_suffixes: string[]
  processors: CategoryPipelineProcessor[]
  prompt_mode: "follow" | "single" | "ab"
  prompt_context: { instruction: string }
  dimensions: CategoryDimensionConfig
  model_nodes: Record<string, boolean>
}

export type CategoryPipelineCatalog = {
  schema_version: "category-pipeline-catalog-v1"
  input_kinds: Array<{ key: "image" | "pdf"; label: string; mime_types: string[]; suffixes: string[] }>
  processors: Array<{
    module: string
    label: string
    input_kinds: string[]
    output_kind: string
    requires_model_node?: string
    config_schema: Record<string, { label: string; type: "integer" | "boolean"; min?: number; max?: number; default?: number | boolean }>
  }>
  dimension_options: Array<{ key: string; label: string }>
  model_nodes: Array<{ key: string; label: string; required: boolean }>
  prompt_modes: Array<"follow" | "single" | "ab">
}

export type ReviewPanelSummary = {
  id: number
  evaluation_id?: number
  required_reviewers: 1 | 3 | 5 | 7 | 9
  submitted_count: number
  status: "collecting" | "lead_adjudication" | "completed"
  revision: number
  blind_answers_hidden: boolean
}

export type EvaluationRecord = Asset & {
  evaluation: Evaluation
  sampling: {
    version: string
    tier: "required" | "sampled" | "deferred" | "reviewed"
    priority: number
    sample_rate: number
    reasons: Array<{ code: string; label: string }>
  }
}

export type Job = {
  id: number
  asset_id: number
  asset_name: string
  category_key: string
  prompt_a_version: string | null
  prompt_b_version: string | null
  prompt_version: string | null
  status: string
  stage: string
  progress: number
  attempts: number
  queue_class: "validation" | "interactive" | "production_batch" | "canary" | "recovery"
  origin_queue_class: "validation" | "interactive" | "production_batch" | "canary" | "recovery"
  parent_job_id: number | null
  technical_attempt: number
  technical_error_type: string | null
  retry_after_at: string | null
  batch_key: string | null
  error_message: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export type JobControl = {
  paused: boolean
  queued_count: number
  processing_count: number
  paused_count: number
  active_count: number
  updated_at: string
}

export type QueueStatusItem = {
  queue_class: Job["queue_class"]
  pending: number
  pending_total: number
  running: number
  reserved: number
  borrowed: number
  effective_limit: number
  weight: number
  effective_weight: number
  blocked_by_breaker: number
  blocked_by_credentials: number
  blocked_by_control: number
  delayed_by_retry_after: number
  dispatchable_pending: number
}

export type QueueStatus = {
  version: string
  global_limit: number
  shares: Record<string, number>
  weights: Record<string, number>
  validation_boost: number
  queues: QueueStatusItem[]
  credentials_configured: boolean
  control_paused: boolean
}

export type CircuitBreaker = {
  id: number
  scope_type: "strategy" | "batch"
  scope_key: string
  state: "closed" | "open" | "half_open"
  failure_count: number
  cooldown_until: string | null
  cooldown_elapsed: boolean
  reason: string | null
  updated_at: string
}

export type ModelConfig = {
  id: number
  name: string
  provider: string
  protocol: "openai_chat" | "openai_responses" | "anthropic_messages" | "custom_json"
  capabilities: string[]
  description: string
  base_url: string
  api_path: string
  model_id: string
  temperature: number
  max_tokens: number
  timeout_seconds: number
  max_retries: number
  max_concurrency: number
  structured_output: boolean
  high_risk_review_enabled: boolean
  thinking_mode: "auto" | "enabled" | "disabled"
  input_micros_per_million_tokens: number
  output_micros_per_million_tokens: number
  max_input_tokens: number
  benchmark_enabled: boolean
  active: boolean
  has_api_key: boolean
  api_key_mask: string
  updated_at: string
}

export type ModelRegistryEntry = {
  id: number
  role: "main" | "tuning" | "benchmark"
  name: string
  provider: string
  protocol: "openai_chat" | "openai_responses" | "anthropic_messages" | "custom_json"
  capabilities: string[]
  description: string
  base_url: string
  api_path: string
  model_id: string
  temperature: number
  max_tokens: number
  timeout_seconds: number
  max_retries: number
  max_concurrency: number
  max_requests_per_minute: number
  max_input_tokens: number
  input_micros_per_million_tokens: number
  output_micros_per_million_tokens: number
  monthly_budget_micros: number
  thinking_mode: "auto" | "enabled" | "disabled"
  level: string
  structured_output: boolean
  active: boolean
  source_model_config_id: number | null
  source_optimizer_config_id: number | null
  has_api_key: boolean
  api_key_mask: string
  created_by: string
  created_at: string
  updated_at: string
}

export type OptimizerConfig = Omit<
  ModelConfig,
  "max_concurrency" | "high_risk_review_enabled" | "thinking_mode" | "benchmark_enabled" | "active"
>

export type SamplingPolicy = {
  id: number
  version: string
  revision: number
  sample_rate: number
  low_confidence_threshold: number
  medium_confidence_threshold: number
  cold_start_required_count: number
  high_level_required_from: number
  updated_by: string
  updated_at: string
}

export type ReviewWorkflowPolicy = {
  id: number
  version: string
  revision: number
  initial_reviewers: 1 | 3 | 5 | 7 | 9
  supported_reviewer_counts: Array<1 | 3 | 5 | 7 | 9>
  updated_by: string
  updated_at: string
}

export type PromptVersion = {
  id: number
  category_key: string
  pipeline_scope?: "full_pipeline" | "baseline_regression" | "shared"
  stage: "A" | "B"
  name: string
  version: string
  system_prompt: string
  user_prompt: string
  rubric_version: string
  status: "draft" | "published" | "archived"
  source: string
  source_optimization_run_id?: number | null
  rollback_prompt_id?: number | null
  canary_status?: "not_started" | "planned" | "running" | "passed" | "failed"
  metrics?: PromptVersionMetrics
  change_note: string
  created_by: string
  created_at: string
  updated_at: string
}

export type PromptVersionMetrics = {
  sample_accuracy: number | null
  dimension_accuracy: Record<string, number>
  grade_accuracy: number | null
  review_coverage: number
  sample_size_n: number
  total_evaluations: number
  corrected_sample_count: number
  unreviewed_not_counted_as_correct: true
}

export type PromptMetricSnapshot = {
  id: number
  prompt_id: number
  task_set_key: string
  task_set_hash: string
  evaluation_ids: number[]
  metrics: {
    schema_version: "prompt-accuracy-v1"
    N: number
    reviewed_sample_count: number
    corrected_sample_count: number
    sample_accuracy: number | null
    dimension_accuracy: Record<string, number>
    grade_accuracy: number | null
    review_coverage: number
    unreviewed_count: number
    denominator_policy: "completed_human_initial_review_only"
  }
  total_count: number
  reviewed_count: number
  created_by: string
  created_at: string
}

export type OptimizationCase = {
  id: number
  evaluation_id: number | null
  final_review_id: number | null
  source_type: "human_review" | "production_feedback" | "baseline_regression"
  source_event_id: number | null
  baseline_regression_item_id?: number | null
  prompt_version: string
  severity: "P0" | "P1" | "P2" | "P3"
  status: "pending" | "batched" | "processing" | "completed" | "failed"
  case: Record<string, unknown>
  attempt_count: number
  next_attempt_at: string | null
  last_error: string
  automation_run_id: number | null
  created_at: string
  updated_at: string
}

export type AutomationPolicy = {
  id: 1
  enabled: boolean
  dry_run: boolean
  revision: number
  case_threshold: number
  immediate_severities: Array<"P0" | "P1" | "P2" | "P3">
  daily_budget_micros: number
  cooldown_seconds: number
  max_candidates: number
  lease_seconds: number
  max_attempts: number
  base_retry_seconds: number
  last_triggered_at: string | null
  updated_by: string
  updated_at: string
  budget: {
    spent_micros: number
    reserved_micros: number
    used_micros: number
    remaining_micros: number
    limit_micros: number
  }
  real_model_calls_enabled: boolean
  runtime: {
    status: "ready" | "waiting" | "blocked"
    checked_at: string
    worker: {
      active_worker_count: number
      stale_after_seconds: number
      workers: Array<{
        worker_id: string
        active: boolean
        started_at: string
        last_seen_at: string
        last_tick_at: string | null
        last_status: string
        last_error: string
        last_result: Record<string, unknown>
        consecutive_errors: number
      }>
    }
    queue: {
      eligible_case_count: number
      next_category_key: string | null
      next_prompt_version: string | null
      available_for_prompt: number
      required_for_prompt: number
    }
    optimizer: {
      configured: boolean
      source: string | null
      model_id: string | null
    }
    budget: {
      spent_micros: number
      reserved_micros: number
      used_micros: number
      remaining_micros: number
      limit_micros: number
    }
    blockers: Array<{
      code: string
      message: string
      severity: "blocking" | "warning" | "waiting" | "info"
    }>
  }
  auto_publish_enabled: false
}

export type AutomationRun = {
  id: number
  run_key: string
  base_prompt_version: string
  policy_revision: number
  status:
    | "planned"
    | "awaiting_executor"
    | "processing"
    | "succeeded"
    | "running"
    | "awaiting_release_review"
    | "failed"
    | "cancelled"
  dry_run: boolean
  trigger_reason: string
  case_ids: number[]
  frozen_input: Record<string, unknown>
  result: Record<string, unknown>
  candidate_count: number
  estimated_cost_micros: number
  actual_cost_micros: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  retryable: boolean
  error_message: string
  created_by: string
  created_at: string
  finished_at: string | null
  publishes_automatically: false
}

export type ProductionFeedbackEvent = {
  id: number
  event_id: string
  schema_version: "production-feedback-v1"
  event_type: "human_correction_finalized"
  source_system: string
  occurred_at: string
  payload_hash: string
  payload: Record<string, unknown>
  status: "accepted" | "mapped" | "rejected"
  optimization_case_id: number | null
  received_by: string
  received_at: string
  writes_production_database: false
}

export type BenchmarkVariant = {
  id: number
  model_key: "sol" | "terra" | "luna"
  provider: string
  model_id: string
  model_config_id: number | null
  pricing: {
    input_micros_per_million_tokens: number
    output_micros_per_million_tokens: number
    human_review_cost_micros: number
  }
  status: "pending" | "running" | "completed" | "failed"
  metrics: {
    sample_size?: number
    quality_accuracy?: number
    p0_p1_error_count?: number
    low_confidence_rate?: number
    human_review_rate?: number
    latency_p50_ms?: number
    latency_p95_ms?: number
    model_cost_micros?: number
    total_cost_with_human_micros?: number
    retry_stability?: number
  }
  error_message: string
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  actual_cost_micros: number
}

export type ModelBenchmark = {
  id: number
  experiment_key: string
  name: string
  status: "draft" | "running" | "completed" | "failed" | "cancelled"
  execution_mode: "disabled" | "test" | "real"
  cohort_hash: string
  snapshot_hash: string
  frozen_snapshot: {
    cohort_asset_ids: number[]
    strategy_bundle: Record<string, unknown>
    agent_plan: Record<string, unknown>
    samples?: Array<Record<string, unknown>>
    predicted_cost_micros?: number
    max_round_cost_micros?: number
  }
  quality_gate: Record<string, unknown>
  decision: {
    recommendation?: "sol" | "terra" | "luna" | "none"
    reason?: string
    pareto_model_keys?: string[]
    requires_human_decision?: true
  }
  created_by: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  max_round_cost_micros: number
  actual_cost_micros: number
  real_model_calls_enabled: boolean
  variants: BenchmarkVariant[]
}

export type AuditEvent = {
  id: number
  event_key: string
  category: string
  action: string
  subject_type: string
  subject_id: string
  actor: string
  payload: Record<string, unknown>
  created_at: string
}

export type LabelRelease = {
  id: number
  release_key: string
  content_key: string
  category_key: string
  evaluation_id: number | null
  final_review_id: number | null
  source_release_id: number | null
  status: "pending_review" | "approved" | "published" | "rejected"
  label_schema_version: string
  payload_hash: string
  label: Record<string, unknown>
  requested_by: string
  requested_at: string
  approved_by: string | null
  approved_at: string | null
  published_at: string | null
  published_label_id: number | null
  published_version: number | null
  is_current: boolean | null
}

export type IntegrationStatus = {
  upstream_content_ingress: {
    configured: boolean
    schema_version: string
    events: string[]
    material_fetch: boolean
  }
  downstream_label_consumer: {
    configured: boolean
    schema_version: string
    read_model: string
    cursor_api: string
  }
  external_writes_enabled: boolean
}

export type PromptOptimizationRun = {
  id: number
  category_key: string
  base_prompt_id: number
  base_prompt_version: string
  sample_set_id: number
  sample_set_name: string
  optimizer_model_id: string
  status: "queued" | "running" | "completed" | "failed"
  progress: number
  sample_count: number
  corrected_count: number
  diagnosis: Record<string, any>
  candidate_system_prompt: string
  candidate_user_prompt: string
  change_note: string
  error_message: string
  materialized_prompt_id?: number | null
  paired_regression_ids?: number[]
  created_by: string
  created_at: string
  finished_at: string | null
}

export type HistoricalCorrectionPreviewItem = {
  schema_version: string
  dedupe_key: string
  sample_role: "target_error" | "stable_control" | "blind_holdout" | "reason_only"
  correction_candidate: {
    scope: "overall"
    human_level: string | number | null
    model_level: string | number | null
    reason: string | null
    reason_only: boolean
  }
  provenance: {
    source_file: string
    sheet: string
    source_row: number
    source_file_sha256: string
    source_row_sha256: string
    owner_confirmed: true
  }
  forms_gold: false
}

export type HistoricalCorrectionPreview = {
  files: Array<{
    source_file: string
    content_sha256: string
    sheet: Record<string, any>
    batch_key: string
    preview_item_count: number
  }>
  summary: {
    uploaded_file_count: number
    unique_item_count: number
    duplicate_count: number
    blind_holdout_ratio: number
    role_counts: Record<HistoricalCorrectionPreviewItem["sample_role"], number>
  }
  items: HistoricalCorrectionPreviewItem[]
  writes_business_database: false
  downloads_performed: false
  model_runs_performed: false
  forms_gold: false
}

export type SampleSetSummary = {
  id: number
  category_key: string
  name: string
  description: string
  kind: "golden" | "test"
  status: "draft" | "locked"
  item_count: number
  truth_complete_count: number
  latest_truth_revision: number
  created_by: string
  created_at: string
}

export type QualityAssetsSummary = {
  sample_set_count: number
  item_count: number
  truth_complete_count: number
  by_kind: Record<string, QualityAssetsSummaryBucket>
  by_category: Record<string, QualityAssetsSummaryBucket>
  by_status: Record<string, QualityAssetsSummaryBucket>
  by_truth_complete: Record<"true" | "false", number>
}

export type QualityAssetsSummaryBucket = {
  sample_sets: number
  items: number
  truth_complete: number
}

export type ProjectionReconciliation = {
  id: number
  contract_id: number
  manifest_id: number
  target_table: string
  status: "matched" | "drift" | "failed"
  reason: string
  row_count: number
  missing_count: number
  unexpected_count: number
  expected_payload_hash: string
  payload_hash: string
  version_match: boolean
  checkpoint: Record<string, unknown>
  compensation: {
    retryable?: boolean
    strategy?: string
    canonical_rows_mutated?: boolean
  }
  created_at: string
}

export type ProjectionContract = {
  id: number
  contract_key: string
  version: number
  target_role: "unified_dimension" | "search_labels" | "quality_governance"
  table_name: string
  environment: "local" | "test"
  primary_key: string[]
  field_mappings: Record<string, string>
  input_versions: Record<string, unknown>
  mode: "snapshot" | "incremental_outbox"
  idempotency_key_template: string
  checkpoint: Record<string, unknown>
  reconciliation: Record<string, unknown>
  rollback: Record<string, unknown>
  owner: string
  status: "draft" | "active" | "retired"
  contract_hash: string
  created_by: string
  created_at: string
  latest_reconciliation: ProjectionReconciliation | null
}

export type SemanticApplicability = "required" | "optional" | "not_applicable"

export type TagDemandContractField = {
  field_key: string
  cardinality: "single" | "multi"
  localized: boolean
  vocabulary_owner: string
  max_values: number
  default_value: Array<Record<string, unknown>>
}

export type SourceIdentityContract = {
  source_system: string
  object_grain: "asset"
  identity_fields: ["res_type", "ll_id"]
  optional_disambiguator: "res_id" | null
  version_field: string
  deletion_field: string
  uniqueness_status: "unverified" | "verified" | "conflict"
  verification_evidence_hash: string | null
}

export type FieldSupplyDefinition = {
  field_key: string
  fact_namespace: "semantic" | "quality" | "governance"
  object_grain: "asset" | "image" | "text_fragment"
  production_method: "source_direct" | "rule" | "model" | "human" | "hybrid"
  source_authority: string
  owner: string
  freshness_sla_hours: number
  null_semantics: Array<"not_applicable" | "not_detected" | "unknown" | "empty_valid">
  rollback_strategy: "previous_release" | "compensation_release"
}

export type TagDemandContractDefinition = {
  schema_version: "tag-demand-contract-v1" | "tag-demand-contract-v2"
  semantic_schema: {
    schema_version: "semantic-tag-schema-v1"
    fields: Record<string, TagDemandContractField>
  }
  category_applicability: Record<string, Record<string, SemanticApplicability>>
  execution_variants: Array<{
    site_scope: "domestic" | "overseas"
    asset_scope: "whole" | "single" | "other" | "unknown"
    locale: "zh" | "en"
    category_key: string
    prompt_variant: "whole" | "single"
    prompt_version: string
    model_version: string
    field_applicability_overrides?: Record<string, SemanticApplicability>
  }>
  quality_gates: Record<string, {
    min_precision: number
    min_recall: number
    min_mapping_coverage: number
    max_conflict_rate: number
  }>
  projection_targets: Array<{ target_key: string; mode: "dry_run"; locale: "zh" | "en" }>
  source_identity?: SourceIdentityContract
  field_supply?: Record<string, FieldSupplyDefinition>
}

export type TagDemandContract = {
  id: number
  contract_key: string
  version: number
  status: "draft" | "candidate" | "active" | "retired"
  definition: TagDemandContractDefinition
  contract_hash: string
  approved_by: string | null
  approved_at: string | null
  created_by: string
  created_at: string
}

export type SourceIdentityVerification = {
  id: number
  contract_key: string
  source_system: string
  key_fields: ["res_type", "ll_id"]
  result: "verified" | "conflict"
  probe_hash: string
  data_window: string
  scoped_row_count: number
  duplicate_key_count: number
  res_id_conflict_count: number
  status: "draft" | "approved" | "superseded" | "rejected"
  created_by: string
  approved_by: string | null
  created_at: string
  approved_at: string | null
}

export type ContentIdentityRecord = {
  id: number
  source_system: string
  content_id: string
  content_key: string | null
  category_key: string
  content_version: string
  source_res_type: 1 | 6 | null
  source_ll_id: string | null
  source_res_id: string | null
  identity_status: "legacy_unverified" | "pending_verification" | "verified" | "conflict"
  identity_hash: string | null
  identity_verification_id: number | null
  status: string
  updated_at: string
}

export type SampleSetItem = {
  id: number
  asset_id: number
  asset_name: string
  image_url: string
  expected_level: string | null
  expected_category: string
  note: string
  truth: SampleTruth
  truth_revision: number
  truth_updated_by: string | null
  truth_updated_at: string | null
  source_model_id: string
  source_level: string | null
  added_by: string
  created_at: string
}

export type SampleTruth = {
  level?: string | null
  category?: string
  quality_severity?: string
  media_form?: Record<string, "yes" | "no" | "uncertain">
  dimensions?: Record<string, number>
  dimension_schema?: {
    binding_version?: string
    schema_id?: number
    schema_key?: string
    version?: string
    canonical_hash?: string
    definition?: DimensionSchemaDefinition
  }
}

export type SampleItemHistory = {
  item: SampleSetItem
  evaluations: Array<Evaluation & { reviews: NonNullable<Evaluation["human_review"]>[] }>
  truth_revisions: Array<{
    id: number
    revision: number
    truth: SampleTruth
    reason: string
    reviewer_name: string
    created_at: string
  }>
  regressions: Array<{
    id: number
    run_id: number
    run_name: string
    status: string
    passed: boolean | null
    comparison: Record<string, any>
    created_at: string
    finished_at: string | null
  }>
}

export type RegressionSummary = {
  id: number
  name: string
  regression_mode?: "standard" | "paired"
  trigger_prompt_id?: number | null
  sample_set_id: number
  sample_set_name: string
  prompt_a_id: number
  prompt_a_version: string
  prompt_b_id: number
  prompt_b_version: string
  status: "queued" | "running" | "passed" | "regressed"
  threshold: number
  total: number
  completed: number
  passed: number
  failed: number
  pass_rate: number
  release_gate_passed: boolean
  baseline_strategy_bundle_id?: number | null
  candidate_strategy_bundle_id?: number | null
  metric_rules_version?: string | null
  recommendation?: "pass" | "fail" | "pending" | null
  approval_status?: "pending" | "approved" | "rejected" | null
  approved_by?: string | null
  approval_note?: string
  created_by: string
  created_at: string
  finished_at: string | null
}

export type StrategyBundleSummary = {
  id: number
  model_id: string
  prompt_a_version: string
  prompt_b_version: string | null
  rubric_version: string
  engine_version: string
  risk_review_version: string | null
  agent_plan_version: string
  sampling_policy_revision: number | null
  created_at: string
}

export type RegressionDetail = {
  summary: RegressionSummary
  items: Array<{
    id: number
    sample_item_id: number
    asset_id: number
    asset_name: string
    image_url: string
    expected: SampleTruth
    status: string
    passed: boolean | null
    comparison: Record<string, any>
    evaluation: Evaluation | null
  }>
}

export type SampleSetDetail = {
  summary: SampleSetSummary
  items: SampleSetItem[]
}

export type MigrationContext = {
  candidate: { model_id: string; name: string; has_api_key: boolean }
  baselines: Array<{ model_id: string; asset_count: number }>
  sample_sets: SampleSetSummary[]
}

export type MigrationSummary = {
  id: number
  name: string
  baseline_model_id: string
  candidate_model_id: string
  sample_size: number
  status: "running" | "review" | "accepted" | "regressed"
  completed: number
  pending: number
  review_required: number
  reviewed: number
  auto_exact_rate: number
  verdicts: Record<"candidate_better" | "same" | "baseline_better", number>
  created_by: string
  created_at: string
}

export type MigrationItem = {
  id: number
  asset_id: number
  asset_name: string
  image_url: string
  status: string
  requires_review: boolean
  comparison: {
    reasons: string[]
    level_delta: number | null
    baseline_level: string | null
    candidate_level: string | null
    baseline_score: number | null
    candidate_score: number | null
    baseline_category: string | null
    candidate_category: string | null
  } | null
  human_verdict: "candidate_better" | "same" | "baseline_better" | null
  reviewer_name: string | null
  review_note: string
  baseline: Evaluation
  candidate: Evaluation | null
}

export type MigrationDetail = {
  summary: MigrationSummary
  items: MigrationItem[]
}
