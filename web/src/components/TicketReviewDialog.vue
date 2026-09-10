<template>
  <v-dialog
    :model-value="modelValue"
    max-width="760"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card v-if="ticket" class="review-dialog-card">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-3 px-4 bg-surface-variant">
        <span class="text-subtitle-1 font-weight-bold text-primary">{{ ticket.id }}</span>
        <span v-if="ticket.title" class="text-subtitle-1 font-weight-medium text-truncate mr-1">
          {{ ticket.title }}
        </span>
        <v-chip size="small" variant="tonal" class="repo-chip">
          {{ ticket.repo }}
        </v-chip>
        <v-chip size="small" :color="stateColor" variant="tonal">
          {{ ticket.state }}
        </v-chip>
        <v-spacer />
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

      <v-card-text class="pa-4">
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>

        <div class="mb-3">
          <div class="text-caption font-weight-bold text-medium-emphasis mb-1.5">审查结论</div>
          <v-btn-toggle
            v-model="verdict"
            mandatory
            color="primary"
            density="comfortable"
            class="d-flex"
          >
            <v-btn
              value="failed"
              color="error"
              variant="tonal"
              class="flex-grow-1"
              :prepend-icon="mdiAlertCircleOutline"
            >
              不通过 / 需修复
            </v-btn>
            <v-btn
              value="passed"
              color="success"
              variant="tonal"
              class="flex-grow-1"
              :prepend-icon="mdiCheckCircleOutline"
            >
              人工确认通过
            </v-btn>
          </v-btn-toggle>
        </div>

        <v-alert
          v-if="verdict === 'failed'"
          type="info"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          可在此修改 AI 审查意见或追加你的人工审查要求。AI 修复时将严格遵循这些意见。
        </v-alert>
        <v-alert
          v-else
          type="success"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          确认代码符合要求。若该票有独立 Worktree 子分支，将自动合并并转为 done 状态。
        </v-alert>

        <v-textarea
          v-model="summary"
          label="审查意见与修改要求（Markdown）"
          rows="10"
          auto-grow
          hide-details="auto"
          spellcheck="false"
          placeholder="可直接编辑或在下方追加具体审查意见与修改要求..."
          class="font-mono text-body-2"
        />

        <div v-if="verdict === 'failed'" class="mt-3">
          <v-checkbox
            v-model="autoImplement"
            label="保存后立即启动 Agent 进行修复 (Auto Implement)"
            color="primary"
            density="compact"
            hide-details
          />
        </div>
      </v-card-text>

      <v-divider />

      <v-card-actions class="px-4 py-2">
        <v-spacer />
        <v-btn variant="text" :disabled="loading" @click="$emit('update:modelValue', false)">
          取消
        </v-btn>
        <v-btn
          :color="verdict === 'failed' ? (autoImplement ? 'primary' : 'warning') : 'success'"
          :loading="loading"
          :prepend-icon="verdict === 'failed' && autoImplement ? mdiAutoFix : undefined"
          @click="submit"
        >
          {{ verdict === "failed" ? (autoImplement ? "提交并启动修复" : "保存审查意见") : "确认通过" }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  mdiAlertCircleOutline,
  mdiAutoFix,
  mdiCheckCircleOutline,
  mdiClose,
} from "@mdi/js";
import { submitTicketReview } from "../api/client";
import type { JobSnapshot, Ticket } from "../api/types";
import { phaseColor } from "../composables/labels";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  ticket: Ticket | null;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: boolean): void;
  (e: "reviewed", ticketId: string, jobs: JobSnapshot[]): void;
}>();

const verdict = ref<"failed" | "passed">("failed");
const summary = ref("");
const autoImplement = ref(true);
const loading = ref(false);
const error = ref("");

const stateColor = computed(() => {
  if (!props.ticket?.state) return "";
  return phaseColor(props.ticket.state);
});

watch(
  () => [props.modelValue, props.ticket],
  ([open]) => {
    if (open && props.ticket) {
      summary.value = props.ticket.last_summary || "";
      if (props.ticket.state === "blocked") {
        verdict.value = "failed";
      } else if (props.ticket.state === "done") {
        verdict.value = "passed";
      } else {
        verdict.value = props.ticket.last_summary?.includes("REVIEW_FAILED") ? "failed" : "failed";
      }
      autoImplement.value = true;
      error.value = "";
    }
  },
  { immediate: true },
);

async function submit() {
  if (!props.ticket) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await submitTicketReview(props.jira, props.ticket.id, {
      verdict: verdict.value,
      summary: summary.value,
      auto_implement: verdict.value === "failed" ? autoImplement.value : false,
    });
    emit("update:modelValue", false);
    emit("reviewed", props.ticket.id, res.jobs || []);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.review-dialog-card {
  border-radius: 12px;
}
.font-mono {
  font-family: var(--font-mono, monospace);
}
</style>
