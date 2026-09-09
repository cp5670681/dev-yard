<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 mb-2" />
    <div class="d-flex flex-wrap align-start justify-space-between ga-3 mb-4">
      <div>
        <h1 class="text-h5 text-md-h4">{{ detail?.title || jira }}</h1>
        <p class="text-medium-emphasis mb-0">
          <span v-if="detail?.title" class="me-2">{{ jira }}</span>{{ lede }}
        </p>
      </div>
      <v-chip v-if="detail" :color="phaseColor(detail.phase)" variant="tonal">
        {{ detail.phase }}
      </v-chip>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <JobPanel
      v-for="id in jobIds"
      :key="id"
      :job-id="id"
      @done="onJobDone"
    />
    <template v-if="detail">
      <v-stepper :model-value="stepperValue" alt-labels class="mb-6" hide-actions>
        <v-stepper-header>
          <template v-for="(step, i) in detail.steps" :key="step.id">
            <v-stepper-item
              :value="i + 1"
              :title="STEP_LABELS[step.id] || step.id"
              :complete="step.done"
              :color="step.current ? 'primary' : step.done ? 'success' : undefined"
            />
            <v-divider v-if="i < detail.steps.length - 1" />
          </template>
        </v-stepper-header>
      </v-stepper>

      <div class="d-flex flex-wrap align-center ga-2 mb-4">
        <v-tooltip :text="nextAction?.reason || nextAction?.label || ''" :disabled="!nextAction?.reason">
          <template #activator="{ props: tip }">
            <v-btn
              v-if="nextAction"
              v-bind="tip"
              color="primary"
              :disabled="!nextAction.enabled"
              :loading="acting === nextAction.id"
              :block="!mdAndUp"
              @click="confirmAction(nextAction.id)"
            >
              {{ ACTION_LABELS[nextAction.id] || nextAction.label }}
            </v-btn>
          </template>
        </v-tooltip>
        <template v-if="mdAndUp">
          <v-tooltip
            v-for="a in otherActions"
            :key="a.id"
            :text="a.reason || ACTION_LABELS[a.id] || a.label"
            :disabled="!a.reason"
          >
            <template #activator="{ props: tip }">
              <v-btn
                v-bind="tip"
                variant="tonal"
                :disabled="!a.enabled"
                :loading="acting === a.id"
                @click="confirmAction(a.id)"
              >
                {{ ACTION_LABELS[a.id] || a.label }}
              </v-btn>
            </template>
          </v-tooltip>
        </template>
        <v-menu v-else>
          <template #activator="{ props: menuProps }">
            <v-btn v-bind="menuProps" variant="tonal" :block="!mdAndUp">更多操作</v-btn>
          </template>
          <v-list>
            <v-list-item
              v-for="a in otherActions"
              :key="a.id"
              :title="ACTION_LABELS[a.id] || a.label"
              :disabled="!a.enabled"
              :subtitle="a.reason || undefined"
              @click="confirmAction(a.id)"
            />
          </v-list>
        </v-menu>
        <v-switch
          v-if="detail.phase !== 'open'"
          v-model="forceOpen"
          hide-details
          density="compact"
          color="warning"
          label="重置阶段（会删截图）"
        />
      </div>
      <v-tabs :model-value="'board'" class="mb-4" show-arrows color="primary">
        <v-tab :to="`/r/${jira}`">看板</v-tab>
        <v-tab
          v-for="d in detail.docs"
          :key="d.slug"
          :to="`/r/${jira}/docs/${d.slug}`"
        >
          {{ d.filename }}
          <v-chip v-if="!d.filled" size="x-small" class="ml-2" variant="text">骨架</v-chip>
        </v-tab>
      </v-tabs>
      <TicketBoard
        :tickets="detail.tickets"
        @implement="(id) => confirmAction('implement', id)"
        @review="(id) => confirmAction('review', id)"
      />
      <v-card v-if="detail.assets.length" class="mt-6" variant="outlined">
        <v-card-title>截图</v-card-title>
        <v-card-text>
          <v-row>
            <v-col v-for="name in detail.assets" :key="name" cols="6" md="3">
              <v-hover v-slot="{ isHovering, props: hoverProps }">
                <v-card
                  v-bind="hoverProps"
                  :elevation="isHovering ? 6 : 0"
                  variant="tonal"
                  class="cursor-pointer"
                  @click="preview = name"
                >
                  <v-img :src="assetUrl(name)" :alt="name" height="120" cover />
                  <v-card-subtitle class="text-truncate">{{ name }}</v-card-subtitle>
                </v-card>
              </v-hover>
            </v-col>
          </v-row>
        </v-card-text>
      </v-card>
      <v-expansion-panels v-if="detail.worktrees.length || detail.contract_summary" class="mt-4" variant="accordion">
        <v-expansion-panel v-if="detail.worktrees.length" title="Worktrees">
          <v-expansion-panel-text>
            <v-list density="compact">
              <v-list-item v-for="p in detail.worktrees" :key="p">
                <v-list-item-title class="text-break font-weight-regular">
                  <code>{{ p }}</code>
                </v-list-item-title>
                <template #append>
                  <v-btn icon size="x-small" variant="text" @click="copy(p)">
                    <v-icon :icon="mdiContentCopy" size="16" />
                  </v-btn>
                </template>
              </v-list-item>
            </v-list>
          </v-expansion-panel-text>
        </v-expansion-panel>
        <v-expansion-panel v-if="detail.contract_summary" title="契约审查摘要">
          <v-expansion-panel-text>
            <pre class="job-log">{{ detail.contract_summary }}</pre>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>

    <v-dialog v-model="confirm.open" max-width="420">
      <v-card>
        <v-card-title>确认操作</v-card-title>
        <v-card-text>{{ confirm.text }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirm.open = false">取消</v-btn>
          <v-btn color="primary" @click="runConfirmed">继续</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="previewOpen" max-width="960">
      <v-card v-if="preview">
        <v-img :src="assetUrl(preview)" :alt="preview" />
        <v-card-actions>
          <span class="text-caption px-2">{{ preview }}</span>
          <v-spacer />
          <v-btn :href="assetUrl(preview)" target="_blank" variant="text">新窗口</v-btn>
          <v-btn variant="text" @click="preview = ''">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import { mdiContentCopy } from "@mdi/js";
import { getRequirement, runAction } from "@/api/client";
import type { ReqDetail } from "@/api/types";
import JobPanel from "@/components/JobPanel.vue";
import TicketBoard from "@/components/TicketBoard.vue";
import { runningJobs, watchJobs } from "@/state/jobs";
import { ACTION_LABELS, phaseColor, STEP_LABELS } from "@/composables/labels";
import { useSnack } from "@/composables/snack";

const { mdAndUp } = useDisplay();
const snack = useSnack();
const route = useRoute();
const router = useRouter();
const jira = computed(() => String(route.params.jira || ""));
const detail = ref<ReqDetail | null>(null);
const error = ref("");
const forceOpen = ref(false);
const acting = ref("");
const preview = ref("");
const previewOpen = computed({
  get: () => !!preview.value,
  set: (v: boolean) => {
    if (!v) preview.value = "";
  },
});
const confirm = reactive({ open: false, action: "", ticketId: "", text: "" });
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

const crumbs = computed(() => [
  { title: "需求", to: "/" },
  { title: jira.value, disabled: true },
]);

const stepperValue = computed(() => {
  if (!detail.value) return 1;
  const idx = detail.value.steps.findIndex((s) => s.current);
  return idx >= 0 ? idx + 1 : detail.value.steps.filter((s) => s.done).length;
});

const lede = computed(() => {
  if (!detail.value) return "目录还没写完，任务在跑。";
  const c = detail.value.contract ? ` · 契约 ${detail.value.contract}` : "";
  const next = STEP_LABELS[detail.value.next] || detail.value.next;
  return `下一步 ${next}${c}`;
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

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snack.notify("已复制路径", "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

function confirmAction(action: string, ticketId?: string) {
  const risky = action === "open" && forceOpen.value;
  const freeze = action === "freeze";
  if (risky || freeze) {
    confirm.action = action;
    confirm.ticketId = ticketId || "";
    confirm.text = freeze
      ? "冻结后会切 worktree。确认继续？"
      : "强制重抽会重置阶段并删除截图。确认继续？";
    confirm.open = true;
    return;
  }
  void onAction(action, ticketId);
}

function runConfirmed() {
  confirm.open = false;
  void onAction(confirm.action, confirm.ticketId || undefined);
}

async function onAction(action: string, ticketId?: string) {
  error.value = "";
  acting.value = action;
  try {
    const out = await runAction(jira.value, action, {
      ticket_id: ticketId,
      force: action === "open" ? forceOpen.value : false,
    });
    const job = out.jobs[0]?.id;
    if (job) {
      await router.replace({ query: { ...route.query, job } });
    }
    snack.notify(`已启动 ${ACTION_LABELS[action] || action}`, "success");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
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
  background: rgb(var(--v-theme-background));
  color: #d5deea;
  border-radius: 8px;
  padding: 0.8rem;
  white-space: pre-wrap;
  font-size: 0.8rem;
}
.cursor-pointer {
  cursor: pointer;
}
</style>
