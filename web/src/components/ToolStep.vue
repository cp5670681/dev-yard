<template>
  <div class="tool-step">
    <div class="d-flex align-center ga-2">
      <v-icon :icon="toolIcon(step.name)" size="15" class="tool-step-icon" />
      <span class="text-caption font-weight-medium">{{ toolLabel(step.name) }}</span>
      <span class="text-caption text-medium-emphasis text-truncate flex-grow-1">
        {{ step.summary || step.name }}
      </span>
      <v-progress-circular
        v-if="step.status === 'running'"
        indeterminate
        size="12"
        width="2"
        class="flex-grow-0"
      />
      <v-icon
        v-else-if="step.status === 'error'"
        :icon="mdiAlertCircleOutline"
        size="15"
        color="error"
        class="flex-grow-0"
      />
      <v-icon
        v-else
        :icon="mdiCheckAll"
        size="15"
        color="success"
        class="flex-grow-0"
      />
    </div>

    <div v-if="step.args" class="mt-1">
      <code class="tool-args" :title="prettyArgs">{{ compactArgs }}</code>
    </div>

    <div v-if="step.result">
      <div class="d-flex align-center mt-1">
        <span class="text-caption text-medium-emphasis">结果</span>
        <span class="text-caption text-disabled ml-1">· {{ lineCount }} 行</span>
        <v-spacer />
        <v-btn
          v-if="clampable"
          size="x-small"
          variant="text"
          density="comfortable"
          @click="expanded = !expanded"
        >
          {{ expanded ? "收起" : "展开全文" }}
        </v-btn>
      </div>
      <pre class="tool-pre" :class="{ 'tool-clamp': clampable && !expanded }">{{
        step.result
      }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { mdiAlertCircleOutline, mdiCheckAll } from "@mdi/js";
import { toolIcon, toolLabel } from "@/composables/tools";
import type { ToolStep } from "@/api/types";

const props = defineProps<{ step: ToolStep }>();

const expanded = ref(false);

const prettyArgs = computed(() => {
  const raw = props.step.args || "";
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
});

const compactArgs = computed(() => prettyArgs.value.replace(/\s+/g, " ").trim());

const lineCount = computed(() => (props.step.result || "").split("\n").length);

const clampable = computed(() => {
  const text = props.step.result || "";
  return lineCount.value > 14 || text.length > 1400;
});
</script>

<style scoped>
.tool-step {
  padding: 0.4rem 0;
  border-top: 1px dashed rgba(var(--v-border-color), var(--v-border-opacity));
}
.tool-step:first-child {
  border-top: none;
  padding-top: 0;
}
.tool-step-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.tool-pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.72rem;
  line-height: 1.45;
  background: rgba(var(--v-theme-surface-variant), 0.55);
  border-radius: 4px;
  padding: 0.35rem 0.5rem;
}
.tool-args {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.72rem;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.68);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tool-clamp {
  max-height: 8rem;
  overflow: hidden;
  position: relative;
}
.tool-clamp::after {
  content: "";
  position: absolute;
  inset: auto 0 0 0;
  height: 1.6rem;
  background: linear-gradient(
    transparent,
    rgba(var(--v-theme-surface-variant), 0.95)
  );
}
</style>
