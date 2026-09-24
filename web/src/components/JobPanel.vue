<template>
  <v-card class="mb-4 job-panel-card" variant="outlined">
    <v-card-title class="d-flex align-center flex-wrap ga-3 py-2 px-3 bg-surface-variant">
      <span class="font-weight-bold text-subtitle-2">{{ actionLabel }}{{ tickets }}</span>
      <v-chip size="small" :color="stateColor" variant="tonal" class="jira-lozenge">{{ job.state }}</v-chip>
      <v-chip v-if="job.env_waiting" size="small" color="warning" variant="tonal">
        等待环境
      </v-chip>
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
      <v-btn
        v-if="stoppable"
        variant="tonal"
        size="small"
        color="warning"
        :prepend-icon="mdiStopCircleOutline"
        :loading="cancelling"
        @click="cancel"
      >
        停止
      </v-btn>
    </v-card-title>
    <v-progress-linear
      v-if="job.state === 'running' || job.state === 'queued'"
      indeterminate
      color="primary"
      height="2"
    />
    <v-card-text class="pa-3">
      <pre class="job-log" :class="{ folded: waiting }">{{ job.log || "等待输出…" }}</pre>
      <GrillForm
        v-if="waiting && job.grill"
        :job-id="job.id"
        :grill="job.grill"
        @submitted="refresh"
      />
      <p class="text-caption text-medium-emphasis mt-2.5 mb-0">{{ hint }}</p>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { mdiStopCircleOutline } from "@mdi/js";
import { ApiError, cancelJob, getJob } from "@/api/client";
import type { JobSnapshot } from "@/api/types";
import { openPi } from "@/state/pi";
import { ACTION_LABELS } from "@/composables/labels";
import { useSnack } from "@/composables/snack";
import GrillForm from "./GrillForm.vue";

const TERMINAL = ["ok", "error", "cancelled"];

function isTerminal(state: string): boolean {
  return TERMINAL.includes(state);
}

const props = defineProps<{ jobId: string; initial?: JobSnapshot | null }>();
const emit = defineEmits<{ done: [job: JobSnapshot]; update: [job: JobSnapshot] }>();
const snack = useSnack();

function isMissingJob(e: unknown): boolean {
  if (e instanceof ApiError && e.status === 404) return true;
  if (
    typeof e === "object" &&
    e !== null &&
    "status" in e &&
    (e as { status: unknown }).status === 404
  ) {
    return true;
  }
  const msg = e instanceof Error ? e.message : String(e);
  return /\b404\b|not found|unknown job/i.test(msg);
}

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
    env_waiting: false,
  },
);

const actionLabel = computed(
  () => job.value.label || ACTION_LABELS[job.value.action] || job.value.action,
);
const waiting = computed(() => job.value.state === "waiting");
const tickets = computed(() => {
  const ids = job.value.ticket_ids || [];
  return ids.length ? " " + ids.join(", ") : "";
});
const stateColor = computed(() => {
  if (job.value.state === "ok") return "success";
  if (job.value.state === "error") return "error";
  if (job.value.state === "cancelled") return "warning";
  if (job.value.state === "waiting") return "primary";
  return "info";
});
const stoppable = computed(() => !isTerminal(job.value.state));
const cancelling = ref(false);

async function cancel() {
  cancelling.value = true;
  try {
    await cancelJob(props.jobId);
  } catch (e) {
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  } finally {
    cancelling.value = false;
  }
}
const hint = computed(() => {
  if (job.value.env_waiting) {
    return "另一个需求正在占用该测试环境，排队等待其释放（同 env 串行）。可用「停止」取消。";
  }
  if (job.value.action === "grill") {
    if (job.value.state === "waiting") return "勾选或改写后提交。有建议的选项已默认选中。";
    if (job.value.state === "running" || job.value.state === "queued") {
      return "正在生成本轮问题…";
    }
    return "对齐按轮提问。每轮生成后在上方表单里选或改，提交后再问下一轮。";
  }
  return "抽取 / Spec / Tickets / Implement / Review 在网页里用 pi -p 一次性跑完。";
});

let es: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function clearReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function apply(next: JobSnapshot) {
  job.value = next;
  emit("update", next);
  if (isTerminal(next.state)) {
    clearReconnect();
    es?.close();
    es = null;
    emit("done", next);
  }
}

function refresh() {
  getJob(props.jobId)
    .then(apply)
    .catch((e) => {
      const msg = e instanceof Error ? e.message : String(e);
      if (isMissingJob(e)) {
        clearReconnect();
        es?.close();
        es = null;
        apply({
          ...job.value,
          state: "error",
          log: (job.value.log || "") + (job.value.log ? "\n" : "") + (msg === "unknown job" ? "任务不存在或已结束" : msg),
        });
        return;
      }
      snack.notify(msg, "error");
    });
}

function bind() {
  clearReconnect();
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
    emit("update", job.value);
  });
  es.addEventListener("done", (e) => {
    apply(JSON.parse((e as MessageEvent).data) as JobSnapshot);
  });
  es.onerror = () => {
    if (!es || es.readyState === EventSource.CONNECTING) return;
    const closed = es.readyState === EventSource.CLOSED;
    getJob(props.jobId)
      .then((next) => {
        apply(next);
        if (closed && !isTerminal(next.state)) {
          clearReconnect();
          reconnectTimer = setTimeout(bind, 1500);
        }
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : String(e);
        if (isMissingJob(e)) {
          clearReconnect();
          es?.close();
          es = null;
          apply({
            ...job.value,
            state: "error",
            log: (job.value.log || "") + (job.value.log ? "\n" : "") + (msg === "unknown job" ? "任务不存在或已结束" : msg),
          });
          return;
        }
        snack.notify(msg, "error");
        if (closed) {
          clearReconnect();
          reconnectTimer = setTimeout(bind, 3000);
        }
      });
  };
}

onMounted(bind);
onUnmounted(() => {
  clearReconnect();
  es?.close();
  es = null;
});
watch(
  () => props.jobId,
  () => bind(),
);
</script>

<style scoped>
.job-panel-card {
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
}
.job-log {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  padding: 0.8rem 1rem;
  max-height: 22rem;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
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
