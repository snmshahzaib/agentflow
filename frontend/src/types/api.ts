/**
 * Types mirroring the backend's response schemas.
 *
 * Hand-written rather than generated: the surface is small, and an explicit
 * type is where a frontend/backend mismatch gets caught at compile time.
 */

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'approved'
  | 'failed'
  | 'cancelled'

export type NodeName = 'plan' | 'research' | 'analyse' | 'citation_check' | 'draft'

export type StepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

export interface TaskSummary {
  id: string
  question: string
  status: TaskStatus
  current_node: NodeName | null
  created_at: string
  updated_at: string
}

export interface TaskDetail extends TaskSummary {
  constraints_json: Record<string, unknown>
  search_mode: string
  llm_provider: string
  tokens_used: number
  calls_used: number
  max_tokens_budget: number
  max_calls_budget: number
  cost_usd_estimate: number
  error_code: string | null
  error_message: string | null
}

export interface StepView {
  id: string
  node: NodeName
  status: StepStatus
  attempt: number
  input_summary: string | null
  output_json: Record<string, unknown> | null
  error: string | null
  started_at: string
  finished_at: string | null
  duration_ms: number | null
}

export type SupportStatus = 'supported' | 'partial' | 'unsupported'

export interface SourceView {
  id: string
  /** The label this source carries in the report, e.g. "S1". */
  label: string
  subtask_index: number
  url: string
  title: string | null
  excerpt: string | null
  provider: string
  rank: number
  /** True when this came from the committed fixture corpus, not live retrieval. */
  is_fixture: boolean
  retrieved_at: string
}

export interface ClaimView {
  id: string
  ordinal: number
  claim_text: string
  /**
   * Whether a retrieved source *states* this claim. Not a judgement about
   * whether the claim is true — the UI must never present it as one.
   */
  support_status: SupportStatus
  rationale: string | null
  source_ids: string[]
  source_labels: string[]
}

export interface ReportView {
  id: string
  version: number
  status: 'draft' | 'approved' | 'superseded'
  content_markdown: string
  created_at: string
}

export const SUPPORT_LABELS: Record<SupportStatus, string> = {
  supported: 'Supported',
  partial: 'Partial',
  unsupported: 'Unsupported',
}

export interface NodeModelInfo {
  node: string
  model: string
  context_length: number | null
  supports_structured_outputs: boolean | null
  prompt_usd_per_million: number | null
  completion_usd_per_million: number | null
}

export interface Meta {
  version: string
  app_env: string
  llm_provider: string
  search_provider: string
  offline_mode: boolean
  catalogue_source: string
  catalogue_size: number
  require_structured_output: boolean
  models: NodeModelInfo[]
  fallback_models: string[]
  max_subtasks: number
  max_tokens_per_task: number
  max_calls_per_task: number
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
  correlation_id: string | null
}

/** Terminal statuses stop the client polling. */
export const TERMINAL_STATUSES: readonly TaskStatus[] = ['approved', 'failed', 'cancelled']

export const ACTIVE_STATUSES: readonly TaskStatus[] = ['queued', 'running']

export const NODE_ORDER: readonly NodeName[] = [
  'plan',
  'research',
  'analyse',
  'citation_check',
  'draft',
]

export const NODE_LABELS: Record<NodeName, string> = {
  plan: 'Plan',
  research: 'Retrieve sources',
  analyse: 'Analyse',
  citation_check: 'Check citations',
  draft: 'Draft report',
}

/**
 * Deliberately explicit: this step checks whether a claim points at a
 * retrieved source. It does not check whether the claim is true, and the UI
 * must never imply otherwise.
 */
export const NODE_DESCRIPTIONS: Record<NodeName, string> = {
  plan: 'Breaks the question into a small number of bounded subtasks.',
  research: 'Retrieves sources through the configured search provider. No model call.',
  analyse: 'Synthesises findings, each tied back to a retrieved source.',
  citation_check: 'Checks whether each claim is supported by a retrieved source — not whether it is true.',
  draft: 'Writes the report with citations and a generated limitations section.',
}
