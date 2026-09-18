<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 mb-1" />
    <h1 class="text-h5 text-sm-h4 mb-1">测试</h1>
    <p class="text-medium-emphasis mb-4">只读。改预期等于洗白失败。</p>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <ReqDocTabs v-if="docs.length" :jira="jira" :docs="docs" current="qa" />
    <v-tabs v-else class="mb-4" show-arrows color="primary">
      <v-tab :to="`/r/${jira}`">看板</v-tab>
      <v-tab :to="`/r/${jira}/qa`">测试</v-tab>
    </v-tabs>

    <v-empty-state
      v-if="empty"
      title="还没有用例"
      text="提测后点自动测，或 `dev-yard req test --design-only`。撞到缺 qa.yaml 就先配测试环境。"
    >
      <template #actions>
        <v-btn to="/qa-config" variant="tonal" :prepend-icon="mdiClipboardCheckOutline">
          去配置测试环境
        </v-btn>
      </template>
    </v-empty-state>

    <template v-if="payload">
      <!-- Run selector + summary chips -->
      <div v-if="runs.length" class="d-flex flex-wrap align-center ga-2 mb-3">
        <v-select
          v-model="selectedRunId"
          :items="runItems"
          label="执行轮次"
          density="compact"
          hide-details
          variant="outlined"
          class="run-select"
        />
        <template v-if="showSummary">
          <v-chip size="small" color="success" variant="tonal">
            通过 {{ summary.passed || 0 }}
          </v-chip>
          <v-chip size="small" color="error" variant="tonal">
            失败 {{ summary.failed || 0 }}
          </v-chip>
          <v-chip size="small" color="warning" variant="tonal">
            阻塞 {{ summary.blocked || 0 }}
          </v-chip>
          <v-chip v-if="summary.skipped" size="small" color="grey" variant="tonal">
            跳过 {{ summary.skipped }}
          </v-chip>
        </template>
        <v-chip v-else-if="liveActive" size="small" color="warning" variant="flat">
          <v-progress-circular indeterminate size="10" width="2" class="mr-1" />
          执行中
        </v-chip>
      </div>

      <!-- Live banner (newest run in flight) -->
      <v-card v-if="liveActive" class="mb-4" variant="tonal" color="warning">
        <v-card-text>
          <p class="text-caption mb-2">
            <span v-for="(p, i) in livePools" :key="p.id">
              <span v-if="i"> · </span>{{ p.model || p.id }} {{ p.inflight }}/{{ p.concurrency }}
            </span>
          </p>
          <div class="d-flex flex-wrap ga-2 mb-2">
            <v-chip
              v-for="c in liveRunning"
              :key="c.id"
              size="small"
              color="primary"
              variant="tonal"
              class="cursor-pointer"
              :title="`查看用例 ${c.id} 详情`"
              @click="openCaseDetail(c.id)"
            >
              {{ c.id }} {{ c.title }} · {{ c.model }}
            </v-chip>
          </div>
          <p v-if="liveReady.length" class="text-caption text-medium-emphasis mb-0">
            就绪未派发 {{ liveReady.map((c) => c.id).join(", ") }}
          </p>
        </v-card-text>
      </v-card>

      <!-- New failures vs previous run -->
      <v-alert
        v-if="newFailures.length && !newFailuresDismissed"
        type="warning"
        variant="tonal"
        border="start"
        closable
        class="mb-4"
        @click:close="newFailuresDismissed = true"
      >
        <div class="d-flex flex-wrap align-center ga-1">
          <span class="font-weight-medium">较上轮新增失败 {{ newFailures.length }} 条：</span>
          <v-chip
            v-for="c in newFailures"
            :key="c.case"
            size="small"
            color="error"
            variant="tonal"
            class="cursor-pointer"
            @click="openCaseDetail(c.case)"
          >
            {{ c.case }}
          </v-chip>
        </div>
      </v-alert>

      <!-- Per-case table for selected run -->
      <v-card v-if="selectedRun" variant="outlined" class="mb-4">
        <v-card-title class="py-2 px-3">本轮执行（{{ selectedRun.cases.length }} 条）</v-card-title>
        <v-table v-if="selectedRun.cases.length" density="compact" hover>
          <thead>
            <tr>
              <th class="pr-1" style="width: 84px">状态</th>
              <th>用例</th>
              <th style="width: 72px">断言</th>
              <th style="width: 26%">失败原因</th>
              <th style="width: 130px">截图</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in selectedRun.cases"
              :key="c.case"
              class="cursor-pointer case-row"
              :title="`查看 ${c.case} 详情`"
              @click="openCaseDetail(c.case)"
            >
              <td class="pr-1">
                <v-progress-circular
                  v-if="isRunningCase(c)"
                  indeterminate
                  size="12"
                  width="2"
                  class="mr-1"
                />
                <v-chip size="x-small" :color="statusColor(c.status)" variant="tonal">
                  {{ c.status || "未跑" }}
                </v-chip>
              </td>
              <td>
                <v-tooltip location="top" max-width="420" :disabled="!c.title">
                  <template #activator="{ props: tip }">
                    <span v-bind="tip" class="text-truncate d-inline-block" style="max-width: 100%">
                      <code class="mr-1">{{ c.case }}</code>{{ c.title || "" }}
                    </span>
                  </template>
                  <span>{{ c.title || c.case }}</span>
                </v-tooltip>
                <span v-if="c.model" class="text-caption text-medium-emphasis ml-2 d-none d-md-inline">
                  {{ c.model }}
                </span>
              </td>
              <td>
                <v-chip
                  v-if="casePass(c).total"
                  size="x-small"
                  :color="casePass(c).passed === casePass(c).total ? 'success' : 'error'"
                  variant="tonal"
                >
                  {{ casePass(c).passed }}/{{ casePass(c).total }}
                </v-chip>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
              <td>
                <span
                  v-if="c.failure?.step_desc"
                  class="text-error text-caption reason-cell d-inline-block text-truncate"
                  :title="failTitle(c)"
                >
                  步骤 {{ c.failure.step || "?" }}: {{ c.failure.step_desc }}
                </span>
                <span v-else-if="c.reason" class="text-caption text-medium-emphasis reason-cell d-inline-block text-truncate" :title="c.reason">
                  {{ c.reason }}
                </span>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
              <td @click.stop>
                <div v-if="caseShots(c).length" class="d-flex ga-1 align-center">
                  <v-img
                    v-for="(shot, i) in caseShots(c).slice(0, 2)"
                    :key="shot.url"
                    :src="shot.url"
                    :alt="shot.caption || ''"
                    width="40"
                    height="28"
                    cover
                    class="rounded-sm cursor-pointer shot-thumb"
                    :title="shot.caption"
                    @click="openViewer(c, i)"
                  />
                  <span
                    v-if="caseShots(c).length > 2"
                    class="text-caption text-primary cursor-pointer flex-shrink-0"
                    @click="openViewer(c, 2)"
                  >
                    +{{ caseShots(c).length - 2 }}
                  </span>
                </div>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
            </tr>
          </tbody>
        </v-table>
        <v-card-text v-else class="text-medium-emphasis text-body-2 py-6 text-center">
          本轮还没有执行记录。
        </v-card-text>
      </v-card>

      <!-- Collapsed sections: definitions / changes / run history -->
      <v-expansion-panels class="mb-4" variant="accordion">
        <v-expansion-panel v-if="payload.cases.length" title="用例定义">
          <v-expansion-panel-text>
            <v-list density="compact">
              <v-list-item
                v-for="c in payload.cases"
                :key="c.id"
                class="cursor-pointer"
                :title="`查看 ${c.id} 详情`"
                @click="openCaseDetail(c.id)"
              >
                <v-list-item-title>
                  <code>{{ c.id }}</code>
                  {{ c.title }}
                </v-list-item-title>
                <v-list-item-subtitle>
                  {{ c.priority || "-" }} · {{ c.repo || "-" }} · covers {{ (c.covers || []).join(", ") || "-" }}
                  · {{ latestStatus(c.id) }}
                </v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-expansion-panel-text>
        </v-expansion-panel>

        <v-expansion-panel v-if="changes.length" title="改动点">
          <v-expansion-panel-text>
            <v-table density="compact">
              <thead>
                <tr>
                  <th>id</th>
                  <th>repo</th>
                  <th>ref</th>
                  <th>desc</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ch in changes" :key="ch.id">
                  <td><code>{{ ch.id }}</code></td>
                  <td>{{ ch.repo }}</td>
                  <td class="text-break">{{ ch.ref }}</td>
                  <td>{{ ch.desc }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>

        <v-expansion-panel v-if="runs.length" title="历史轮次">
          <v-expansion-panel-text>
            <v-table density="compact">
              <thead>
                <tr>
                  <th>run</th>
                  <th>env</th>
                  <th>结果</th>
                  <th style="width: 80px"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="run in runs" :key="run.run_id">
                  <td>
                    <code>{{ run.run_id }}</code>
                    <v-chip v-if="run.status === 'unreadable'" size="x-small" color="error" variant="tonal" class="ml-1">
                      无法解析
                    </v-chip>
                    <v-chip v-if="selectedRunId === run.run_id" size="x-small" color="primary" variant="tonal" class="ml-1">
                      当前
                    </v-chip>
                  </td>
                  <td>{{ run.env || "-" }}</td>
                  <td class="text-caption">
                    passed {{ run.summary?.passed || 0 }} /
                    failed {{ run.summary?.failed || 0 }} /
                    blocked {{ run.summary?.blocked || 0 }} /
                    skipped {{ run.summary?.skipped || 0 }}
                  </td>
                  <td>
                    <v-btn
                      v-if="selectedRunId !== run.run_id"
                      size="x-small"
                      variant="tonal"
                      @click="selectRun(run.run_id)"
                    >
                      查看
                    </v-btn>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>

    <CaseDetailDialog v-model="caseDialog.open" :jira="jira" :case-id="caseDialog.caseId" />
    <ScreenshotViewer
      v-model="viewer.open"
      v-model:index="viewer.index"
      :images="viewer.images"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { mdiClipboardCheckOutline } from "@mdi/js";
import { getQa, getRequirement } from "@/api/client";
import type { DocMeta, QaPage, ShotItem } from "@/api/types";
import CaseDetailDialog from "@/components/CaseDetailDialog.vue";
import ReqDocTabs from "@/components/ReqDocTabs.vue";
import ScreenshotViewer from "@/components/ScreenshotViewer.vue";
import { assertionPassCount, caseShotItems } from "@/composables/qa";
import { runningJobs, watchJobs } from "@/state/jobs";

const route = useRoute();
const router = useRouter();
const jira = computed(() => String(route.params.jira || ""));
const payload = ref<QaPage | null>(null);
const docs = ref<DocMeta[]>([]);
const error = ref("");
const selectedRunId = ref("");
const newFailuresDismissed = ref(false);
const caseDialog = reactive({ open: false, caseId: "" });
const caseFromQuery = ref(false);
const viewer = reactive({ open: false, index: 0, images: [] as ShotItem[] });

const crumbs = computed(() => [
  { title: "需求", to: "/" },
  { title: jira.value, to: `/r/${jira.value}` },
  { title: "测试", disabled: true },
]);

const empty = computed(() => {
  if (!payload.value) return false;
  return !payload.value.cases.length && !payload.value.runs.length && !payload.value.meta;
});

const changes = computed(() => {
  const raw = payload.value?.meta?.changes;
  return Array.isArray(raw) ? raw : [];
});

const runs = computed(() => payload.value?.runs || []);

const selectedRun = computed(() => {
  const list = runs.value;
  return list.find((r) => r.run_id === selectedRunId.value) || list[0] || null;
});

const summary = computed(() => selectedRun.value?.summary || {});

const liveProgress = computed(() => selectedRun.value?.progress || null);

const liveActive = computed(() => {
  if (!selectedRun.value || selectedRun.value !== runs.value[0]) return false;
  return Boolean(
    liveProgress.value?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state),
    ),
  );
});

const showSummary = computed(
  () => Boolean(summary.value && (summary.value.total || selectedRun.value?.cases.length)),
);

const livePools = computed(() => liveProgress.value?.pools || []);
const liveRunning = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "running") || [],
);
const liveReady = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "ready") || [],
);

const runItems = computed(() =>
  runs.value.map((run) => ({
    title: `${run.run_id} · ${run.env || "-"} · ✓${run.summary?.passed || 0} ✗${run.summary?.failed || 0}${
      run.status === "unreadable" ? " · 无法解析" : ""
    }`,
    value: run.run_id,
  })),
);

const newFailures = computed(() => {
  const run = selectedRun.value;
  if (!run) return [];
  const idx = runs.value.findIndex((r) => r.run_id === run.run_id);
  const prev = idx >= 0 ? runs.value[idx + 1] : undefined;
  if (!prev) return [];
  return run.cases.filter((c) => {
    if (c.status !== "failed" && c.status !== "blocked") return false;
    const before = prev.cases.find((p) => p.case === c.case);
    return !before || (before.status !== "failed" && before.status !== "blocked");
  });
});

const hasActiveRun = computed(() =>
  Boolean(
    runs.value[0]?.progress?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state),
    ),
  ),
);

function isRunningCase(c: QaPage["runs"][number]["cases"][number]) {
  const live = liveProgress.value?.cases?.find((x) => x.id === c.case);
  return live?.state === "running";
}

function casePass(c: QaPage["runs"][number]["cases"][number]) {
  return assertionPassCount(c.assertions);
}

function failTitle(c: QaPage["runs"][number]["cases"][number]): string {
  return [c.failure?.step_desc, c.reason].filter(Boolean).join("\n");
}

function caseShots(c: QaPage["runs"][number]["cases"][number]): ShotItem[] {
  const runId = selectedRun.value?.run_id || "";
  if (!runId) return [];
  return caseShotItems(jira.value, runId, c.case, c.screenshots || [], c.failure?.evidence);
}

function openViewer(c: QaPage["runs"][number]["cases"][number], index: number) {
  viewer.images = caseShots(c);
  viewer.index = index;
  viewer.open = true;
}

function openCaseDetail(caseId: string, fromQuery = false) {
  if (!caseId) return;
  caseDialog.caseId = caseId;
  caseDialog.open = true;
  caseFromQuery.value = fromQuery;
}

function selectRun(runId: string) {
  selectedRunId.value = runId;
  newFailuresDismissed.value = false;
  if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
}

function statusColor(status: string) {
  if (status === "passed") return "success";
  if (status === "failed") return "error";
  if (status === "blocked") return "warning";
  if (status === "skipped") return "grey";
  return "info";
}

function latestStatus(id: string) {
  const run = runs.value[0];
  if (!run) return "未跑";
  const live = run.progress?.cases?.find((c) => c.id === id);
  if (live?.state === "running") return `${live.model || ""} 在跑`.trim();
  const row = run.cases.find((c) => c.case === id);
  return row?.status || "未跑";
}

async function load() {
  error.value = "";
  try {
    const [qa, detail] = await Promise.all([getQa(jira.value), getRequirement(jira.value)]);
    payload.value = qa;
    docs.value = detail.docs || [];
    const ids = qa.runs.map((r) => r.run_id);
    if (!ids.includes(selectedRunId.value)) {
      selectedRunId.value = ids[0] || "";
      newFailuresDismissed.value = false;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

// Deep link: /r/:jira/qa?case=case-XX opens the dialog once.
watch(
  () => route.query.case,
  (value) => {
    const caseId = String(value || "");
    if (caseId) openCaseDetail(caseId, true);
  },
  { immediate: true },
);

watch(
  () => caseDialog.open,
  (open) => {
    if (open || !caseFromQuery.value) return;
    caseFromQuery.value = false;
    const query = { ...route.query };
    delete query.case;
    void router.replace({ query });
  },
);

// Auto-refresh: SSE for web jobs, interval for CLI-started runs.
let stopJobs: (() => void) | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;

watch(runningJobs, (jobs, prev) => {
  const mine = (list: typeof jobs) =>
    list.some((j) => j.jira === jira.value && j.action === "run-test");
  if (mine(jobs) && !mine(prev || [])) void load();
});

watch(
  hasActiveRun,
  (active) => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
    if (active) pollTimer = setInterval(() => void load(), 5000);
  },
  { immediate: true },
);

onMounted(() => {
  stopJobs = watchJobs();
  void load();
});
onUnmounted(() => {
  stopJobs?.();
  if (pollTimer) clearInterval(pollTimer);
});
watch(jira, load);
</script>

<style scoped>
.run-select {
  max-width: 460px;
  min-width: 280px;
}
.case-row:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}
.reason-cell {
  max-width: 100%;
  vertical-align: middle;
}
.shot-thumb {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.cursor-pointer {
  cursor: pointer;
}
</style>
