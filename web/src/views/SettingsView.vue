<template>
  <div>
    <div class="mb-4">
      <h1 class="text-h5 text-sm-h4 mb-1">配置</h1>
      <p class="text-medium-emphasis mb-0">
        写入工作区根 <code>repos.yaml</code>。冻结分支只影响之后新冻结的需求；已冻结的沿用
        <code>STATUS.yaml</code> 里记下的名字。
      </p>
    </div>
    <v-tabs v-model="tab" color="primary" class="mb-4" show-arrows>
      <v-tab value="dev" :prepend-icon="mdiCodeBraces">开发</v-tab>
      <v-tab value="git" :prepend-icon="mdiSourceBranch">分支</v-tab>
      <v-tab value="pi" :prepend-icon="mdiCreationOutline">模型</v-tab>
    </v-tabs>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-alert v-if="tab === 'pi' && catalogError" type="warning" class="mb-4">
      {{ catalogError }}
    </v-alert>

    <v-card v-if="tab === 'dev'" variant="outlined">
      <v-card-text>
        <div class="text-subtitle-2 mb-2">TDD 开发与审查模式</div>
        <p class="text-body-2 text-medium-emphasis mb-4">
          控制实现（<code>implement</code>）和代码审查（<code>review</code>）阶段是否使用测试驱动开发（TDD）。
          写入 <code>repos.yaml</code> 的 <code>dev.tdd</code>。
        </p>
        <v-switch
          v-model="tdd"
          color="primary"
          inset
          :label="tdd ? '已开启 TDD（默认：编写测试套件、红绿循环、审查覆盖）' : '已关闭 TDD（免测模式：直接编写业务代码，不写不跑测试，免测审查）'"
          hide-details
        />
        <v-alert
          :type="tdd ? 'info' : 'warning'"
          variant="tonal"
          class="mt-4 mb-0"
          density="compact"
        >
          <span v-if="tdd">
            当前处于 <strong>TDD 模式</strong>：Agent 实现每张票时将编写测试并运行测试套件（如 RSpec / Jest）。
          </span>
          <span v-else>
            当前处于 <strong>免测模式</strong>：Agent 实现阶段直接编写业务代码并进行静态走查，审查阶段不会因缺少测试文件打回。
          </span>
        </v-alert>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn color="primary" :loading="savingDev" @click="saveDev">保存</v-btn>
      </v-card-actions>
    </v-card>

    <v-card v-else-if="tab === 'git'" variant="outlined">
      <v-card-text>
        <div class="text-subtitle-2 mb-2">冻结分支模板</div>
        <p class="text-body-2 text-medium-emphasis mb-4">
          必须包含 <code>{jira}</code>。并行票的子分支是「冻结名-票号」，不能写成
          <code>冻结名/票号</code>（git 不允许嵌套 ref）。
        </p>
        <v-text-field
          v-model="freezeBranch"
          label="git.freeze_branch"
          variant="outlined"
          density="comfortable"
          hide-details="auto"
          placeholder="req/{jira}"
        />
        <div class="mt-3 mb-1 text-caption text-medium-emphasis">常用预设</div>
        <div class="d-flex flex-wrap ga-2">
          <v-chip
            v-for="ex in examples"
            :key="ex"
            size="small"
            :variant="freezeBranch === ex ? 'flat' : 'outlined'"
            :color="freezeBranch === ex ? 'primary' : undefined"
            @click="freezeBranch = ex"
          >
            {{ ex }}
          </v-chip>
        </div>
        <v-alert type="info" variant="tonal" class="mt-4 mb-0" density="compact">
          预览：冻结 <code>{{ preview }}</code>
          · 并行票 <code>{{ ticketPreview }}</code>
        </v-alert>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn color="primary" :loading="savingGit" @click="saveGit">保存</v-btn>
      </v-card-actions>
    </v-card>

    <v-card v-else variant="outlined">
      <v-card-text>
        <p class="text-body-2 text-medium-emphasis mb-4">
          选项来自本机 <code>pi --list-models</code>。provider 和 model 成对回退。实现：仓库对 →
          阶段 implement → 全局 → 环境变量 → pi 默认。契约审查只用 contract 阶段/全局，审查只用
          review 阶段/全局，不用仓库模型。写入 <code>pi:</code>。
        </p>
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
import { mdiCodeBraces, mdiCreationOutline, mdiSourceBranch } from "@mdi/js";
import {
  getDevSettings,
  getGitSettings,
  getPiSettings,
  saveDevSettings,
  saveGitSettings,
  savePiSettings,
} from "@/api/client";
import type { PiCatalogProvider } from "@/api/types";
import { STEP_LABELS } from "@/composables/labels";
import { useSnack } from "@/composables/snack";

const snack = useSnack();
const tab = ref("dev");
const error = ref("");
const catalogError = ref("");
const saving = ref(false);
const savingGit = ref(false);
const savingDev = ref(false);
const tdd = ref(true);
const provider = ref("");
const model = ref("");
const stageIds = ref<string[]>([]);
const stages = reactive<Record<string, { provider: string; model: string }>>({});
const catalog = ref<PiCatalogProvider[]>([]);
const freezeBranch = ref("req/{jira}");
const examples = ref<string[]>(["req/{jira}", "feature/{jira}", "feat/{jira}", "{jira}"]);

const SAMPLE_JIRA = "PROJ-101";
const SAMPLE_TICKET = "T1";

const preview = computed(() => freezeBranch.value.replaceAll("{jira}", SAMPLE_JIRA));
const ticketPreview = computed(() => `${preview.value}-${SAMPLE_TICKET}`);

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
    const dev = await getDevSettings();
    tdd.value = dev.tdd ?? true;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
  try {
    const git = await getGitSettings();
    freezeBranch.value = git.freeze_branch || "req/{jira}";
    if (git.examples?.length) examples.value = git.examples;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
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
    const msg = e instanceof Error ? e.message : String(e);
    error.value = error.value ? `${error.value}; ${msg}` : msg;
  }
});

async function saveGit() {
  savingGit.value = true;
  error.value = "";
  try {
    const saved = await saveGitSettings({ freeze_branch: freezeBranch.value });
    freezeBranch.value = saved.freeze_branch;
    snack.notify("已写入 repos.yaml", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingGit.value = false;
  }
}

async function saveDev() {
  savingDev.value = true;
  error.value = "";
  try {
    const saved = await saveDevSettings({ tdd: tdd.value });
    tdd.value = saved.tdd;
    snack.notify("已写入 repos.yaml", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingDev.value = false;
  }
}

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
