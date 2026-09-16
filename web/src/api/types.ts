export type JobState = "queued" | "running" | "waiting" | "ok" | "error";

export interface JobBrief {
  id: string;
  jira: string;
  action: string;
  state: JobState;
  ticket_ids: string[] | null;
  label?: string;
}

export interface PiRun {
  index: number;
  cwd: string;
  started_at?: string | null;
}

export interface GrillQuestion {
  id: string;
  title?: string;
  body?: string;
  options?: { id: string; label: string }[];
  suggested?: string;
  suggested_text?: string;
}

export interface GrillRound {
  round: number;
  intro?: string;
  questions: GrillQuestion[];
}

export interface QaProgressCase {
  id: string;
  title?: string;
  state: string;
  repo?: string;
  depends_on?: string[];
  pool?: string | null;
  model?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  reason?: string;
}

export interface QaProgress {
  run_id: string;
  env?: string;
  pools?: {
    id: string;
    provider?: string | null;
    model?: string | null;
    concurrency: number;
    priority: number;
    inflight: number;
  }[];
  cases?: QaProgressCase[];
}

export interface JobSnapshot extends JobBrief {
  log: string;
  grill: GrillRound | null;
  pi_runs: PiRun[];
  qa_progress?: QaProgress | null;
}

export interface JobsOut {
  jobs: JobSnapshot[];
}

export interface ReqSummary {
  jira: string;
  title: string | null;
  phase: string;
  next: string;
  tickets: number;
  done: number;
  contract?: string | null;
}

export interface Ticket {
  id: string;
  title: string;
  repo: string;
  state: string;
  depends_on: string[];
  parallel: boolean;
  can_implement: boolean;
  can_review: boolean;
  last_summary: string | null;
  source?: string;
  finding?: string;
}

export interface Action {
  id: string;
  label: string;
  enabled: boolean;
  reason: string;
}

export interface DocMeta {
  slug: string;
  filename: string;
  filled: boolean;
  exists: boolean;
}

export interface Step {
  id: string;
  done: boolean;
  current: boolean;
}

export interface ReqDetail {
  jira: string;
  title: string | null;
  phase: string;
  next: string;
  contract: string | null;
  contract_summary: string | null;
  contract_summary_html?: string;
  test: {
    status?: string;
    latest_id?: string;
    latest_verdict?: string;
    source?: string;
    received_at?: string;
    summary?: string;
    findings?: { id?: string; title?: string; detail?: string; repo?: string }[];
  } | null;
  repos?: string[];
  worktrees: string[];
  assets: string[];
  steps: Step[];
  tickets: Ticket[];
  actions: Action[];
  docs: DocMeta[];
  stage_runs?: Record<string, { at?: string; ok?: boolean; summary?: string }>;
  qa?: {
    has_cases: boolean;
    has_meta: boolean;
    latest_run: null | {
      run_id: string;
      env?: string;
      summary?: {
        total?: number;
        passed?: number;
        failed?: number;
        blocked?: number;
        skipped?: number;
      };
    };
    progress: QaProgress | null;
  } | null;
}

export interface QaCase {
  id: string;
  module?: string;
  title?: string;
  priority?: string;
  repo?: string;
  covers?: string[];
  depends_on?: string[];
  body?: string;
  html?: string;
  path?: string;
  status?: string;
}

export interface QaRunCase {
  case: string;
  title?: string;
  status: string;
  repo?: string;
  model?: string;
  reason?: string;
  failure?: { step?: number; step_desc?: string; evidence?: string } | null;
  screenshots?: string[];
}

export interface QaPage {
  meta: {
    status?: string;
    changes?: { id: string; repo?: string; ref?: string; desc?: string }[];
    [key: string]: unknown;
  } | null;
  cases: QaCase[];
  runs: {
    run_id: string;
    env?: string;
    status?: string;
    summary?: Record<string, number>;
    workers?: unknown[];
    progress?: QaProgress | null;
    cases: QaRunCase[];
  }[];
}

export interface DocPayload {
  jira: string;
  slug: string;
  filename: string;
  filled: boolean;
  exists: boolean;
  text: string;
  html: string;
}

export interface Repo {
  alias: string;
  url: string;
  default_base: string;
  role: string;
  path: string;
  provider: string;
  model: string;
}

export interface Meta {
  root: string;
  root_name: string;
}

export interface StageModelCfg {
  provider: string;
  model: string;
}

export interface PiCatalogProvider {
  id: string;
  models: string[];
}

export interface PiCatalog {
  providers: PiCatalogProvider[];
  error: string | null;
}

export interface PiSettings {
  provider: string;
  model: string;
  stages: Record<string, StageModelCfg>;
  stage_ids: string[];
  catalog: PiCatalog;
}

export interface PiEntry {
  role?: string;
  text?: string;
  html?: string;
  thinking?: string;
  tool_name?: string;
  is_error?: boolean;
  streaming?: boolean;
  suggested_actions?: SuggestedAction[];
  tools?: { name?: string; args?: string }[];
}

export interface SuggestedAction {
  action: string;
  jira?: string;
  ticket_id?: string;
  repos?: string[];
  strategy?: string;
  force?: boolean;
  reason?: string;
}

export interface AssistantSession {
  id: string;
  route: string;
  jira: string | null;
  created_at: string;
  state: "idle" | "streaming" | string;
  entries: PiEntry[];
  error: string | null;
}

export interface AssistantContext {
  route: string;
  jira: string | null;
  repos: { alias: string; role: string; default_base: string; path: boolean }[];
  requirements: { jira: string; phase: string; next: string; title: string | null }[];
  requirement?: {
    jira: string;
    phase: string;
    next: string;
    title: string | null;
    actions: { id: string; label: string; enabled: boolean; reason: string }[];
  };
}

export interface TicketDiffFile {
  path: string;
  status: string;
}

export interface TicketDiff {
  jira: string;
  ticket_id: string;
  title: string;
  repo: string;
  state: string;
  base?: string;
  head?: string;
  log?: string;
  stat?: string;
  diff: string;
  files: TicketDiffFile[];
  message?: string;
}

export interface ReqDiffRepo {
  repo: string;
  default_base: string;
  base: string;
  log: string;
  stat: string;
  diff: string;
  files: TicketDiffFile[];
}

export interface ReqDiff {
  jira: string;
  phase: string;
  repos: ReqDiffRepo[];
}

export interface TicketReviewIn {
  verdict: "passed" | "failed" | "blocked" | string;
  summary?: string;
  auto_implement?: boolean;
}

export interface TicketReviewOut {
  jira: string;
  ticket_id: string;
  ticket: Record<string, unknown>;
  jobs: JobSnapshot[];
  error?: string | null;
}

export interface ContractReviewIn {
  verdict: "passed" | "failed" | string;
  summary?: string;
  auto_implement?: boolean;
}

export interface ContractReviewOut {
  jira: string;
  contract: {
    contract_review: string;
    contract_summary: string;
  };
  jobs: JobSnapshot[];
  error?: string | null;
}


