<template>
  <v-app>
    <v-app-bar :color="isDark ? 'surface' : '#0052CC'" elevation="0" :border="isDark" class="jira-app-bar" height="56">
      <v-app-bar-nav-icon v-if="!mdAndUp" :color="isDark ? undefined : 'white'" @click="drawer = !drawer" />
      <v-avatar color="#2684FF" rounded="sm" size="28" class="ml-2 mr-2 jira-avatar-border">
        <span class="text-white font-weight-black text-subtitle-2">Y</span>
      </v-avatar>
      <span class="jira-product" :class="{ 'text-white': !isDark }">dev-yard</span>
      <v-app-bar-title :class="isDark ? 'text-truncate font-weight-medium' : 'text-truncate font-weight-medium text-white'">
        {{ barTitle }}
      </v-app-bar-title>
      <v-chip
        v-if="mdAndUp && runningJobs.length"
        size="small"
        :color="isDark ? 'primary' : 'white'"
        :variant="isDark ? 'tonal' : 'flat'"
        class="mr-2 font-weight-medium"
        :class="{ 'text-primary': !isDark }"
      >
        <v-progress-circular indeterminate size="12" width="2" class="mr-2" :color="isDark ? 'primary' : 'primary'" />
        {{ runningJobs.length }} 个任务
      </v-chip>
      <v-btn
        v-if="!mdAndUp && waitingJobs.length"
        icon
        :to="jobHref(waitingJobs[0])"
        :color="isDark ? undefined : 'white'"
        @click="drawer = false"
      >
        <v-badge color="error" dot>
          <v-icon :icon="mdiProgressClock" />
        </v-badge>
      </v-btn>

      <v-btn
        icon
        variant="text"
        size="small"
        class="mr-1"
        :color="isDark ? undefined : 'white'"
        :title="isDark ? '切换为 Jira 浅色风格' : '切换为 Jira 深色风格'"
        @click="toggleTheme"
      >
        <v-icon :icon="isDark ? mdiWeatherSunny : mdiWeatherNight" size="20" />
      </v-btn>
    </v-app-bar>

    <v-navigation-drawer
      v-model="drawer"
      :permanent="mdAndUp"
      :temporary="!mdAndUp"
      width="260"
      color="surface"
      border
    >
      <v-list-item :title="'dev-yard'" :subtitle="meta?.root_name || ''" class="mt-2" to="/">
        <template #prepend>
          <v-avatar color="primary" rounded="sm" size="36">
            <span class="text-white font-weight-black text-subtitle-1">Y</span>
          </v-avatar>
        </template>
      </v-list-item>
      <v-list nav density="comfortable" class="mt-2">
        <v-list-item
          to="/"
          title="需求"
          subtitle="看板与阶段"
          :prepend-icon="mdiViewDashboardOutline"
          active-class="jira-nav-active"
        />
        <v-list-item
          to="/repos"
          title="仓库"
          subtitle="登记业务仓"
          :prepend-icon="mdiSourceRepository"
          active-class="jira-nav-active"
        />
        <v-list-item
          to="/open"
          title="打开需求"
          subtitle="抽取一张 Jira"
          :prepend-icon="mdiPlusBoxOutline"
          active-class="jira-nav-active"
        />
        <v-list-item
          to="/settings"
          title="配置"
          subtitle="分支、模型"
          :prepend-icon="mdiTune"
          active-class="jira-nav-active"
        />
        <v-list-item
          to="/qa-config"
          title="测试配置"
          subtitle="qa.yaml 环境与并发"
          :prepend-icon="mdiClipboardCheckOutline"
          active-class="jira-nav-active"
        />
      </v-list>
      <v-divider class="my-2" />
      <v-list-subheader v-if="reqRows.length">需求</v-list-subheader>
      <v-list v-if="reqRows.length" density="compact" nav>
        <v-tooltip
          v-for="row in reqRows"
          :key="row.jira"
          :text="row.subtitle"
          :disabled="!row.subtitle"
          location="end"
        >
          <template #activator="{ props: tip }">
            <v-list-item
              v-bind="tip"
              :to="`/r/${row.jira}`"
              :title="row.jira"
              :subtitle="row.subtitle"
              active-class="jira-nav-active"
              @click="onNav"
            >
              <template #prepend>
                <v-progress-circular
                  v-if="row.job && row.job.state !== 'waiting'"
                  indeterminate
                  size="18"
                  width="2"
                  color="primary"
                />
                <v-icon v-else-if="row.job" :icon="mdiProgressClock" color="error" size="18" />
                <v-icon v-else :icon="mdiClipboardTextOutline" size="18" />
              </template>
              <template #append>
                <v-chip
                  v-if="row.phase"
                  size="x-small"
                  variant="tonal"
                  :color="phaseColor(row.phase)"
                  class="jira-lozenge"
                >
                  {{ row.phase }}
                </v-chip>
                <v-badge
                  v-if="row.job && row.job.state === 'waiting'"
                  color="error"
                  content="待答"
                  inline
                  class="ml-1"
                />
              </template>
            </v-list-item>
          </template>
        </v-tooltip>
      </v-list>
      <template #append>
        <v-divider />
        <div class="text-caption text-medium-emphasis px-4 py-3 text-break">
          本机 {{ meta?.root || "" }}
        </div>
      </template>
    </v-navigation-drawer>

    <v-main>
      <v-container class="page-wrap" fluid>
        <router-view />
      </v-container>
    </v-main>
    <PiDrawer />
    <AssistantDrawer />
    <v-btn
      class="assistant-fab"
      color="primary"
      icon
      size="large"
      :title="'助手'"
      @click="openAssistant"
    >
      <v-icon :icon="mdiCreationOutline" />
    </v-btn>
    <v-snackbar v-model="snack.show" :color="snack.color" timeout="2800" location="bottom right">
      {{ snack.text }}
    </v-snackbar>
  </v-app>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useDisplay, useTheme } from "vuetify";
import {
  mdiClipboardCheckOutline,
  mdiClipboardTextOutline,
  mdiPlusBoxOutline,
  mdiProgressClock,
  mdiSourceRepository,
  mdiTune,
  mdiCreationOutline,
  mdiViewDashboardOutline,
  mdiWeatherNight,
  mdiWeatherSunny,
} from "@mdi/js";
import { getMeta, listRequirements } from "@/api/client";
import type { JobBrief, Meta, ReqSummary } from "@/api/types";
import { ACTION_LABELS, phaseColor } from "@/composables/labels";
import { jobHref, runningJobs, watchJobs } from "@/state/jobs";
import { provideSnack } from "@/composables/snack";
import AssistantDrawer from "@/components/AssistantDrawer.vue";
import PiDrawer from "@/components/PiDrawer.vue";
import { openAssistant } from "@/state/assistant";

const snack = provideSnack();
const { mdAndUp } = useDisplay();
const theme = useTheme();
const route = useRoute();
const drawer = ref(true);
const meta = ref<Meta | null>(null);
const reqItems = ref<ReqSummary[]>([]);

let stop: (() => void) | undefined;

const isDark = computed(() => theme.global.current.value.dark);

function toggleTheme() {
  const next = isDark.value ? "light" : "dark";
  theme.global.name.value = next;
  try {
    localStorage.setItem("dev-yard-theme", next);
  } catch {
    /* ignore */
  }
}

watch(
  mdAndUp,
  (wide) => {
    drawer.value = wide;
  },
  { immediate: true },
);
watch(
  () => route.fullPath,
  () => {
    if (!mdAndUp.value) drawer.value = false;
    void loadRequirements();
  },
);

const waitingJobs = computed(() => runningJobs.value.filter((j) => j.state === "waiting"));

interface ReqRow {
  jira: string;
  subtitle: string;
  phase: string;
  job: JobBrief | null;
}

function jiraRank(jira: string) {
  const m = /(\d+)\s*$/.exec(jira);
  return m ? Number(m[1]) : 0;
}

const reqRows = computed<ReqRow[]>(() => {
  const live = new Map<string, JobBrief>();
  for (const j of runningJobs.value) {
    if (j.action === "repo_add") continue;
    live.set(j.jira.toUpperCase(), j);
  }
  const rows: ReqRow[] = reqItems.value
    .map((it) => ({
      jira: it.jira,
      subtitle: it.title || "",
      phase: it.phase,
      job: live.get(it.jira.toUpperCase()) ?? null,
    }))
    .sort((a, b) => jiraRank(b.jira) - jiraRank(a.jira) || b.jira.localeCompare(a.jira));
  const known = new Set(reqItems.value.map((i) => i.jira.toUpperCase()));
  const extras: ReqRow[] = runningJobs.value
    .filter((j) => j.action !== "repo_add" && !known.has(j.jira.toUpperCase()))
    .map((j) => ({
      jira: j.jira,
      subtitle: ACTION_LABELS[j.action] || j.action,
      phase: "",
      job: j,
    }));
  return [...extras, ...rows];
});

const barTitle = computed(() => {
  const name = String(route.name || "");
  if (name === "requirement" || name === "doc") return String(route.params.jira || "需求");
  if (name === "repos") return "仓库";
  if (name === "settings") return "配置";
  if (name === "qa-config") return "测试配置";
  if (name === "open") return "打开需求";
  return "需求";
});

async function loadRequirements() {
  try {
    reqItems.value = await listRequirements();
  } catch {
    /* keep the previous list if the fetch fails */
  }
}

onMounted(async () => {
  stop = watchJobs();
  try {
    meta.value = await getMeta();
  } catch {
    meta.value = null;
  }
  await loadRequirements();
});
onUnmounted(() => stop?.());

function onNav() {
  if (!mdAndUp.value) drawer.value = false;
}
</script>

<style>
.page-wrap {
  max-width: 1480px;
  padding: 16px !important;
}
@media (min-width: 960px) {
  .page-wrap {
    padding: 20px 24px !important;
  }
}
.jira-app-bar {
  transition: background-color 0.2s ease;
}
.jira-product {
  font-weight: 600;
  font-size: 16px;
  margin-right: 16px;
  white-space: nowrap;
}
.jira-avatar-border {
  box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.35);
}
.jira-nav-active {
  background-color: rgba(var(--v-theme-primary), 0.1) !important;
  color: rgb(var(--v-theme-primary)) !important;
  font-weight: 600 !important;
}
.jira-lozenge {
  font-size: 11px !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.03em !important;
  border-radius: 3px !important;
  height: 20px !important;
  padding: 0 6px !important;
}
.assistant-fab {
  position: fixed !important;
  right: 24px;
  bottom: 24px;
  z-index: 20;
}
</style>
