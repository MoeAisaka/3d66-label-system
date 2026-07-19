export type User = {
  id: number
  username: string
  display_name: string
}

export type Dashboard = {
  asset_count: number
  queued: number
  processing: number
  needs_review: number
  levels: Record<string, number>
  model: { name: string; model_id: string; has_api_key: boolean }
}

export type Evaluation = {
  id: number
  asset_id: number
  job_id: number
  precheck: Record<string, any>
  aesthetic: Record<string, any> | null
  scoring: Record<string, any>
  score: number | null
  level: string | null
  final_level: string | null
  confidence: number | null
  needs_review: boolean
  human_review: {
    id: number
    reviewer_name: string
    decision: "approved" | "corrected" | "rejected"
    corrected_level: string | null
    note: string
    corrections: ReviewCorrection[]
    created_at: string
  } | null
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
}

export type ReviewCorrection = {
  target_type: "dimension" | "scoring"
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
  size_bytes: number
  width: number | null
  height: number | null
  created_at: string
  image_url: string
  duplicate?: boolean
}

export type EvaluationRecord = Asset & {
  evaluation: Evaluation
}

export type Job = {
  id: number
  asset_id: number
  asset_name: string
  prompt_a_version: string | null
  prompt_b_version: string | null
  status: string
  stage: string
  progress: number
  attempts: number
  error_message: string
  created_at: string
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
  has_api_key: boolean
  api_key_mask: string
  updated_at: string
}

export type OptimizerConfig = Omit<ModelConfig, "max_concurrency">

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
  change_note: string
  created_by: string
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
  created_by: string
  created_at: string
  finished_at: string | null
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
  created_by: string
  created_at: string
  finished_at: string | null
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
