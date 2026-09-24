<template>
  <div>
    <h1 class="text-h5 text-sm-h4 mb-1">外部导入</h1>
    <p class="text-medium-emphasis mb-6">
      只给需求文档 + 每仓一个已有代码分支，跳过对齐/规约/拆票/契约，直接落到 freeze
      worktree 并提测，随后即可设计用例、跑测试、下 bug。
    </p>

    <v-card variant="outlined" max-width="900">
      <v-card-text class="pt-6">
        <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>

        <v-form ref="form" @submit.prevent="submitForm">
          <div class="text-subtitle-2 mb-2">需求文档来源</div>
          <v-btn-toggle v-model="source" mandatory color="primary" density="comfortable" class="mb-4">
            <v-btn value="url">🔗 链接</v-btn>
            <v-btn value="text">📝 文本 / 文件</v-btn>
            <v-btn value="none">✨ 空白</v-btn>
          </v-btn-toggle>

          <v-text-field
            v-if="source === 'url'"
            v-model="targetUrl"
            label="需求链接或标识"
            placeholder="https://jira.company.com/browse/PG-13068 或 PG-13068"
            autocomplete="off"
            hint="支持 Jira、Confluence、任意 PRD 链接"
            persistent-hint
            class="mb-4"
            @update:model-value="onTargetUrlChange"
          />

          <v-textarea
            v-if="source === 'text'"
            v-model="rawText"
            label="需求正文（Markdown / 纯文本）"
            placeholder="粘贴需求背景、详细规则、验收标准，或上传本地文件..."
            rows="6"
            auto-grow
            class="mb-2"
          />
          <v-file-input
            v-if="source === 'text'"
            v-model="docFile"
            label="或上传本地文档（覆盖上面的文本）"
            accept=".md,.markdown,.txt"
            show-size
            density="comfortable"
            class="mb-4"
            @update:model-value="onFileSelected"
          />

          <v-text-field
            v-model="reqKey"
            label="需求标识 (Key)"
            placeholder="例如 PG-13068"
            autocomplete="off"
            :rules="[rules.required, rules.key, rules.reserved]"
            hint="工作区目录名；可从链接自动提取"
            persistent-hint
            class="mb-4"
            @update:model-value="userCustomizedKey = true"
          />

          <div class="d-flex align-center mb-2">
            <div class="text-subtitle-2">代码分支</div>
            <v-spacer />
            <v-btn size="small" variant="text" :prepend-icon="mdiPlus" @click="addRow">加一仓</v-btn>
          </div>
          <v-card
            v-for="(row, i) in rows"
            :key="i"
            variant="tonal"
            class="pa-3 mb-3"
          >
            <v-row dense align="center">
              <v-col cols="12" sm="3">
                <v-combobox
                  v-model="row.alias"
                  :items="repoAliases"
                  label="仓 (repo)"
                  density="comfortable"
                  hide-details
                />
              </v-col>
              <v-col cols="12" sm="5">
                <v-text-field
                  v-model="row.ref"
                  label="代码分支 / ref"
                  placeholder="origin/feature/xxx"
                  density="comfortable"
                  hide-details
                />
              </v-col>
              <v-col cols="12" sm="3">
                <v-text-field
                  v-model="row.base"
                  label="diff 基线（可选）"
                  placeholder="留空自动 merge-base"
                  density="comfortable"
                  hide-details
                />
              </v-col>
              <v-col cols="12" sm="1" class="d-flex justify-end">
                <v-btn
                  :icon="mdiClose"
                  size="small"
                  variant="text"
                  :disabled="rows.length <= 1"
                  @click="rows.splice(i, 1)"
                />
              </v-col>
            </v-row>
          </v-card>

          <v-switch
            v-model="autoSubmit"
            color="primary"
            hide-details
            label="导入后自动提测（merge 进各仓 test_branch 并 push）"
            class="mb-1"
          />
          <v-switch
            v-model="force"
            color="warning"
            hide-details
            label="强制覆盖已有需求"
            class="mb-4"
          />

          <v-btn type="submit" color="primary" block size="large" :loading="busy">
            导入并{{ autoSubmit ? "提测" : "冻结" }}
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { mdiClose, mdiPlus } from "@mdi/js";
import { importRequirement, listRepos } from "@/api/client";
import type { Repo } from "@/api/types";
import { useSnack } from "@/composables/snack";

const router = useRouter();
const snack = useSnack();
const form = ref<{ validate: () => Promise<{ valid: boolean }> } | null>(null);

const source = ref<"url" | "text" | "none">("url");
const targetUrl = ref("");
const reqKey = ref("");
const rawText = ref("");
const docFile = ref<File[] | File | null>(null);
const autoSubmit = ref(true);
const force = ref(false);
const busy = ref(false);
const error = ref("");
const repos = ref<Repo[]>([]);
const userCustomizedKey = ref(false);
const rows = ref<{ alias: string; ref: string; base: string }[]>([
  { alias: "", ref: "", base: "" },
]);

const repoAliases = computed(() => repos.value.map((r) => r.alias));

const rules = {
  required: (v: string) => !!v?.trim() || "此项为必填",
  key: (v: string) =>
    /^[a-zA-Z0-9_.-]+$/.test(v?.trim()) || "仅支持英文、数字、中划线、下划线及点",
  reserved: (v: string) =>
    v?.trim().toLowerCase() !== "docs" || "docs 为系统保留关键字",
};

onMounted(async () => {
  try {
    repos.value = await listRepos();
  } catch {
    /* alias input is a combobox, so a failed fetch still allows free text */
  }
});

function addRow() {
  rows.value.push({ alias: "", ref: "", base: "" });
}

function extractKeyFromTarget(target: string): string {
  const s = target.trim();
  if (!s) return "";
  if (!s.startsWith("http://") && !s.startsWith("https://") && !s.includes("/") && !s.includes("\\")) {
    return s;
  }
  const jiraMatch = s.match(
    /(?:\/browse\/|\/issues\/|\/projects\/[^/]+\/issues\/|[?&]selectedIssue=)([A-Za-z][A-Za-z0-9]+-\d+)/i
  );
  if (jiraMatch) return jiraMatch[1].toUpperCase();
  try {
    const url = new URL(s);
    const parts = url.pathname.split("/").filter(Boolean);
    const last = parts[parts.length - 1]?.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "");
    if (last) return last;
  } catch {
    // ignore
  }
  return s.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "");
}

function onTargetUrlChange(val: string) {
  if (!userCustomizedKey.value || !reqKey.value) {
    const extracted = extractKeyFromTarget(val);
    if (extracted) reqKey.value = extracted;
  }
}

function onFileSelected(fileOrFiles: unknown) {
  const file = Array.isArray(fileOrFiles) ? fileOrFiles[0] : (fileOrFiles as File | null);
  if (file) void file.text().then((t: string) => (rawText.value = t));
}

async function submitForm() {
  error.value = "";
  const result = await form.value?.validate();
  if (result && !result.valid) return;

  const branches: Record<string, string> = {};
  const bases: Record<string, string> = {};
  for (const row of rows.value) {
    const alias = row.alias.trim();
    const ref = row.ref.trim();
    if (!alias && !ref) continue;
    if (!alias || !ref) {
      error.value = "每行都要同时填「仓」和「分支」";
      return;
    }
    branches[alias] = ref;
    if (row.base.trim()) bases[alias] = row.base.trim();
  }
  if (Object.keys(branches).length === 0) {
    error.value = "至少填一行完整的「仓 + 分支」";
    return;
  }

  const key = reqKey.value.trim();
  if (!key) {
    error.value = "请填写需求标识 (Key)";
    return;
  }
  const targetVal = source.value === "url" ? targetUrl.value.trim() : "";
  const payloadText = source.value === "text" ? rawText.value : "";
  if (source.value === "url" && !targetVal) {
    error.value = "请填写需求链接或标识";
    return;
  }
  if (source.value === "text" && !payloadText.trim()) {
    error.value = "文本来源需要粘贴正文或上传文件";
    return;
  }

  busy.value = true;
  try {
    const out = await importRequirement({
      jira: key,
      // `req open` has no "url" source: a link is fetched by the pi agent ("pi").
      source: source.value === "url" ? "pi" : source.value,
      target: targetVal,
      payload: payloadText,
      branches,
      bases,
      submit: autoSubmit.value,
      force: force.value,
    });
    const job = out.jobs[0]?.id;
    snack.notify(`已开始导入 ${key}`, "success");
    await router.push({ name: "requirement", params: { jira: key }, query: job ? { job } : {} });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>
