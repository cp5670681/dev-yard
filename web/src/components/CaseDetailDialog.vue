<template>
  <v-dialog
    :model-value="modelValue"
    max-width="1100"
    scrollable
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card class="d-flex flex-column case-dialog">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-2 px-3 bg-surface-variant">
        <v-progress-circular
          v-if="loading"
          indeterminate
          size="16"
          width="2"
          color="primary"
        />
        <code class="text-body-2">{{ caseId }}</code>
        <span class="text-subtitle-2 case-dialog-title text-truncate">
          {{ data?.case.title || "" }}
        </span>
        <template v-if="data">
          <v-chip
            v-if="stateLabel"
            size="x-small"
            :color="stateColor"
            variant="tonal"
            class="jira-lozenge font-weight-medium"
          >
            {{ stateLabel }}
          </v-chip>
          <v-chip
            v-if="data.case.priority"
            size="x-small"
            :color="priorityColor"
            variant="flat"
            class="px-1 font-weight-bold text-caption"
          >
            {{ data.case.priority }}
          </v-chip>
          <v-chip
            v-if="data.case.module"
            size="x-small"
            color="secondary"
            variant="tonal"
            class="px-1 text-caption"
          >
            {{ data.case.module }}
          </v-chip>
          <v-chip
            v-if="data.case.repo"
            size="x-small"
            variant="tonal"
            class="px-1 text-caption"
          >
            {{ data.case.repo }}
          </v-chip>
        </template>
        <v-spacer />
        <v-btn
          :icon="mdiRefresh"
          size="small"
          variant="text"
          title="刷新"
          :disabled="loading"
          @click="load"
        />
        <v-btn :icon="mdiClose" size="small" variant="text" title="关闭" @click="close" />
      </v-card-title>
      <v-divider />

      <v-alert
        v-if="isLiveActive"
        color="primary"
        variant="tonal"
        density="compact"
        class="mx-3 mt-3"
      >
        <div class="d-flex align-center ga-2">
          <v-progress-circular indeterminate size="14" width="2" color="primary" />
          <span class="text-body-2">
            正在测试{{ data?.live?.model ? ` · ${data.live.model}` : "" }}
          </span>
        </div>
      </v-alert>

      <v-card-text class="flex-grow-1 overflow-y-auto">
        <v-alert v-if="error" type="error" variant="tonal" class="mb-3">
          {{ error }}
        </v-alert>
        <div v-else-if="loading" class="d-flex justify-center py-10">
          <v-progress-circular indeterminate size="28" width="3" color="primary" />
        </div>
        <template v-else-if="data">
          <v-expansion-panels v-model="panels" variant="accordion" multiple>
            <v-expansion-panel>
              <v-expansion-panel-title>
                <div class="d-flex flex-wrap align-center ga-2">
                  <span class="font-weight-medium">执行结果</span>
                  <template v-if="latest">
                    <code class="text-caption">{{ latest.run_id }}</code>
                    <v-chip v-if="latest.env" size="x-small" variant="tonal" class="px-1">
                      {{ latest.env }}
                    </v-chip>
                    <v-chip
                      v-if="pass.total"
                      size="x-small"
                      :color="pass.passed === pass.total ? 'success' : 'error'"
                      variant="tonal"
                      class="px-1 font-weight-medium"
                    >
                      断言 {{ pass.passed }}/{{ pass.total }}
                    </v-chip>
                    <span v-if="latest.model" class="text-caption text-medium-emphasis">
                      {{ latest.model }}
                    </span>
                  </template>
                </div>
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <div v-if="!latest" class="text-body-2 text-medium-emphasis py-2">
                  尚未执行过。
                </div>
                <template v-else>
                  <v-alert
                    v-if="latest.failure"
                    type="error"
                    variant="tonal"
                    density="compact"
                    class="mb-3"
                    :icon="mdiAlertCircleOutline"
                  >
                    <div class="font-weight-medium">
                      步骤 {{ latest.failure.step || "?" }}: {{ latest.failure.step_desc }}
                    </div>
                    <div v-if="latest.reason" class="text-body-2 text-medium-emphasis mt-1 pre-wrap">
                      {{ latest.reason }}
                    </div>
                  </v-alert>
                  <v-table v-if="latest.assertions?.length" density="compact" class="assert-table">
                    <thead>
                      <tr>
                        <th class="assert-col-type">类型</th>
                        <th>预期</th>
                        <th>实际</th>
                        <th class="assert-col-result">结果</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(a, i) in latest.assertions"
                        :key="i"
                        :class="{ 'assert-row--failed': a.status !== 'passed' }"
                      >
                        <td>
                          <v-chip
                            size="x-small"
                            variant="tonal"
                            :color="qaTypeColor(a.type)"
                            class="px-1"
                          >
                            {{ a.type || "-" }}
                          </v-chip>
                        </td>
                        <td class="assert-cell">{{ a.expected }}</td>
                        <td class="assert-cell">{{ a.actual }}</td>
                        <td>
                          <v-icon
                            :icon="a.status === 'passed' ? mdiCheck : mdiCloseThick"
                            :color="a.status === 'passed' ? 'success' : 'error'"
                            size="16"
                          />
                        </td>
                      </tr>
                    </tbody>
                  </v-table>
                  <div v-else class="text-body-2 text-medium-emphasis py-1">
                    本轮无断言记录（可能被阻塞或跳过）。
                  </div>
                  <div v-if="shotItems.length" class="mt-3">
                    <div class="text-caption text-medium-emphasis mb-1">
                      截图（点击放大，←/→ 切换）
                    </div>
                    <v-row>
                      <v-col
                        v-for="(shot, i) in shotItems"
                        :key="shot.url"
                        cols="6"
                        md="3"
                      >
                        <v-card
                          variant="tonal"
                          class="cursor-pointer"
                          @click="openViewer(i)"
                        >
                          <v-img :src="shot.url" :alt="shot.caption || ''" height="96" cover />
                          <v-card-subtitle class="text-truncate pa-1">
                            {{ shot.caption }}
                          </v-card-subtitle>
                        </v-card>
                      </v-col>
                    </v-row>
                  </div>
                </template>
              </v-expansion-panel-text>
            </v-expansion-panel>

            <v-expansion-panel title="用例定义">
              <v-expansion-panel-text>
                <div class="markdown" v-html="data.html" />
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </template>
      </v-card-text>

      <v-divider />
      <v-card-actions>
        <v-btn
          v-if="canFileBug"
          color="error"
          variant="tonal"
          :prepend-icon="mdiBugOutline"
          :loading="filing"
          @click="fileBug"
        >
          下 bug
        </v-btn>
        <v-spacer />
        <v-btn variant="text" @click="close">关闭</v-btn>
      </v-card-actions>

      <ScreenshotViewer
        v-model="viewer.open"
        v-model:index="viewer.index"
        :images="shotItems"
      />
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import {
  mdiAlertCircleOutline,
  mdiBugOutline,
  mdiCheck,
  mdiClose,
  mdiCloseThick,
  mdiRefresh,
} from "@mdi/js";
import { fileCaseBug, getQaCase } from "@/api/client";
import type { QaCaseDetail } from "@/api/types";
import { QA_STATE_COLOR, QA_STATE_LABELS } from "@/composables/labels";
import {
  assertionPassCount,
  caseShotItems,
  qaTypeColor,
} from "@/composables/qa";
import { useSnack } from "@/composables/snack";
import ScreenshotViewer from "./ScreenshotViewer.vue";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  caseId: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [boolean];
}>();

const snack = useSnack();
const loading = ref(false);
const filing = ref(false);
const error = ref("");
const data = ref<QaCaseDetail | null>(null);
const panels = ref<number[]>([0]);
const viewer = reactive({ open: false, index: 0 });

const latest = computed(() => data.value?.latest_run || null);
const pass = computed(() => assertionPassCount(latest.value?.assertions));

// A failed/blocked case can be opened as a bug ticket by hand.
const canFileBug = computed(() => {
  const state = latest.value?.state || data.value?.live?.state || "";
  return state === "failed" || state === "blocked";
});

async function fileBug() {
  if (!props.caseId || filing.value) return;
  filing.value = true;
  error.value = "";
  try {
    const out = await fileCaseBug(props.jira, props.caseId);
    snack.notify(`已下 bug 票 ${out.ticket_id}（${out.repo}）`, "success");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    error.value = msg;
    snack.notify(msg, "error");
  } finally {
    filing.value = false;
  }
}

const isLiveActive = computed(() => {
  const state = data.value?.live?.state || "";
  return ["pending", "ready", "running"].includes(state);
});

const stateLabel = computed(() => {
  const state = latest.value?.state || data.value?.live?.state || "";
  return state ? QA_STATE_LABELS[state] || state : "";
});

const stateColor = computed(() => {
  const state = latest.value?.state || data.value?.live?.state || "";
  return QA_STATE_COLOR[state] || "grey";
});

const priorityColor = computed(() => {
  const p = (data.value?.case.priority || "").toUpperCase();
  if (p === "P0") return "error";
  if (p === "P1") return "warning";
  if (p === "P2") return "primary";
  return "grey";
});

const shotItems = computed(() => {
  const run = latest.value;
  if (!run || !run.run_id) return [];
  return caseShotItems(
    props.jira,
    run.run_id,
    data.value?.case.id || props.caseId,
    run.screenshots || [],
    run.failure?.evidence,
  );
});

function openViewer(index: number) {
  viewer.index = index;
  viewer.open = true;
}

function close() {
  emit("update:modelValue", false);
}

async function load() {
  if (!props.jira || !props.caseId) return;
  loading.value = true;
  error.value = "";
  try {
    data.value = await getQaCase(props.jira, props.caseId);
    panels.value = [0];
    viewer.open = false;
  } catch (e) {
    data.value = null;
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.modelValue, props.caseId],
  ([open], prev) => {
    if (!open) return;
    // caseId change or fresh open: refetch; in-place refresh keeps panels.
    if (!prev || prev[1] !== props.caseId || !prev[0]) {
      void load();
    }
  },
  { immediate: true },
);
</script>

<style scoped>
.case-dialog {
  max-height: 90vh;
}
.case-dialog-title {
  min-width: 120px;
  max-width: 340px;
}
.assert-table {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
}
.assert-col-type {
  width: 72px;
}
.assert-col-result {
  width: 60px;
}
.assert-cell {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.8rem;
  line-height: 1.45;
  vertical-align: top;
  min-width: 200px;
}
.assert-row--failed {
  background: rgba(var(--v-theme-error), 0.06);
}
:global(.v-theme--dark) .assert-row--failed {
  background: rgba(var(--v-theme-error), 0.12);
}
.pre-wrap {
  white-space: pre-wrap;
  word-break: break-word;
}
.cursor-pointer {
  cursor: pointer;
}
</style>
