<template>
  <v-dialog
    :model-value="modelValue"
    max-width="1200"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card class="diff-card">
      <!-- Title Bar -->
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-2.5 px-4 bg-surface-variant">
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

        <!-- Stats Chips -->
        <template v-if="parsedFiles.length">
          <v-chip size="small" variant="flat" color="surface" class="ms-1 font-mono text-caption">
            {{ parsedFiles.length }} 个文件
          </v-chip>
          <v-chip v-if="totalAdditions > 0" size="small" variant="tonal" color="success" class="font-mono text-caption">
            +{{ totalAdditions }}
          </v-chip>
          <v-chip v-if="totalDeletions > 0" size="small" variant="tonal" color="error" class="font-mono text-caption">
            -{{ totalDeletions }}
          </v-chip>
        </template>

        <v-spacer />

        <v-btn
          v-if="parsedFiles.length"
          size="small"
          variant="tonal"
          :prepend-icon="allCollapsed ? mdiUnfoldMoreHorizontal : mdiUnfoldLessHorizontal"
          class="me-1"
          @click="toggleAllCollapse"
        >
          {{ allCollapsed ? "全部展开" : "全部折叠" }}
        </v-btn>

        <v-btn
          v-if="data?.diff"
          size="small"
          variant="tonal"
          :prepend-icon="copied ? mdiCheck : mdiContentCopy"
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

          <!-- Quick Jump File Navigation Bar -->
          <div v-if="parsedFiles.length > 0" class="file-nav-container mb-4">
            <div class="d-flex align-center justify-space-between mb-2">
              <span class="text-caption font-weight-bold text-medium-emphasis">
                文件列表（点击直接跳转至对应文件）：
              </span>
              <span class="text-caption text-medium-emphasis font-mono">
                共 {{ parsedFiles.length }} 个变更文件
              </span>
            </div>
            <div class="d-flex flex-wrap ga-1.5 file-nav-chips">
              <v-chip
                v-for="file in parsedFiles"
                :key="file.id"
                size="small"
                variant="outlined"
                class="font-mono text-caption file-jump-chip cursor-pointer"
                :class="{ 'file-jump-chip-active': activeFileId === file.id }"
                @click="jumpToFile(file.id)"
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
                <span class="file-chip-path text-truncate">{{ file.path }}</span>
                <span v-if="file.additions > 0" class="text-success ms-1.5 font-weight-bold">+{{ file.additions }}</span>
                <span v-if="file.deletions > 0" class="text-error ms-1 font-weight-bold">-{{ file.deletions }}</span>
              </v-chip>
            </div>
          </div>

          <!-- Git Stat Summary -->
          <div v-if="data.stat" class="mb-4">
            <v-expansion-panels variant="accordion">
              <v-expansion-panel title="文件改动统计 (--stat)">
                <v-expansion-panel-text>
                  <pre class="stat-block">{{ data.stat }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>

          <!-- Git Log Summary -->
          <div v-if="data.log" class="mb-4">
            <v-expansion-panels variant="accordion">
              <v-expansion-panel title="提交记录 (git log)">
                <v-expansion-panel-text>
                  <pre class="stat-block">{{ data.log }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </div>

          <!-- File by File Diff Blocks -->
          <div v-if="parsedFiles.length > 0" class="file-diffs-list">
            <v-card
              v-for="file in parsedFiles"
              :id="file.id"
              :key="file.id"
              variant="outlined"
              class="file-diff-card mb-4"
              :class="{ 'file-card-highlight': activeFileId === file.id }"
            >
              <!-- File Diff Header -->
              <div
                class="file-diff-header d-flex align-center px-3 py-2 bg-surface-variant cursor-pointer select-none"
                @click="file.collapsed = !file.collapsed"
              >
                <v-icon
                  :icon="file.collapsed ? mdiChevronRight : mdiChevronDown"
                  size="20"
                  class="me-1.5 text-medium-emphasis"
                />
                <v-chip
                  size="x-small"
                  :color="fileStatusColor(file.status)"
                  variant="flat"
                  class="font-weight-bold me-2 text-uppercase"
                >
                  {{ fileStatusLabel(file.status) }}
                </v-chip>
                <span class="file-path font-mono text-body-2 font-weight-bold text-truncate">
                  {{ file.path }}
                </span>

                <v-spacer />

                <div class="d-flex align-center ga-2 ms-2">
                  <span v-if="file.additions > 0" class="text-caption font-mono font-weight-bold text-success">
                    +{{ file.additions }}
                  </span>
                  <span v-if="file.deletions > 0" class="text-caption font-mono font-weight-bold text-error">
                    -{{ file.deletions }}
                  </span>
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    title="复制文件路径"
                    @click.stop="copyPath(file.path)"
                  >
                    <v-icon :icon="copiedPath === file.path ? mdiCheck : mdiContentCopy" size="16" />
                  </v-btn>
                </div>
              </div>

              <!-- File Diff Body -->
              <v-expand-transition>
                <div v-show="!file.collapsed" class="file-diff-content font-mono">
                  <div v-if="!file.lines.length" class="pa-4 text-center text-caption text-medium-emphasis">
                    (无代码变动)
                  </div>
                  <div
                    v-for="(line, lineIdx) in file.lines"
                    :key="lineIdx"
                    class="diff-row"
                    :class="`diff-row-${line.type}`"
                  >
                    <span class="line-no line-no-old">{{ line.oldLineNo ?? "" }}</span>
                    <span class="line-no line-no-new">{{ line.newLineNo ?? "" }}</span>
                    <span class="line-prefix">{{ linePrefix(line.type) }}</span>
                    <span class="line-text">{{ line.content }}</span>
                  </div>
                </div>
              </v-expand-transition>
            </v-card>
          </div>

          <div v-else-if="!data.message" class="text-center text-medium-emphasis py-8">
            没有检测到代码改动
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
import { computed, nextTick, ref, watch } from "vue";
import {
  mdiCheck,
  mdiChevronDown,
  mdiChevronRight,
  mdiClose,
  mdiContentCopy,
  mdiRefresh,
  mdiUnfoldLessHorizontal,
  mdiUnfoldMoreHorizontal,
} from "@mdi/js";
import { getTicketDiff } from "@/api/client";
import type { TicketDiff } from "@/api/types";
import { ticketColor } from "@/composables/labels";

interface ParsedLine {
  type: "add" | "del" | "hunk" | "context" | "header";
  oldLineNo?: number;
  newLineNo?: number;
  content: string;
}

interface ParsedFileDiff {
  id: string;
  path: string;
  status: string;
  additions: number;
  deletions: number;
  collapsed: boolean;
  lines: ParsedLine[];
}

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  ticketId: string;
}>();

defineEmits<{
  "update:modelValue": [val: boolean];
}>();

const loading = ref(false);
const error = ref("");
const data = ref<TicketDiff | null>(null);
const copied = ref(false);
const copiedPath = ref("");
const activeFileId = ref("");

const stateColor = computed(() => (data.value ? ticketColor(data.value.state) : ""));

const parsedFiles = ref<ParsedFileDiff[]>([]);

const totalAdditions = computed(() =>
  parsedFiles.value.reduce((sum, f) => sum + f.additions, 0),
);
const totalDeletions = computed(() =>
  parsedFiles.value.reduce((sum, f) => sum + f.deletions, 0),
);

const allCollapsed = computed(() =>
  parsedFiles.value.length > 0 && parsedFiles.value.every((f) => f.collapsed),
);

function toggleAllCollapse() {
  const target = !allCollapsed.value;
  parsedFiles.value.forEach((f) => {
    f.collapsed = target;
  });
}

function parseUnifiedDiff(
  rawDiff: string,
  fileList: { path: string; status: string }[],
): ParsedFileDiff[] {
  if (!rawDiff && (!fileList || !fileList.length)) return [];

  const rawChunks: { header: string; lines: string[] }[] = [];
  const lines = rawDiff ? rawDiff.split("\n") : [];
  let currentChunk: { header: string; lines: string[] } | null = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("diff --git ") || line.startsWith("untracked: ")) {
      if (currentChunk) {
        rawChunks.push(currentChunk);
      }
      currentChunk = { header: line, lines: [line] };
    } else if (currentChunk) {
      currentChunk.lines.push(line);
    } else if (line.trim()) {
      currentChunk = { header: line, lines: [line] };
    }
  }
  if (currentChunk) {
    rawChunks.push(currentChunk);
  }

  const result: ParsedFileDiff[] = [];
  const matchedPaths = new Set<string>();

  rawChunks.forEach((chunk, idx) => {
    let path = "";
    let status = "M";

    if (chunk.header.startsWith("diff --git ")) {
      const match = chunk.header.match(/diff --git a\/(.*?) b\/(.*)$/);
      if (match) {
        path = match[2] || match[1];
      }
    } else if (chunk.header.startsWith("untracked: ")) {
      path = chunk.header.replace("untracked: ", "").trim();
      status = "A";
    }

    for (const l of chunk.lines) {
      if (l.startsWith("new file mode")) status = "A";
      else if (l.startsWith("deleted file mode")) status = "D";
      else if (l.startsWith("rename from")) status = "R";
      if (!path) {
        if (l.startsWith("+++ b/")) path = l.slice(6);
        else if (l.startsWith("--- a/")) path = l.slice(6);
      }
    }

    if (!path && fileList[idx]) {
      path = fileList[idx].path;
      status = fileList[idx].status || status;
    }

    const known = fileList.find((f) => f.path === path);
    if (known) {
      status = known.status || status;
      matchedPaths.add(path);
    } else if (path) {
      matchedPaths.add(path);
    }

    let oldLine = 0;
    let newLine = 0;
    let additions = 0;
    let deletions = 0;
    const parsedLines: ParsedLine[] = [];

    let inHunk = false;

    for (const l of chunk.lines) {
      if (l.startsWith("@@")) {
        inHunk = true;
        const hunkMatch = l.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
        if (hunkMatch) {
          oldLine = parseInt(hunkMatch[1], 10);
          newLine = parseInt(hunkMatch[2], 10);
        }
        parsedLines.push({
          type: "hunk",
          content: l,
        });
      } else if (!inHunk) {
        if (
          l.startsWith("diff --git") ||
          l.startsWith("index ") ||
          l.startsWith("--- ") ||
          l.startsWith("+++ ") ||
          l.startsWith("new file mode") ||
          l.startsWith("deleted file mode") ||
          l.startsWith("untracked:")
        ) {
          // skip raw git header metadata to keep view clean, or render header
        } else if (l.trim()) {
          parsedLines.push({
            type: "header",
            content: l,
          });
        }
      } else {
        if (l.startsWith("+") && !l.startsWith("+++")) {
          additions++;
          parsedLines.push({
            type: "add",
            newLineNo: newLine++,
            content: l.slice(1),
          });
        } else if (l.startsWith("-") && !l.startsWith("---")) {
          deletions++;
          parsedLines.push({
            type: "del",
            oldLineNo: oldLine++,
            content: l.slice(1),
          });
        } else if (l.startsWith(" ") || l === "") {
          parsedLines.push({
            type: "context",
            oldLineNo: oldLine++,
            newLineNo: newLine++,
            content: l.startsWith(" ") ? l.slice(1) : l,
          });
        } else if (l.startsWith("\\ No newline at end of file")) {
          parsedLines.push({
            type: "header",
            content: l,
          });
        } else {
          parsedLines.push({
            type: "context",
            content: l,
          });
        }
      }
    }

    result.push({
      id: `diff-file-${result.length}`,
      path: path || `file-${idx + 1}`,
      status,
      additions,
      deletions,
      collapsed: false,
      lines: parsedLines,
    });
  });

  for (const f of fileList) {
    if (!matchedPaths.has(f.path)) {
      result.push({
        id: `diff-file-${result.length}`,
        path: f.path,
        status: f.status || "M",
        additions: 0,
        deletions: 0,
        collapsed: false,
        lines: [{ type: "header", content: `(无文本变动 / 纯元数据变更)` }],
      });
    }
  }

  return result;
}

function linePrefix(type: string): string {
  if (type === "add") return "+";
  if (type === "del") return "-";
  if (type === "hunk") return " ";
  return " ";
}

function fileStatusColor(status: string): string {
  const s = status.toUpperCase();
  if (s === "A") return "success";
  if (s === "D") return "error";
  if (s === "M") return "warning";
  if (s === "R") return "info";
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

async function jumpToFile(fileId: string) {
  const target = parsedFiles.value.find((f) => f.id === fileId);
  if (target) {
    target.collapsed = false;
  }
  activeFileId.value = fileId;
  await nextTick();
  const el = document.getElementById(fileId);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function copyPath(path: string) {
  try {
    await navigator.clipboard.writeText(path);
    copiedPath.value = path;
    setTimeout(() => {
      copiedPath.value = "";
    }, 2000);
  } catch (e) {
    console.error("复制失败:", e);
  }
}

async function loadDiff() {
  if (!props.jira || !props.ticketId) return;
  loading.value = true;
  error.value = "";
  try {
    data.value = await getTicketDiff(props.jira, props.ticketId);
    parsedFiles.value = parseUnifiedDiff(
      data.value.diff || "",
      data.value.files || [],
    );
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
      activeFileId.value = "";
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
  max-height: 90vh;
}

.diff-card-body {
  overflow-y: auto;
}

.font-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.file-nav-container {
  background: rgba(var(--v-theme-surface-variant), 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 10px 12px;
}

.file-jump-chip {
  transition: all 0.2s ease;
}

.file-jump-chip:hover {
  border-color: rgba(var(--v-theme-primary), 0.8);
  background: rgba(var(--v-theme-primary), 0.08);
}

.file-jump-chip-active {
  border-color: rgb(var(--v-theme-primary)) !important;
  background: rgba(var(--v-theme-primary), 0.15) !important;
}

.file-chip-path {
  max-width: 260px;
}

.file-diff-card {
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  overflow: hidden;
  transition: box-shadow 0.25s ease, border-color 0.25s ease;
}

.file-card-highlight {
  border-color: rgba(var(--v-theme-primary), 0.8) !important;
  box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.3);
}

.file-diff-header {
  min-height: 40px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.file-diff-header:hover {
  background: rgba(255, 255, 255, 0.06) !important;
}

.file-diff-content {
  background: #141820;
  font-size: 12.5px;
  line-height: 1.5;
  overflow-x: auto;
}

.diff-row {
  display: flex;
  min-height: 22px;
  white-space: pre;
}

.diff-row:hover {
  background: rgba(255, 255, 255, 0.04);
}

.line-no {
  user-select: none;
  width: 44px;
  min-width: 44px;
  padding: 0 8px;
  text-align: right;
  color: #4a5568;
  font-size: 11px;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
}

.line-prefix {
  user-select: none;
  width: 20px;
  min-width: 20px;
  text-align: center;
  flex-shrink: 0;
  font-weight: bold;
}

.line-text {
  flex-grow: 1;
  padding-right: 16px;
  word-break: break-all;
}

.diff-row-add {
  background: rgba(46, 160, 67, 0.16);
  color: #56d364;
}

.diff-row-add .line-no-new {
  color: #56d364;
}

.diff-row-del {
  background: rgba(248, 81, 73, 0.16);
  color: #f85149;
}

.diff-row-del .line-no-old {
  color: #f85149;
}

.diff-row-hunk {
  background: rgba(56, 139, 253, 0.15);
  color: #79c0ff;
  font-weight: 500;
}

.diff-row-hunk .line-no {
  background: rgba(56, 139, 253, 0.08);
  color: #58a6ff;
}

.diff-row-header {
  color: #8b949e;
  font-style: italic;
  padding-left: 8px;
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

.cursor-pointer {
  cursor: pointer;
}

.select-none {
  user-select: none;
}
</style>
