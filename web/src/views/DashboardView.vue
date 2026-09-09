<template>
  <div>
    <div class="d-flex flex-column flex-sm-row align-sm-start justify-space-between ga-3 mb-6">
      <div>
        <h1 class="text-h5 text-sm-h4">需求</h1>
        <p class="text-medium-emphasis mt-1 mb-0">
          一个 Jira 一张看板。文档阶段可在网页用 pi -p 跑。
        </p>
      </div>
      <v-btn color="primary" :prepend-icon="mdiPlus" to="/open" class="align-self-stretch align-self-sm-center">
        打开需求
      </v-btn>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-row v-if="loading">
      <v-col v-for="n in 3" :key="n" cols="12" sm="6" md="4">
        <v-skeleton-loader type="card" />
      </v-col>
    </v-row>
    <v-empty-state
      v-else-if="!items.length && !error"
      :icon="mdiClipboardTextOffOutline"
      title="还没有需求"
      text="先在「仓库」登记业务仓，再打开一张 Jira。"
    >
      <template #actions>
        <v-btn to="/repos" variant="tonal">登记仓库</v-btn>
        <v-btn color="primary" to="/open">打开需求</v-btn>
      </template>
    </v-empty-state>
    <v-row v-else>
      <v-col v-for="item in items" :key="item.jira" cols="12" sm="6" md="4">
        <v-hover v-slot="{ isHovering, props: hoverProps }">
          <v-card
            v-bind="hoverProps"
            :to="`/r/${item.jira}`"
            :elevation="isHovering ? 8 : 0"
            border
          >
            <v-card-text>
              <div class="d-flex justify-space-between align-center">
                <span class="font-weight-medium text-subtitle-1">{{ item.jira }}</span>
                <v-chip size="small" :color="phaseColor(item.phase)" variant="tonal">
                  {{ item.phase }}
                </v-chip>
              </div>
              <p class="text-medium-emphasis my-3">
                下一步 · {{ STEP_LABELS[item.next] || item.next }}
              </p>
              <v-progress-linear
                :model-value="item.tickets ? (item.done / item.tickets) * 100 : 0"
                :color="item.tickets && item.done === item.tickets ? 'success' : 'primary'"
                height="6"
                rounded
              />
              <p class="text-caption text-medium-emphasis mt-3 mb-0">
                {{ item.tickets ? `${item.done}/${item.tickets} 票完成` : "尚未拆票" }}
              </p>
            </v-card-text>
          </v-card>
        </v-hover>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { mdiClipboardTextOffOutline, mdiPlus } from "@mdi/js";
import { listRequirements } from "@/api/client";
import type { ReqSummary } from "@/api/types";
import { phaseColor, STEP_LABELS } from "@/composables/labels";

const items = ref<ReqSummary[]>([]);
const error = ref("");
const loading = ref(true);

onMounted(async () => {
  try {
    items.value = await listRequirements();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
});
</script>
