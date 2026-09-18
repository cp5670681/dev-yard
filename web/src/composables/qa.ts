import type { QaAssertion, ShotItem } from "@/api/types";

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
