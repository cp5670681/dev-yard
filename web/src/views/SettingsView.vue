<template>
  <div>
    <div class="mb-6">
      <h1 class="text-h5 text-sm-h4 mb-1">模型</h1>
      <p class="text-medium-emphasis mb-0">
        选项来自本机 <code>pi --list-models</code>。provider 和 model 成对回退。实现：仓库对 →
        阶段 implement → 全局 → 环境变量 → pi 默认。契约审查只用 contract 阶段/全局，审查只用 review 阶段/全局，不用仓库模型。保存到
        <code>repos.yaml</code> 的 <code>pi:</code>。
      </p>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-alert v-if="catalogError" type="warning" class="mb-4">
      {{ catalogError }}
    </v-alert>
    <v-card variant="outlined">
      <v-card-text>
        <div class="text-subtitle-2 mb-3">全局默认</div>
        <v-row>
          <v-col cols="12" sm="6">
            <v-select
              v-model="provider"
              :items="providerItems"
              label="provider"
              variant="outlined"
              density="comfortable"
              hide-details
              clearable
              @update:model-value="onProviderChange('', $event)"
            />
          </v-col>
          <v-col cols="12" sm="6">
            <v-select
              v-model="model"
              :items="modelItems(provider)"
              label="model"
              variant="outlined"
              density="comfortable"
              hide-details
              clearable
            />
          </v-col>
        </v-row>
        <div class="text-subtitle-2 mt-6 mb-3">按阶段覆盖</div>
        <v-row v-for="id in stageIds" :key="id">
          <v-col cols="12" sm="2" class="d-flex align-center">
            <span class="font-weight-medium">{{ STEP_LABELS[id] || id }}</span>
            <span class="text-caption text-medium-emphasis ml-2">{{ id }}</span>
          </v-col>
          <v-col cols="12" sm="5">
            <v-select
              v-model="stages[id].provider"
              :items="providerItems"
              :label="`${id} provider`"
              variant="outlined"
              density="comfortable"
              hide-details
              clearable
              @update:model-value="onProviderChange(id, $event)"
            />
          </v-col>
          <v-col cols="12" sm="5">
            <v-select
              v-model="stages[id].model"
              :items="modelItems(stages[id].provider)"
              :label="`${id} model`"
              variant="outlined"
              density="comfortable"
              hide-details
              clearable
            />
          </v-col>
        </v-row>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn color="primary" :loading="saving" @click="save">保存</v-btn>
      </v-card-actions>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { getPiSettings, savePiSettings } from "@/api/client";
import type { PiCatalogProvider } from "@/api/types";
import { STEP_LABELS } from "@/composables/labels";
import { useSnack } from "@/composables/snack";

const snack = useSnack();
const error = ref("");
const catalogError = ref("");
const saving = ref(false);
const provider = ref("");
const model = ref("");
const stageIds = ref<string[]>([]);
const stages = reactive<Record<string, { provider: string; model: string }>>({});
const catalog = ref<PiCatalogProvider[]>([]);

const providerItems = computed(() => catalog.value.map((p) => p.id));

function modelItems(providerId: string | null | undefined): string[] {
  const pid = providerId || "";
  if (!pid) {
    const all: string[] = [];
    for (const p of catalog.value) {
      for (const m of p.models) {
        if (!all.includes(m)) all.push(m);
      }
    }
    return all;
  }
  return catalog.value.find((p) => p.id === pid)?.models ?? [];
}

function ensureSaved(pid: string, mid: string) {
  if (!pid && !mid) return;
  if (pid && !catalog.value.some((p) => p.id === pid)) {
    catalog.value = [{ id: pid, models: mid ? [mid] : [] }, ...catalog.value];
    return;
  }
  if (pid && mid) {
    const p = catalog.value.find((row) => row.id === pid);
    if (p && !p.models.includes(mid)) p.models = [mid, ...p.models];
  }
}

function onProviderChange(stageId: string, next: string | null) {
  const pid = next || "";
  if (stageId) {
    const row = stages[stageId];
    if (row && pid && row.model && !modelItems(pid).includes(row.model)) {
      row.model = "";
    }
    return;
  }
  if (pid && model.value && !modelItems(pid).includes(model.value)) {
    model.value = "";
  }
}

onMounted(async () => {
  try {
    const cfg = await getPiSettings();
    provider.value = cfg.provider;
    model.value = cfg.model;
    stageIds.value = cfg.stage_ids;
    catalog.value = cfg.catalog?.providers || [];
    catalogError.value = cfg.catalog?.error || "";
    for (const id of cfg.stage_ids) {
      const row = cfg.stages[id] || { provider: "", model: "" };
      stages[id] = { provider: row.provider, model: row.model };
      ensureSaved(row.provider, row.model);
    }
    ensureSaved(cfg.provider, cfg.model);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

async function save() {
  saving.value = true;
  error.value = "";
  try {
    const payload = {
      provider: provider.value || "",
      model: model.value || "",
      stages: Object.fromEntries(
        stageIds.value.map((id) => [
          id,
          {
            provider: stages[id]?.provider || "",
            model: stages[id]?.model || "",
          },
        ]),
      ),
    };
    await savePiSettings(payload);
    snack.notify("已写入 repos.yaml", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}
</script>
