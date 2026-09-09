<template>
  <v-app>
    <v-app-bar color="surface" elevation="0" border>
      <v-app-bar-nav-icon v-if="!mdAndUp" @click="drawer = !drawer" />
      <v-avatar color="primary" rounded="lg" size="32" class="ml-2 mr-3">
        <span class="text-grey-darken-4 font-weight-bold">Y</span>
      </v-avatar>
      <v-app-bar-title class="text-truncate">{{ barTitle }}</v-app-bar-title>
      <v-chip
        v-if="mdAndUp && runningJobs.length"
        size="small"
        color="primary"
        variant="tonal"
        class="mr-2"
      >
        <v-progress-circular indeterminate size="12" width="2" class="mr-2" />
        {{ runningJobs.length }} 个任务
      </v-chip>
      <v-btn
        v-if="!mdAndUp && waitingJobs.length"
        icon
        :to="jobHref(waitingJobs[0])"
        @click="drawer = false"
      >
        <v-badge color="error" dot>
          <v-icon :icon="mdiProgressClock" />
        </v-badge>
      </v-btn>
    </v-app-bar>

    <v-navigation-drawer
      v-model="drawer"
      :permanent="mdAndUp"
      :temporary="!mdAndUp"
      width="280"
      color="surface"
    >
      <v-list-item :title="'dev-yard'" :subtitle="meta?.root_name || ''" class="mt-2" to="/">
        <template #prepend>
          <v-avatar color="primary" rounded="lg" size="36">
            <span class="text-grey-darken-4 font-weight-bold">Y</span>
          </v-avatar>
        </template>
      </v-list-item>
      <v-list nav density="comfortable" class="mt-2">
        <v-list-item
          to="/"
          title="需求"
          subtitle="看板与阶段"
          :prepend-icon="mdiViewDashboardOutline"
        />
        <v-list-item
          to="/repos"
          title="仓库"
          subtitle="登记业务仓"
          :prepend-icon="mdiSourceRepository"
        />
        <v-list-item
          to="/open"
          title="打开需求"
          subtitle="抽取一张 Jira"
          :prepend-icon="mdiPlusBoxOutline"
        />
        <v-list-item
          to="/settings"
          title="模型"
          subtitle="按阶段选 pi 模型"
          :prepend-icon="mdiTune"
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
import { useDisplay } from "vuetify";
import {
  mdiClipboardTextOutline,
  mdiPlusBoxOutline,
  mdiProgressClock,
  mdiSourceRepository,
  mdiTune,
  mdiViewDashboardOutline,
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
const route = useRoute();
const drawer = ref(true);
const meta = ref<Meta | null>(null);
let stop: (() => void) | undefined;

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
    padding: 28px 32px !important;
  }
}
</style>
