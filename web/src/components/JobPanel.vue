<template>
  <v-card class="mb-4" variant="outlined">
    <v-card-title class="d-flex align-center flex-wrap ga-3">
      <span>{{ job.action }}{{ tickets }}</span>
      <v-chip size="small" :color="stateColor" variant="tonal">{{ job.state }}</v-chip>
      <v-progress-circular
        v-if="job.state === 'running' || job.state === 'queued'"
        indeterminate
        size="16"
        width="2"
        color="primary"
      />
      <v-spacer />
      <v-btn
        v-for="run in job.pi_runs || []"
        :key="run.index"
        variant="tonal"
        size="small"
        @click="openPi(job.id, run.index)"
      >
        查看对话{{ (job.pi_runs || []).length > 1 ? ` · ${run.index + 1}` : "" }}
      </v-btn>
    </v-card-title>
    <v-progress-linear
      v-if="job.state === 'running' || job.state === 'queued'"
      indeterminate
      color="primary"
    />
    <v-card-text>
      <pre class="job-log" :class="{ folded: waiting }">{{ job.log || "等待输出…" }}</pre>
      <GrillForm
        v-if="waiting && job.grill"
        :job-id="job.id"
        :grill="job.grill"
        @submitted="refresh"
      />
      <p class="text-caption text-medium-emphasis mt-3 mb-0">{{ hint }}</p>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { getJob } from "@/api/client";
import type { JobSnapshot } from "@/api/types";
import { openPi } from "@/state/pi";
import GrillForm from "./GrillForm.vue";

const props = defineProps<{ jobId: string; initial?: JobSnapshot | null }>();
const emit = defineEmits<{ done: [job: JobSnapshot] }>();

const job = ref<JobSnapshot>(
  props.initial || {
    id: props.jobId,
    jira: "",
    action: "",
    state: "queued",
    ticket_ids: null,
    log: "",
    grill: null,
    pi_runs: [],
  },
);

const waiting = computed(() => job.value.state === "waiting");
const tickets = computed(() => {
  const ids = job.value.ticket_ids || [];
  return ids.length ? " " + ids.join(", ") : "";
});
const stateColor = computed(() => {
  if (job.value.state === "ok") return "success";
  if (job.value.state === "error") return "error";
  if (job.value.state === "waiting") return "primary";
  return "info";
});
const hint = computed(() => {
  if (job.value.action === "grill") {
    if (job.value.state === "waiting") return "勾选或改写后提交。有建议的选项已默认选中。";
    if (job.value.state === "running" || job.value.state === "queued") {
      return "正在生成本轮问题…";
    }
    return "Grill 按轮提问。每轮生成后在上方表单里选或改，提交后再问下一轮。";
  }
  return "抽取 / Spec / Tickets / Implement / Review 在网页里用 pi -p 一次性跑完。";
});

let es: EventSource | null = null;

function apply(next: JobSnapshot) {
  job.value = next;
  if (next.state === "ok" || next.state === "error") {
    es?.close();
    es = null;
    emit("done", next);
  }
}

function refresh() {
  getJob(props.jobId).then(apply).catch(() => undefined);
}

function bind() {
  es?.close();
  es = new EventSource(`/api/jobs/${encodeURIComponent(props.jobId)}/events`);
  es.addEventListener("snapshot", (e) => {
    apply(JSON.parse((e as MessageEvent).data) as JobSnapshot);
  });
  es.addEventListener("log", (e) => {
    const chunk = JSON.parse((e as MessageEvent).data) as string;
    job.value = { ...job.value, log: (job.value.log || "") + chunk };
  });
  es.addEventListener("state", (e) => {
    const next = JSON.parse((e as MessageEvent).data) as JobSnapshot;
    job.value = { ...job.value, ...next };
  });
  es.addEventListener("done", (e) => {
    apply(JSON.parse((e as MessageEvent).data) as JobSnapshot);
  });
}

onMounted(bind);
onUnmounted(() => {
  es?.close();
  es = null;
});
watch(
  () => props.jobId,
  () => bind(),
);
</script>

<style scoped>
.job-log {
  background: #0a0d12;
  color: #d5deea;
  border-radius: 8px;
  padding: 0.8rem;
  max-height: 22rem;
  overflow: auto;
  font-size: 0.8rem;
  line-height: 1.45;
  white-space: pre-wrap;
  margin: 0;
}
.job-log.folded {
  max-height: 8rem;
}
@media (max-width: 959px) {
  .job-log {
    max-height: 12rem;
  }
}
</style>
