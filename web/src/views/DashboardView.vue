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
              <div class="d-flex justify-space-between align-start ga-2">
                <div class="overflow-hidden">
                  <div class="font-weight-medium text-subtitle-1 text-wrap">
                    {{ item.title || item.jira }}
                  </div>
                  <div v-if="item.title" class="text-caption text-medium-emphasis">
                    {{ item.jira }}
                  </div>
                </div>
                <div class="d-flex align-center ga-1 flex-shrink-0">
                  <v-chip size="small" :color="phaseColor(item.phase)" variant="tonal">
                    {{ item.phase }}
                  </v-chip>
                  <v-btn
                    icon
                    size="small"
                    variant="text"
                    aria-label="删除需求"
                    @click.prevent.stop="askDelete(item)"
                  >
                    <v-icon :icon="mdiDeleteOutline" size="18" />
                  </v-btn>
                </div>
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
    <v-dialog v-model="confirm.open" max-width="480">
      <v-card>
        <v-card-title>删除需求？</v-card-title>
        <v-card-text>
          将删除 <strong>{{ confirm.jira }}</strong> 的文档、截图和本票 worktree，并去掉对应本地分支。
          不会改 Jira，也不会动共用术语和 ADR。此操作不可恢复。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirm.open = false">取消</v-btn>
          <v-btn color="error" :loading="confirm.busy" @click="doDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { mdiClipboardTextOffOutline, mdiDeleteOutline, mdiPlus } from "@mdi/js";
import { deleteRequirement, listRequirements } from "@/api/client";
import type { ReqSummary } from "@/api/types";
import { phaseColor, STEP_LABELS } from "@/composables/labels";
import { forgetRecent, pruneRecents } from "@/composables/recents";
import { useSnack } from "@/composables/snack";

const items = ref<ReqSummary[]>([]);
const error = ref("");
const loading = ref(true);
const snack = useSnack();
const confirm = reactive({ open: false, jira: "", busy: false });

async function load() {
  error.value = "";
  try {
    items.value = await listRequirements();
    pruneRecents(items.value.map((i) => i.jira));
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function askDelete(item: ReqSummary) {
  confirm.jira = item.jira;
  confirm.open = true;
}

async function doDelete() {
  confirm.busy = true;
  try {
    await deleteRequirement(confirm.jira);
    forgetRecent(confirm.jira);
    confirm.open = false;
    snack.notify(`已删除 ${confirm.jira}`, "success");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    snack.notify(error.value, "error");
  } finally {
    confirm.busy = false;
  }
}

onMounted(async () => {
  loading.value = true;
  await load();
  loading.value = false;
});
</script>
