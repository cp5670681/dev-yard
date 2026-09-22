import { ref } from "vue";
import type { JobBrief } from "@/api/types";

export const runningJobs = ref<JobBrief[]>([]);
// True once the stream (or its fetch fallback) has delivered a frame. Views use
// it to tell "no jobs" apart from "not connected yet", so a payload snapshot of
// the job list is only trusted until live data arrives.
export const jobsReady = ref(false);

let es: EventSource | null = null;
let listeners = 0;

function connect() {
  if (es) return;
  es = new EventSource("/api/jobs/events");
  let fails = 0;
  es.onopen = () => {
    fails = 0;
  };
  es.addEventListener("jobs", (e) => {
    fails = 0;
    runningJobs.value = JSON.parse((e as MessageEvent).data) as JobBrief[];
    jobsReady.value = true;
  });
  es.onerror = () => {
    fails += 1;
    if (fails < 3) return;
    fetch("/api/jobs")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((items: JobBrief[]) => {
        if (Array.isArray(items)) {
          runningJobs.value = items;
          jobsReady.value = true;
        }
        fails = 0;
      })
      .catch(() => undefined);
  };
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
