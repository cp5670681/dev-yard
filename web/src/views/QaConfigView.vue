<template>
  <div>
    <div class="mb-6">
      <h1 class="text-h5 text-sm-h4 mb-1">测试配置</h1>
      <p class="text-medium-emphasis mb-0">
        写入工作区根 <code>qa.yaml</code>，<code>req test</code> 跑测时读它。可配置多个环境，
        <b>默认环境</b> 是跑测时未指定 <code>--env</code> 时用的那个。账号密码、DB 连接串
        <b>明文</b>存这里（文件已 gitignore），加账号直接多填一行，无需再配环境变量。
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

    <template v-if="form && currentEnv">
      <v-card variant="outlined" class="mb-4">
        <v-card-title>环境</v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="6">
              <v-select
                :model-value="editing"
                :items="envNames"
                label="编辑哪个环境"
                variant="outlined"
                density="comfortable"
                hide-details
                @update:model-value="selectEnv"
              />
            </v-col>
            <v-col cols="12" md="6" class="d-flex align-center">
              <v-switch
                :model-value="form.active_env === editing"
                label="设为默认环境（active_env）"
                color="primary"
                hide-details
                density="comfortable"
                @update:model-value="setActive"
              />
            </v-col>
          </v-row>
          <v-row dense class="mt-1">
            <v-col cols="12" sm="6">
              <v-text-field
                v-model="nameDraft"
                label="环境名"
                variant="outlined"
                density="comfortable"
                hide-details
                hint="改名后保存即生效。"
                @change="renameCurrent"
              />
            </v-col>
            <v-col cols="12" sm="6" class="d-flex align-center ga-1">
              <v-text-field
                v-model="newEnvName"
                label="新增环境名"
                variant="outlined"
                density="comfortable"
                hide-details
                placeholder="test"
              />
              <v-btn :prepend-icon="mdiPlus" variant="tonal" @click="addEnv">新增</v-btn>
              <v-btn
                :prepend-icon="mdiDeleteOutline"
                color="error"
                variant="text"
                :disabled="envNames.length <= 1"
                @click="removeEnv"
              >
                删除
              </v-btn>
            </v-col>
          </v-row>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>{{ editing }} · 连接</v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="currentEnv.base_url"
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
                v-model="currentEnv.db.url"
                label="db.url"
                variant="outlined"
                density="comfortable"
                placeholder="postgres://user:pass@host:5432/db"
                hide-details="auto"
                hint="可留空；留空则禁止用 usql 做 DB 断言。密码含特殊字符需 URL 编码；显示 ******** 表示已存，保存会保留。"
              />
            </v-col>
          </v-row>
          <v-text-field
            v-model="currentEnv.script.runner"
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
        <v-card-title>{{ editing }} · 登录态</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="currentEnv.auth.default"
            label="auth.default"
            variant="outlined"
            density="comfortable"
            hide-details
            hint="默认用哪个账号，对应下面的账号名。"
          />
          <v-row v-for="(acct, i) in currentAccounts" :key="i" class="mt-2" dense>
            <v-col cols="12" sm="3">
              <v-text-field v-model="acct.name" label="账号名" variant="outlined" density="comfortable" hide-details />
            </v-col>
            <v-col cols="12" sm="3">
              <v-text-field
                v-model="acct.username"
                label="username"
                variant="outlined"
                density="comfortable"
                hide-details
              />
            </v-col>
            <v-col cols="12" sm="3">
              <v-text-field
                v-model="acct.password"
                label="password"
                type="password"
                variant="outlined"
                density="comfortable"
                hide-details="auto"
                hint="显示 ******** 表示已存，保存会保留；填新值替换，留空清除。"
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
              <v-btn :icon="mdiDeleteOutline" variant="text" size="small" @click="currentAccounts.splice(i, 1)" />
            </v-col>
          </v-row>
          <v-btn :prepend-icon="mdiPlus" variant="text" class="mt-2" @click="addAccount">加账号</v-btn>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>{{ editing }} · 备注</v-card-title>
        <v-card-text>
          <v-textarea
            v-model="currentDraft.notes"
            label="notes（一行一条）"
            variant="outlined"
            density="comfortable"
            rows="3"
            hide-details
            hint="会注入 context.md，提醒跑测的人注意什么。"
          />
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title>浏览器与并发</v-card-title>
        <v-card-subtitle>全环境共用。</v-card-subtitle>
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
          磁盘上的当前内容（密码、DB 连接串已脱敏为 ********），保存后由服务端写回。
          保存会重排这个文件，注释不会保留。
        </p>
        <pre class="qa-raw">{{ state.raw || "（还没有 qa.yaml，保存后创建）" }}</pre>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { mdiDeleteOutline, mdiPlus } from "@mdi/js";
import { getQaConfig, saveQaConfig } from "@/api/client";
import type { QaConfigPayload, QaConfigState, QaEnvCfg } from "@/api/types";
import { useSnack } from "@/composables/snack";

const CHANNELS = ["chrome", "chromium", "msedge", "firefox", "webkit"];

interface AccountRow {
  name: string;
  username: string;
  password: string;
  state_file: string;
}

/** Per-env edit state kept out of `form.envs` until save, so switching envs
 *  never merges (and silently drops) rows the user is still editing. */
interface EnvDraft {
  accounts: AccountRow[];
  notes: string;
}

const snack = useSnack();
const state = ref<QaConfigState | null>(null);
const form = ref<QaConfigPayload | null>(null);
const drafts = ref<Record<string, EnvDraft>>({});
const editing = ref("");
const nameDraft = ref("");
const newEnvName = ref("");
const error = ref("");
const saving = ref(false);

const catalog = computed(() => state.value?.catalog ?? { providers: [], error: null });
const catalogError = computed(() => catalog.value.error || "");
const providerItems = computed(() => catalog.value.providers.map((p) => p.id));
const envNames = computed(() => (form.value ? Object.keys(form.value.envs) : []));
const currentEnv = computed(() => {
  if (!form.value) return null;
  return form.value.envs[editing.value] ?? null;
});
const currentDraft = computed<EnvDraft>(
  () => drafts.value[editing.value] ?? { accounts: [], notes: "" },
);
const currentAccounts = computed(() => currentDraft.value.accounts);
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

function blankEnv(): QaEnvCfg {
  return {
    base_url: "",
    auth: { default: "default", accounts: {} },
    db: { url: "" },
    script: { runner: "" },
    notes: [],
  };
}

function blankDraft(): EnvDraft {
  return { accounts: [{ name: "", username: "", password: "", state_file: "" }], notes: "" };
}

function initDraft(name: string) {
  const env = form.value?.envs[name];
  const accounts: AccountRow[] = [];
  if (env) {
    for (const [acctName, a] of Object.entries(env.auth.accounts ?? {})) {
      accounts.push({
        name: acctName,
        username: a.username,
        password: a.password,
        state_file: a.state_file,
      });
    }
  }
  if (!accounts.length) accounts.push({ name: "", username: "", password: "", state_file: "" });
  drafts.value[name] = { accounts, notes: (env?.notes ?? []).join("\n") };
}

function selectEnv(next: string | null) {
  const name = next || "";
  if (!name || name === editing.value) return;
  editing.value = name;
  nameDraft.value = name;
  if (!drafts.value[name]) initDraft(name);
}

function setActive(on: boolean | null) {
  if (!form.value) return;
  if (on) {
    form.value.active_env = editing.value;
  } else if (form.value.active_env === editing.value) {
    const other = envNames.value.find((n) => n !== editing.value);
    if (other) form.value.active_env = other;
  }
}

function addEnv() {
  if (!form.value) return;
  const name = newEnvName.value.trim();
  if (!name) {
    error.value = "请填环境名。";
    return;
  }
  if (form.value.envs[name]) {
    error.value = `环境「${name}」已存在。`;
    return;
  }
  error.value = "";
  form.value.envs[name] = blankEnv();
  drafts.value[name] = blankDraft();
  newEnvName.value = "";
  editing.value = name;
  nameDraft.value = name;
}

function removeEnv() {
  if (!form.value) return;
  if (envNames.value.length <= 1) {
    error.value = "至少保留一个环境。";
    return;
  }
  const name = editing.value;
  delete form.value.envs[name];
  delete drafts.value[name];
  if (form.value.active_env === name) {
    form.value.active_env = envNames.value[0];
  }
  editing.value = envNames.value[0];
  nameDraft.value = editing.value;
  if (!drafts.value[editing.value]) initDraft(editing.value);
}

function renameCurrent() {
  if (!form.value) return;
  const from = editing.value;
  const to = nameDraft.value.trim();
  if (!to) {
    error.value = "环境名不能为空。";
    nameDraft.value = from;
    return;
  }
  if (to === from) return;
  if (form.value.envs[to]) {
    error.value = `环境「${to}」已存在。`;
    nameDraft.value = from;
    return;
  }
  error.value = "";
  const rebuilt: Record<string, QaEnvCfg> = {};
  for (const [key, value] of Object.entries(form.value.envs)) {
    rebuilt[key === from ? to : key] = value;
  }
  form.value.envs = rebuilt;
  const draft = drafts.value[from];
  if (draft) {
    delete drafts.value[from];
    drafts.value[to] = draft;
  }
  if (form.value.active_env === from) form.value.active_env = to;
  editing.value = to;
  nameDraft.value = to;
}

function addWorker() {
  form.value?.workers.push({ id: "", provider: null, model: null, concurrency: 1, priority: 100 });
}

function addAccount() {
  currentAccounts.value.push({ name: "", username: "", password: "", state_file: "" });
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
      drafts.value = {};
      const names = Object.keys(res.payload.envs);
      for (const name of names) initDraft(name);
      editing.value = names.includes(res.payload.active_env)
        ? res.payload.active_env
        : names[0] ?? "";
      nameDraft.value = editing.value;
      for (const w of res.payload.workers) ensureSaved(w.provider, w.model);
      if (!res.payload.workers.length) addWorker();
    })
    .catch((e) => {
      error.value = e instanceof Error ? e.message : String(e);
    });
}

function buildAccounts(rows: AccountRow[]): Record<string, AccountRow> {
  const built: Record<string, AccountRow> = {};
  for (const row of rows) {
    const key = row.name.trim();
    if (key) built[key] = { ...row, name: key };
  }
  return built;
}

/** Rows the form would silently drop or that the server would reject by index. */
function accountProblems(): string[] {
  const problems: string[] = [];
  for (const [envName, draft] of Object.entries(drafts.value)) {
    const seen = new Set<string>();
    draft.accounts.forEach((row, i) => {
      const name = row.name.trim();
      const filled = [row.username, row.password, row.state_file].some((v) => v.trim());
      if (!name) {
        if (filled) problems.push(`环境 ${envName} 第 ${i + 1} 个账号没填名字，保存会把它丢掉。`);
        return;
      }
      if (seen.has(name)) problems.push(`环境 ${envName} 账号名「${name}」重复。`);
      seen.add(name);
    });
  }
  return problems;
}

async function save() {
  if (!form.value || !currentEnv.value) return;
  const problems = accountProblems();
  if (problems.length) {
    error.value = problems.join(" ");
    return;
  }
  if (!form.value.active_env || !form.value.envs[form.value.active_env]) {
    error.value = "默认环境必须是一个已配置的环境。";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    const envs: Record<string, QaEnvCfg> = {};
    for (const [name, env] of Object.entries(form.value.envs)) {
      const draft = drafts.value[name] ?? { accounts: [], notes: "" };
      envs[name] = {
        base_url: env.base_url,
        auth: { default: env.auth.default, accounts: buildAccounts(draft.accounts) },
        db: { ...env.db },
        script: { ...env.script },
        notes: draft.notes
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      };
    }
    const saved = await saveQaConfig({
      active_env: form.value.active_env,
      browser: { ...form.value.browser },
      workers: form.value.workers.map((w) => ({
        ...w,
        concurrency: intOr(w.concurrency, 1),
        priority: intOr(w.priority, 100),
      })),
      envs,
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
