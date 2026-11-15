import type { Meta, StepView, TaskDetail } from '@/types/api'

export const offlineMeta: Meta = {
  version: '0.1.0',
  app_env: 'test',
  llm_provider: 'stub',
  search_provider: 'fixture',
  offline_mode: true,
  catalogue_source: 'stub',
  catalogue_size: 0,
  require_structured_output: true,
  models: [
    { node: 'plan', model: 'stub/deterministic', context_length: null, supports_structured_outputs: null, prompt_usd_per_million: null, completion_usd_per_million: null },
  ],
  fallback_models: [],
  max_subtasks: 4,
  max_tokens_per_task: 120000,
  max_calls_per_task: 12,
}

export const liveMeta: Meta = {
  ...offlineMeta,
  llm_provider: 'openrouter',
  search_provider: 'tavily',
  offline_mode: false,
  catalogue_source: 'live',
  catalogue_size: 467,
}

export const awaitingTask: TaskDetail = {
  id: '11111111-1111-4111-8111-111111111111',
  question: 'Compare three open-source workflow orchestration frameworks.',
  status: 'awaiting_approval',
  current_node: 'draft',
  created_at: '2026-11-15T12:00:00Z',
  updated_at: '2026-11-15T12:01:00Z',
  constraints_json: {},
  search_mode: 'fixture',
  llm_provider: 'stub',
  tokens_used: 1200,
  calls_used: 4,
  max_tokens_budget: 120000,
  max_calls_budget: 12,
  cost_usd_estimate: 0,
  error_code: null,
  error_message: null,
}

export const steps: StepView[] = [
  {
    id: 's1',
    node: 'plan',
    status: 'succeeded',
    attempt: 1,
    input_summary: 'skeleton: no model call',
    output_json: { mode: 'skeleton' },
    error: null,
    started_at: '2026-11-15T12:00:01Z',
    finished_at: '2026-11-15T12:00:02Z',
    duration_ms: 12,
  },
  {
    id: 's2',
    node: 'research',
    status: 'failed',
    attempt: 1,
    input_summary: null,
    output_json: null,
    error: 'provider_unavailable',
    started_at: '2026-11-15T12:00:03Z',
    finished_at: '2026-11-15T12:00:04Z',
    duration_ms: 30,
  },
  {
    id: 's3',
    node: 'research',
    status: 'succeeded',
    attempt: 2,
    input_summary: null,
    output_json: null,
    error: null,
    started_at: '2026-11-15T12:00:05Z',
    finished_at: '2026-11-15T12:00:06Z',
    duration_ms: 25,
  },
]
