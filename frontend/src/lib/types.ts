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
  display_order?: number
  weight?: number
  grade_points?: Record<string, number>
  aggregation_role?: string
  anchors?: Record<string, string>
}

export type DimensionSchemaDefinition = {
  format_version?: string
  package_key?: string
  package_version?: string
  compatibility_revision?: string
  dimensions: DimensionDefinition[]
  aggregation?: {
    grade_points?: Record<string, number>
    level_thresholds?: Record<string, number>
    score_round_digits?: number
  }
  output_contract?: {
    dimension_output_keys?: string[]
    unknown_key_policy?: string
  }
  core_dimension_keys?: string[]
  family_dimension_keys?: string[]
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
    category_key: "space_image" | "pdf_text" | "material_image"
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
  precheck: Record<string, any>
  aesthetic: Record<string, any> | null
  dimension_schema: EvaluationDimensionSchema
  scoring: Record<string, any>
  score: number | null
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
  target_type: "dimension"
  field_key: string
  model_value: number | string | null
  human_value: number | string | null
  reason_codes: string[]
  note: string
}

export type Asset = {
  id: number
  name: string
  mime_type: string
  category_key: "space_image" | "pdf_text" | "material_image"
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
  schema_version: "baseline-level-metrics-v1"
  levels: BaselineLevel[]
  total: number
  completed: number
  pending: number
  denominator: number
  valid_predictions: number
  failed: number
  exact_hits: number
  adjacent_hits: number
  deviations: number
  exact_accuracy: number
  adjacent_accuracy: number
  confusion_matrix: Record<BaselineLevel, Record<BaselineLevel, number>>
}

export type BaselinePromptSelection = {
  id: number | null
  stage: "A" | "B"
  name: string | null
  version: string | null
  rubric_version: string | null
}

export type BaselineDimensionSelection = {
  mode: "strategy_snapshot"
  manual_selection_supported: false
  route_policy_id: string | null
  schemas: Array<{
    schema_key: string | null
    version: string | null
    schema_type: string | null
    family_key: string | null
    canonical_hash: string | null
  }>
}

export type BaselineRegressionRun = {
  id: number
  baseline_set_id: number
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
    schema_version: "baseline-run-selection-v1"
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
  items: BaselineRegressionItem[]
}

export type MaterialPackage = {
  id: number
  package_key: string
  name: string
  source: "manual_upload" | "production_import" | "legacy_backfill"
  category_key: "space_image" | "pdf_text" | "material_image"
  item_count: number
  unique_asset_count: number
  active_asset_count: number
  removed_asset_count: number
  duplicate_count: number
  status_summary: Record<NonNullable<Asset["evaluation_status"]>, number>
  created_by: string
  created_at: string
}

export type EvaluationCategoryProfile = {
  id: number
  category_key: "space_image" | "pdf_text" | "material_image"
  display_name: string
  status: "draft" | "active" | "retired"
  allowed_mime_types: string[]
  preprocess_config: Record<string, unknown>
  prompt_a_id: number | null
  prompt_b_id: number | null
  model_config_id: number | null
  rubric_version: string
  dimension_schema_key: string | null
  dimension_schema_version: string | null
  updated_at: string
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
  prompt_a_version: string | null
  prompt_b_version: string | null
  prompt_version: string | null
  status: string
  stage: string
  progress: number
  attempts: number
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
  input_micros_per_million_tokens: number
  output_micros_per_million_tokens: number
  max_input_tokens: number
  benchmark_enabled: boolean
  active: boolean
  has_api_key: boolean
  api_key_mask: string
  updated_at: string
}

export type OptimizerConfig = Omit<
  ModelConfig,
  "max_concurrency" | "high_risk_review_enabled" | "benchmark_enabled" | "active"
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

export type PromptOptimizationRun = {
  id: number
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
  name: string
  description: string
  kind: "golden" | "test"
  status: "draft" | "locked"
  item_count: number
  truth_complete_count: number
  created_by: string
  created_at: string
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
