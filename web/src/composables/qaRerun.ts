import { getJob, rerunQaCases } from "@/api/client";
import type { JobSnapshot, JobState } from "@/api/types";
import type { SnackKind } from "@/composables/snack";

const TERMINAL = new Set<JobState>(["ok", "error", "cancelled"]);

/**
 * Sentinel stored in the `rerunningCase` ref while a batch re-run is in flight.
 * It never matches a real case id, so per-card spinners stay off while the
 * global "busy" lock (`rerunningCase !== ""`) still holds.
 */
export const BATCH_RERUN_CASE = "__batch__";

export function jobTail(log: string | undefined): string {
  const lines = (log || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines[lines.length - 1] || "";
}

/** Resolve when the job reaches a terminal state. Submission alone is not success. */
export function settleJob(jobId: string): Promise<JobSnapshot> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const es = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);

    const finish = (job: JobSnapshot) => {
      if (settled || !TERMINAL.has(job.state)) return;
      settled = true;
      es.close();
      resolve(job);
    };

    const fail = (err: unknown) => {
      if (settled) return;
      settled = true;
      es.close();
      reject(err);
    };

    // `state` is a partial frame (no log). Only snapshot and done are full jobs.
    const take = (event: Event) => {
      finish(JSON.parse((event as MessageEvent).data) as JobSnapshot);
    };
    es.addEventListener("snapshot", take);
    es.addEventListener("done", take);
    es.onerror = () => {
      getJob(jobId).then(finish).catch(fail);
    };
    getJob(jobId).then(finish).catch(fail);
  });
}

export interface RerunResult {
  ids: string[];
  label: string;
  job: JobSnapshot;
}

/**
 * Submit a (possibly batched) re-run and resolve once the job is terminal.
 * A batch goes out as one job with many case ids, so the pool runs them
 * concurrently instead of the UI serialising one click per case.
 */
export async function submitRerun(
  jira: string,
  caseIds: string[],
  onJob?: (jobId: string) => unknown,
): Promise<RerunResult> {
  const ids = caseIds.map((c) => c.trim()).filter(Boolean);
  if (!ids.length) throw new Error("没有可重测的用例");
  const out = await rerunQaCases(jira, ids);
  const jobId = out.jobs[0]?.id;
  if (!jobId) throw new Error("重测没有返回任务");
  await onJob?.(jobId);
  const job = await settleJob(jobId);
  return { ids, label: ids.length === 1 ? ids[0] : `${ids.length} 条用例`, job };
}

export interface StatusTally {
  passed: number;
  failed: number;
  blocked: number;
  other: number;
}

/** Count a re-run's outcomes from a run's case rows. Unknown ids count as `other`. */
export function tallyStatuses(
  cases: { case: string; status?: string }[] | undefined,
  ids: string[],
): StatusTally {
  const byId = new Map((cases || []).map((c) => [c.case, c.status] as const));
  const tally: StatusTally = { passed: 0, failed: 0, blocked: 0, other: 0 };
  for (const id of ids) {
    const status = byId.get(id);
    if (status === "passed") tally.passed++;
    else if (status === "failed") tally.failed++;
    else if (status === "blocked") tally.blocked++;
    else tally.other++;
  }
  return tally;
}

/** Human summary of a batch re-run; single-case wording stays as the case id. */
export function tallyMessage(ids: string[], tally: StatusTally): string {
  if (ids.length === 1) return `${ids[0]} 重测完成`;
  const parts = [
    `重测 ${ids.length} 条：${tally.passed} 通过 / ${tally.failed} 失败 / ${tally.blocked} 阻塞`,
  ];
  if (tally.other) parts.push(`${tally.other} 未出结果`);
  return parts.join("，");
}

/** A re-run that produced nothing conclusive must not read as a clean pass. */
export function tallyKind(tally: StatusTally): SnackKind {
  if (tally.failed > 0) return "error";
  if (tally.blocked > 0 || tally.other > 0) return "info";
  return "success";
}

/** Newest run that contains every id — the run `req_test` amends on a re-run. */
export function findRunWithCases<T extends { cases?: { case: string }[] }>(
  runs: T[] | undefined,
  ids: string[],
): T | undefined {
  return runs?.find((run) => ids.every((id) => (run.cases || []).some((c) => c.case === id)));
}
