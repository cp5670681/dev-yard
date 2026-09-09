<template>
  <div>
    <h1 class="text-h5 text-sm-h4 mb-1">打开需求</h1>
    <p class="text-medium-emphasis mb-6">
      默认走本机 pi 的 mcp-atlassian-pro，只抽当前这张 Jira 的产品说明。
    </p>
    <v-card variant="outlined">
      <v-card-text>
        <v-alert v-if="error" type="error" class="mb-4" variant="tonal">{{ error }}</v-alert>
        <v-text-field
          v-model="jira"
          label="Jira key"
          placeholder="PG-13068"
          autocomplete="off"
        />
        <v-select
          v-model="source"
          label="来源"
          :items="sources"
          item-title="label"
          item-value="value"
        />
        <v-checkbox
          v-model="force"
          label="强制重抽（会重置 phase 并删除 assets/）"
          hide-details
        />
        <v-btn class="mt-4" color="primary" block :loading="busy" @click="submit">开始抽取</v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { openRequirement } from "@/api/client";

const router = useRouter();
const jira = ref("");
const source = ref("pi");
const force = ref(false);
const busy = ref(false);
const error = ref("");
const sources = [
  { value: "pi", label: "pi + mcp-atlassian-pro（推荐）" },
  { value: "http", label: "HTTP 爬取（会带历史页）" },
  { value: "none", label: "只建骨架，不联网" },
];

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const key = jira.value.trim().toUpperCase();
    const out = await openRequirement(key, source.value, force.value);
    const job = out.jobs[0]?.id;
    await router.push({
      name: "requirement",
      params: { jira: key },
      query: job ? { job } : {},
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>
