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
      text="提测后点自动测，或 `dev-yard req test --design-only`。"
    />

    <v-card v-if="payload?.meta && payload.meta.status !== 'unreadable'" class="mb-4" variant="outlined">
      <v-card-title>改动点</v-card-title>
      <v-card-text>
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
      </v-card-text>
    </v-card>

    <v-card v-if="payload?.cases.length" class="mb-4" variant="outlined">
      <v-card-title>用例</v-card-title>
      <v-list density="compact">
        <v-list-item
          v-for="c in payload.cases"
          :key="c.id"
          :active="selectedCase === c.id"
          :to="`/r/${jira}/qa?case=${encodeURIComponent(c.id)}`"
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
      <v-card-text v-if="openCase">
        <div v-if="openCase.status === 'unreadable'" class="text-error">结果文件无法解析</div>
        <div v-else class="markdown" v-html="openCase.html" />
      </v-card-text>
    </v-card>

    <v-expansion-panels v-if="payload?.runs.length" v-model="openRuns" multiple variant="accordion">
      <v-expansion-panel v-for="run in payload.runs" :key="run.run_id">
        <v-expansion-panel-title>
          <div class="d-flex flex-wrap align-center ga-2">
            <span>{{ run.run_id }}</span>
            <v-chip v-if="run.status === 'unreadable'" size="x-small" color="error" variant="tonal">
              无法解析
            </v-chip>
            <span v-else class="text-caption text-medium-emphasis">
              {{ run.env }} · passed {{ run.summary?.passed || 0 }} /
              failed {{ run.summary?.failed || 0 }} /
              blocked {{ run.summary?.blocked || 0 }} /
              skipped {{ run.summary?.skipped || 0 }}
            </span>
          </div>
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <p v-if="run.status === 'unreadable'" class="text-error">结果文件无法解析</p>
          <v-list v-else density="compact">
            <v-list-item v-for="c in run.cases" :key="c.case">
              <v-list-item-title>
                <v-progress-circular
                  v-if="isRunning(run, c)"
                  indeterminate
                  size="12"
                  width="2"
                  class="mr-1"
                />
                <code>{{ c.case }}</code>
                {{ c.title || "" }}
                <v-chip size="x-small" class="ml-2" :color="statusColor(c.status)" variant="tonal">
                  {{ c.status || "未跑" }}
                </v-chip>
                <span v-if="c.model" class="text-caption ml-2">{{ c.model }}</span>
              </v-list-item-title>
              <v-list-item-subtitle v-if="c.reason">{{ c.reason }}</v-list-item-subtitle>
              <div v-if="(c.status === 'failed' || c.status === 'blocked') && shotList(run, c).length" class="mt-2">
                <v-row>
                  <v-col v-for="name in shotList(run, c)" :key="name" cols="6" md="3">
                    <v-card variant="tonal" class="cursor-pointer" @click="preview = shotUrl(run.run_id, c.case, name)">
                      <v-img :src="shotUrl(run.run_id, c.case, name)" :alt="name" height="96" cover />
                      <v-card-subtitle class="text-truncate">{{ name }}</v-card-subtitle>
                    </v-card>
                  </v-col>
                </v-row>
              </div>
            </v-list-item>
          </v-list>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <v-dialog v-model="previewOpen" max-width="960">
      <v-card v-if="preview">
        <v-img :src="preview" />
        <v-card-actions>
          <v-spacer />
          <v-btn :href="preview" target="_blank" variant="text">新窗口</v-btn>
          <v-btn variant="text" @click="preview = ''">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { getQa, getRequirement } from "@/api/client";
import type { DocMeta, QaPage } from "@/api/types";
import ReqDocTabs from "@/components/ReqDocTabs.vue";

const route = useRoute();
const jira = computed(() => String(route.params.jira || ""));
const payload = ref<QaPage | null>(null);
const docs = ref<DocMeta[]>([]);
const error = ref("");
const preview = ref("");
const previewOpen = computed({
  get: () => Boolean(preview.value),
  set: (v: boolean) => {
    if (!v) preview.value = "";
  },
});
const openRuns = ref<number[]>([]);

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

const selectedCase = computed(() => String(route.query.case || ""));
const openCase = computed(() => {
  if (!selectedCase.value || !payload.value) return null;
  return payload.value.cases.find((c) => c.id === selectedCase.value) || null;
});

function latestStatus(id: string) {
  const run = payload.value?.runs[0];
  if (!run) return "未跑";
  const live = run.progress?.cases?.find((c) => c.id === id);
  if (live?.state === "running") return `${live.model || ""} 在跑`.trim();
  const row = run.cases.find((c) => c.case === id);
  return row?.status || "未跑";
}

function isRunning(run: QaPage["runs"][number], c: QaPage["runs"][number]["cases"][number]) {
  const live = run.progress?.cases?.find((x) => x.id === c.case);
  return live?.state === "running";
}

function shotList(_run: QaPage["runs"][number], c: QaPage["runs"][number]["cases"][number]) {
  const names = new Set(c.screenshots || []);
  const ev = c.failure?.evidence;
  if (ev) names.add(String(ev).split("/").pop() || ev);
  return [...names];
}

function shotUrl(runId: string, caseId: string, name: string) {
  return `/r/${encodeURIComponent(jira.value)}/qa/evidence/${encodeURIComponent(runId)}/${encodeURIComponent(caseId)}/screenshots/${encodeURIComponent(name)}`;
}

function statusColor(status: string) {
  if (status === "passed") return "success";
  if (status === "failed") return "error";
  if (status === "blocked") return "warning";
  if (status === "skipped") return "grey";
  return "info";
}

async function load() {
  error.value = "";
  try {
    const [qa, detail] = await Promise.all([getQa(jira.value), getRequirement(jira.value)]);
    payload.value = qa;
    docs.value = detail.docs || [];
    openRuns.value = qa.runs.length ? [0] : [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

onMounted(load);
watch(jira, load);
</script>
