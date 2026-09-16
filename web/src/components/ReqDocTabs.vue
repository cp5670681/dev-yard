<template>
  <v-tabs :model-value="current" class="mb-4" show-arrows color="primary">
    <v-tab value="board" :to="`/r/${jira}`">看板</v-tab>
    <v-tab
      v-for="d in docs"
      :key="d.slug"
      :value="d.slug"
      :to="`/r/${jira}/docs/${d.slug}`"
    >
      {{ d.filename }}
      <v-chip v-if="!d.filled" size="x-small" class="ml-2" variant="text">骨架</v-chip>
    </v-tab>
    <v-tab value="qa" :to="`/r/${jira}/qa`">测试</v-tab>
  </v-tabs>
</template>

<script setup lang="ts">
import type { DocMeta } from "@/api/types";

defineProps<{
  jira: string;
  docs: DocMeta[];
  current: string;
}>();
</script>
