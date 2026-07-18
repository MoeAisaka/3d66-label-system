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
    created_at: string
  } | null
  versions: Record<string, string | null>
  created_at: string
}

export type Asset = {
  id: number
  name: string
  mime_type: string
  size_bytes: number
  width: number | null
  height: number | null
  status: string
  created_at: string
  image_url: string
  evaluation: Evaluation | null
  duplicate?: boolean
}

export type Job = {
  id: number
  asset_id: number
  asset_name: string
  status: string
  stage: string
  progress: number
  attempts: number
  error_message: string
  created_at: string
  started_at: string | null
  finished_at: string | null
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
  has_api_key: boolean
  api_key_mask: string
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
  change_note: string
  created_by: string
  created_at: string
}

export type MigrationContext = {
  candidate: { model_id: string; name: string; has_api_key: boolean }
  baselines: Array<{ model_id: string; asset_count: number }>
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
