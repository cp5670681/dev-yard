<template>
  <div>
    <div class="d-flex flex-column flex-sm-row align-sm-start justify-space-between ga-3 mb-4">
      <div>
        <v-breadcrumbs :items="crumbs" density="compact" class="px-0 mb-1" />
        <h1 class="text-h5 text-sm-h4">{{ doc?.filename }}</h1>
        <p class="text-medium-emphasis mb-0">
          {{ doc?.filled ? "已填写" : "还是骨架，可以让 agent 写，或在下面改" }}
        </p>
      </div>
      <v-btn-toggle v-model="mode" mandatory density="comfortable" color="primary" divided>
        <v-btn value="read">阅读</v-btn>
        <v-btn value="edit">编辑</v-btn>
      </v-btn-toggle>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-tabs class="mb-4" show-arrows color="primary">
      <v-tab :to="`/r/${jira}`">看板</v-tab>
      <v-tab
        v-for="d in docs"
        :key="d.slug"
        :to="`/r/${jira}/docs/${d.slug}`"
      >
        {{ d.filename }}
        <v-chip v-if="!d.filled" size="x-small" class="ml-2" variant="text">骨架</v-chip>
      </v-tab>
    </v-tabs>
    <v-card v-if="editing" variant="outlined">
      <v-card-text>
        <v-textarea v-model="text" rows="28" auto-grow hide-details spellcheck="false" />
        <div class="d-flex ga-2 mt-4">
          <v-btn color="primary" :loading="busy" @click="save">保存</v-btn>
          <v-btn variant="tonal" :disabled="busy" @click="mode = 'read'">取消</v-btn>
        </div>
      </v-card-text>
    </v-card>
    <v-card v-else variant="outlined">
      <v-card-text>
        <div v-if="doc?.text" class="markdown" v-html="doc.html" />
        <v-empty-state v-else title="空文件" text="切到编辑，或让 agent 填写。" />
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { getDoc, getRequirement, saveDoc } from "@/api/client";
import type { DocMeta, DocPayload } from "@/api/types";
import { useSnack } from "@/composables/snack";

const route = useRoute();
const snack = useSnack();
const jira = computed(() => String(route.params.jira || ""));
const slug = computed(() => String(route.params.slug || ""));
const doc = ref<DocPayload | null>(null);
const docs = ref<DocMeta[]>([]);
const text = ref("");
const mode = ref<"read" | "edit">(route.query.edit === "1" ? "edit" : "read");
const editing = computed(() => mode.value === "edit");
const error = ref("");
const busy = ref(false);

const crumbs = computed(() => [
  { title: "需求", to: "/" },
  { title: jira.value, to: `/r/${jira.value}` },
  { title: doc.value?.filename || slug.value, disabled: true },
]);

async function load() {
  error.value = "";
  try {
    const [payload, detail] = await Promise.all([
      getDoc(jira.value, slug.value),
      getRequirement(jira.value),
    ]);
    doc.value = payload;
    text.value = payload.text;
    docs.value = detail.docs;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function save() {
  busy.value = true;
  error.value = "";
  try {
    doc.value = await saveDoc(jira.value, slug.value, text.value);
    snack.notify("已保存", "success");
    mode.value = "read";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

watch([jira, slug], () => void load(), { immediate: true });
</script>

<style>
.markdown {
  line-height: 1.65;
}
.markdown h1,
.markdown h2,
.markdown h3 {
  margin: 1.2rem 0 0.5rem;
}
.markdown pre {
  background: rgba(var(--v-theme-surface-variant), 0.6);
  color: rgb(var(--v-theme-on-surface));
  padding: 0.8rem 1rem;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.85rem;
}
.markdown img {
  max-width: 100%;
  border-radius: 8px;
}
.markdown table {
  width: 100%;
  border-collapse: collapse;
}
.markdown th,
.markdown td {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding: 0.4rem 0.55rem;
}
</style>
