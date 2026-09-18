<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 mb-1" />
    <div class="ghx-header">
      <div class="ghx-header-top">
        <div>
          <div v-if="detail?.title" class="ghx-board-name">{{ jira }}</div>
          <h1 class="ghx-sprint-title">{{ detail?.title || jira }}</h1>
        </div>
        <div class="d-flex align-center ga-2">
          <v-chip
            v-if="detail?.contract"
            size="small"
            :color="detail.contract === 'passed' ? 'success' : 'error'"
            variant="tonal"
            class="jira-lozenge font-weight-bold cursor-pointer"
            :prepend-icon="detail.contract === 'passed' ? mdiCheckCircleOutline : mdiAlertCircleOutline"
            @click="contractDialog = true"
          >
            {{ detail.contract === 'passed' ? '契约审查通过' : '契约审查未通过' }}
          </v-chip>
          <v-chip v-if="detail" :color="phaseColor(detail.phase)" variant="tonal" class="jira-lozenge">
            {{ detail.phase }}
          </v-chip>
          <v-btn
            v-if="detail"
            variant="text"
            color="error"
            size="small"
            :prepend-icon="mdiDeleteOutline"
            @click="deleteOpen = true"
          >
            删除需求
          </v-btn>
        </div>
      </div>
      <p class="ghx-lede mb-0">{{ lede }}</p>
    </div>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <JobPanel
      v-for="id in jobIds"
      :key="id"
      :job-id="id"
      @done="onJobDone"
      @update="onJobUpdate"
    />
    <template v-if="detail">
      <div class="d-flex flex-wrap align-center ga-2 mb-4">
        <template v-for="(step, i) in detail.steps" :key="step.id">
          <v-chip
            :color="step.current ? 'primary' : step.done ? 'success' : undefined"
            :variant="step.current ? 'flat' : step.done ? 'tonal' : 'outlined'"
            size="small"
            class="jira-lozenge font-weight-bold"
          >
            {{ i + 1 }}. {{ STEP_LABELS[step.id] || step.id }}
          </v-chip>
          <span v-if="i < detail.steps.length - 1" class="text-medium-emphasis">›</span>
        </template>
      </div>

      <div class="d-flex flex-wrap align-center ga-2 mb-4">
        <v-tooltip :text="nextAction?.reason || nextAction?.label || ''" :disabled="!nextAction?.reason">
          <template #activator="{ props: tip }">
            <span v-if="nextAction" v-bind="tip" class="d-inline-block">
              <v-btn
                color="primary"
                class="ghx-create-btn"
                :disabled="!nextAction.enabled"
                :loading="acting === nextAction.id"
                :block="!mdAndUp"
                @click="confirmAction(nextAction.id, undefined, nextAction)"
              >
                {{ ACTION_LABELS[nextAction.id] || nextAction.label }}
              </v-btn>
            </span>
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
              <span v-bind="tip" class="d-inline-block" @click="!a.enabled && confirmAction(a.id, undefined, a)">
                <v-btn
                  variant="tonal"
                  :disabled="!a.enabled"
                  :loading="acting === a.id"
                  @click="confirmAction(a.id, undefined, a)"
                >
                  {{ ACTION_LABELS[a.id] || a.label }}
                  <v-chip
                    v-if="detail.stage_runs?.[a.id]"
                    class="ml-2"
                    size="x-small"
                    :color="detail.stage_runs[a.id].ok ? 'success' : 'error'"
                    variant="flat"
                  >
                    {{ detail.stage_runs[a.id].ok ? "ok" : "err" }}
                  </v-chip>
                </v-btn>
              </span>
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
              @click="confirmAction(a.id, undefined, a)"
            >
              <template v-if="detail.stage_runs?.[a.id]" #append>
                <v-chip
                  size="x-small"
                  :color="detail.stage_runs[a.id].ok ? 'success' : 'error'"
                  variant="flat"
                >
                  {{ detail.stage_runs[a.id].ok ? "ok" : "err" }}
                </v-chip>
              </template>
            </v-list-item>
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

      <!-- 契约审查状态显式横幅 -->
      <v-alert
        v-if="detail.contract === 'failed'"
        type="error"
        variant="tonal"
        border="start"
        class="mb-4"
        :icon="mdiFileDocumentAlertOutline"
      >
        <div class="d-flex flex-column flex-sm-row justify-space-between align-sm-center ga-3">
          <div>
            <div class="text-subtitle-1 font-weight-bold">
              跨仓契约审查未通过 (Contract Review Failed)
            </div>
            <div class="text-body-2 text-medium-emphasis mt-0.5">
              检测到跨仓接口或业务契约存在缺口（阻塞项）。需修复契约缺口并重新审查通过后，方可进入提测阶段。
            </div>
          </div>
          <div class="d-flex flex-wrap align-center ga-2 flex-shrink-0">
            <v-btn
              v-if="detail.contract_summary"
              size="small"
              variant="outlined"
              color="error"
              :prepend-icon="mdiEyeOutline"
              @click="contractDialog = true"
            >
              查看契约报告
            </v-btn>
            <v-btn
              size="small"
              color="error"
              variant="flat"
              :prepend-icon="mdiWrench"
              :loading="acting === 'fix-contract'"
              @click="confirmAction('fix-contract')"
            >
              立即按契约修
            </v-btn>
            <v-btn
              size="small"
              variant="text"
              color="error"
              :prepend-icon="mdiRefresh"
              :loading="acting === 'contract'"
              @click="confirmAction('contract')"
            >
              重跑契约审查
            </v-btn>
          </div>
        </div>
      </v-alert>

      <v-alert
        v-else-if="detail.contract === 'passed'"
        type="success"
        variant="tonal"
        border="start"
        density="comfortable"
        class="mb-4"
        :icon="mdiFileDocumentCheckOutline"
      >
        <div class="d-flex justify-space-between align-center flex-wrap ga-2">
          <div>
            <span class="font-weight-bold">跨仓契约审查已通过 (Contract Review Passed)</span>
            <span class="text-caption text-medium-emphasis ms-2">各仓代码与 SPEC 接口及时序契约一致</span>
          </div>
          <v-btn
            v-if="detail.contract_summary"
            size="x-small"
            variant="text"
            color="success"
            :prepend-icon="mdiEyeOutline"
            @click="contractDialog = true"
          >
            查看报告
          </v-btn>
        </div>
      </v-alert>
      <ReqDocTabs :jira="jira" :docs="detail.docs" current="board" />

      <v-card
        v-if="liveProgress && liveHasActive"
        class="mb-4"
        variant="tonal"
        color="warning"
      >
        <v-card-title class="text-subtitle-1">自动测试</v-card-title>
        <v-card-text>
          <p class="text-caption mb-2">
            <span v-for="(p, i) in livePools" :key="p.id">
              <span v-if="i"> · </span>{{ p.model || p.id }} {{ p.inflight }}/{{ p.concurrency }}
            </span>
          </p>
          <div class="d-flex flex-wrap ga-2 mb-2">
            <v-chip
              v-for="c in liveRunning"
              :key="c.id"
              size="small"
              color="primary"
              variant="tonal"
              class="cursor-pointer"
              :title="`查看用例 ${c.id} 详情`"
              @click="openCaseDetail(c.id)"
            >
              {{ c.id }} {{ c.title }} · {{ c.model }}
            </v-chip>
          </div>
          <p v-if="liveReady.length" class="text-caption text-medium-emphasis mb-0">
            就绪未派发 {{ liveReady.map((c) => c.id).join(", ") }}
          </p>
        </v-card-text>
      </v-card>

      <TicketBoard
        :tickets="detail.tickets"
        :qa-cases="detail.qa?.cases"
        :qa-progress="liveProgress"
        :jira="jira"
        :phase="detail.phase"
        @implement="(id) => confirmAction('implement', id)"
        @review="(id) => confirmAction('review', id)"
        @diff="(id) => openDiff(id)"
        @feedback="(ticket) => openReview(ticket)"
        @preview-screenshot="(url) => preview = url"
        @fill-bug="confirmAction('fill-test-report')"
        @open-case="openCaseDetail"
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
      <v-expansion-panels
        v-if="detail.worktrees.length || detail.contract_summary || detail.test"
        class="mt-4"
        variant="accordion"
      >
        <v-expansion-panel v-if="detail.worktrees.length" title="Worktrees">
          <v-expansion-panel-text>
            <div class="d-flex justify-end mb-2">
              <v-btn
                size="small"
                variant="tonal"
                color="primary"
                :prepend-icon="mdiCloudUploadOutline"
                :loading="acting === 'push'"
                @click="confirmAction('push')"
              >
                一键推送到远端
              </v-btn>
            </div>
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
        <v-expansion-panel v-if="detail.contract_summary">
          <v-expansion-panel-title>
            <div class="d-flex align-center ga-2">
              <span>契约审查摘要</span>
              <v-chip
                size="x-small"
                :color="detail.contract === 'passed' ? 'success' : 'error'"
                variant="tonal"
                class="font-weight-bold"
              >
                {{ detail.contract === 'passed' ? '通过 passed' : '未通过 failed' }}
              </v-chip>
            </div>
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div v-if="detail.contract_summary_html" class="markdown" v-html="detail.contract_summary_html" />
            <pre v-else class="job-log">{{ detail.contract_summary }}</pre>
          </v-expansion-panel-text>
        </v-expansion-panel>
        <v-expansion-panel v-if="detail.test || detail.qa?.latest_run" title="提测 / bug">
          <v-expansion-panel-text>
            <p v-if="detail.test" class="mb-2">
              状态 {{ detail.test.status || "-" }}
              · 最近一轮 {{ detail.test.latest_verdict || "-" }}
              · 来源 {{ detail.test.source || "-" }}
            </p>
            <p v-if="detail.qa?.latest_run" class="mb-2">
              自动测
              passed {{ detail.qa.latest_run.summary?.passed || 0 }} /
              failed {{ detail.qa.latest_run.summary?.failed || 0 }} /
              blocked {{ detail.qa.latest_run.summary?.blocked || 0 }} /
              skipped {{ detail.qa.latest_run.summary?.skipped || 0 }}
              ·
              <router-link :to="`/r/${jira}/qa`">打开测试页</router-link>
            </p>
            <p v-if="detail.test?.summary" class="text-medium-emphasis">
              {{ detail.test.summary }}
            </p>
            <p class="text-caption text-medium-emphasis mt-2">
              缺陷内容在看板 B 票里，可单票实现 / 审查。
            </p>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>

    <v-dialog v-model="reportForm.open" max-width="780">
      <v-card>
        <v-card-title>提 bug</v-card-title>
        <v-card-text>
          <v-select
            v-model="reportForm.verdict"
            label="本轮"
            :items="[
              { title: '提交缺陷 failed', value: 'failed' },
              { title: '本轮通过（无新 bug） passed', value: 'passed' },
            ]"
          />
          <v-text-field v-model="reportForm.summary" label="摘要（可选）" class="mt-2" />
          <template v-if="reportForm.verdict !== 'passed'">
            <div
              v-for="(row, i) in reportForm.findings"
              :key="i"
              class="mt-4 pa-3 rounded-lg"
              style="border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity))"
            >
              <div class="d-flex align-center ga-2 mb-2">
                <span class="text-caption text-medium-emphasis">{{ row.id }}</span>
                <v-spacer />
                <v-btn
                  size="x-small"
                  variant="text"
                  :disabled="reportForm.findings.length < 2"
                  @click="reportForm.findings.splice(i, 1)"
                >
                  删除
                </v-btn>
              </div>
              <v-text-field v-model="row.title" label="标题" density="compact" />
              <v-select
                v-model="row.repo"
                label="仓库"
                :items="detail?.repos || []"
                density="compact"
                class="mt-2"
                :rules="[(v: string) => !!v || '必须选择仓库']"
              />
              <v-textarea v-model="row.detail" label="复现 / 期望" rows="3" class="mt-2" />
              <v-text-field
                v-model="row.dependsOn"
                label="依赖（本批 finding id，空格分隔，如 F1）"
                density="compact"
                class="mt-2"
              />
            </div>
            <v-btn class="mt-3" size="small" variant="tonal" @click="addFindingRow">再加一条</v-btn>
          </template>
          <v-textarea
            v-model="reportForm.body"
            label="备注（可选）"
            rows="3"
            class="mt-4"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="reportForm.open = false">取消</v-btn>
          <v-btn color="primary" :loading="acting === 'fill-test-report'" @click="submitReport">
            提交
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deleteOpen" max-width="480">
      <v-card>
        <v-card-title>删除需求？</v-card-title>
        <v-card-text>
          将删除 <strong>{{ jira }}</strong> 的文档、截图和本票 worktree，并去掉对应本地分支。
          不会改 Jira，也不会动共用术语和 ADR。此操作不可恢复。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleteOpen = false">取消</v-btn>
          <v-btn color="error" :loading="acting === 'delete'" @click="doDelete">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="confirm.open" max-width="420">
      <v-card>
        <v-card-title>确认操作</v-card-title>
        <v-card-text>
          <p class="mb-2">{{ confirm.text }}</p>
          <v-select
            v-if="confirm.action === 'run-test' && qaEnvs.length"
            v-model="confirm.env"
            :items="qaEnvs"
            label="环境（qa.yaml envs）"
            variant="outlined"
            density="comfortable"
            hide-details
            class="mb-2"
          />
          <v-checkbox
            v-if="confirm.action === 'run-test' && incompleteRun"
            v-model="confirm.resume"
            hide-details
            density="compact"
            :label="`继续未完成的 run ${incompleteRun.run_id || ''}（剩 ${incompleteRun.pending || 0} 条）`"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirm.open = false">取消</v-btn>
          <v-btn color="primary" @click="runConfirmed">继续</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="previewOpen" max-width="960">
      <v-card v-if="preview">
        <v-img :src="previewImgSrc(preview)" :alt="preview" />
        <v-card-actions>
          <span class="text-caption px-2 text-truncate">{{ preview }}</span>
          <v-spacer />
          <v-btn :href="previewImgSrc(preview)" target="_blank" variant="text">新窗口</v-btn>
          <v-btn variant="text" @click="preview = ''">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <ContractReviewDialog
      v-model="contractDialog"
      :jira="jira"
      :contract="detail?.contract || null"
      :summary="detail?.contract_summary || null"
      :summary-html="detail?.contract_summary_html"
      @reviewed="onContractReviewed"
    />

    <TicketDiffDialog
      v-model="diffDialog.open"
      :jira="jira"
      :ticket-id="diffDialog.ticketId"
    />
    <CaseDetailDialog
      v-model="caseDialog.open"
      :jira="jira"
      :case-id="caseDialog.caseId"
    />
    <TicketReviewDialog
      v-model="reviewDialog.open"
      :jira="jira"
      :ticket="reviewDialog.ticket"
      @reviewed="onTicketReviewed"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import {
  mdiAlertCircleOutline,
  mdiCheckCircleOutline,
  mdiCloudUploadOutline,
  mdiContentCopy,
  mdiDeleteOutline,
  mdiEyeOutline,
  mdiFileDocumentAlertOutline,
  mdiFileDocumentCheckOutline,
  mdiRefresh,
  mdiWrench,
} from "@mdi/js";
import { deleteRequirement, getRequirement, runAction, submitTestReport } from "@/api/client";
import type { Action, JobSnapshot, QaProgress, ReqDetail, Ticket } from "@/api/types";
import ContractReviewDialog from "@/components/ContractReviewDialog.vue";
import JobPanel from "@/components/JobPanel.vue";
import ReqDocTabs from "@/components/ReqDocTabs.vue";
import TicketBoard from "@/components/TicketBoard.vue";
import CaseDetailDialog from "@/components/CaseDetailDialog.vue";
import TicketDiffDialog from "@/components/TicketDiffDialog.vue";
import TicketReviewDialog from "@/components/TicketReviewDialog.vue";
import { runningJobs, watchJobs } from "@/state/jobs";
import { ACTION_LABELS, phaseColor, STEP_LABELS } from "@/composables/labels";
import { forgetRecent } from "@/composables/recents";
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
const confirm = reactive({
  open: false,
  action: "",
  ticketId: "",
  text: "",
  env: "",
  resume: true,
});
const qaEnvs = computed(() => detail.value?.qa?.envs ?? []);
const incompleteRun = computed(() => detail.value?.qa?.incomplete_run || null);
const deleteOpen = ref(false);
const diffDialog = reactive({ open: false, ticketId: "" });
const reviewDialog = reactive({ open: false, ticket: null as Ticket | null });
const contractDialog = ref(false);
const jobProgress = ref<QaProgress | null>(null);
const caseDialog = reactive({ open: false, caseId: "" });
const caseFromQuery = ref(false);

function openDiff(ticketId: string) {
  diffDialog.ticketId = ticketId;
  diffDialog.open = true;
}

function openCaseDetail(caseId: string, fromQuery = false) {
  if (!caseId) return;
  caseDialog.caseId = caseId;
  caseDialog.open = true;
  caseFromQuery.value = fromQuery;
}

watch(
  () => caseDialog.open,
  (open) => {
    if (open || !caseFromQuery.value) return;
    caseFromQuery.value = false;
    // Deep link consumed: drop the param so internal reloads don't reopen it.
    const query = { ...route.query };
    delete query.case;
    void router.replace({ query });
  },
);

watch(
  () => route.query.case,
  (value) => {
    const caseId = String(value || "");
    if (caseId) openCaseDetail(caseId, true);
  },
  { immediate: true },
);

function openReview(ticket: Ticket) {
  reviewDialog.ticket = ticket;
  reviewDialog.open = true;
}

async function onTicketReviewed(ticketId: string, jobs: JobSnapshot[]) {
  reviewDialog.open = false;
  if (jobs.length > 0 && jobs[0]?.id) {
    await router.replace({ query: { ...route.query, job: jobs[0].id } });
    snack.notify(`已保存反馈并启动修复 (${ticketId})`, "success");
  } else {
    snack.notify(`已更新 ${ticketId} 审查结果`, "success");
  }
  await load();
}

async function onContractReviewed(jobs: JobSnapshot[]) {
  contractDialog.value = false;
  if (jobs.length > 0 && jobs[0]?.id) {
    await router.replace({ query: { ...route.query, job: jobs[0].id } });
    snack.notify("已保存契约审查反馈并启动修复", "success");
  } else {
    snack.notify("已更新跨仓契约审查结果", "success");
  }
  await load();
}
const reportForm = reactive({
  open: false,
  verdict: "failed",
  summary: "",
  body: "",
  findings: [
    { id: "F1", title: "", repo: "", detail: "", dependsOn: "" },
  ],
});

function addFindingRow() {
  const n = reportForm.findings.length + 1;
  reportForm.findings.push({
    id: `F${n}`,
    title: "",
    repo: detail.value?.repos?.[0] || "",
    detail: "",
    dependsOn: "",
  });
}

function resetFindingRows() {
  const repo = detail.value?.repos?.[0] || "";
  reportForm.findings = [{ id: "F1", title: "", repo, detail: "", dependsOn: "" }];
}
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

const lede = computed(() => {
  if (!detail.value) return "目录还没写完，任务在跑。";
  const c = detail.value.contract ? ` · 契约 ${detail.value.contract}` : "";
  const next =
    ACTION_LABELS[detail.value.next] || STEP_LABELS[detail.value.next] || detail.value.next;
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

const liveProgress = computed(() => jobProgress.value || detail.value?.qa?.progress || null);
const liveHasActive = computed(() =>
  Boolean(
    liveProgress.value?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state),
    ),
  ),
);
const livePools = computed(() => liveProgress.value?.pools || []);
const liveRunning = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "running") || [],
);
const liveReady = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "ready") || [],
);

function onJobUpdate(job: JobSnapshot) {
  if (job.action === "run-test" && job.qa_progress) {
    jobProgress.value = job.qa_progress;
  }
}

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

function previewImgSrc(nameOrUrl: string) {
  if (!nameOrUrl) return "";
  if (nameOrUrl.startsWith("/") || nameOrUrl.startsWith("http")) {
    return nameOrUrl;
  }
  return assetUrl(nameOrUrl);
}

async function copy(text: string, msg = "已复制路径") {
  try {
    await navigator.clipboard.writeText(text);
    snack.notify(msg, "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

function confirmAction(action: string, ticketId?: string, act?: Action) {
  const meta = act || detail.value?.actions.find((a) => a.id === action);
  if (meta && !meta.enabled) {
    snack.notify(meta.reason || "当前不能执行", "info");
    return;
  }
  if (action === "fill-test-report") {
    reportForm.verdict = "failed";
    reportForm.summary = "";
    reportForm.body = "";
    resetFindingRows();
    reportForm.open = true;
    return;
  }
  const risky = action === "open" && forceOpen.value;
  const freeze = action === "freeze";
  const push = action === "push";
  const sync = action === "sync";
  const runTest = action === "run-test";
  if (risky || freeze || push || sync || runTest) {
    confirm.action = action;
    confirm.ticketId = ticketId || "";
    confirm.text = push
      ? `将把各个仓库的 worktree 分支 (req/${jira.value}) 推送到远端。确认继续？`
      : sync
      ? "将 fetch 远端，并把已冻结 worktree 更新到 origin/<default_base>（默认快进）。确认继续？"
      : freeze
      ? "冻结后会切 worktree。确认继续？"
      : runTest
      ? "将按 qa.yaml 设计并执行 UI 用例；失败会拆 B 票。确认继续？"
      : "强制重抽会重置阶段并删除截图。确认继续？";
    if (runTest) {
      const envs = qaEnvs.value;
      const preferred = detail.value?.qa?.active_env || "";
      confirm.env = envs.includes(preferred) ? preferred : envs[0] || "";
      confirm.resume = Boolean(incompleteRun.value);
    }
    confirm.open = true;
    return;
  }
  void onAction(action, ticketId);
}

function runConfirmed() {
  confirm.open = false;
  const env = confirm.action === "run-test" ? confirm.env : undefined;
  const resume = confirm.action === "run-test" ? confirm.resume : undefined;
  void onAction(confirm.action, confirm.ticketId || undefined, env, resume);
}

async function doDelete() {
  error.value = "";
  acting.value = "delete";
  try {
    await deleteRequirement(jira.value);
    forgetRecent(jira.value);
    deleteOpen.value = false;
    snack.notify(`已删除 ${jira.value}`, "success");
    await router.push("/");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

async function submitReport() {
  error.value = "";
  acting.value = "fill-test-report";
  try {
    let findings: {
      id: string;
      title: string;
      detail: string;
      repo: string;
      depends_on: string[];
    }[] = [];
    if (reportForm.verdict !== "passed") {
      const missing = reportForm.findings.find((row) => !row.repo.trim());
      if (missing) {
        snack.notify("每条缺陷必须选择仓库", "error");
        return;
      }
      for (const row of reportForm.findings) {
        findings.push({
          id: row.id,
          title: row.title,
          detail: row.detail,
          repo: row.repo,
          depends_on: row.dependsOn.trim() ? row.dependsOn.trim().split(/\s+/) : [],
        });
      }
    }
    await submitTestReport(jira.value, {
      verdict: reportForm.verdict,
      body: reportForm.body,
      summary: reportForm.summary,
      findings,
    });
    reportForm.open = false;
    snack.notify(
      reportForm.verdict === "passed" ? "本轮已通过" : "已拆成 bug 票",
      "success",
    );
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

async function onAction(action: string, ticketId?: string, env?: string, resume?: boolean) {
  error.value = "";
  acting.value = action;
  try {
    const out = await runAction(jira.value, action, {
      ticket_id: ticketId,
      force: action === "open" ? forceOpen.value : false,
      env: env || undefined,
      resume: action === "run-test" ? Boolean(resume) : undefined,
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
.ghx-header-top {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.ghx-board-name {
  font-size: 12px;
  color: #5e6c84;
  margin-bottom: 2px;
}
.ghx-sprint-title {
  font-size: 24px;
  font-weight: 500;
  line-height: 1.25;
  color: #172b4d;
  margin: 0;
}
.v-theme--dark .ghx-sprint-title {
  color: rgb(var(--v-theme-on-surface));
}
.ghx-lede {
  font-size: 13px;
  color: #5e6c84;
  margin-bottom: 12px !important;
}
.ghx-create-btn {
  text-transform: none !important;
  font-weight: 600;
  letter-spacing: 0;
}
.job-log {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  padding: 0.8rem 1rem;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
}
.cursor-pointer {
  cursor: pointer;
}
</style>
