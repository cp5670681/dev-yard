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
  phase: string;
  next: string;
  tickets: number;
  done: number;
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
  phase: string;
  next: string;
  contract: string | null;
  contract_summary: string | null;
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
}

export interface Meta {
  root: string;
  root_name: string;
}

export interface PiEntry {
  role?: string;
  text?: string;
  thinking?: string;
  tool_name?: string;
  is_error?: boolean;
  tools?: { name?: string; args?: string }[];
}
