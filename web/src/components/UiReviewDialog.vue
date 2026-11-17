<template>
  <v-dialog
    :model-value="modelValue"
    max-width="840"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card class="ui-review-card">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-3 px-4 bg-surface-variant">
        <v-icon
          :icon="
            verdict === 'passed'
              ? mdiFileDocumentCheckOutline
              : verdict === 'failed'
                ? mdiFileDocumentAlertOutline
                : mdiHelpCircleOutline
          "
          :color="verdict === 'passed' ? 'success' : verdict === 'failed' ? 'error' : 'warning'"
        />
        <span class="text-subtitle-1 font-weight-bold text-primary">{{ jira }}</span>
        <span class="text-subtitle-1 font-weight-medium">原型与 UI 核对报告</span>
        <v-chip
          size="small"
          :color="verdict === 'passed' ? 'success' : verdict === 'failed' ? 'error' : 'warning'"
          variant="tonal"
          class="font-weight-bold"
        >
          {{ verdict === 'passed' ? '通过 PASSED' : verdict === 'failed' ? '未通过 FAILED' : '待选择' }}
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

      <v-card-text class="pa-4" style="max-height: 70vh;">
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>

        <div class="d-flex flex-wrap justify-space-between align-center ga-2 mb-3">
          <div class="text-caption font-weight-bold text-medium-emphasis">核对结论与判定</div>
          <v-btn-toggle
            v-model="mode"
            mandatory
            density="compact"
            color="primary"
            variant="outlined"
          >
            <v-btn value="preview" size="small" :prepend-icon="mdiEyeOutline">
              预览报告
            </v-btn>
            <v-btn value="edit" size="small" :prepend-icon="mdiPencilOutline">
              编辑意见
            </v-btn>
          </v-btn-toggle>
        </div>

        <div class="mb-3">
          <v-btn-toggle
            v-model="verdict"
            :mandatory="uiReview !== 'inconclusive'"
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
              未通过 (FAILED)
            </v-btn>
            <v-btn
              value="passed"
              color="success"
              variant="tonal"
              class="flex-grow-1"
              :prepend-icon="mdiCheckCircleOutline"
            >
              通过 (PASSED)
            </v-btn>
          </v-btn-toggle>
        </div>

        <v-alert
          v-if="verdict === 'failed'"
          type="error"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          确认前端代码与原型图/HTML原型存在偏差。提交后将生成 UI 修复票。
        </v-alert>
        <v-alert
          v-else-if="verdict === 'passed'"
          type="success"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          确认前端页面、组件与交互逻辑已完整还原原型与需求文档。
        </v-alert>

        <!-- 预览模式 -->
        <div v-if="mode === 'preview'" class="preview-container">
          <div v-if="previewHtml" class="markdown" v-html="previewHtml" />
          <pre v-else-if="summaryText" class="job-log">{{ summaryText }}</pre>
          <div
            v-else
            class="text-body-2 text-medium-emphasis pa-4 text-center"
          >
            暂无原型核对报告，点击上方「编辑意见」可手动录入，或运行「原型核对」生成。
          </div>
        </div>

        <!-- 编辑模式 -->
        <div v-else>
          <v-textarea
            v-model="summaryText"
            label="原型核对意见与修改要求（Markdown）"
            rows="12"
            auto-grow
            hide-details="auto"
            spellcheck="false"
            placeholder="可在此编辑或追加前端代码与原型图、HTML原型的出入说明..."
            class="font-mono text-body-2"
          />
        </div>

        <div v-if="verdict === 'failed'" class="mt-3">
          <v-checkbox
            v-model="autoImplement"
            label="保存后立即启动 Agent 进行按原型修复 (Auto Implement --from-ui)"
            color="primary"
            density="compact"
            hide-details
          />
        </div>
      </v-card-text>

      <v-divider />

      <v-card-actions class="px-4 py-2">
        <v-btn
          v-if="summaryText"
          variant="text"
          size="small"
          :prepend-icon="mdiContentCopy"
          @click="copyReport"
        >
          复制报告
        </v-btn>
        <v-spacer />
        <v-btn variant="text" :disabled="loading" @click="$emit('update:modelValue', false)">
          取消
        </v-btn>
        <v-btn
          :color="verdict === 'failed' ? (autoImplement ? 'primary' : 'warning') : verdict === 'passed' ? 'success' : 'grey'"
          :loading="loading"
          :disabled="!verdict"
          :prepend-icon="verdict === 'failed' && autoImplement ? mdiAutoFix : undefined"
          @click="submit"
        >
          {{
            !verdict
              ? "请选择结论"
              : verdict === "failed"
                ? autoImplement
                  ? "提交并启动按原型修复"
                  : "保存原型意见"
                : "确认原型通过"
          }}
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
  mdiContentCopy,
  mdiEyeOutline,
  mdiFileDocumentAlertOutline,
  mdiFileDocumentCheckOutline,
  mdiHelpCircleOutline,
  mdiPencilOutline,
} from "@mdi/js";
import { submitUiReview } from "@/api/client";
import type { JobSnapshot } from "@/api/types";
import { useSnack } from "@/composables/snack";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  uiReview: string | null;
  summary: string | null;
  summaryHtml?: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: boolean): void;
  (e: "reviewed", payload: { verdict: string; summary: string; jobs: JobSnapshot[] }): void;
}>();

const snack = useSnack();
const mode = ref<"preview" | "edit">("preview");
const verdict = ref<string>("");
const summaryText = ref<string>("");
const autoImplement = ref(true);
const loading = ref(false);
const error = ref("");

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      mode.value = "preview";
      verdict.value = props.uiReview || "";
      summaryText.value = props.summary || "";
      error.value = "";
      autoImplement.value = verdict.value === "failed";
    }
  },
  { immediate: true },
);

const previewHtml = computed(() => {
  if (summaryText.value === props.summary && props.summaryHtml) {
    return props.summaryHtml;
  }
  return "";
});

async function copyReport() {
  if (!summaryText.value) return;
  try {
    await navigator.clipboard.writeText(summaryText.value);
    snack.notify("已复制核对报告到剪贴板", "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

async function submit() {
  if (!verdict.value) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await submitUiReview(props.jira, {
      verdict: verdict.value,
      summary: summaryText.value,
      auto_implement: verdict.value === "failed" && autoImplement.value,
    });
    snack.notify(
      verdict.value === "passed" ? "已确认原型核对通过" : "已更新原型核对意见",
      "success",
    );
    emit("reviewed", {
      verdict: verdict.value,
      summary: summaryText.value,
      jobs: res.jobs || [],
    });
    emit("update:modelValue", false);
  } catch (err: any) {
    error.value = err.message || "提交失败";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.preview-container {
  min-height: 120px;
}
.job-log {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: monospace;
  font-size: 13px;
  line-height: 1.5;
  background-color: rgba(var(--v-theme-surface-variant), 0.3);
  padding: 12px;
  border-radius: 4px;
}
</style>
