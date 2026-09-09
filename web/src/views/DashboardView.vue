<template>
  <div>
    <div class="d-flex flex-column flex-sm-row align-sm-start justify-space-between ga-3 mb-6">
      <div>
        <h1 class="text-h5 text-sm-h4">需求</h1>
        <p class="text-medium-emphasis mt-1 mb-0">
          一个 Jira 一张看板。文档阶段可在网页用 pi -p 跑。
        </p>
      </div>
      <v-btn color="primary" to="/open" class="align-self-stretch align-self-sm-center">
        打开需求
      </v-btn>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" variant="tonal">{{ error }}</v-alert>
    <v-sheet v-if="!items.length && !error" class="pa-12 text-center" rounded="lg" border>
      <h2 class="text-h6 mb-2">还没有需求</h2>
      <p class="text-medium-emphasis">先在「仓库」登记业务仓，再打开一张 Jira。</p>
      <div class="d-flex justify-center ga-3 mt-4">
        <v-btn to="/repos" variant="tonal">登记仓库</v-btn>
        <v-btn color="primary" to="/open">打开需求</v-btn>
      </div>
    </v-sheet>
    <v-row v-else>
      <v-col v-for="item in items" :key="item.jira" cols="12" sm="6" md="4">
        <v-card :to="`/r/${item.jira}`" hover>
          <v-card-text>
            <div class="d-flex justify-space-between align-center">
              <span class="font-weight-medium">{{ item.jira }}</span>
              <v-chip size="small" variant="tonal">{{ item.phase }}</v-chip>
            </div>
            <p class="text-medium-emphasis my-3">下一步 · {{ item.next }}</p>
            <v-progress-linear
              :model-value="item.tickets ? (item.done / item.tickets) * 100 : 0"
              color="primary"
              rounded
            />
            <p class="text-caption text-medium-emphasis mt-3 mb-0">
              {{ item.tickets ? `${item.done}/${item.tickets} 票完成` : "尚未拆票" }}
            </p>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { listRequirements } from "@/api/client";
import type { ReqSummary } from "@/api/types";

const items = ref<ReqSummary[]>([]);
const error = ref("");

onMounted(async () => {
  try {
    items.value = await listRequirements();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});
</script>
