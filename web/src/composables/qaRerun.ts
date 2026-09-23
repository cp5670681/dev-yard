import { getJob } from "@/api/client";
import type { JobSnapshot, JobState } from "@/api/types";

const TERMINAL = new Set<JobState>(["ok", "error", "cancelled"]);

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
