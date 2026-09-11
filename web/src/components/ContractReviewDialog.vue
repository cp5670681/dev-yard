<template>
  <v-dialog
    :model-value="modelValue"
    max-width="840"
    scrollable
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-card class="contract-review-card">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-3 px-4 bg-surface-variant">
        <v-icon
          :icon="verdict === 'passed' ? mdiFileDocumentCheckOutline : mdiFileDocumentAlertOutline"
          :color="verdict === 'passed' ? 'success' : 'error'"
        />
        <span class="text-subtitle-1 font-weight-bold text-primary">{{ jira }}</span>
        <span class="text-subtitle-1 font-weight-medium">跨仓契约审查</span>
        <v-chip
          size="small"
          :color="verdict === 'passed' ? 'success' : 'error'"
          variant="tonal"
          class="font-weight-bold"
        >
          {{ verdict === 'passed' ? '通过 PASSED' : '未通过 FAILED' }}
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
          <div class="text-caption font-weight-bold text-medium-emphasis">审查结论与判定</div>
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
          可在此修改 AI 契约审查意见或追加人工契约要求。后续点击「按契约修」时，AI 修复将严格遵循这些意见。
        </v-alert>
        <v-alert
          v-else
          type="success"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          确认所有业务仓代码与 SPEC 跨仓契约一致。确认通过后将解锁「提测」阶段。
        </v-alert>

        <!-- 预览模式 -->
        <div v-if="mode === 'preview'" class="preview-container">
          <div v-if="summaryHtml" class="markdown" v-html="summaryHtml" />
          <pre v-else-if="summaryText" class="job-log">{{ summaryText }}</pre>
          <v-empty-state
            v-else
            title="暂无契约审查报告"
            text="点击上方「编辑意见」可手动录入，或运行「契约审查」生成。"
          />
        </div>

        <!-- 编辑模式 -->
        <div v-else>
          <v-textarea
            v-model="summaryText"
            label="契约审查意见与修改要求（Markdown）"
            rows="12"
            auto-grow
            hide-details="auto"
            spellcheck="false"
            placeholder="可在此编辑或追加跨仓契约审查意见、接口与时序缺口..."
            class="font-mono text-body-2"
          />
        </div>

        <div v-if="verdict === 'failed'" class="mt-3">
          <v-checkbox
            v-model="autoImplement"
            label="保存后立即启动 Agent 进行按契约修复 (Auto Implement --from-contract)"
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
          :color="verdict === 'failed' ? (autoImplement ? 'primary' : 'warning') : 'success'"
          :loading="loading"
          :prepend-icon="verdict === 'failed' && autoImplement ? mdiAutoFix : undefined"
          @click="submit"
        >
          {{
            verdict === "failed"
              ? autoImplement
                ? "提交并启动按契约修复"
                : "保存契约意见"
              : "确认契约通过"
          }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  mdiAlertCircleOutline,
  mdiAutoFix,
  mdiCheckCircleOutline,
  mdiClose,
  mdiContentCopy,
  mdiEyeOutline,
  mdiFileDocumentAlertOutline,
  mdiFileDocumentCheckOutline,
  mdiPencilOutline,
} from "@mdi/js";
import { submitContractReview } from "@/api/client";
import type { JobSnapshot } from "@/api/types";
import { useSnack } from "@/composables/snack";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  contract: string | null;
  summary: string | null;
  summaryHtml?: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "reviewed", jobs: JobSnapshot[]): void;
}>();

const snack = useSnack();
const verdict = ref<string>("failed");
const summaryText = ref("");
const autoImplement = ref(true);
const loading = ref(false);
const error = ref("");
const mode = ref<"preview" | "edit">("preview");

watch(
  () => [props.modelValue, props.contract, props.summary],
  ([open]) => {
    if (open) {
      summaryText.value = props.summary || "";
      verdict.value = props.contract === "passed" ? "passed" : "failed";
      autoImplement.value = true;
      error.value = "";
      mode.value = props.summary ? "preview" : "edit";
    }
  },
  { immediate: true },
);

async function copyReport() {
  try {
    await navigator.clipboard.writeText(summaryText.value);
    snack.notify("已复制契约报告", "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    const res = await submitContractReview(props.jira, {
      verdict: verdict.value,
      summary: summaryText.value,
      auto_implement: verdict.value === "failed" && autoImplement.value,
    });
    emit("update:modelValue", false);
    emit("reviewed", res.jobs || []);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.contract-review-card {
  border-radius: 8px;
}
.preview-container {
  min-height: 120px;
}
</style>
