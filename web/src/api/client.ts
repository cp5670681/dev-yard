import type {
  AccountCandidate,
  AssistantContext,
  AssistantSession,
  ContractReviewIn,
  ContractReviewOut,
  DevSettings,
  DocPayload,
  GitSettings,
  JobsOut,
  JobSnapshot,
  Meta,
  PiSettings,
  QaCaseDetail,
  QaConfigSave,
  QaConfigState,
  QaPage,
  Repo,
  ReqAccounts,
  ReqDetail,
  ReqDiff,
  ReqSummary,
  TicketDiff,
  TicketReviewIn,
  TicketReviewOut,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail != null) detail = JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function getMeta() {
  return api<Meta>("/api/meta");
}

export function listRequirements() {
  return api<ReqSummary[]>("/api/requirements");
}

export function getRequirement(jira: string) {
  return api<ReqDetail>(`/api/requirements/${encodeURIComponent(jira)}`);
}

export function deleteRequirement(jira: string) {
  return api<{ ok: boolean; jira: string }>(
    `/api/requirements/${encodeURIComponent(jira)}`,
    { method: "DELETE" },
  );
}

export function getQa(jira: string) {
  return api<QaPage>(`/api/requirements/${encodeURIComponent(jira)}/qa`);
}

export function getReqAccounts(jira: string) {
  return api<ReqAccounts>(
    `/api/requirements/${encodeURIComponent(jira)}/accounts`,
  );
}

export function discoverReqAccounts(jira: string) {
  return api<{ jira: string; env: string; candidates: AccountCandidate[] }>(
    `/api/requirements/${encodeURIComponent(jira)}/accounts/discover`,
    { method: "POST" },
  );
}

export function autoFillReqAccounts(jira: string, refresh = false) {
  return api<ReqAccounts>(
    `/api/requirements/${encodeURIComponent(jira)}/accounts/auto?refresh=${
      refresh ? "true" : "false"
    }`,
    { method: "POST" },
  );
}

export function refreshReqAccounts(jira: string) {
  return api<ReqAccounts>(
    `/api/requirements/${encodeURIComponent(jira)}/accounts/refresh`,
    { method: "POST" },
  );
}

export function getQaCase(jira: string, caseId: string) {
  return api<QaCaseDetail>(
    `/api/requirements/${encodeURIComponent(jira)}/qa/cases/${encodeURIComponent(caseId)}`,
  );
}

export function fileCaseBug(jira: string, caseId: string) {
  return api<{
    jira: string;
    case_id: string;
    ticket_id: string;
    title: string;
    repo: string;
  }>(
    `/api/requirements/${encodeURIComponent(jira)}/qa/cases/${encodeURIComponent(caseId)}/bug`,
    { method: "POST" },
  );
}

export function triageQaCases(jira: string, productOnly = true) {
  return api<{ filed: Record<string, string>; skipped: string[] }>(
    `/api/requirements/${encodeURIComponent(jira)}/qa/triage?product_only=${productOnly}`,
    { method: "POST" },
  );
}

export function rerunQaCases(jira: string, caseIds: string[], env = "") {
  return api<JobsOut>(
    `/api/requirements/${encodeURIComponent(jira)}/qa/rerun`,
    { method: "POST", body: JSON.stringify({ case_ids: caseIds, env }) },
  );
}

export function getDoc(jira: string, slug: string) {
  return api<DocPayload>(
    `/api/requirements/${encodeURIComponent(jira)}/docs/${encodeURIComponent(slug)}`,
  );
}

export function saveDoc(jira: string, slug: string, body: string) {
  return api<DocPayload>(
    `/api/requirements/${encodeURIComponent(jira)}/docs/${encodeURIComponent(slug)}`,
    { method: "PUT", body: JSON.stringify({ body }) },
  );
}

export function uploadAttachments(jira: string, files: File[]) {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  return api<{ jira: string; added: string[]; uploads: string[] }>(
    `/api/requirements/${encodeURIComponent(jira)}/uploads`,
    { method: "POST", body: form },
  );
}

export function deleteAttachment(jira: string, name: string) {
  return api<{ ok: boolean; jira: string; uploads: string[] }>(
    `/api/requirements/${encodeURIComponent(jira)}/uploads/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
}

export function attachmentUrl(jira: string, name: string) {
  return `/r/${encodeURIComponent(jira)}/uploads/${encodeURIComponent(name)}`;
}

export function openRequirement(
  jira: string,
  source: string = "pi",
  force: boolean = false,
  extra?: { target?: string; payload?: string },
) {
  return api<JobsOut>("/api/open", {
    method: "POST",
    body: JSON.stringify({
      jira,
      key: jira,
      source,
      force,
      target: extra?.target,
      payload: extra?.payload,
    }),
  });
}

export function importRequirement(input: {
  jira: string;
  source?: string;
  target?: string;
  payload?: string;
  branches: Record<string, string>;
  bases?: Record<string, string>;
  submit?: boolean;
  force?: boolean;
}) {
  return api<JobsOut>("/api/requirements/import", {
    method: "POST",
    body: JSON.stringify({
      jira: input.jira,
      key: input.jira,
      source: input.source ?? "pi",
      target: input.target ?? "",
      payload: input.payload ?? "",
      branches: input.branches,
      bases: input.bases ?? {},
      submit: input.submit ?? true,
      force: input.force ?? false,
    }),
  });
}

export function runAction(
  jira: string,
  action: string,
  payload: {
    ticket_id?: string;
    force?: boolean;
    source?: string;
    repos?: string[];
    strategy?: string;
    env?: string;
    resume?: boolean | null;
    approve?: boolean;
    redesign?: boolean;
    feedback?: string;
    note?: string;
    repo?: string;
    grill?: boolean;
    run?: boolean;
  } = {},
) {
  return api<JobsOut>(
    `/api/requirements/${encodeURIComponent(jira)}/actions/${encodeURIComponent(action)}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function submitTestReport(
  jira: string,
  payload: {
    verdict: string;
    body?: string;
    summary?: string;
    findings?: {
      id?: string;
      title?: string;
      detail?: string;
      repo?: string;
      depends_on?: string[];
    }[];
  },
) {
  return api<{ jira: string; phase: string; test: ReqDetail["test"] }>(
    `/api/requirements/${encodeURIComponent(jira)}/test-report`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function listRepos() {
  return api<Repo[]>("/api/repos");
}

export function addRepo(payload: {
  alias: string;
  url: string;
  default_base: string;
  role: string;
  path: string;
  provider?: string | null;
  model?: string | null;
  test_branch?: string | null;
}) {
  return api<JobsOut>("/api/repos", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      provider: payload.provider ?? "",
      model: payload.model ?? "",
      test_branch: payload.test_branch ?? "",
    }),
  });
}

export function setRepoPi(
  alias: string,
  provider: string | null,
  model: string | null,
  test_branch: string | null,
) {
  return api<Repo[]>(`/api/repos/${encodeURIComponent(alias)}`, {
    method: "PUT",
    body: JSON.stringify({
      provider: provider ?? "",
      model: model ?? "",
      test_branch: test_branch ?? "",
    }),
  });
}

export function getGitSettings() {
  return api<GitSettings>("/api/git");
}

export function saveGitSettings(payload: { freeze_branch: string }) {
  return api<GitSettings>("/api/git", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getDevSettings() {
  return api<DevSettings>("/api/dev");
}

export function saveDevSettings(payload: { tdd: boolean }) {
  return api<DevSettings>("/api/dev", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getPiSettings() {
  return api<PiSettings>("/api/pi");
}

export function savePiSettings(payload: {
  provider: string;
  model: string;
  stages: Record<string, { provider: string; model: string }>;
}) {
  return api<PiSettings>("/api/pi", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getQaConfig() {
  return api<QaConfigState>("/api/qa-config");
}

export function saveQaConfig(payload: QaConfigSave) {
  return api<QaConfigState>("/api/qa-config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function checkQaEnv(payload: { env?: string; jira?: string }) {
  return api<{
    ok: boolean;
    env: string;
    use: string;
    site: string;
    steps: { step: string; status: string; detail: string }[];
  }>("/api/qa-check-env", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getJob(jobId: string) {
  return api<JobSnapshot>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function submitAnswers(
  jobId: string,
  answers: { id: string; option: string; text: string }[],
) {
  return api<{ ok: boolean }>(`/api/jobs/${encodeURIComponent(jobId)}/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
  });
}

export function cancelJob(jobId: string) {
  return api<{ ok: boolean; state: string }>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

export function getTicketDiff(jira: string, ticketId: string) {
  return api<TicketDiff>(
    `/api/requirements/${encodeURIComponent(jira)}/tickets/${encodeURIComponent(ticketId)}/diff`,
  );
}

export function getRequirementDiff(jira: string) {
  return api<ReqDiff>(`/api/requirements/${encodeURIComponent(jira)}/diff`);
}

export function submitTicketReview(
  jira: string,
  ticketId: string,
  payload: TicketReviewIn,
) {
  return api<TicketReviewOut>(
    `/api/requirements/${encodeURIComponent(jira)}/tickets/${encodeURIComponent(ticketId)}/review`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function deleteTicket(jira: string, ticketId: string) {
  return api<{ jira: string; ticket_id: string; title: string; source: string }>(
    `/api/requirements/${encodeURIComponent(jira)}/tickets/${encodeURIComponent(ticketId)}`,
    { method: "DELETE" },
  );
}

export function getAssistantContext(route: string, jira?: string) {
  const q = new URLSearchParams({ route });
  if (jira) q.set("jira", jira);
  return api<AssistantContext>(`/api/assistant/context?${q.toString()}`);
}

export function createAssistantSession(route: string, jira?: string) {
  return api<AssistantSession>("/api/assistant/sessions", {
    method: "POST",
    body: JSON.stringify({ route, jira: jira || "" }),
  });
}

export function getAssistantSession(id: string) {
  return api<AssistantSession>(`/api/assistant/sessions/${encodeURIComponent(id)}`);
}

export function sendAssistantMessage(
  id: string,
  text: string,
  extra?: { route?: string; jira?: string | null },
) {
  return api<AssistantSession>(
    `/api/assistant/sessions/${encodeURIComponent(id)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({
        text,
        route: extra?.route || "",
        jira: extra?.jira ?? null,
      }),
    },
  );
}

export function abortAssistant(id: string) {
  return api<AssistantSession>(
    `/api/assistant/sessions/${encodeURIComponent(id)}/abort`,
    { method: "POST" },
  );
}

export function dropAssistant(id: string) {
  return api<{ ok: boolean; id: string }>(
    `/api/assistant/sessions/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function submitContractReview(
  jira: string,
  payload: ContractReviewIn,
) {
  return api<ContractReviewOut>(
    `/api/requirements/${encodeURIComponent(jira)}/contract/review`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}


