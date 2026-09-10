<template>
  <v-dialog
    :model-value="modelValue"
    max-width="1100"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card class="diff-card">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-3 px-4 bg-surface-variant">
        <span class="text-subtitle-1 font-weight-bold text-primary">{{ ticketId }}</span>
        <span v-if="data?.title" class="text-subtitle-1 font-weight-medium text-truncate mr-1">
          {{ data.title }}
        </span>
        <v-chip v-if="data?.repo" size="small" variant="tonal" class="repo-chip">
          {{ data.repo }}
        </v-chip>
        <v-chip v-if="data?.state" size="small" :color="stateColor" variant="tonal">
          {{ data.state }}
        </v-chip>
        <v-spacer />
        <v-btn
          v-if="data?.diff"
          size="small"
          variant="tonal"
          :prepend-icon="mdiContentCopy"
          class="me-1"
          @click="copyDiff"
        >
          {{ copied ? "已复制" : "复制 Diff" }}
        </v-btn>
        <v-btn
          icon
          size="small"
          variant="text"
          :loading="loading"
          @click="loadDiff"
        >
          <v-icon :icon="mdiRefresh" size="20" />
        </v-btn>
        <v-btn
          icon
          size="small"
          variant="text"
          @click="$emit('update:modelValue', false)"
        >
          <v-icon :icon="mdiClose" size="20" />
        </v-btn>
      </v-card-title>

      <v-divider />

      <v-card-text class="pa-4 diff-card-body">
        <div v-if="loading" class="d-flex justify-center align-center py-12">
          <v-progress-circular indeterminate color="primary" size="48" />
        </div>

        <v-alert v-else-if="error" type="error" variant="tonal" class="mb-4">
          {{ error }}
        </v-alert>

        <template v-else-if="data">
          <v-alert
            v-if="data.message"
            type="info"
            variant="tonal"
            density="compact"
            class="mb-4"
          >
            {{ data.message }}
          </v-alert>

          <!-- Ref info & Base / Head -->
          <div v-if="data.base || data.head" class="d-flex align-center ga-2 mb-3 text-caption text-medium-emphasis">
            <span v-if="data.base">基准: <code>{{ data.base }}</code></span>
            <span v-if="data.base && data.head">›</span>
            <span v-if="data.head">当前: <code>{{ data.head }}</code></span>
          </div>

          <!-- Changed Files Chips -->
          <div v-if="data.files && data.files.length" class="mb-3">
            <div class="text-caption font-weight-bold text-medium-emphasis mb-1.5">
              变更文件 ({{ data.files.length }} 个文件):
            </div>
            <div class="d-flex flex-wrap ga-1.5">
              <v-chip
                v-for="file in data.files"
                :key="file.path"
                size="small"
                variant="outlined"
                class="font-mono text-caption"
              >
                <v-badge
                  inline
                  dot
                  :color="fileStatusColor(file.status)"
                  class="me-1"
                />
                <span class="font-weight-bold me-1 text-uppercase text-medium-emphasis">
                  {{ fileStatusLabel(file.status) }}
                </span>
                <span>{{ file.path }}</span>
              </v-chip>
            </div>
          </div>

          <!-- Git Stat Summary -->
          <div v-if="data.stat" class="mb-3">
            <v-expansion-panels variant="accordion">
              <v-expansion-panel title="文件改动统计 (--stat)">
                <v-expansion-panel-text>
                  <pre class="stat-block">{{ data.stat }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>

          <!-- Git Log Summary -->
          <div v-if="data.log" class="mb-3">
            <v-expansion-panels variant="accordion">
              <v-expansion-panel title="提交记录 (git log)">
                <v-expansion-panel-text>
                  <pre class="stat-block">{{ data.log }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>

          <!-- Diff Code Viewer -->
          <div class="diff-viewer-wrapper">
            <div class="d-flex justify-space-between align-center mb-1 text-caption text-medium-emphasis px-1">
              <span>代码差异 (Unified Diff)</span>
              <span>{{ diffLines.length }} 行</span>
            </div>
            <div class="diff-container font-mono">
              <div
                v-for="(line, idx) in diffLines"
                :key="idx"
                class="diff-line"
                :class="lineClass(line)"
              >
                <span class="line-num">{{ idx + 1 }}</span>
                <span class="line-content">{{ line }}</span>
              </div>
            </div>
          </div>
        </template>
      </v-card-text>

      <v-divider />

      <v-card-actions class="px-4 py-2">
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:modelValue', false)">关闭</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { mdiClose, mdiContentCopy, mdiRefresh } from "@mdi/js";
import { getTicketDiff } from "@/api/client";
import type { TicketDiff } from "@/api/types";
import { ticketColor } from "@/composables/labels";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  ticketId: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [val: boolean];
}>();

const loading = ref(false);
const error = ref("");
const data = ref<TicketDiff | null>(null);
const copied = ref(false);

const stateColor = computed(() => (data.value ? ticketColor(data.value.state) : ""));

const diffLines = computed(() => {
  if (!data.value?.diff) return [];
  return data.value.diff.split("\n");
});

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line-file";
  if (line.startsWith("diff --git") || line.startsWith("index ")) return "diff-line-header";
  if (line.startsWith("@@")) return "diff-line-hunk";
  if (line.startsWith("+")) return "diff-line-add";
  if (line.startsWith("-")) return "diff-line-del";
  if (line.startsWith("git log ") || line.startsWith("untracked:")) return "diff-line-section";
  return "diff-line-context";
}

function fileStatusColor(status: string): string {
  const s = status.toUpperCase();
  if (s === "A") return "success";
  if (s === "D") return "error";
  if (s === "M") return "warning";
  return "info";
}

function fileStatusLabel(status: string): string {
  const s = status.toUpperCase();
  if (s === "A") return "新增";
  if (s === "D") return "删除";
  if (s === "M") return "修改";
  if (s === "R") return "重命名";
  return status;
}

async function loadDiff() {
  if (!props.jira || !props.ticketId) return;
  loading.value = true;
  error.value = "";
  try {
    data.value = await getTicketDiff(props.jira, props.ticketId);
  } catch (e) {
    error.value = (e as Error).message || "获取 Diff 失败";
  } finally {
    loading.value = false;
  }
}

async function copyDiff() {
  if (!data.value?.diff) return;
  try {
    await navigator.clipboard.writeText(data.value.diff);
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 2000);
  } catch (e) {
    console.error("复制失败:", e);
  }
}

watch(
  () => [props.modelValue, props.ticketId],
  ([open]) => {
    if (open && props.jira && props.ticketId) {
      loadDiff();
    }
  },
  { immediate: true },
);
</script>

<style scoped>
.diff-card {
  display: flex;
  flex-direction: column;
  max-height: 88vh;
}

.diff-card-body {
  overflow-y: auto;
}

.font-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.stat-block {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-all;
  background: rgba(var(--v-theme-surface-variant), 0.4);
  padding: 8px 12px;
  border-radius: 4px;
  margin: 0;
}

.diff-viewer-wrapper {
  margin-top: 8px;
}

.diff-container {
  font-size: 12.5px;
  line-height: 1.45;
  background: #1e1e24;
  color: #e2e8f0;
  border-radius: 6px;
  padding: 8px 0;
  overflow-x: auto;
  max-height: 520px;
  overflow-y: auto;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.diff-line {
  display: flex;
  padding: 1px 12px;
  white-space: pre;
  min-height: 20px;
}

.line-num {
  user-select: none;
  width: 38px;
  flex-shrink: 0;
  color: #64748b;
  text-align: right;
  padding-right: 12px;
  font-size: 11px;
}

.line-content {
  flex-grow: 1;
  word-break: break-all;
}

.diff-line-add {
  background: rgba(34, 197, 94, 0.16);
  color: #4ade80;
}

.diff-line-del {
  background: rgba(239, 68, 68, 0.16);
  color: #f87171;
}

.diff-line-hunk {
  background: rgba(59, 130, 246, 0.18);
  color: #93c5fd;
  font-weight: 500;
}

.diff-line-header {
  color: #cbd5e1;
  font-weight: bold;
  background: rgba(255, 255, 255, 0.04);
}

.diff-line-file {
  color: #e2e8f0;
  font-weight: bold;
  background: rgba(255, 255, 255, 0.08);
}

.diff-line-section {
  color: #f59e0b;
  font-weight: bold;
  background: rgba(245, 158, 11, 0.1);
}

.diff-line-context {
  color: #cbd5e1;
}
</style>
