<template>
  <div>
    <h1 class="text-h5 text-sm-h4 mb-1">仓库</h1>
    <p class="text-medium-emphasis mb-6">
      alias 必须和 TICKETS.md 里的 <code>- repo:</code> 完全一致。本机 path 不要提交进 git。不填
      path 时会 clone，页面上能看到进度。
    </p>
    <JobPanel
      v-if="jobId"
      :job-id="jobId"
      @done="onJobDone"
    />
    <v-alert v-if="error" type="error" class="mb-4" variant="tonal">{{ error }}</v-alert>
    <v-card variant="outlined" class="mb-6">
      <v-card-text>
        <div v-if="!repos.length" class="text-medium-emphasis">还没有登记仓库。</div>
        <div v-else-if="!mdAndUp">
          <v-card v-for="r in repos" :key="r.alias" variant="tonal" class="mb-3">
            <v-card-text>
              <div class="font-weight-medium"><code>{{ r.alias }}</code> · {{ r.role }}</div>
              <div class="text-caption text-medium-emphasis mt-1">base {{ r.default_base }}</div>
              <div class="text-caption text-break mt-1">{{ r.url }}</div>
              <div class="text-caption text-break">{{ r.path || "（托管 clone）" }}</div>
            </v-card-text>
          </v-card>
        </div>
        <v-table v-else>
          <thead>
            <tr>
              <th>alias</th>
              <th>role</th>
              <th>base</th>
              <th>url</th>
              <th>path</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in repos" :key="r.alias">
              <td><code>{{ r.alias }}</code></td>
              <td>{{ r.role }}</td>
              <td>{{ r.default_base }}</td>
              <td class="text-truncate" style="max-width: 22rem">{{ r.url }}</td>
              <td class="text-truncate" style="max-width: 22rem">
                {{ r.path || "（托管 clone）" }}
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>
    <v-card variant="outlined">
      <v-card-title>登记一个仓</v-card-title>
      <v-card-text>
        <v-row>
          <v-col cols="12" md="6">
            <v-text-field v-model="alias" label="alias（可空，默认 git 项目名）" placeholder="research" />
          </v-col>
          <v-col cols="12" md="6">
            <v-text-field v-model="url" label="url" placeholder="git@host:group/repo.git" />
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
        </v-row>
        <v-btn color="primary" block :loading="busy" @click="submit">添加</v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import { addRepo, listRepos } from "@/api/client";
import type { Repo } from "@/api/types";
import JobPanel from "@/components/JobPanel.vue";

const { mdAndUp } = useDisplay();
const route = useRoute();
const router = useRouter();
const repos = ref<Repo[]>([]);
const error = ref("");
const busy = ref(false);
const alias = ref("");
const url = ref("");
const defaultBase = ref("main");
const role = ref("svc");
const path = ref("");
const jobId = ref(typeof route.query.job === "string" ? route.query.job : "");

async function reload() {
  repos.value = await listRepos();
}

onMounted(async () => {
  try {
    await reload();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
});

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const out = await addRepo({
      alias: alias.value,
      url: url.value,
      default_base: defaultBase.value,
      role: role.value,
      path: path.value,
    });
    jobId.value = out.jobs[0]?.id || "";
    await router.replace({ query: jobId.value ? { job: jobId.value } : {} });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function onJobDone() {
  await reload();
}
</script>
