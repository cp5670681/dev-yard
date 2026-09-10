<template>
  <div>
    <div class="d-flex flex-column flex-sm-row align-sm-start justify-space-between ga-3 mb-6">
      <div>
        <h1 class="text-h5 text-sm-h4 mb-1">仓库</h1>
        <p class="text-medium-emphasis mb-0">
          alias 必须和 TICKETS.md 里的 <code>- repo:</code> 完全一致。本机 path 不要提交进 git。不填
          path 时会 clone，页面上能看到进度。
        </p>
      </div>
      <v-btn color="primary" class="align-self-stretch align-self-sm-center" @click="dialog = true">
        登记仓库
      </v-btn>
    </div>
    <JobPanel
      v-if="jobId"
      :job-id="jobId"
      @done="onJobDone"
    />
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-empty-state
      v-if="!repos.length && !error"
      :icon="mdiSourceRepository"
      title="还没有登记仓库"
      text="添加 git 地址后，不填 path 会在本机托管 clone。"
    >
      <template #actions>
        <v-btn color="primary" @click="dialog = true">登记一个仓</v-btn>
      </template>
    </v-empty-state>
    <v-card v-else variant="outlined">
      <v-card-text>
        <div v-if="!mdAndUp">
          <v-card v-for="r in repos" :key="r.alias" variant="tonal" class="mb-3">
            <v-card-text>
              <div class="d-flex align-center justify-space-between">
                <div class="font-weight-medium">
                  <code>{{ r.alias }}</code>
                  <v-chip size="x-small" class="ml-2" variant="tonal">{{ r.role }}</v-chip>
                </div>
                <v-btn icon size="x-small" variant="text" @click="copy(r.url)">
                  <v-icon :icon="mdiContentCopy" size="16" />
                </v-btn>
              </div>
              <div class="text-caption text-medium-emphasis mt-1">base {{ r.default_base }}</div>
              <div class="text-caption text-medium-emphasis">实现 {{ repoPiLabel(r) }}</div>
              <div class="text-caption text-break mt-1">{{ r.url }}</div>
              <div class="text-caption text-break">{{ r.path || "（托管 clone）" }}</div>
              <v-btn size="small" variant="text" class="mt-2 px-0" @click="openEdit(r)">改模型</v-btn>
            </v-card-text>
          </v-card>
        </div>
        <v-table v-else hover>
          <thead>
            <tr>
              <th>alias</th>
              <th>role</th>
              <th>base</th>
              <th>url</th>
              <th>path</th>
              <th>实现模型</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in repos" :key="r.alias">
              <td><code>{{ r.alias }}</code></td>
              <td>
                <v-chip size="x-small" variant="tonal">{{ r.role }}</v-chip>
              </td>
              <td>{{ r.default_base }}</td>
              <td>
                <div class="d-flex align-center ga-1">
                  <span class="text-truncate" style="max-width: 22rem">{{ r.url }}</span>
                  <v-tooltip text="复制 url">
                    <template #activator="{ props: tip }">
                      <v-btn v-bind="tip" icon size="x-small" variant="text" @click="copy(r.url)">
                        <v-icon :icon="mdiContentCopy" size="16" />
                      </v-btn>
                    </template>
                  </v-tooltip>
                </div>
              </td>
              <td class="text-truncate" style="max-width: 22rem">
                {{ r.path || "（托管 clone）" }}
              </td>
              <td class="text-caption">{{ repoPiLabel(r) }}</td>
              <td>
                <v-btn size="small" variant="text" @click="openEdit(r)">改</v-btn>
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>

    <v-dialog v-model="dialog" max-width="640" scrollable>
      <v-card>
        <v-card-title>登记一个仓</v-card-title>
        <v-card-text>
          <v-form @submit.prevent="submit">
            <v-row>
              <v-col cols="12" md="6">
                <v-text-field v-model="alias" label="alias（可空，默认 git 项目名）" placeholder="research" />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field v-model="url" label="url" placeholder="git@host:group/repo.git" :rules="[urlRule]" />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field v-model="defaultBase" label="default_base" />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field v-model="role" label="role（可选标记，不参与调度）" placeholder="be / fe / 任意" />
              </v-col>
              <v-col cols="12">
                <v-text-field v-model="path" label="本地 path（可选，已有工作副本时填写）" />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="addProvider"
                  :items="providerItems"
                  label="implement provider（可选，与 model 成对）"
                  variant="outlined"
                  density="comfortable"
                  hide-details
                  clearable
                  @update:model-value="onAddProviderChange"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="addModel"
                  :items="modelItems(addProvider)"
                  label="implement model"
                  variant="outlined"
                  density="comfortable"
                  hide-details
                  clearable
                />
              </v-col>
            </v-row>
          </v-form>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = false">取消</v-btn>
          <v-btn color="primary" :loading="busy" :disabled="!url.trim() || !pairOk(addProvider, addModel)" @click="submit">添加</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="editDialog" max-width="520">
      <v-card>
        <v-card-title>实现模型 · {{ editAlias }}</v-card-title>
        <v-card-text>
          <p class="text-caption text-medium-emphasis mb-4">
            成对配置。留空则实现回退到阶段 implement → 全局。审查不读这项。
          </p>
          <v-select
            v-model="editProvider"
            :items="providerItems"
            label="provider"
            variant="outlined"
            density="comfortable"
            class="mb-3"
            hide-details
            clearable
            @update:model-value="onEditProviderChange"
          />
          <v-select
            v-model="editModel"
            :items="modelItems(editProvider)"
            label="model"
            variant="outlined"
            density="comfortable"
            hide-details
            clearable
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="editDialog = false">取消</v-btn>
          <v-btn color="primary" :loading="editBusy" :disabled="!pairOk(editProvider, editModel)" @click="saveEdit">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import { mdiContentCopy, mdiSourceRepository } from "@mdi/js";
import { addRepo, getPiSettings, listRepos, setRepoPi } from "@/api/client";
import type { PiCatalogProvider, Repo } from "@/api/types";
import { useSnack } from "@/composables/snack";
import JobPanel from "@/components/JobPanel.vue";

const { mdAndUp } = useDisplay();
const snack = useSnack();
const route = useRoute();
const router = useRouter();
const repos = ref<Repo[]>([]);
const error = ref("");
const busy = ref(false);
const dialog = ref(false);
const alias = ref("");
const url = ref("");
const defaultBase = ref("main");
const role = ref("svc");
const path = ref("");
const addProvider = ref("");
const addModel = ref("");
const editDialog = ref(false);
const editBusy = ref(false);
const editAlias = ref("");
const editProvider = ref("");
const editModel = ref("");
const catalog = ref<PiCatalogProvider[]>([]);
const jobId = ref(typeof route.query.job === "string" ? route.query.job : "");
const urlRule = (v: string) => !!v.trim() || "需要 git url";
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

function pairOk(provider: string, model: string) {
  return (!provider && !model) || (!!provider && !!model);
}

function repoPiLabel(r: Repo) {
  if (r.provider && r.model) return `${r.provider} / ${r.model}`;
  return "（继承 implement）";
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

function onAddProviderChange(next: string | null) {
  const pid = next || "";
  if (!pid) {
    addModel.value = "";
  } else if (addModel.value && !modelItems(pid).includes(addModel.value)) {
    addModel.value = "";
  }
}

function onEditProviderChange(next: string | null) {
  const pid = next || "";
  if (!pid) {
    editModel.value = "";
  } else if (editModel.value && !modelItems(pid).includes(editModel.value)) {
    editModel.value = "";
  }
}

async function reload() {
  repos.value = await listRepos();
}

onMounted(async () => {
  try {
    await reload();
    const cfg = await getPiSettings();
    catalog.value = cfg.catalog?.providers || [];
    for (const r of repos.value) ensureSaved(r.provider, r.model);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snack.notify("已复制", "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

async function submit() {
  if (!url.value.trim()) return;
  error.value = "";
  busy.value = true;
  try {
    const out = await addRepo({
      alias: alias.value,
      url: url.value,
      default_base: defaultBase.value,
      role: role.value,
      path: path.value,
      provider: addProvider.value,
      model: addModel.value,
    });
    jobId.value = out.jobs[0]?.id || "";
    dialog.value = false;
    snack.notify("已提交登记", "success");
    await router.replace({ query: jobId.value ? { job: jobId.value } : {} });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function onJobDone() {
  await reload();
  snack.notify("仓库任务完成", "success");
}

function openEdit(r: Repo) {
  editAlias.value = r.alias;
  editProvider.value = r.provider || "";
  editModel.value = r.model || "";
  ensureSaved(r.provider, r.model);
  editDialog.value = true;
}

async function saveEdit() {
  if (!pairOk(editProvider.value, editModel.value)) return;
  editBusy.value = true;
  error.value = "";
  try {
    repos.value = await setRepoPi(editAlias.value, editProvider.value, editModel.value);
    editDialog.value = false;
    snack.notify("已写入仓库模型", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    editBusy.value = false;
  }
}
</script>
