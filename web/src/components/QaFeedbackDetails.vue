<script setup lang="ts">
import { computed, ref } from "vue";

import type { QaReview } from "@/api/types";

const props = withDefaults(defineProps<{ review: QaReview | null; label?: string }>(), {
  label: "审核意见",
});

const expanded = ref(false);

// The raw feedback is agent-facing markdown and can run to thousands of
// characters. Show a one-line gist; the full text sits behind an explicit
// toggle. Prefer the structured verify verdict, but never present a stale one
// as current.
const summary = computed(() => {
  const review = props.review;
  if (!review?.feedback) return "";
  const verify = review.verify;
  if (verify?.details?.length && !verify.stale) {
    const ids = verify.details.map((d) => d.case).join("、");
    return `${verify.details.length} 条用例数据核实未通过：${ids}`;
  }
  const first = review.feedback.split("\n").map((l) => l.trim()).find(Boolean) || "";
  if (!first) return "（见详情）";
  return first.length > 80 ? `${first.slice(0, 80)}…` : first;
});
</script>

<template>
  <div class="text-body-2 text-medium-emphasis">
    {{ label }}：{{ summary }}
    <v-btn
      size="x-small"
      variant="text"
      density="comfortable"
      class="ms-1"
      @click="expanded = !expanded"
    >
      {{ expanded ? "收起" : "查看详情" }}
    </v-btn>
    <div v-if="expanded" class="feedback-body mt-2">
      <div v-if="review?.feedback_html" class="markdown" v-html="review.feedback_html" />
      <pre v-else class="job-log">{{ review?.feedback }}</pre>
    </div>
  </div>
</template>

<style scoped>
.feedback-body {
  max-height: 260px;
  overflow: auto;
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
