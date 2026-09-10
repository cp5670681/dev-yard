<template>
  <v-app>
    <v-app-bar :color="isDark ? 'surface' : '#0747A6'" elevation="0" :border="isDark" class="jira-app-bar">
      <v-app-bar-nav-icon v-if="!mdAndUp" :color="isDark ? undefined : 'white'" @click="drawer = !drawer" />
      <v-avatar :color="isDark ? 'primary' : '#0052CC'" rounded="lg" size="32" class="ml-2 mr-3 jira-avatar-border">
        <span class="text-white font-weight-black text-subtitle-2">Y</span>
      </v-avatar>
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
      width="280"
      color="surface"
      border
    >
      <v-list-item :title="'dev-yard'" :subtitle="meta?.root_name || ''" class="mt-2 jira-brand-item" to="/">
        <template #prepend>
          <v-avatar color="primary" rounded="lg" size="36">
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
          title="模型"
          subtitle="按阶段选 pi 模型"
          :prepend-icon="mdiTune"
          active-class="jira-nav-active"
        />
      </v-list>
      <v-divider class="my-2" />
      <v-list-subheader v-if="runningJobs.length">进行中</v-list-subheader>
      <v-list v-if="runningJobs.length" density="compact" nav>
        <v-list-item
          v-for="job in runningJobs"
          :key="job.id"
          :to="jobHref(job)"
          :title="jobLabel(job)"
          :subtitle="job.state"
          active-class="jira-nav-active"
          @click="onNav"
        >
          <template #prepend>
            <v-progress-circular
              v-if="job.state !== 'waiting'"
              indeterminate
              size="18"
              width="2"
              color="primary"
            />
            <v-icon v-else :icon="mdiProgressClock" color="error" size="18" />
          </template>
          <template v-if="job.state === 'waiting'" #append>
            <v-badge color="error" content="待答" inline />
          </template>
        </v-list-item>
      </v-list>
      <v-list-subheader v-if="idleRecents.length">最近</v-list-subheader>
      <v-list v-if="idleRecents.length" density="compact" nav>
        <v-list-item
          v-for="jira in idleRecents"
          :key="jira"
          :to="`/r/${jira}`"
          :title="jira"
          subtitle="回到需求页"
          :prepend-icon="mdiClipboardTextOutline"
          active-class="jira-nav-active"
          @click="onNav"
        />
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
  mdiClipboardTextOutline,
  mdiPlusBoxOutline,
  mdiProgressClock,
  mdiSourceRepository,
  mdiTune,
  mdiViewDashboardOutline,
  mdiWeatherNight,
  mdiWeatherSunny,
} from "@mdi/js";
import { getMeta, listRequirements } from "@/api/client";
import type { JobBrief, Meta } from "@/api/types";
import { ACTION_LABELS } from "@/composables/labels";
import { jobHref, runningJobs, watchJobs } from "@/state/jobs";
import { provideSnack } from "@/composables/snack";
import { pruneRecents, recentJiras, touchRecent } from "@/composables/recents";
import PiDrawer from "@/components/PiDrawer.vue";

const snack = provideSnack();
const { mdAndUp } = useDisplay();
const theme = useTheme();
const route = useRoute();
const drawer = ref(true);
const meta = ref<Meta | null>(null);
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
  },
);

const waitingJobs = computed(() => runningJobs.value.filter((j) => j.state === "waiting"));

const idleRecents = computed(() => {
  const live = new Set(
    runningJobs.value.filter((j) => j.action !== "repo_add").map((j) => j.jira),
  );
  return recentJiras.value.filter((j) => !live.has(j));
});

watch(
  () => [route.name, route.params.jira] as const,
  ([name, jira]) => {
    if ((name === "requirement" || name === "doc") && typeof jira === "string") {
      touchRecent(jira);
    }
  },
  { immediate: true },
);

const barTitle = computed(() => {
  const name = String(route.name || "");
  if (name === "requirement" || name === "doc") return String(route.params.jira || "需求");
  if (name === "repos") return "仓库";
  if (name === "settings") return "模型";
  if (name === "open") return "打开需求";
  return "需求";
});

onMounted(async () => {
  stop = watchJobs();
  try {
    meta.value = await getMeta();
  } catch {
    meta.value = null;
  }
  try {
    const items = await listRequirements();
    pruneRecents(items.map((i) => i.jira));
  } catch {
    /* keep recents if list fails */
  }
});
onUnmounted(() => stop?.());

function onNav() {
  if (!mdAndUp.value) drawer.value = false;
}

function jobLabel(job: JobBrief) {
  const action = ACTION_LABELS[job.action] || job.action;
  if (job.action === "repo_add") return action;
  const tickets = (job.ticket_ids || []).join(",");
  return `${job.jira} · ${action}${tickets ? " " + tickets : ""}`;
}
</script>

<style>
.page-wrap {
  max-width: 1400px;
  padding: 16px !important;
}
@media (min-width: 960px) {
  .page-wrap {
    padding: 24px 32px !important;
  }
}
.jira-app-bar {
  transition: background-color 0.2s ease;
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
</style>
