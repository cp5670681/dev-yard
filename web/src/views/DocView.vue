<template>
  <div>
    <div class="d-flex flex-column flex-sm-row align-sm-start justify-space-between ga-3 mb-4">
      <div>
        <div class="text-caption text-medium-emphasis mb-1">
          <router-link to="/">需求</router-link> /
          <router-link :to="`/r/${jira}`">{{ jira }}</router-link> /
          {{ doc?.filename }}
        </div>
        <h1 class="text-h5 text-sm-h4">{{ doc?.filename }}</h1>
        <p class="text-medium-emphasis mb-0">
          {{ doc?.filled ? "已填写" : "还是骨架，可以让 agent 写，或在下面改" }}
        </p>
      </div>
      <v-btn variant="tonal" @click="editing = !editing">
        {{ editing ? "阅读" : "编辑" }}
      </v-btn>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" variant="tonal">{{ error }}</v-alert>
    <v-alert v-if="saved" type="success" class="mb-4" variant="tonal">已保存</v-alert>
    <v-tabs class="mb-4" show-arrows>
      <v-tab :to="`/r/${jira}`">看板</v-tab>
      <v-tab
        v-for="d in docs"
        :key="d.slug"
        :to="`/r/${jira}/docs/${d.slug}`"
      >
        {{ d.filename }}
      </v-tab>
    </v-tabs>
    <v-card v-if="editing" variant="outlined">
      <v-card-text>
        <v-textarea v-model="text" rows="28" auto-grow hide-details spellcheck="false" />
        <v-btn class="mt-4" color="primary" block :loading="busy" @click="save">保存</v-btn>
      </v-card-text>
    </v-card>
    <v-card v-else variant="outlined">
      <v-card-text>
        <div v-if="doc?.text" class="markdown" v-html="doc.html" />
        <p v-else class="text-medium-emphasis">空文件</p>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { getDoc, getRequirement, saveDoc } from "@/api/client";
import type { DocMeta, DocPayload } from "@/api/types";

const route = useRoute();
const jira = computed(() => String(route.params.jira || ""));
const slug = computed(() => String(route.params.slug || ""));
const doc = ref<DocPayload | null>(null);
const docs = ref<DocMeta[]>([]);
const text = ref("");
const editing = computed({
  get: () => route.query.edit === "1" || internalEdit.value,
  set: (v: boolean) => {
    internalEdit.value = v;
  },
});
const internalEdit = ref(false);
const error = ref("");
const saved = ref(false);
const busy = ref(false);

async function load() {
  error.value = "";
  saved.value = false;
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
    saved.value = true;
    internalEdit.value = false;
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
  background: #0a0d12;
  padding: 0.8rem;
  border-radius: 8px;
  overflow: auto;
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
  border: 1px solid rgba(255, 255, 255, 0.12);
  padding: 0.4rem 0.55rem;
}
</style>
