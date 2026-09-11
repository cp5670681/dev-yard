<template>
  <v-hover v-slot="{ isHovering, props: hoverProps }">
    <v-card
      v-bind="hoverProps"
      :elevation="0"
      variant="flat"
      class="ticket-card"
      :class="{ 'ticket-card--running': isRunning, 'ticket-card--hover': isHovering }"
    >
      <v-progress-linear
        v-if="isRunning"
        indeterminate
        color="primary"
        height="2.5"
        class="ticket-card-progress"
      />
      <v-card-text class="pa-2">
        <div class="d-flex align-center ga-1 mb-1">
          <v-progress-circular
            v-if="isRunning"
            indeterminate
            size="12"
            width="2"
            color="primary"
            class="mr-0.5 flex-shrink-0"
          />
          <span class="jira-ticket-key text-truncate">{{ ticket.id }}</span>
          <v-chip v-if="showState" size="x-small" :color="dotColor" variant="tonal" class="jira-lozenge">
            {{ TICKET_STATE_LABELS[ticket.state] || ticket.state }}
          </v-chip>
          <v-chip v-if="ticket.parallel" size="x-small" color="info" variant="text" class="px-0.5 font-weight-bold text-caption">para</v-chip>
          <v-spacer />
          <span v-if="ticket.repo" class="jira-repo-tag text-truncate">{{ ticket.repo }}</span>
        </div>

        <v-tooltip
          location="top"
          max-width="380"
          interactive
          :close-delay="200"
          :disabled="!shouldShowTitleTip"
        >
          <template #activator="{ props: titleTip }">
            <div
              ref="titleEl"
              v-bind="titleTip"
              class="jira-card-title text-break title-clamped"
            >
              {{ ticket.title || ticket.id }}
            </div>
          </template>
          <span>{{ ticket.title || ticket.id }}</span>
        </v-tooltip>

        <v-tooltip
          v-if="ticket.depends_on?.length"
          location="top"
          max-width="380"
          interactive
          :close-delay="200"
          :text="`依赖: ${ticket.depends_on.join(', ')}`"
          :disabled="!shouldShowDependsTip"
        >
          <template #activator="{ props: depTip }">
            <div
              ref="dependsEl"
              v-bind="depTip"
              class="text-caption text-medium-emphasis mt-1 text-truncate"
            >
              依赖 {{ ticket.depends_on.join(", ") }}
            </div>
          </template>
        </v-tooltip>

        <v-tooltip
          v-if="ticket.last_summary"
          location="top"
          max-width="480"
          interactive
          :close-delay="200"
          :disabled="!shouldShowSummaryTip"
        >
          <template #activator="{ props: sumTip }">
            <div
              ref="summaryEl"
              v-bind="sumTip"
              class="text-caption mt-1.5 summary-clamped"
              :class="isErrorSummary ? 'text-error font-weight-medium' : 'text-medium-emphasis'"
            >
              {{ ticket.last_summary }}
            </div>
          </template>
          <div class="tooltip-summary">
            <div v-if="isErrorSummary" class="text-caption font-weight-bold text-error mb-1">
              错误 / 异常信息
            </div>
            <div class="summary-full-text">{{ ticket.last_summary }}</div>
          </div>
        </v-tooltip>

        <div class="ticket-card-actions mt-2">
          <!-- ready -->
          <div v-if="ticket.state === 'ready'" class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              color="primary"
              class="flex-grow-1 font-weight-medium"
              @click="$emit('implement', ticket.id)"
            >
              实现
            </v-btn>
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="px-2"
              disabled
              title="查看代码改动"
            >
              改动
            </v-btn>
          </div>

          <!-- implementing -->
          <div v-else-if="ticket.state === 'implementing'" class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              variant="tonal"
              color="primary"
              class="flex-grow-1 font-weight-medium"
              loading
              disabled
            >
              实现中
            </v-btn>
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="px-2"
              title="查看代码改动"
              @click="$emit('diff', ticket.id)"
            >
              改动
            </v-btn>
          </div>

          <!-- implemented -->
          <div v-else-if="ticket.state === 'implemented'" class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              color="primary"
              class="flex-grow-1 font-weight-medium"
              @click="$emit('review', ticket.id)"
            >
              审查
            </v-btn>
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="px-2"
              title="查看代码改动"
              @click="$emit('diff', ticket.id)"
            >
              改动
            </v-btn>
          </div>

          <!-- reviewing -->
          <div v-else-if="ticket.state === 'reviewing'" class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              variant="tonal"
              color="secondary"
              class="flex-grow-1 font-weight-medium"
              loading
              disabled
            >
              审查中
            </v-btn>
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="px-2"
              title="查看代码改动"
              @click="$emit('diff', ticket.id)"
            >
              改动
            </v-btn>
          </div>

          <!-- blocked (review failed or execution blocked) -->
          <div v-else-if="ticket.state === 'blocked'" class="d-flex flex-column ga-1.5 w-100">
            <div class="d-flex ga-1.5 w-100">
              <v-btn
                size="small"
                density="compact"
                color="primary"
                class="flex-grow-1"
                title="重新触发 AI 审查"
                @click="$emit('review', ticket.id)"
              >
                审查
              </v-btn>
              <v-btn
                size="small"
                density="compact"
                variant="tonal"
                class="flex-grow-1"
                title="重新实现 / AI 修复"
                @click="$emit('implement', ticket.id)"
              >
                实现
              </v-btn>
            </div>
            <div class="d-flex ga-1.5 w-100">
              <v-btn
                size="small"
                density="compact"
                variant="tonal"
                color="warning"
                class="flex-grow-1"
                title="人工复核 / 修改审查要求"
                @click="$emit('feedback', ticket)"
              >
                复核
              </v-btn>
              <v-btn
                size="small"
                density="compact"
                variant="outlined"
                class="flex-grow-1"
                title="查看代码改动"
                @click="$emit('diff', ticket.id)"
              >
                改动
              </v-btn>
            </div>
          </div>

          <!-- done -->
          <div v-else-if="ticket.state === 'done'" class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="flex-grow-1"
              title="查看代码改动"
              @click="$emit('diff', ticket.id)"
            >
              改动
            </v-btn>
            <v-btn
              size="small"
              density="compact"
              variant="tonal"
              color="secondary"
              class="px-2"
              title="人工复核 / 调整审查结果"
              @click="$emit('feedback', ticket)"
            >
              复核
            </v-btn>
          </div>

          <!-- pending / other -->
          <div v-else class="d-flex ga-1.5 w-100">
            <v-btn
              size="small"
              density="compact"
              variant="outlined"
              class="flex-grow-1"
              disabled
            >
              等待中
            </v-btn>
          </div>
        </div>
      </v-card-text>
    </v-card>
  </v-hover>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import type { Ticket } from "@/api/types";
import { ticketColor, TICKET_STATE_LABELS } from "@/composables/labels";
import { runningJobs } from "@/state/jobs";

const props = defineProps<{ ticket: Ticket; showState?: boolean }>();
defineEmits<{
  implement: [id: string];
  review: [id: string];
  diff: [id: string];
  feedback: [ticket: Ticket];
}>();

const dotColor = computed(() => ticketColor(props.ticket.state));

const isRunning = computed(() => {
  if (props.ticket.state === "implementing" || props.ticket.state === "reviewing") {
    return true;
  }
  return runningJobs.value.some(
    (j) =>
      (j.state === "running" || j.state === "queued" || j.state === "waiting") &&
      j.ticket_ids?.includes(props.ticket.id),
  );
});

// 成功摘要也会写 "无 error / warning"；用票状态，不要扫正文。
const isErrorSummary = computed(() => {
  if (!props.ticket.last_summary) return false;
  return props.ticket.state === "blocked";
});

const titleEl = ref<HTMLElement | null>(null);
const dependsEl = ref<HTMLElement | null>(null);
const summaryEl = ref<HTMLElement | null>(null);

const titleDomOverflow = ref(false);
const dependsDomOverflow = ref(false);
const summaryDomOverflow = ref(false);

const shouldShowTitleTip = computed(() => {
  const t = props.ticket.title || "";
  return t.length > 20 || titleDomOverflow.value;
});

const shouldShowDependsTip = computed(() => {
  const deps = props.ticket.depends_on || [];
  return deps.length > 2 || deps.join(", ").length > 14 || dependsDomOverflow.value;
});

const shouldShowSummaryTip = computed(() => {
  const s = props.ticket.last_summary || "";
  if (!s) return false;
  const lineCount = s.split("\n").length;
  return s.length > 70 || lineCount > 4 || isErrorSummary.value || summaryDomOverflow.value;
});

function measureDom() {
  if (titleEl.value) {
    titleDomOverflow.value =
      titleEl.value.scrollHeight > titleEl.value.clientHeight + 1 ||
      titleEl.value.scrollWidth > titleEl.value.clientWidth + 1;
  }
  if (dependsEl.value) {
    dependsDomOverflow.value = dependsEl.value.scrollWidth > dependsEl.value.clientWidth + 1;
  }
  if (summaryEl.value) {
    summaryDomOverflow.value =
      summaryEl.value.scrollHeight > summaryEl.value.clientHeight + 1 ||
      summaryEl.value.scrollWidth > summaryEl.value.clientWidth + 1;
  }
}

onMounted(() => {
  void nextTick(measureDom);
});

watch(
  () => [props.ticket.title, props.ticket.depends_on, props.ticket.last_summary],
  () => {
    void nextTick(measureDom);
  },
  { deep: true },
);
</script>

<style scoped>
.ticket-card {
  position: relative;
  overflow: hidden;
  background: #fff !important;
  border: 1px solid #dfe1e6 !important;
  border-radius: 4px;
  box-shadow: none;
  transition: background-color 0.12s ease, border-color 0.12s ease;
}
.v-theme--dark .ticket-card {
  background: rgb(var(--v-theme-surface)) !important;
  border-color: rgba(255, 255, 255, 0.12) !important;
}
.ticket-card--hover {
  background: #fafbfc !important;
  border-color: #c1c7d0 !important;
}
.ticket-card--running {
  border-color: #4c9aff !important;
}
.ticket-card-progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1;
}
.jira-ticket-key {
  color: #5e6c84;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0;
}
.v-theme--dark .jira-ticket-key {
  color: rgb(var(--v-theme-on-surface-variant));
}
.jira-card-title {
  color: #172b4d;
  font-size: 14px;
  font-weight: 400;
  line-height: 1.35;
}
.v-theme--dark .jira-card-title {
  color: rgb(var(--v-theme-on-surface));
}
.jira-repo-tag {
  max-width: 88px;
  font-size: 11px;
  color: #5e6c84;
  background: #f4f5f7;
  border-radius: 3px;
  padding: 0 5px;
  line-height: 18px;
  flex-shrink: 0;
}
:global(.v-theme--dark) .jira-repo-tag {
  color: rgb(var(--v-theme-on-surface-variant));
  background: rgb(var(--v-theme-surface-variant));
}
.jira-lozenge {
  font-size: 10.5px !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.02em !important;
  border-radius: 3px !important;
  height: 18px !important;
  padding: 0 5px !important;
}
.jira-component-tag {
  font-size: 10.5px !important;
  border-radius: 3px !important;
  height: 18px !important;
  padding: 0 5px !important;
  background: rgba(var(--v-theme-surface-variant), 0.8) !important;
}
.title-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-word;
}
.summary-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-all;
  font-size: 0.75rem;
  line-height: 1.4;
  cursor: pointer;
}
.tooltip-summary {
  max-height: 360px;
  overflow-y: auto;
  font-size: 0.75rem;
  line-height: 1.4;
  padding: 2px 0;
}
.summary-full-text {
  white-space: pre-wrap;
  word-break: break-all;
}
.repo-chip {
  max-width: 80px;
}
</style>

