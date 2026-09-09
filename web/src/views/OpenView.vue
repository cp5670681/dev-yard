<template>
  <div>
    <h1 class="text-h5 text-sm-h4 mb-1">打开需求</h1>
    <p class="text-medium-emphasis mb-6">
      支持任意需求链接（AI 自主探测工具抓取）、直接粘贴文本内容或导入本地文件。
    </p>

    <v-card variant="outlined" max-width="800">
      <v-tabs v-model="tab" color="primary" class="border-b">
        <v-tab value="url">🔗 需求链接 / 目标</v-tab>
        <v-tab value="text">📝 粘贴文本</v-tab>
        <v-tab value="file">📁 本地文件</v-tab>
        <v-tab value="skeleton">✨ 空白模板</v-tab>
      </v-tabs>

      <v-card-text class="pt-6">
        <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>

        <v-form ref="form" @submit.prevent="submit">
          <!-- 模式 1：URL / 远程目标 -->
          <template v-if="tab === 'url'">
            <v-text-field
              v-model="targetUrl"
              label="需求链接或标识"
              placeholder="https://jira.company.com/browse/PG-13068 或 https://github.com/org/repo/issues/42 或 PG-13068"
              autocomplete="off"
              :rules="[rules.required]"
              hint="支持 Jira、GitHub Issue、飞书文档、Wiki 或任意 PRD 网页链接"
              persistent-hint
              class="mb-4"
              @update:model-value="onTargetUrlChange"
              @keydown.enter.prevent="submit"
            />
            <v-text-field
              v-model="reqKey"
              label="需求标识 (Key)"
              placeholder="例如 PG-13068、GH-42、pay-v2"
              autocomplete="off"
              :rules="[rules.required, rules.key, rules.reserved]"
              hint="工作区目录名与 Git 分支名（如 req/<Key>），可由 URL 自动提取或手动修改"
              persistent-hint
              class="mb-4"
              @keydown.enter.prevent="submit"
            />
          </template>

          <!-- 模式 2：直接粘贴文本 -->
          <template v-else-if="tab === 'text'">
            <v-text-field
              v-model="reqKey"
              label="需求标识 (Key)"
              placeholder="例如 auth-refactor、export-order"
              autocomplete="off"
              :rules="[rules.required, rules.key, rules.reserved]"
              hint="工作区目录名与 Git 分支名"
              persistent-hint
              class="mb-4"
              @keydown.enter.prevent="submit"
            />
            <v-textarea
              v-model="rawText"
              label="需求正文内容 (Markdown / 纯文本)"
              placeholder="在此粘贴需求背景、详细规则、验收标准等..."
              rows="8"
              auto-grow
              :rules="[rules.required]"
              hint="内容将直接写入 REQUIREMENT.md，0 秒极速创建"
              persistent-hint
              class="mb-4"
            />
          </template>

          <!-- 模式 3：本地文件 -->
          <template v-else-if="tab === 'file'">
            <v-text-field
              v-model="reqKey"
              label="需求标识 (Key)"
              placeholder="例如 prd-order-v2"
              autocomplete="off"
              :rules="[rules.required, rules.key, rules.reserved]"
              hint="工作区目录名与 Git 分支名"
              persistent-hint
              class="mb-4"
              @keydown.enter.prevent="submit"
            />
            <v-file-input
              v-model="localFiles"
              label="选择本地 Markdown 或文本文件"
              accept=".md,.markdown,.txt"
              show-size
              :rules="[rules.fileRequired]"
              hint="支持 .md / .txt 文件，导入后作为 REQUIREMENT.md"
              persistent-hint
              class="mb-4"
              @update:model-value="onFileSelected"
            />
          </template>

          <!-- 模式 4：空白骨架 -->
          <template v-else-if="tab === 'skeleton'">
            <v-text-field
              v-model="reqKey"
              label="需求标识 (Key)"
              placeholder="例如 FEAT-101"
              autocomplete="off"
              :rules="[rules.required, rules.key, rules.reserved]"
              hint="仅创建空白骨架模板，后续在界面上手动编写"
              persistent-hint
              class="mb-4"
              @keydown.enter.prevent="submit"
            />
          </template>

          <v-switch
            v-model="force"
            color="warning"
            hide-details
            label="强制覆盖已有需求（重置 phase 并清空旧资产）"
            class="mb-4"
          />

          <v-btn type="submit" color="primary" block size="large" :loading="busy">
            {{ submitButtonText }}
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { openRequirement } from "@/api/client";
import { useSnack } from "@/composables/snack";

const router = useRouter();
const snack = useSnack();
const form = ref<{ validate: () => Promise<{ valid: boolean }> } | null>(null);

const tab = ref<"url" | "text" | "file" | "skeleton">("url");
const targetUrl = ref("");
const reqKey = ref("");
const rawText = ref("");
const localFiles = ref<File[] | File | null>(null);
const force = ref(false);
const busy = ref(false);
const error = ref("");
const userCustomizedKey = ref(false);

const rules = {
  required: (v: string) => !!v?.trim() || "此项为必填",
  fileRequired: (v: unknown) => {
    if (Array.isArray(v)) return v.length > 0 || "请选择要导入的文件";
    return !!v || "请选择要导入的文件";
  },
  key: (v: string) =>
    /^[a-zA-Z0-9_.-]+$/.test(v?.trim()) || "仅支持英文、数字、中划线、下划线及点",
  reserved: (v: string) =>
    v?.trim().toLowerCase() !== "docs" || "docs 为系统保留关键字（用于全局 ADR/术语表）",
};

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

  const ghMatch = s.match(/github\.com\/[^/]+\/([^/]+)\/(?:issues|pull)\/(\d+)/i);
  if (ghMatch) return `${ghMatch[1]}-${ghMatch[2]}`;

  const glMatch = s.match(/\/([^/]+)\/-\/(?:issues|merge_requests)\/(\d+)/i);
  if (glMatch) return `${glMatch[1]}-${glMatch[2]}`;

  const confMatch = s.match(/[?&]pageId=(\d+)/i);
  if (confMatch) return `CONF-${confMatch[1]}`;

  const feishuMatch = s.match(/(?:feishu|larksuite)\.cn\/(?:docx|wiki|docs)\/([A-Za-z0-9]+)/i);
  if (feishuMatch) return `FEISHU-${feishuMatch[1].slice(0, 12)}`;

  try {
    const url = new URL(s);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length > 0) {
      const last = parts[parts.length - 1].replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "");
      if (last) return last;
    }
  } catch {
    // ignore
  }
  return s.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "");
}

function onTargetUrlChange(val: string) {
  if (!userCustomizedKey.value || !reqKey.value) {
    const extracted = extractKeyFromTarget(val);
    if (extracted) {
      reqKey.value = extracted;
    }
  }
}

function onFileSelected(fileOrFiles: unknown) {
  const file = Array.isArray(fileOrFiles) ? fileOrFiles[0] : (fileOrFiles as File | null);
  if (file && !reqKey.value) {
    const name = file.name.replace(/\.[^/.]+$/, "");
    reqKey.value = name.replace(/[^a-zA-Z0-9_.-]/g, "-").replace(/^-+|-+$/g, "");
  }
}

const submitButtonText = computed(() => {
  switch (tab.value) {
    case "url":
      return "开始智能抽取";
    case "text":
      return "立即创建需求";
    case "file":
      return "导入本地文档";
    case "skeleton":
      return "创建空白骨架";
    default:
      return "提交";
  }
});

async function submit() {
  error.value = "";
  const result = await form.value?.validate();
  if (result && !result.valid) return;
  busy.value = true;
  try {
    const key = reqKey.value.trim();
    let actualSource = "pi";
    let payloadText = "";
    let targetVal = "";

    if (tab.value === "url") {
      actualSource = "pi";
      targetVal = targetUrl.value.trim();
    } else if (tab.value === "text") {
      actualSource = "text";
      payloadText = rawText.value;
    } else if (tab.value === "file") {
      actualSource = "text";
      const file = Array.isArray(localFiles.value) ? localFiles.value[0] : (localFiles.value as File | null);
      if (!file) throw new Error("请选择文件");
      payloadText = await file.text();
    } else if (tab.value === "skeleton") {
      actualSource = "none";
    }

    const out = await openRequirement(key, actualSource, force.value, {
      target: targetVal,
      payload: payloadText,
    });
    const job = out.jobs[0]?.id;
    snack.notify(`已成功打开需求 ${key}`, "success");
    await router.push({
      name: "requirement",
      params: { jira: key },
      query: job ? { job } : {},
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>
