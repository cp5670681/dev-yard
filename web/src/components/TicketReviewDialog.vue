<template>
  <v-dialog
    :model-value="modelValue"
    max-width="920"
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
            :mandatory="ticket.state !== 'inconclusive'"
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
          v-else-if="!verdict"
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          这次评审没有结论。请先选择通过或不通过。选择不通过后才会启动修复。
        </v-alert>
        <v-alert
          v-else
          type="success"
          variant="tonal"
          density="compact"
          class="mb-3 text-caption"
        >
          只记下通过，不会合进冻结分支。回到卡片后点「合并」。冲突时点「解决冲突」，不用再审一次。
        </v-alert>

        <div class="d-flex justify-end mb-2">
          <v-btn-toggle
            v-model="mode"
            mandatory
            density="compact"
            color="primary"
            variant="outlined"
          >
            <v-btn value="preview" size="small" :prepend-icon="mdiEyeOutline">
              预览
            </v-btn>
            <v-btn value="edit" size="small" :prepend-icon="mdiPencilOutline">
              编辑
            </v-btn>
          </v-btn-toggle>
        </div>

        <!-- 预览模式 -->
        <div v-if="mode === 'preview'" class="preview-container">
          <div v-if="previewHtml" class="markdown" v-html="previewHtml" />
          <pre v-else-if="summary" class="job-log">{{ summary }}</pre>
          <v-empty-state
            v-else
            title="暂无审查意见"
            text="点击「编辑」录入审查意见与修改要求。"
          />
        </div>

        <!-- 编辑模式 -->
        <v-textarea
          v-else
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

        <div class="mt-4">
          <v-btn
            variant="tonal"
            color="primary"
            :prepend-icon="mdiTextBoxEditOutline"
            :loading="alignBusy"
            :disabled="!summary.trim() || loading || applyBusy"
            @click="alignDocs()"
          >
            按审查意见改文档
          </v-btn>
          <div class="text-caption text-medium-emphasis mt-1">
            只改和这条意见冲突的文档句子。不改代码，也不改这张票的状态。
          </div>
        </div>

        <v-alert
          v-if="alignError"
          type="error"
          variant="tonal"
          class="mt-3"
          closable
          @click:close="alignError = ''"
        >
          {{ alignError }}
        </v-alert>

        <div v-if="align" class="mt-3">
          <v-alert
            v-if="align.status === 'clarify'"
            type="warning"
            variant="tonal"
            class="mb-3"
          >
            这条意见还能有别的改法。下面这句话是将要采用的做法，确认后才会生成文档修改。
          </v-alert>
          <v-alert
            v-else-if="align.status === 'noop'"
            type="info"
            variant="tonal"
          >
            四份文档里没有和这条意见冲突的句子。
          </v-alert>
          <template v-else>
            <v-alert type="info" variant="tonal" class="mb-3">
              {{ align.decision }}
              确认后写入文档，不改代码，也不改票的状态。
            </v-alert>
            <div v-for="item in align.diffs" :key="item.file" class="mb-3">
              <div class="text-caption font-weight-bold mb-1">{{ item.file }}</div>
              <pre class="doc-diff">{{ item.diff }}</pre>
            </div>
            <v-btn
              color="primary"
              :loading="applyBusy"
              :disabled="alignBusy"
              @click="applyAlign"
            >
              写入文档
            </v-btn>
          </template>
          <div v-if="align.status === 'clarify'" class="mt-2">
            <v-textarea
              v-model="clarifyText"
              label="准备按这句话改"
              rows="2"
              auto-grow
              hide-details="auto"
            />
            <v-btn
              class="mt-2"
              color="primary"
              variant="tonal"
              :loading="alignBusy"
              :disabled="!clarifyText.trim() || applyBusy"
              @click="alignDocs(clarifyText.trim())"
            >
              按这个理解生成修改
            </v-btn>
          </div>
        </div>
      </v-card-text>

      <v-divider />

      <v-card-actions class="px-4 py-2">
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
                  ? "提交并启动修复"
                  : "保存审查意见"
                : "确认通过"
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
  mdiEyeOutline,
  mdiPencilOutline,
  mdiTextBoxEditOutline,
} from "@mdi/js";
import { applyDocAlign, startDocAlign, submitTicketReview } from "../api/client";
import type { DocAlignProposal, JobSnapshot, Ticket } from "../api/types";
import { jobTail, settleJob } from "../composables/qaRerun";
import { phaseColor } from "../composables/labels";
import { useSnack } from "../composables/snack";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
  ticket: Ticket | null;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: boolean): void;
  (e: "reviewed", ticketId: string, jobs: JobSnapshot[]): void;
  (e: "aligned"): void;
}>();
const snack = useSnack();

const verdict = ref<"failed" | "passed" | null>("failed");
const summary = ref("");
const autoImplement = ref(true);
const loading = ref(false);
const error = ref("");
const mode = ref<"preview" | "edit">("preview");
const alignBusy = ref(false);
const applyBusy = ref(false);
const alignError = ref("");
const align = ref<DocAlignProposal | null>(null);
const clarifyText = ref("");
const previewHtml = computed(() => {
  if (summary.value === (props.ticket?.last_summary || "") && props.ticket?.last_summary_html) {
    return props.ticket.last_summary_html;
  }
  return "";
});

const stateColor = computed(() => {
  if (!props.ticket?.state) return "";
  return phaseColor(props.ticket.state);
});

watch(
  () => [props.modelValue, props.ticket?.id] as const,
  ([open, id], prev) => {
    // A same-ticket reload must not wipe the note still sitting in the box.
    if (prev && prev[0] === true && prev[1] === id && open) return;
    if (open && props.ticket) {
      summary.value = props.ticket.last_summary || "";
      if (props.ticket.state === "blocked") {
        verdict.value = "failed";
        autoImplement.value = true;
      } else if (props.ticket.state === "done" || props.ticket.state === "approved") {
        verdict.value = "passed";
        autoImplement.value = false;
      } else if (props.ticket.state === "inconclusive") {
        verdict.value = null;
        autoImplement.value = false;
      } else {
        verdict.value = "failed";
        autoImplement.value = true;
      }
      error.value = "";
      alignError.value = "";
      align.value = null;
      clarifyText.value = "";
      mode.value = props.ticket.last_summary ? "preview" : "edit";
    }
  },
  { immediate: true },
);

watch(verdict, (value) => {
  if (props.ticket?.state === "inconclusive" && value === "failed") {
    autoImplement.value = true;
  }
});

async function alignDocs(decision = "") {
  if (!props.ticket || !summary.value.trim()) return;
  alignBusy.value = true;
  alignError.value = "";
  align.value = null;
  try {
    const res = await startDocAlign(props.jira, props.ticket.id, {
      summary: summary.value,
      decision,
    });
    const jobId = res.jobs?.[0]?.id;
    if (!jobId) {
      alignError.value = "没有开始对照文档";
      return;
    }
    const job = await settleJob(jobId);
    if (job.state !== "ok" || !job.doc_align) {
      alignError.value = jobTail(job.log) || "没有生成文档修改";
      return;
    }
    align.value = job.doc_align;
    if (job.doc_align.status === "clarify") {
      clarifyText.value = job.doc_align.decision;
    }
  } catch (e) {
    alignError.value = e instanceof Error ? e.message : String(e);
  } finally {
    alignBusy.value = false;
  }
}

async function applyAlign() {
  if (!props.ticket || !align.value || align.value.status !== "ready") return;
  applyBusy.value = true;
  alignError.value = "";
  try {
    const res = await applyDocAlign(props.jira, props.ticket.id, {
      decision: align.value.decision,
      base: align.value.base,
      files: align.value.files,
    });
    const stale = res.qa_stale ? "用例已标为待复核。" : "";
    snack.notify(`文档已按审查意见改好。可以重新审查。${stale}`, "success");
    align.value = null;
    emit("aligned");
  } catch (e) {
    alignError.value = e instanceof Error ? e.message : String(e);
  } finally {
    applyBusy.value = false;
  }
}

async function submit() {
  if (!props.ticket || !verdict.value) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await submitTicketReview(props.jira, props.ticket.id, {
      verdict: verdict.value,
      summary: summary.value,
      auto_implement: verdict.value === "failed" ? autoImplement.value : false,
    });
    if (res.error) snack.notify(res.error, "error");
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
.doc-diff {
  margin: 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  line-height: 1.45;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
}
.preview-container {
  min-height: 120px;
}
.job-log {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  padding: 0.8rem 1rem;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
}
</style>
