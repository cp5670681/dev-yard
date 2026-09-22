import { computed, type ComputedRef, type Ref } from "vue";
import type { JobBrief, QaAssertion, ShotItem } from "@/api/types";
import { jobsReady, runningJobs } from "@/state/jobs";

// QA actions that (re)design or run a case set: while one is in flight the case
// files are still moving, so the review gate must stay closed. Keep in sync
// with _QA_JOB_ACTIONS in src/dev_yard/web/context.py.
export const QA_GATE_ACTIONS = new Set(["run-test", "qa-review"]);

export function isQaJobActive(jobs: JobBrief[], jira: string): boolean {
  return jobs.some((j) => j.jira === jira && QA_GATE_ACTIONS.has(j.action));
}

/**
 * Whether a QA design/run job for `jira` is currently in flight.
 *
 * `snapshot` is the `active_jobs` list embedded in a fetched payload; it covers
 * the window before the live stream connects. Once the stream has delivered a
 * frame it is authoritative, so a job that just finished stops counting even
 * though the payload still lists it.
 */
export function useQaRunActive(
  jira: Ref<string>,
  snapshot: ComputedRef<JobBrief[] | undefined>,
): ComputedRef<boolean> {
  return computed(() =>
    jobsReady.value
      ? isQaJobActive(runningJobs.value, jira.value)
      : Boolean(snapshot.value?.length),
  );
}

/** `"<report-timestamp>:<case-id>"` -> `"<case-id>"` (batch ids use `-`, not `:`). */
export function findingCaseId(finding: string): string {
  const f = (finding || "").trim();
  if (!f) return "";
  const i = f.indexOf(":");
  return i >= 0 ? f.slice(i + 1) : f;
}

export function qaScreenshotUrl(
  jira: string,
  runId: string,
  caseId: string,
  name: string,
): string {
  return `/r/${encodeURIComponent(jira)}/qa/evidence/${encodeURIComponent(
    runId,
  )}/${encodeURIComponent(caseId)}/screenshots/${encodeURIComponent(name)}`;
}

/** Union of case screenshots plus the failure evidence basename, if any. */
export function caseShotItems(
  jira: string,
  runId: string,
  caseId: string,
  names: string[],
  failureEvidence?: string,
): ShotItem[] {
  if (!runId) return [];
  const ordered: string[] = [...(names || [])];
  if (failureEvidence) {
    const base = String(failureEvidence).split("/").pop() || "";
    if (base && !ordered.includes(base)) ordered.push(base);
  }
  return ordered.map((name) => ({
    url: qaScreenshotUrl(jira, runId, caseId, name),
    caption: name,
  }));
}

export function assertionPassCount(
  assertions?: QaAssertion[] | null,
): { passed: number; total: number } {
  const rows = (assertions || []).filter(
    (a) => a && typeof a === "object",
  );
  const passed = rows.filter((a) => a.status === "passed").length;
  return { passed, total: rows.length };
}

export function qaTypeColor(type: string): string {
  if (type === "ui") return "info";
  if (type === "net") return "secondary";
  if (type === "db") return "warning";
  return "grey";
}
