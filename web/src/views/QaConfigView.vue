<template>
  <div>
    <div class="mb-6">
      <h1 class="text-h5 text-sm-h4 mb-1">测试配置</h1>
      <p class="text-medium-emphasis mb-0">
        写入工作区根 <code>qa.yaml</code>，<code>req test</code> 跑测时读它。凭据只填<b>环境变量名</b>，
        密码本身放 <code>.env</code>，不要写进来。
      </p>
    </div>

    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-alert v-if="catalogError" type="warning" class="mb-4">{{ catalogError }}</v-alert>

    <v-card v-if="state && !state.payload" variant="outlined" class="mb-4">
      <v-card-title>qa.yaml 无法解析</v-card-title>
      <v-card-text>
        <p class="text-error mb-3">{{ state.parse_error }}</p>
        <p class="text-medium-emphasis">
          表单不会覆盖它，免得抹掉你手写的内容。按下面原文改好（或删掉该文件）后刷新本页。
        </p>
        <pre class="qa-raw">{{ state.raw }}</pre>
      </v-card-text>
      <v-card-actions class="px-6 pb-4">
        <v-spacer />
        <v-btn variant="text" @click="load">刷新</v-btn>
      </v-card-actions>
    </v-card>

    <template v-if="form && env">
      <v-card variant="outlined" class="mb-4">
        <v-card-title>环境</v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="env.base_url"
                label="base_url"
                variant="outlined"
                density="comfortable"
                placeholder="http://127.0.0.1:8080"
                hide-details="auto"
                hint="必填。前端地址，跑测时浏览器从这里开。"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="env.db.url_env"
                label="db.url_env"
                variant="outlined"
                density="comfortable"
                placeholder="YARD_QA_DB_URL"
                hide-details="auto"
                hint="可留空；留空则禁止用 usql 做 DB 断言。"
              />
            </v-col>
          </v-row>
          <v-text-field
            v-model="env.script.runner"
            label="script.runner"
            variant="outlined"
            density="comfortable"
            placeholder="bin/rails runner"
            hide-details="auto"
            hint="可留空；留空则造数只允许 .sql。"
          />
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>登录态</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="env.auth.default"
            label="auth.default"
            variant="outlined"
            density="comfortable"
            hide-details
            hint="默认用哪个账号，对应下面的账号名。"
          />
          <v-row v-for="(acct, i) in accounts" :key="i" class="mt-2" dense>
            <v-col cols="12" sm="3">
              <v-text-field v-model="acct.name" label="账号名" variant="outlined" density="comfortable" hide-details />
            </v-col>
            <v-col cols="12" sm="3">
              <v-text-field
                v-model="acct.username_env"
                label="username_env"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="3">
              <v-text-field
                v-model="acct.password_env"
                label="password_env"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="2">
              <v-text-field
                v-model="acct.state_file"
                label="state_file"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="1" class="d-flex align-center">
              <v-btn :icon="mdiDeleteOutline" variant="text" size="small" @click="accounts.splice(i, 1)" />
            </v-col>
          </v-row>
          <v-btn :prepend-icon="mdiPlus" variant="text" class="mt-2" @click="addAccount">加账号</v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>浏览器与并发</v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" sm="6">
              <v-combobox
                v-model="form.browser.channel"
                :items="CHANNELS"
                label="browser.channel"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="6" class="d-flex align-center">
              <v-switch
                v-model="form.browser.headed"
                label="headed（有头窗口）"
                color="primary"
                hide-details
                density="comfortable"
              />
            </v-col>
          </v-row>
          <p class="text-caption text-medium-emphasis mt-2 mb-0">
            总并发 &gt; 1 时宿主强制无头，有头窗口会抢资源。当前总并发：{{ totalConcurrency }}。
          </p>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>模型池</v-card-title>
        <v-card-subtitle>
          同时跑几条用例、用哪些模型。留空则回退到工作区 pi 的 qa-run 模型、1 并发。
        </v-card-subtitle>
        <v-card-text>
          <v-row v-for="(w, i) in form.workers" :key="i" dense>
            <v-col cols="12" sm="2">
              <v-text-field v-model="w.id" label="id" variant="outlined" density="comfortable" hide-details />
            </v-col>
            <v-col cols="12" sm="3">
              <v-select
                v-model="w.provider"
                :items="providerItems"
                label="provider"
                variant="outlined"
                density="comfortable"
                hide-details
                clearable
                @update:model-value="onProviderChange(i, $event)"
              />
            </v-col>
            <v-col cols="12" sm="3">
              <v-select
                v-model="w.model"
                :items="modelItems(w.provider)"
                label="model"
                variant="outlined"
                density="comfortable"
                hide-details
                clearable
              />
            </v-col>
            <v-col cols="12" sm="1">
              <v-text-field
                v-model.number="w.concurrency"
                label="并发"
                type="number"
                min="1"
                max="8"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="2">
              <v-text-field
                v-model.number="w.priority"
                label="优先"
                type="number"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="1" class="d-flex align-center">
              <v-btn :icon="mdiDeleteOutline" variant="text" size="small" @click="form.workers.splice(i, 1)" />
            </v-col>
          </v-row>
          <v-btn :prepend-icon="mdiPlus" variant="text" class="mt-2" @click="addWorker">加模型</v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined">
        <v-card-title>备注</v-card-title>
        <v-card-text>
          <v-textarea
            v-model="notes"
            label="notes（一行一条）"
            variant="outlined"
            density="comfortable"
            rows="3"
            hide-details
            hint="会注入 context.md，提醒跑测的人注意什么。"
          />
          <p v-if="otherEnvs.length" class="text-caption text-medium-emphasis mt-4 mb-0">
            其它环境（本页只读，保留在文件里不动）：{{ otherEnvs.join("、") }}
          </p>
        </v-card-text>
        <v-card-actions class="px-6 pb-4">
          <v-spacer />
          <v-btn color="primary" :loading="saving" @click="save">保存</v-btn>
        </v-card-actions>
      </v-card>
    </template>

    <v-card v-if="state" variant="outlined" class="mt-4">
      <v-card-title>原始 YAML</v-card-title>
      <v-card-text>
        <p class="text-medium-emphasis text-body-2">
          磁盘上的当前内容，保存后由服务端写回。保存会重排这个文件，注释不会保留。
        </p>
        <pre class="qa-raw">{{ state.raw || "（还没有 qa.yaml，保存后创建）" }}</pre>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { mdiDeleteOutline, mdiPlus } from "@mdi/js";
import { getQaConfig, saveQaConfig } from "@/api/client";
import type { QaConfigPayload, QaConfigState } from "@/api/types";
import { useSnack } from "@/composables/snack";

const CHANNELS = ["chrome", "chromium", "msedge", "firefox", "webkit"];

interface AccountRow {
  name: string;
  username_env: string;
  password_env: string;
  state_file: string;
}

const snack = useSnack();
const state = ref<QaConfigState | null>(null);
const form = ref<QaConfigPayload | null>(null);
const accounts = reactive<AccountRow[]>([]);
const notes = ref("");
const error = ref("");
const saving = ref(false);

const catalog = computed(() => state.value?.catalog ?? { providers: [], error: null });
const catalogError = computed(() => catalog.value.error || "");
const providerItems = computed(() => catalog.value.providers.map((p) => p.id));
const env = computed(() => {
  if (!form.value) return null;
  return form.value.envs[form.value.active_env] ?? null;
});
const otherEnvs = computed(() => form.value?.other_envs ?? []);
const totalConcurrency = computed(() =>
  (form.value?.workers ?? []).reduce((sum, w) => sum + (Number(w.concurrency) || 1), 0),
);

function modelItems(providerId: string | null | undefined): string[] {
  const pid = providerId || "";
  if (!pid) {
    const all: string[] = [];
    for (const p of catalog.value.providers) {
      for (const m of p.models) {
        if (!all.includes(m)) all.push(m);
      }
    }
    return all;
  }
  return catalog.value.providers.find((p) => p.id === pid)?.models ?? [];
}

/** Keep a pair that is already saved in qa.yaml selectable even if pi no longer lists it. */
function ensureSaved(pid: string | null, mid: string | null) {
  if (!pid) return;
  const known = catalog.value.providers.find((p) => p.id === pid);
  if (!known) {
    catalog.value.providers.unshift({ id: pid, models: mid ? [mid] : [] });
    return;
  }
  if (mid && !known.models.includes(mid)) known.models = [mid, ...known.models];
}

function onProviderChange(index: number, next: string | null) {
  const row = form.value?.workers[index];
  if (!row) return;
  const pid = next || "";
  // Clearing the provider must clear the model too, or the server rejects the
  // pair with "provider and model must be set together".
  if (!pid) {
    row.model = "";
    return;
  }
  if (row.model && !modelItems(pid).includes(row.model)) row.model = "";
}

/** A blank field means "use the default"; 0 is a real priority (runs first). */
function intOr(value: unknown, fallback: number): number {
  if (value === "" || value == null) return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** Rows the form would silently drop or that the server would reject by index. */
function accountProblems(): string[] {
  const seen = new Set<string>();
  const problems: string[] = [];
  accounts.forEach((row, i) => {
    const name = row.name.trim();
    const filled = [row.username_env, row.password_env, row.state_file].some((v) => v.trim());
    if (!name) {
      if (filled) problems.push(`第 ${i + 1} 个账号没填名字，保存会把它丢掉。`);
      return;
    }
    if (seen.has(name)) problems.push(`账号名「${name}」重复。`);
    seen.add(name);
  });
  return problems;
}

function addWorker() {
  form.value?.workers.push({ id: "", provider: null, model: null, concurrency: 1, priority: 100 });
}

function addAccount() {
  accounts.push({ name: "", username_env: "", password_env: "", state_file: "" });
}

function load() {
  error.value = "";
  return getQaConfig()
    .then((res) => {
      state.value = res;
      if (!res.payload) {
        form.value = null;
        return;
      }
      form.value = res.payload;
      const editable = res.payload.envs[res.payload.active_env];
      notes.value = (editable?.notes ?? []).join("\n");
      accounts.splice(0, accounts.length);
      for (const [name, a] of Object.entries(editable?.auth.accounts ?? {})) {
        accounts.push({ name, username_env: a.username_env, password_env: a.password_env, state_file: a.state_file });
      }
      for (const w of res.payload.workers) ensureSaved(w.provider, w.model);
      if (!accounts.length) addAccount();
      if (!res.payload.workers.length) addWorker();
    })
    .catch((e) => {
      error.value = e instanceof Error ? e.message : String(e);
    });
}

async function save() {
  if (!form.value || !env.value) return;
  const problems = accountProblems();
  if (problems.length) {
    error.value = problems.join(" ");
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    const name = form.value.active_env;
    const built: Record<string, AccountRow> = {};
    for (const row of accounts) {
      const key = row.name.trim();
      if (key) built[key] = { ...row, name: key };
    }
    const saved = await saveQaConfig({
      active_env: name,
      browser: { ...form.value.browser },
      workers: form.value.workers.map((w) => ({
        ...w,
        concurrency: intOr(w.concurrency, 1),
        priority: intOr(w.priority, 100),
      })),
      envs: {
        [name]: {
          base_url: env.value.base_url,
          auth: { default: env.value.auth.default, accounts: built },
          db: { ...env.value.db },
          script: { ...env.value.script },
          notes: notes.value
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
      },
      other_envs: form.value.other_envs,
    });
    state.value = saved;
    snack.notify("已写入 qa.yaml", "success");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.qa-raw {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  border-radius: 4px;
  padding: 12px;
  margin: 0;
  overflow-x: auto;
  font-size: 0.8rem;
  line-height: 1.5;
}
</style>
