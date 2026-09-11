export type JobState = "queued" | "running" | "waiting" | "ok" | "error";

export interface JobBrief {
  id: string;
  jira: string;
  action: string;
  state: JobState;
  ticket_ids: string[] | null;
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

export interface JobSnapshot extends JobBrief {
  log: string;
  grill: GrillRound | null;
  pi_runs: PiRun[];
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
  thinking?: string;
  tool_name?: string;
  is_error?: boolean;
  tools?: { name?: string; args?: string }[];
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
}


