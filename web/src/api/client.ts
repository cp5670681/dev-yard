import type {
  DocPayload,
  JobsOut,
  JobSnapshot,
  Meta,
  PiSettings,
  Repo,
  ReqDetail,
  ReqSummary,
} from "./types";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
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
    throw new Error(detail);
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

export function runAction(
  jira: string,
  action: string,
  payload: { ticket_id?: string; force?: boolean; source?: string } = {},
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
    body: string;
    summary?: string;
    findings?: { id?: string; title?: string; detail?: string; repo?: string }[];
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
  provider?: string;
  model?: string;
}) {
  return api<JobsOut>("/api/repos", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function setRepoPi(alias: string, provider: string, model: string) {
  return api<Repo[]>(`/api/repos/${encodeURIComponent(alias)}`, {
    method: "PUT",
    body: JSON.stringify({ provider, model }),
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
