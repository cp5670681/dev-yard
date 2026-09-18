<template>
  <v-hover v-slot="{ isHovering, props: hoverProps }">
    <v-card
      v-bind="hoverProps"
      :elevation="0"
      variant="flat"
      class="qa-test-card"
      :class="{
        'qa-test-card--running': isRunning,
        'qa-test-card--failed': isFailed,
        'qa-test-card--passed': isPassed,
        'qa-test-card--hover': isHovering,
      }"
    >
      <v-progress-linear
        v-if="isRunning"
        indeterminate
        color="primary"
        height="2.5"
        class="qa-card-progress"
      />
      <v-card-text class="pa-2">
        <!-- Top bar: ID, Priority, Module, Repo, Status -->
        <div class="d-flex align-center ga-1 mb-1 flex-wrap">
          <v-progress-circular
            v-if="isRunning"
            indeterminate
            size="12"
            width="2"
            color="primary"
            class="mr-0.5 flex-shrink-0"
          />
          <span class="jira-ticket-key text-truncate font-weight-bold">{{ testCase.id }}</span>
          <v-chip
            v-if="testCase.priority"
            size="x-small"
            :color="priorityColor"
            variant="flat"
            class="px-1 font-weight-bold text-caption"
          >
            {{ testCase.priority }}
          </v-chip>
          <v-chip
            v-if="testCase.module"
            size="x-small"
            color="secondary"
            variant="tonal"
            class="px-1 text-caption"
          >
            {{ testCase.module }}
          </v-chip>
          <v-chip
            v-if="showState"
            size="x-small"
            :color="statusColor"
            variant="tonal"
            class="jira-lozenge font-weight-medium"
          >
            {{ stateLabel }}
          </v-chip>
          <v-chip
            v-if="passCount.total"
            size="x-small"
            :color="passCount.passed === passCount.total ? 'success' : 'error'"
            variant="tonal"
            class="px-1 font-weight-medium"
            :title="`断言通过 ${passCount.passed}/${passCount.total}`"
          >
            {{ passCount.passed }}/{{ passCount.total }}
          </v-chip>
          <v-spacer />
          <span v-if="testCase.repo" class="jira-repo-tag text-truncate">{{ testCase.repo }}</span>
        </div>

        <!-- Case Title -->
        <v-tooltip
          location="top"
          max-width="380"
          interactive
          :close-delay="200"
          :disabled="!testCase.title"
        >
          <template #activator="{ props: titleTip }">
            <div
              v-bind="titleTip"
              class="jira-card-title text-break title-clamped"
            >
              {{ testCase.title || testCase.id }}
            </div>
          </template>
          <span>{{ testCase.title || testCase.id }}</span>
        </v-tooltip>

        <!-- Covers / Depends on -->
        <div v-if="testCase.covers?.length || testCase.depends_on?.length" class="text-caption text-medium-emphasis mt-1 text-truncate">
          <span v-if="testCase.covers?.length" class="mr-2">
            覆盖 {{ testCase.covers.join(", ") }}
          </span>
          <span v-if="testCase.depends_on?.length">
            依赖 {{ testCase.depends_on.join(", ") }}
          </span>
        </div>

        <!-- Running info / Model tag -->
        <div v-if="testCase.model" class="d-flex align-center ga-1 mt-1 text-caption text-medium-emphasis">
          <v-icon :icon="mdiRobotOutline" size="14" />
          <span class="text-truncate">{{ testCase.model }}</span>
        </div>

        <!-- Reason / Failure error message -->
        <v-tooltip
          v-if="testCase.reason || testCase.failure?.step_desc"
          location="top"
          max-width="480"
          interactive
          :close-delay="200"
        >
          <template #activator="{ props: reasonTip }">
            <div
              v-bind="reasonTip"
              class="text-caption mt-1.5 reason-clamped"
              :class="isFailed ? 'text-error font-weight-medium' : 'text-medium-emphasis'"
            >
              <v-icon
                v-if="isFailed"
                :icon="mdiAlertCircleOutline"
                size="14"
                color="error"
                class="mr-0.5"
              />
              {{ testCase.failure?.step_desc ? `步骤 ${testCase.failure.step || ''}: ${testCase.failure.step_desc}` : testCase.reason }}
            </div>
          </template>
          <div class="tooltip-reason">
            <div v-if="isFailed" class="text-caption font-weight-bold text-error mb-1">
              测试失败原因
            </div>
            <div class="reason-full-text">
              {{ testCase.failure?.step_desc ? `步骤 ${testCase.failure.step || ''}: ${testCase.failure.step_desc}\n` : '' }}{{ testCase.reason }}
            </div>
          </div>
        </v-tooltip>

        <!-- Screenshot thumbnail preview for failed tests -->
        <div v-if="hasScreenshots" class="mt-2">
          <div class="d-flex ga-1 overflow-x-auto py-1">
            <div
              v-for="name in (testCase.screenshots || []).slice(0, 3)"
              :key="name"
              class="screenshot-thumb cursor-pointer"
              :title="`点击查看截图: ${name}`"
              @click.stop="openScreenshot(name)"
            >
              <v-img
                :src="screenshotUrl(name)"
                :alt="name"
                width="64"
                height="40"
                cover
                class="rounded-sm"
              />
            </div>
            <div
              v-if="(testCase.screenshots?.length || 0) > 3"
              class="screenshot-more d-flex align-center justify-center rounded-sm text-caption text-medium-emphasis"
              @click.stop="openFirstScreenshot"
            >
              +{{ (testCase.screenshots?.length || 0) - 3 }}
            </div>
          </div>
        </div>

        <!-- Action buttons -->
        <div class="qa-card-actions mt-2 d-flex ga-1.5 w-100">
          <v-btn
            size="small"
            density="compact"
            variant="tonal"
            color="primary"
            class="flex-grow-1 font-weight-medium"
            title="就地查看用例步骤、断言结果与截图"
            @click="emit('open-case', testCase.id)"
          >
            用例详情
          </v-btn>
          <v-btn
            v-if="hasScreenshots"
            size="small"
            density="compact"
            variant="outlined"
            class="px-2"
            title="查看截图"
            @click.stop="openFirstScreenshot"
          >
            截图 ({{ testCase.screenshots?.length || 0 }})
          </v-btn>
        </div>
      </v-card-text>
    </v-card>
  </v-hover>
</template>

<script setup lang="ts">
import { computed } from "vue";
import {
  mdiRobotOutline,
  mdiAlertCircleOutline,
} from "@mdi/js";
import type { QaCaseItem } from "@/api/types";
import { QA_STATE_LABELS, QA_STATE_COLOR } from "@/composables/labels";
import { assertionPassCount, qaScreenshotUrl } from "@/composables/qa";

const props = withDefaults(
  defineProps<{
    testCase: QaCaseItem;
    jira: string;
    showState?: boolean;
  }>(),
  {
    showState: false,
  }
);

const emit = defineEmits<{
  "preview-screenshot": [url: string];
  "open-case": [caseId: string];
}>();

const isRunning = computed(() => props.testCase.state === "running");
const isFailed = computed(
  () => props.testCase.state === "failed" || props.testCase.state === "blocked"
);
const isPassed = computed(() => props.testCase.state === "passed");

const stateLabel = computed(() => {
  return QA_STATE_LABELS[props.testCase.state] || props.testCase.state;
});

const statusColor = computed(() => {
  return QA_STATE_COLOR[props.testCase.state] || "grey";
});

const priorityColor = computed(() => {
  const p = (props.testCase.priority || "").toUpperCase();
  if (p === "P0") return "error";
  if (p === "P1") return "warning";
  if (p === "P2") return "primary";
  return "grey";
});

const hasScreenshots = computed(() => {
  return (props.testCase.screenshots?.length || 0) > 0 && props.testCase.run_id;
});

const passCount = computed(() => assertionPassCount(props.testCase.assertions));

function screenshotUrl(name: string): string {
  return qaScreenshotUrl(
    props.jira,
    props.testCase.run_id || "",
    props.testCase.id,
    name,
  );
}

function openScreenshot(name: string) {
  emit("preview-screenshot", screenshotUrl(name));
}

function openFirstScreenshot() {
  if (props.testCase.screenshots?.length) {
    openScreenshot(props.testCase.screenshots[0]);
  }
}
</script>

<style scoped>
.qa-test-card {
  position: relative;
  background: #ffffff;
  border-radius: 3px;
  box-shadow: 0 1px 1px rgba(9, 30, 66, 0.25), 0 0 1px 1px rgba(9, 30, 66, 0.13);
  transition: box-shadow 0.12s ease-in-out, background-color 0.12s ease-in-out, border-color 0.12s ease-in-out;
  cursor: default;
  user-select: none;
  border-left: 3.5px solid transparent;
}

:global(.v-theme--dark) .qa-test-card {
  background: #22272b;
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.5), 0 0 1px 1px rgba(255, 255, 255, 0.08);
}

.qa-test-card--running {
  border-left-color: rgb(var(--v-theme-primary));
  animation: qa-pulse-border 2s infinite ease-in-out;
}

.qa-test-card--passed {
  border-left-color: rgb(var(--v-theme-success));
}

.qa-test-card--failed {
  border-left-color: rgb(var(--v-theme-error));
}

.qa-test-card--hover {
  background: #f4f5f7;
  box-shadow: 0 4px 8px -2px rgba(9, 30, 66, 0.25), 0 0 1px rgba(9, 30, 66, 0.31);
}

:global(.v-theme--dark) .qa-test-card--hover {
  background: #282e33;
  box-shadow: 0 4px 8px -2px rgba(0, 0, 0, 0.7), 0 0 1px rgba(255, 255, 255, 0.2);
}

.qa-card-progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 2;
  border-top-left-radius: 3px;
  border-top-right-radius: 3px;
}

.jira-ticket-key {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Noto, sans-serif;
  font-size: 11.5px;
  font-weight: 700;
  color: #0052cc;
  letter-spacing: 0.2px;
}

:global(.v-theme--dark) .jira-ticket-key {
  color: #579dff;
}

.jira-repo-tag {
  font-size: 11px;
  font-weight: 500;
  color: #5e6c84;
  background: #ebecf0;
  padding: 1px 5px;
  border-radius: 3px;
  max-width: 90px;
}

:global(.v-theme--dark) .jira-repo-tag {
  color: #b6c2cf;
  background: #282e33;
}

.jira-card-title {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.35;
  color: #172b4d;
}

:global(.v-theme--dark) .jira-card-title {
  color: #dee4ea;
}

.title-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.reason-clamped {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.3;
}

.tooltip-reason {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow-y: auto;
}

.screenshot-thumb {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 3px;
  overflow: hidden;
  transition: transform 0.1s ease;
}

.screenshot-thumb:hover {
  transform: scale(1.05);
}

.screenshot-more {
  width: 32px;
  height: 40px;
  background: rgba(var(--v-theme-on-surface), 0.08);
  cursor: pointer;
  font-size: 11px;
  font-weight: bold;
}

.screenshot-more:hover {
  background: rgba(var(--v-theme-on-surface), 0.15);
}

@keyframes qa-pulse-border {
  0%, 100% {
    border-left-color: rgb(var(--v-theme-primary));
  }
  50% {
    border-left-color: rgba(var(--v-theme-primary), 0.3);
  }
}
</style>
