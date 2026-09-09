<template>
  <div>
    <h1 class="text-h5 text-sm-h4 mb-1">打开需求</h1>
    <p class="text-medium-emphasis mb-6">
      默认走本机 pi 的 mcp-atlassian-pro，只抽当前这张 Jira 的产品说明。
    </p>
    <v-card variant="outlined" max-width="720">
      <v-card-text>
        <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>
        <v-form ref="form" @submit.prevent="submit">
          <v-text-field
            v-model="jira"
            label="Jira key"
            placeholder="PG-13068"
            autocomplete="off"
            :rules="[rules.required, rules.key]"
            hint="例如 PG-13068"
            persistent-hint
            class="mb-4"
            @keydown.enter.prevent="submit"
          />
          <div class="text-subtitle-2 mb-2">来源</div>
          <v-radio-group v-model="source" hide-details class="mb-4">
            <v-radio
              v-for="s in sources"
              :key="s.value"
              :value="s.value"
              :label="s.label"
            >
              <template #label>
                <div>
                  <div>{{ s.label }}</div>
                  <div class="text-caption text-medium-emphasis">{{ s.hint }}</div>
                </div>
              </template>
            </v-radio>
          </v-radio-group>
          <v-switch
            v-model="force"
            color="warning"
            hide-details
            label="强制重抽（会重置 phase 并删除 assets/）"
            class="mb-4"
          />
          <v-btn type="submit" color="primary" block size="large" :loading="busy">
            开始抽取
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { openRequirement } from "@/api/client";
import { useSnack } from "@/composables/snack";

const router = useRouter();
const snack = useSnack();
const form = ref<{ validate: () => Promise<{ valid: boolean }> } | null>(null);
const jira = ref("");
const source = ref("pi");
const force = ref(false);
const busy = ref(false);
const error = ref("");
const sources = [
  { value: "pi", label: "pi + mcp-atlassian-pro（推荐）", hint: "只抽当前票的产品说明" },
  { value: "http", label: "HTTP 爬取", hint: "会带上历史页，较慢" },
  { value: "none", label: "只建骨架", hint: "不联网，手工补文档" },
];
const rules = {
  required: (v: string) => !!v.trim() || "请填写 Jira key",
  key: (v: string) => /^[A-Za-z][A-Za-z0-9]+-\d+$/.test(v.trim()) || "格式类似 PG-13068",
};

async function submit() {
  error.value = "";
  const result = await form.value?.validate();
  if (result && !result.valid) return;
  busy.value = true;
  try {
    const key = jira.value.trim().toUpperCase();
    const out = await openRequirement(key, source.value, force.value);
    const job = out.jobs[0]?.id;
    snack.notify(`已开始抽取 ${key}`, "success");
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
