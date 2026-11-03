export type JsonObject = Record<string, unknown>
export interface Asset {
  name: string
  path: string
  size?: number
  kind?: string
  generation?: string
  run?: string
}
export interface Scene {
  name: string
  path: string
  task_path: string
  config_path?: string
  generation_count: number
  protocol_count: number
  prompt_count: number
  protocol_files: Asset[]
  prompts: Asset[]
}
export interface TaskProfile {
  title: string
  output: string
  objective: string
}
export interface PlatformState {
  project_root: string
  default_scene: string
  scenes: Scene[]
  protocols: Record<string, string[]>
  base_protocols: Asset[]
  task_profiles: Record<string, TaskProfile>
  project_vision: { name: string; summary: string }
  experiment_artifacts: Asset[]
  output_artifacts: Asset[]
  database_counts: Record<string, number>
  ai_jobs: JsonObject[]
}
export type JobStatus = 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | 'interrupted'
export interface Job {
  id: string
  kind: string
  status: JobStatus
  stage: string
  progress: number
  elapsed_s: number
  created_at: string
  payload: JsonObject
  logs: string[]
  error: string
  error_info?: ErrorInfo
  result: JsonObject | null
  cancel_requested?: boolean
}
export interface ErrorInfo {
  code: string
  stage: string
  message: string
  suggestion: string
  retryable: boolean
  request_id: string
}
export interface RegisteredCandidate {
  id: string
  name: string
  family: 'python' | 'statistics'
  scene: string
  task_profile: string
  supported_profiles: string[]
  source_path: string
  source_sha256: string
  instrumented_entry: boolean
  search_history_known: boolean
  created_at: string
}
export interface ResearchSummary {
  candidate_id: string
  name: string
  split: string
  distribution: string
  num_tags: number
  perturbation: string
  completed: number
  expected: number
  pass_rate: number
  pass_ci95: number[]
  mean_cost_ms: number | null
  eligible: boolean
  is_pareto: boolean
  exclusion_reasons: string[]
  cost_reduction_fraction: number | null
}
export interface ResearchRun {
  manifest: {
    run_id: string
    status: string
    config: JsonObject
    independence_status: string
    completed: number
    candidates: RegisteredCandidate[]
  }
  rows: ResearchSummary[]
  audit: { passed: boolean; failures: string[] }
  artifacts: Asset[]
}
export interface ValidationRun {
  seed: number
  semantic_hit: boolean | null
  estimated_values: unknown[]
  target_values: unknown[]
  cost_origin: string
  communication_cost_ms: number | null
  failure_reason: string
}
export interface ValidationReport extends JsonObject {
  protocol: string
  final_decision: string
  semantic_pass_rate: number | null
  completed_runs: number
  total_runs: number
  source_sha256: string
  validation_stages: { id: string; label: string; status: string; detail: unknown }[]
  runs: ValidationRun[]
}
export interface QualitySummary extends JsonObject {
  protocol: string
  quality_pass_rate: number | null
  quality_score: number | null
  mean_communication_cost_ms: number | null
  communication_eligible: boolean
}
export interface IterationResult extends JsonObject {
  iterations: {
    iteration: number
    protocol_name: string
    parent_a: string
    parent_b: string
    status: string
    target_file?: string
    prompt_file?: string
    evaluation?: QualitySummary
    selection?: { retained: boolean; reason: string }
  }[]
  population: string[]
  stop_reason: string
  selection_mode: string
  dry_run: boolean
  budget: { model_requests: number; max_model_requests: number; elapsed_seconds: number }
  independent_validation: {
    status: string
    independent: boolean
    winner?: string
    winner_passed?: boolean
    evidence_path?: string
    communication_reduction_fraction?: number | null
  } | null
  evidence_path?: string
}
export interface ComparisonRow extends JsonObject {
  protocol: string
  task: string
  mean_error: number | null
  mean_communication_bits: number | null
  quality_pass_rate: number | null
  success_rate: number | null
  is_pareto: boolean
  pareto_eligible: boolean
  pareto_exclusion_reasons: string[]
}
export interface EvidenceRun extends JsonObject {
  available: boolean
  run_id?: string
  runs: { run_id: string; path: string }[]
  manifest?: JsonObject
  audit?: { passed: boolean; [key: string]: unknown }
  rows?: ComparisonRow[]
  artifacts?: Asset[]
}
export interface Preview extends JsonObject {
  name: string
  path: string
  kind: string
  format?: string
  size: number
  text?: string
  content?: string
  data?: unknown
  headers?: string[]
  rows?: string[][]
  total?: number
  page?: number
  total_pages?: number
  page_size?: number
}
