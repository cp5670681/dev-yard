import { ref } from "vue";
import type { JobBrief } from "@/api/types";

export const runningJobs = ref<JobBrief[]>([]);

let es: EventSource | null = null;
let listeners = 0;

function connect() {
  if (es) return;
  es = new EventSource("/api/jobs/events");
  es.addEventListener("jobs", (e) => {
    runningJobs.value = JSON.parse((e as MessageEvent).data) as JobBrief[];
  });
}

function disconnect() {
  es?.close();
  es = null;
}

export function watchJobs() {
  listeners += 1;
  connect();
  return () => {
    listeners -= 1;
    if (listeners <= 0) disconnect();
  };
}

export function jobHref(job: JobBrief) {
  if (job.action === "repo_add") return { name: "repos", query: { job: job.id } };
  return { name: "requirement", params: { jira: job.jira }, query: { job: job.id } };
}
