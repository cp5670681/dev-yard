import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import type { JobBrief, QaAssertion, QaProgress, ShotItem } from "@/api/types";
import { jobsReady, runningJobs } from "@/state/jobs";

const LIVE_CASE_STATES = new Set(["pending", "ready", "running", "held"]);

/** A full 「执行用例」 has no case list. A re-run names the cases it touches. */
export function isFullSuiteRun(job: { action?: string; qa_cases?: string[] | null }): boolean {
  return job.action === "qa-run" && !(job.qa_cases && job.qa_cases.length);
}

export function progressIsLive(progress: { cases?: { state?: string }[] } | null | undefined): boolean {
  return Boolean(progress?.cases?.some((c) => LIVE_CASE_STATES.has(c.state || "")));
}

/** Placeholder rows shown the instant 「执行用例」 is submitted. */
export function queuedCaseProgress(
  cases: { id: string; title?: string }[],
  env?: string,
): QaProgress {
  return {
    run_id: "",
    env,
    cases: cases.map((c) => ({
      id: c.id,
      title: c.title,
      state: "pending",
      reason: "已提交，等待开始",
    })),
  };
}

/** Identity of one progress document. A later ping changes pools or case state. */
export function progressKey(
  progress:
    | {
        run_id?: string;
        cases?: { id?: string; state?: string }[];
        pools?: { id?: string }[];
      }
    | null
    | undefined,
): string {
  const cases = (progress?.cases || []).map((c) => `${c.id || ""}:${c.state || ""}`).join(",");
  const pools = (progress?.pools || []).map((p) => p.id || "").join(",");
  return `${progress?.run_id || ""}|${cases}|${pools}`;
}

export function useSuiteRun(jira: Ref<string>) {
  const suiteArmed = ref(false);
  const sawLive = ref(false);
  // The document already on screen when 「执行用例」 was confirmed. It must not
  // count as this submission's live progress (a partial round's held rows are live).
  const baselineKey = ref("");

  const fullRunJob = computed(() =>
    runningJobs.value.some((j) => j.jira === jira.value && isFullSuiteRun(j)),
  );
  const suiteLocked = computed(() => suiteArmed.value || fullRunJob.value);

  function armSuite(current?: Parameters<typeof progressKey>[0]): boolean {
    if (suiteLocked.value) return false;
    baselineKey.value = progressKey(current);
    suiteArmed.value = true;
    sawLive.value = false;
    return true;
  }

  function disarmSuite() {
    suiteArmed.value = false;
  }

  function noteProgress(progress: Parameters<typeof progressKey>[0]) {
    if (suiteArmed.value && progressKey(progress) === baselineKey.value) return;
    if (progressIsLive(progress)) sawLive.value = true;
  }

  function finishSuite() {
    sawLive.value = true;
    suiteArmed.value = false;
  }

  function overlayProgress(
    real: QaProgress | null | undefined,
    cases: { id: string; title?: string }[],
  ): QaProgress | null {
    // Keep the submitted placeholder until this attempt publishes a new document.
    if (suiteArmed.value && progressKey(real) === baselineKey.value) {
      return cases.length ? queuedCaseProgress(cases, real?.env) : (real ?? null);
    }
    if (progressIsLive(real) || sawLive.value) return real ?? null;
    if (suiteLocked.value && cases.length) return queuedCaseProgress(cases, real?.env);
    return real ?? null;
  }

  watch(jira, () => {
    suiteArmed.value = false;
    sawLive.value = false;
    baselineKey.value = "";
  });

  return {
    suiteLocked,
    armSuite,
    disarmSuite,
    noteProgress,
    finishSuite,
    overlayProgress,
  };
}

// QA actions that (re)design or run a case set: while one is in flight the case
// files are still moving, so the review gate must stay closed. Keep in sync
// with _QA_JOB_ACTIONS in src/dev_yard/web/context.py.
export const QA_GATE_ACTIONS = new Set(["qa-design", "qa-review", "qa-run"]);

export function isQaJobActive(jobs: JobBrief[], jira: string): boolean {
  return jobs.some((j) => j.jira === jira && QA_GATE_ACTIONS.has(j.action));
}

/**
 * Whether a QA design/review/run job for `jira` is currently in flight.
 *
 * `snapshot` is the `active_jobs` list embedded in a fetched payload; it covers
 * the window before the live stream connects. Once the stream has delivered a
 * frame it is authoritative, so a job that just finished stops counting even
 * though the payload still lists it.
 */
export function useQaActive(
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
