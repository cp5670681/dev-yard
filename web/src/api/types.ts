export type JobState = "queued" | "running" | "waiting" | "ok" | "error" | "cancelled";

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

export interface QaEnvFault {
  class?: string;
  message?: string;
}

export interface QaProgress {
  run_id: string;
  env?: string;
  env_fault?: QaEnvFault;
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
  last_summary_html?: string | null;
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

export interface QaAssertion {
  type: string;
  expected?: string;
  actual?: string;
  status?: string;
  carrier?: string | null;
}

export interface ShotItem {
  url: string;
  caption?: string;
}

export interface QaCaseItem {
  id: string;
  title: string;
  module?: string;
  priority?: string;
  repo?: string;
  covers?: string[];
  depends_on?: string[];
  state: string;
  model?: string;
  reason?: string;
  failure?: { step?: number; step_desc?: string; evidence?: string } | null;
  assertions?: QaAssertion[];
  screenshots?: string[];
  run_id?: string;
}

export interface ReqDetail {
  jira: string;
  title: string | null;
  phase: string;
  next: string;
  branch?: string;
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
  uploads: string[];
  steps: Step[];
  tickets: Ticket[];
  actions: Action[];
  docs: DocMeta[];
  stage_runs?: Record<string, { at?: string; ok?: boolean; summary?: string }>;
  qa?: {
    has_cases: boolean;
    has_meta: boolean;
    envs?: string[];
    active_env?: string;
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
      cases?: QaRunCase[];
    };
    progress: QaProgress | null;
    incomplete_run?: { run_id?: string; pending?: number } | null;
    cases?: QaCaseItem[];
    review?: QaReview | null;
    active_jobs?: JobBrief[];
  } | null;
  changes?: ChangeEntry[];
}

export interface ChangeEntry {
  id: string;
  at?: string;
  actor?: string;
  note?: string;
  repo?: string;
  ticket?: string;
  grilled?: boolean;
  contract_touched?: boolean;
  stale?: { qa?: boolean };
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
  assertions?: QaAssertion[];
  screenshots?: string[];
}

export interface QaCaseDetailLatestRun {
  run_id: string;
  env?: string;
  state?: string;
  model?: string;
  reason?: string;
  failure?: { step?: number; step_desc?: string; evidence?: string } | null;
  assertions?: QaAssertion[];
  screenshots?: string[];
}

export interface QaCaseDetail {
  jira: string;
  case: {
    id: string;
    title: string;
    module?: string;
    priority?: string;
    repo?: string;
    covers?: string[];
    depends_on?: string[];
    path?: string;
  };
  html: string;
  latest_run: QaCaseDetailLatestRun | null;
  live: {
    run_id?: string;
    state?: string;
    model?: string | null;
    reason?: string;
  } | null;
}

export interface QaReview {
  status: string;
  approved: boolean;
  stale: boolean;
  stale_reason?: string;
  feedback: string;
  updated_at: string;
  fingerprint: string;
}

export interface QaPage {
  meta: {
    status?: string;
    changes?: { id: string; repo?: string; ref?: string; desc?: string }[];
    [key: string]: unknown;
  } | null;
  cases: QaCase[];
  review?: QaReview | null;
  active_jobs?: JobBrief[];
  open_questions?: { count: number; body: string; exists?: boolean; error?: string };
  runs: {
    run_id: string;
    env?: string;
    status?: string;
    summary?: Record<string, number>;
    workers?: unknown[];
    progress?: QaProgress | null;
    env_fault?: QaEnvFault;
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
  test_branch: string;
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

export interface GitSettings {
  freeze_branch: string;
  default_freeze_branch: string;
  preview: string;
  ticket_preview: string;
  placeholders: string[];
  examples: string[];
}

export interface DevSettings {
  tdd: boolean;
}

export interface QaAccountCfg {
  username: string;
  password: string;
  state_file: string;
}

export interface QaExecCfg {
  use: string;
  payload: string;
  parallel: boolean;
  allow_cross_site: boolean;
  timeout: number;
  runner: string;
  workdir: string;
  sql_runner: string;
  target: string;
  port: number;
  container: string;
  jms_host: string;
  jms_port: number;
  jms_user: string;
  default_node: string;
  nodes_text: string;
  namespace: string;
  pod_selector: string;
  pod_pattern: string;
  shell: boolean;
  run_text: string;
  ping_text: string;
  skill: string;
  db_exec: string;
  parse_error?: string;
}

export interface QaEnvCfg {
  base_url: string;
  auth: { default: string; accounts: Record<string, QaAccountCfg> };
  db: { url: string; exec?: string };
  script: { runner: string };
  notes: string[];
  exec: QaExecCfg;
}

export interface QaWorkerCfg {
  id: string;
  /** null once the select is cleared — the server reads it as "". */
  provider: string | null;
  model: string | null;
  concurrency: number;
  priority: number;
}

export interface QaModelCfg {
  /** null once the select is cleared — the server reads it as "". */
  provider: string | null;
  model: string | null;
}

export interface QaConfigPayload {
  active_env: string;
  browser: { channel: string; headed: boolean };
  /** qa-design agent model; empty falls back to the workspace pi pair. */
  design: QaModelCfg;
  workers: QaWorkerCfg[];
  envs: Record<string, QaEnvCfg>;
  env_names: string[];
  serialize_accounts?: boolean;
  /** new env name → old name, so a rename keeps masked secrets on the server. */
  renamed?: Record<string, string>;
}

/** PUT body: the env names are derived from `envs`, so they are not sent. */
export type QaConfigSave = Omit<QaConfigPayload, "env_names">;

export interface QaConfigState {
  exists: boolean;
  parse_error: string;
  raw: string;
  /** null when qa.yaml cannot be parsed; repair or delete the file first. */
  payload: QaConfigPayload | null;
  catalog: PiCatalog;
}

export interface ToolStep {
  id: string;
  name: string;
  args: string;
  summary: string;
  status: "running" | "ok" | "error" | string;
  result: string | null;
}

export interface PiEntry {
  /** Stable turn id assigned by the hub (assistant sessions). */
  id?: string;
  role?: string;
  text?: string;
  html?: string;
  thinking?: string;
  /** Milliseconds spent before the answer started (assistant turns). */
  thinking_ms?: number;
  /** Tool calls folded into this assistant turn, with their results. */
  steps?: ToolStep[];
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


