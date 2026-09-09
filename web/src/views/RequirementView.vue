<template>
  <div>
    <div class="text-caption text-medium-emphasis mb-1">
      <router-link to="/">需求</router-link> / {{ jira }}
    </div>
    <div class="d-flex flex-wrap align-start justify-space-between ga-3 mb-4">
      <div>
        <h1 class="text-h5 text-md-h4">{{ jira }}</h1>
        <p class="text-medium-emphasis mb-0">{{ lede }}</p>
      </div>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" variant="tonal">{{ error }}</v-alert>
    <JobPanel
      v-for="id in jobIds"
      :key="id"
      :job-id="id"
      @done="onJobDone"
    />
    <template v-if="detail">
      <v-slide-group show-arrows class="mb-4">
        <v-slide-group-item v-for="(step, i) in detail.steps" :key="step.id">
          <v-chip
            class="ma-1"
            :color="step.current ? 'primary' : step.done ? 'success' : undefined"
            :variant="step.current || step.done ? 'tonal' : 'outlined'"
          >
            {{ i + 1 }} {{ stepLabels[step.id] || step.id }}
          </v-chip>
        </v-slide-group-item>
      </v-slide-group>

      <div class="d-flex flex-wrap align-center ga-2 mb-4">
        <v-btn
          v-if="nextAction"
          color="primary"
          :disabled="!nextAction.enabled"
          :block="!mdAndUp"
          :title="nextAction.reason"
          @click="onAction(nextAction.id)"
        >
          {{ nextAction.label }}
        </v-btn>
        <template v-if="mdAndUp">
          <v-btn
            v-for="a in otherActions"
            :key="a.id"
            variant="tonal"
            :disabled="!a.enabled"
            :title="a.reason"
            @click="onAction(a.id)"
          >
            {{ a.label }}
          </v-btn>
        </template>
        <v-menu v-else>
          <template #activator="{ props: menuProps }">
            <v-btn v-bind="menuProps" variant="tonal" :block="!mdAndUp">更多操作</v-btn>
          </template>
          <v-list>
            <v-list-item
              v-for="a in otherActions"
              :key="a.id"
              :title="a.label"
              :disabled="!a.enabled"
              :subtitle="a.reason || undefined"
              @click="onAction(a.id)"
            />
          </v-list>
        </v-menu>
        <v-checkbox
          v-if="detail.phase !== 'open'"
          v-model="forceOpen"
          hide-details
          density="compact"
          label="重置阶段（会删截图）"
        />
      </div>
      <v-tabs :model-value="'board'" class="mb-4" show-arrows>
        <v-tab :to="`/r/${jira}`">看板</v-tab>
        <v-tab
          v-for="d in detail.docs"
          :key="d.slug"
          :to="`/r/${jira}/docs/${d.slug}`"
        >
          {{ d.filename }}{{ d.filled ? "" : " · 骨架" }}
        </v-tab>
      </v-tabs>
      <TicketBoard
        :tickets="detail.tickets"
        @implement="(id) => onAction('implement', id)"
        @review="(id) => onAction('review', id)"
      />
      <v-card v-if="detail.assets.length" class="mt-6" variant="outlined">
        <v-card-title>截图</v-card-title>
        <v-card-text>
          <v-row>
            <v-col v-for="name in detail.assets" :key="name" cols="6" md="3">
              <a :href="assetUrl(name)" target="_blank" rel="noopener">
                <v-img :src="assetUrl(name)" :alt="name" height="120" cover class="rounded" />
                <div class="text-caption mt-1">{{ name }}</div>
              </a>
            </v-col>
          </v-row>
        </v-card-text>
      </v-card>
      <v-card v-if="detail.worktrees.length" class="mt-4" variant="outlined">
        <v-card-title>Worktrees</v-card-title>
        <v-card-text>
          <div v-for="p in detail.worktrees" :key="p" class="text-break"><code>{{ p }}</code></div>
        </v-card-text>
      </v-card>
      <v-card v-if="detail.contract_summary" class="mt-4" variant="outlined">
        <v-card-title>契约审查摘要</v-card-title>
        <v-card-text>
          <pre class="job-log">{{ detail.contract_summary }}</pre>
        </v-card-text>
      </v-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import { getRequirement, runAction } from "@/api/client";
import type { ReqDetail } from "@/api/types";
import JobPanel from "@/components/JobPanel.vue";
import TicketBoard from "@/components/TicketBoard.vue";
import { runningJobs, watchJobs } from "@/state/jobs";

const stepLabels: Record<string, string> = {
  open: "抽取",
  grill: "Grill",
  spec: "Spec",
  tickets: "拆票",
  freeze: "冻结",
  implement: "实现",
  review: "审查",
  done: "完成",
};

const { mdAndUp } = useDisplay();
const route = useRoute();
const router = useRouter();
const jira = computed(() => String(route.params.jira || ""));
const detail = ref<ReqDetail | null>(null);
const error = ref("");
const forceOpen = ref(false);
const extraJob = computed(() =>
  typeof route.query.job === "string" ? route.query.job : "",
);
const jobIds = computed(() => {
  const live = runningJobs.value
    .filter((j) => j.jira === jira.value)
    .map((j) => j.id);
  if (extraJob.value && !live.includes(extraJob.value)) return [extraJob.value, ...live];
  return live;
});

const lede = computed(() => {
  if (!detail.value) return "目录还没写完，任务在跑。";
  const c = detail.value.contract ? ` · 契约 ${detail.value.contract}` : "";
  return `phase=${detail.value.phase} · 下一步 ${detail.value.next}${c}`;
});

const nextAction = computed(() => {
  if (!detail.value) return null;
  return (
    detail.value.actions.find((a) => a.id === detail.value?.next) ||
    detail.value.actions.find((a) => a.enabled) ||
    null
  );
});
const otherActions = computed(() => {
  if (!detail.value || !nextAction.value) return detail.value?.actions || [];
  return detail.value.actions.filter((a) => a.id !== nextAction.value?.id);
});

let stop: (() => void) | undefined;

async function load() {
  error.value = "";
  try {
    detail.value = await getRequirement(jira.value);
  } catch (e) {
    detail.value = null;
    if (!jobIds.value.length) {
      error.value = e instanceof Error ? e.message : String(e);
    }
  }
}

function assetUrl(name: string) {
  return `/r/${encodeURIComponent(jira.value)}/assets/${encodeURIComponent(name)}`;
}

async function onAction(action: string, ticketId?: string) {
  error.value = "";
  try {
    const out = await runAction(jira.value, action, {
      ticket_id: ticketId,
      force: action === "open" ? forceOpen.value : false,
    });
    const job = out.jobs[0]?.id;
    if (job) {
      await router.replace({ query: { ...route.query, job } });
    }
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function onJobDone() {
  void load();
}

onMounted(() => {
  stop = watchJobs();
  void load();
});
onUnmounted(() => stop?.());
watch(jira, () => void load());
watch(runningJobs, () => {
  if (!detail.value) void load();
});
</script>

<style scoped>
.job-log {
  background: #0a0d12;
  color: #d5deea;
  border-radius: 8px;
  padding: 0.8rem;
  white-space: pre-wrap;
  font-size: 0.8rem;
}
</style>
