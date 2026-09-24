<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 mb-1" />
    <h1 class="text-h5 text-sm-h4 mb-1">测试</h1>
    <p class="text-medium-emphasis mb-4">
      用例正文只读。审核通过后才会开始执行；改预期等于洗白失败。
    </p>
    <v-alert v-if="error" type="error" class="mb-4" closable @click:close="error = ''">
      {{ error }}
    </v-alert>
    <v-alert
      v-if="phaseDrift"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      状态记录与证据不一致：state.yaml 记为 <code>{{ recordedPhase }}</code>，
      按证据应为 <code>{{ payload?.phase }}</code>（以证据为准，可重跑刷新）。
    </v-alert>

    <v-card
      v-if="triagePending.length || triageAuto.length"
      variant="tonal"
      :color="triagePending.length ? 'warning' : 'info'"
      class="mb-4"
    >
      <v-card-title class="d-flex align-center ga-2 py-2">
        <v-icon :icon="mdiHelpCircleOutline" size="20" />
        失败分流
      </v-card-title>
      <v-card-text class="text-body-2">
        <p v-if="triagePending.length" class="mb-1">
          <strong>待判定（疑似产品缺陷）：</strong>{{ triagePending.join("、") }}
          —— 在失败用例上「下 bug」，或「打回重做」修用例。
        </p>
        <p v-if="triageAuto.length" class="mb-0 text-medium-emphasis">
          将自动回流用例缺陷：{{ triageAuto.join("、") }}（下次设计时只改种子，不占人工判定）。
        </p>
      </v-card-text>
      <v-card-actions v-if="triagePending.length">
        <v-btn
          size="small"
          color="error"
          variant="tonal"
          :loading="triaging"
          :disabled="triaging"
          @click="fileProductBugs"
        >
          批量下 bug（仅 product 项）
        </v-btn>
      </v-card-actions>
    </v-card>
    <ReqDocTabs v-if="docs.length" :jira="jira" :docs="docs" current="qa" />
    <v-tabs v-else class="mb-4" show-arrows color="primary">
      <v-tab :to="`/r/${jira}`">看板</v-tab>
      <v-tab :to="`/r/${jira}/qa`">测试</v-tab>
    </v-tabs>

    <v-empty-state
      v-if="empty"
      title="还没有用例"
      text="提测后点「设计用例」，或 `dev-yard req test --design-only`。撞到缺 qa.yaml 就先配测试环境。"
    >
      <template #actions>
        <v-btn
          color="primary"
          variant="tonal"
          :prepend-icon="mdiClipboardCheckOutline"
          :loading="acting === 'design'"
          @click="designCases"
        >
          设计用例
        </v-btn>
        <v-btn to="/qa-config" variant="text">
          去配置测试环境
        </v-btn>
      </template>
    </v-empty-state>

    <template v-if="payload">
      <!-- Design in flight: cases are still moving, hold the review gate -->
      <v-card v-if="designRunning" variant="tonal" color="info" class="mb-4">
        <v-card-title class="d-flex align-center ga-2 py-2">
          <v-icon :icon="mdiClipboardCheckOutline" size="20" />
          用例处理中
          <v-progress-circular indeterminate size="16" width="2" />
        </v-card-title>
        <v-card-text class="text-body-2 text-medium-emphasis">
          用例生成 / 审核进行中，完成后这里会变成审核入口。此期间的审批会审到还在变动的用例。
        </v-card-text>
      </v-card>

      <!-- Human review gate: cases must be approved before running -->
      <v-card v-else-if="reviewPanel" variant="tonal" :color="reviewColor" class="mb-4">
        <v-card-title class="d-flex align-center ga-2 py-2">
          <v-icon :icon="mdiClipboardCheckOutline" size="20" />
          用例审核
          <v-chip size="small" variant="flat">{{ reviewLabel }}</v-chip>
        </v-card-title>
        <v-card-text>
          <p v-if="review?.stale && !review?.feedback" class="text-body-2 mb-2">
            用例在通过之后又改动过，需要重新审核。
          </p>
          <p v-else-if="!review?.feedback" class="text-body-2 mb-2 text-medium-emphasis">
            用例已生成，通过后才会开始执行。
          </p>

          <p v-if="verifyDetails.length" class="text-body-2 mb-1 text-warning">
            <strong>数据核实未通过（design-blocked，执行时会跳过）：</strong>
            共 {{ verifyDetails.length }} 条 —— {{ verifyCaseIds }}
          </p>
          <p v-if="verifyDetails.length" class="text-caption text-medium-emphasis mb-2">
            详见 <code>qa/design-verify/BLOCKED.md</code>，修好前置后打回重做。
          </p>
          <p v-else-if="verifyStale" class="text-body-2 mb-2 text-medium-emphasis">
            数据核实结果已过期（用例改动过），需重新核实后再审核。
          </p>

          <v-expansion-panels
            v-if="verifyDetails.length"
            variant="accordion"
            flat
            class="mb-2"
          >
            <v-expansion-panel v-for="d in verifyDetails" :key="d.case">
              <v-expansion-panel-title class="text-body-2">
                <code>{{ d.case }}</code>
                <span class="ml-2 text-medium-emphasis">{{ d.reason || `${d.rows ?? 0} rows` }}</span>
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <template v-if="d.verify_sql">
                  <div class="text-caption text-medium-emphasis">verify.sql</div>
                  <pre class="job-log">{{ d.verify_sql }}</pre>
                </template>
                <template v-if="d.lint?.detail">
                  <div class="text-caption text-medium-emphasis mt-2">lint</div>
                  <pre class="job-log">{{ d.lint.detail }}</pre>
                </template>
                <template v-if="d.error">
                  <div class="text-caption text-medium-emphasis mt-2">错误</div>
                  <pre class="job-log">{{ d.error }}</pre>
                </template>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <v-expansion-panels v-if="review?.feedback" variant="accordion" flat class="mb-2">
            <v-expansion-panel>
              <v-expansion-panel-title class="text-body-2">查看原始审核意见</v-expansion-panel-title>
              <v-expansion-panel-text>
                <div v-if="review.feedback_html" class="markdown" v-html="review.feedback_html" />
                <pre v-else class="job-log">{{ review.feedback }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <v-textarea
            v-model="feedbackText"
            label="审核意见（打回时必填；会交给 qa-design 重做用例）"
            rows="2"
            auto-grow
            density="compact"
            variant="outlined"
            hide-details
          />
        </v-card-text>
        <v-card-actions>
          <v-btn
            color="warning"
            variant="tonal"
            :loading="acting === 'reject'"
            :disabled="!feedbackText.trim()"
            @click="rejectCases"
          >
            打回重做
          </v-btn>
          <v-spacer />
          <v-btn
            color="success"
            :loading="acting === 'approve'"
            @click="approveCases"
          >
            通过审核
          </v-btn>
        </v-card-actions>
      </v-card>

      <!-- Approved: execution is a separate, explicit step -->
      <v-card
        v-else-if="hasCases && reviewApproved && !liveActive"
        variant="tonal"
        color="success"
        class="mb-4"
      >
        <v-card-text class="d-flex flex-wrap align-center ga-3">
          <v-icon :icon="mdiClipboardCheckOutline" size="20" />
          <span class="text-body-2">用例已审核通过，可以执行。</span>
          <v-spacer />
          <v-btn
            color="success"
            variant="flat"
            :loading="acting === 'run'"
            @click="executeCases"
          >
            执行用例
          </v-btn>
        </v-card-text>
      </v-card>

      <!-- Design-time ambiguities the agent could not resolve (qa/OPEN-QUESTIONS.md) -->
      <v-card v-if="openQuestions.exists" variant="tonal" color="warning" class="mb-4">
        <v-card-title class="d-flex align-center ga-2 py-2">
          <v-icon :icon="mdiHelpCircleOutline" size="20" />
          待澄清问题
          <v-chip size="small" variant="flat">{{ openQuestions.count }}</v-chip>
        </v-card-title>
        <v-card-text>
          <p v-if="openQuestions.error" class="text-body-2 mb-2">
            qa/OPEN-QUESTIONS.md 存在但读取失败，请人工查看。
          </p>
          <p v-else-if="openQuestions.count" class="text-body-2 mb-2">
            设计期发现的歧义（qa/OPEN-QUESTIONS.md）。回答后可「打回重做」并带上意见，让 qa-design 重新生成用例。
          </p>
          <p v-else class="text-body-2 mb-2">
            已产出空文件（qa/OPEN-QUESTIONS.md），设计期无疑义。
          </p>
          <pre v-if="openQuestions.body" class="text-body-2" style="white-space: pre-wrap">{{ openQuestions.body }}</pre>
        </v-card-text>
      </v-card>

      <!-- Run selector + summary chips -->
      <div v-if="runs.length" class="d-flex flex-wrap align-center ga-2 mb-3">
        <v-select
          v-model="selectedRunId"
          :items="runItems"
          label="执行轮次"
          density="compact"
          hide-details
          variant="outlined"
          class="run-select"
        />
        <template v-if="showSummary">
          <v-chip size="small" color="success" variant="tonal">
            通过 {{ summary.passed || 0 }}
          </v-chip>
          <v-chip size="small" color="error" variant="tonal">
            失败 {{ summary.failed || 0 }}
          </v-chip>
          <v-chip size="small" color="warning" variant="tonal">
            阻塞 {{ summary.blocked || 0 }}
          </v-chip>
          <v-chip v-if="summary.skipped" size="small" color="grey" variant="tonal">
            跳过 {{ summary.skipped }}
          </v-chip>
        </template>
        <v-chip v-else-if="liveActive" size="small" color="warning" variant="flat">
          <v-progress-circular indeterminate size="10" width="2" class="mr-1" />
          执行中
        </v-chip>
        <v-menu v-if="rerunnableCases.length" location="bottom end">
          <template #activator="{ props: menu }">
            <v-btn
              v-bind="menu"
              size="small"
              variant="tonal"
              color="warning"
              :prepend-icon="mdiRefresh"
              :append-icon="mdiMenuDown"
              :loading="rerunningCase === BATCH_RERUN_CASE"
              :disabled="rerunningCase !== ''"
              title="按池并发重测本轮用例"
            >
              重测 ({{ rerunnableCases.length }})
            </v-btn>
          </template>
          <v-list density="compact" min-width="200">
            <v-list-item
              v-if="blockedCases.length"
              :disabled="rerunningCase !== ''"
              @click="rerunCases(blockedCases.map((c) => c.case))"
            >
              <v-list-item-title>仅重测阻塞 ({{ blockedCases.length }})</v-list-item-title>
            </v-list-item>
            <v-list-item
              v-if="failedCases.length"
              :disabled="rerunningCase !== ''"
              @click="rerunCases(failedCases.map((c) => c.case))"
            >
              <v-list-item-title>仅重测失败 ({{ failedCases.length }})</v-list-item-title>
            </v-list-item>
            <v-list-item
              :disabled="rerunningCase !== ''"
              @click="rerunCases(rerunnableCases.map((c) => c.case))"
            >
              <v-list-item-title>失败 + 阻塞 ({{ rerunnableCases.length }})</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </div>

      <v-alert
        v-if="envFault"
        type="error"
        variant="tonal"
        class="mb-4"
        :title="`环境故障 ${envFault.class || ''}`"
      >
        {{ envFault.message }}
      </v-alert>

      <!-- Live banner (newest run in flight) -->
      <v-card v-if="liveActive" class="mb-4" variant="tonal" color="warning">
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

      <!-- New failures vs previous run -->
      <v-alert
        v-if="newFailures.length && !newFailuresDismissed"
        type="warning"
        variant="tonal"
        border="start"
        closable
        class="mb-4"
        @click:close="newFailuresDismissed = true"
      >
        <div class="d-flex flex-wrap align-center ga-1">
          <span class="font-weight-medium">较上轮新增失败 {{ newFailures.length }} 条：</span>
          <v-chip
            v-for="c in newFailures"
            :key="c.case"
            size="small"
            color="error"
            variant="tonal"
            class="cursor-pointer"
            @click="openCaseDetail(c.case)"
          >
            {{ c.case }}
          </v-chip>
        </div>
      </v-alert>

      <!-- Per-case table for selected run -->
      <v-card v-if="selectedRun" variant="outlined" class="mb-4">
        <v-card-title class="py-2 px-3">本轮执行（{{ selectedRun.cases.length }} 条）</v-card-title>
        <v-table v-if="selectedRun.cases.length" density="compact" hover>
          <thead>
            <tr>
              <th class="pr-1" style="width: 84px">状态</th>
              <th>用例</th>
              <th style="width: 72px">断言</th>
              <th style="width: 26%">失败原因</th>
              <th style="width: 130px">截图</th>
              <th style="width: 84px"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in selectedRun.cases"
              :key="c.case"
              class="cursor-pointer case-row"
              :title="`查看 ${c.case} 详情`"
              @click="openCaseDetail(c.case)"
            >
              <td class="pr-1">
                <v-progress-circular
                  v-if="isRunningCase(c)"
                  indeterminate
                  size="12"
                  width="2"
                  class="mr-1"
                />
                <v-chip size="x-small" :color="statusColor(c.status)" variant="tonal">
                  {{ c.status || "未跑" }}
                </v-chip>
              </td>
              <td>
                <v-tooltip location="top" max-width="420" :disabled="!c.title">
                  <template #activator="{ props: tip }">
                    <span v-bind="tip" class="text-truncate d-inline-block" style="max-width: 100%">
                      <code class="mr-1">{{ c.case }}</code>{{ c.title || "" }}
                    </span>
                  </template>
                  <span>{{ c.title || c.case }}</span>
                </v-tooltip>
                <span v-if="c.model" class="text-caption text-medium-emphasis ml-2 d-none d-md-inline">
                  {{ c.model }}
                </span>
              </td>
              <td>
                <v-chip
                  v-if="casePass(c).total"
                  size="x-small"
                  :color="casePass(c).passed === casePass(c).total ? 'success' : 'error'"
                  variant="tonal"
                >
                  {{ casePass(c).passed }}/{{ casePass(c).total }}
                </v-chip>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
              <td>
                <span
                  v-if="c.failure?.step_desc"
                  class="text-error text-caption reason-cell d-inline-block text-truncate"
                  :title="failTitle(c)"
                >
                  步骤 {{ c.failure.step || "?" }}: {{ c.failure.step_desc }}
                </span>
                <span v-else-if="c.reason" class="text-caption text-medium-emphasis reason-cell d-inline-block text-truncate" :title="c.reason">
                  {{ c.reason }}
                </span>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
              <td @click.stop>
                <div v-if="caseShots(c).length" class="d-flex ga-1 align-center">
                  <v-img
                    v-for="(shot, i) in caseShots(c).slice(0, 2)"
                    :key="shot.url"
                    :src="shot.url"
                    :alt="shot.caption || ''"
                    width="40"
                    height="28"
                    cover
                    class="rounded-sm cursor-pointer shot-thumb"
                    :title="shot.caption"
                    @click="openViewer(c, i)"
                  />
                  <span
                    v-if="caseShots(c).length > 2"
                    class="text-caption text-primary cursor-pointer flex-shrink-0"
                    @click="openViewer(c, 2)"
                  >
                    +{{ caseShots(c).length - 2 }}
                  </span>
                </div>
                <span v-else class="text-caption text-disabled">-</span>
              </td>
              <td @click.stop>
                <div class="d-flex ga-1">
                  <v-btn
                    v-if="canFileBug(c)"
                    size="x-small"
                    variant="tonal"
                    color="error"
                    :loading="acting === `file-bug-${c.case}`"
                    :disabled="acting !== ''"
                    :title="`把 ${c.case} 下成 bug 票`"
                    @click="fileBug(c.case)"
                  >
                    下 bug
                  </v-btn>
                  <v-btn
                    v-if="canRerun(c)"
                    size="x-small"
                    variant="tonal"
                    color="warning"
                    :loading="rerunningCase === c.case"
                    :disabled="rerunningCase !== ''"
                    :title="`重新执行 ${c.case}（不改动本轮其它用例）`"
                    @click="rerunCase(c.case)"
                  >
                    重测
                  </v-btn>
                </div>
              </td>
            </tr>
          </tbody>
        </v-table>
        <v-card-text v-else class="text-medium-emphasis text-body-2 py-6 text-center">
          本轮还没有执行记录。
        </v-card-text>
      </v-card>

      <!-- Collapsed sections: definitions / changes / run history -->
      <v-expansion-panels class="mb-4" variant="accordion">
        <v-expansion-panel v-if="payload.cases.length" title="用例定义">
          <v-expansion-panel-text>
            <v-list density="compact">
              <v-list-item
                v-for="c in payload.cases"
                :key="c.id"
                class="cursor-pointer"
                :title="`查看 ${c.id} 详情`"
                @click="openCaseDetail(c.id)"
              >
                <v-list-item-title>
                  <code>{{ c.id }}</code>
                  {{ c.title }}
                </v-list-item-title>
                <v-list-item-subtitle>
                  {{ c.priority || "-" }} · {{ c.repo || "-" }} · covers {{ (c.covers || []).join(", ") || "-" }}
                  · {{ latestStatus(c.id) }}
                </v-list-item-subtitle>
              </v-list-item>
            </v-list>
          </v-expansion-panel-text>
        </v-expansion-panel>

        <v-expansion-panel v-if="changes.length" title="改动点">
          <v-expansion-panel-text>
            <v-table density="compact">
              <thead>
                <tr>
                  <th>id</th>
                  <th>repo</th>
                  <th>ref</th>
                  <th>desc</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="ch in changes" :key="ch.id">
                  <td><code>{{ ch.id }}</code></td>
                  <td>{{ ch.repo }}</td>
                  <td class="text-break">{{ ch.ref }}</td>
                  <td>{{ ch.desc }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>

        <v-expansion-panel v-if="runs.length" title="历史轮次">
          <v-expansion-panel-text>
            <v-table density="compact">
              <thead>
                <tr>
                  <th>run</th>
                  <th>env</th>
                  <th>结果</th>
                  <th style="width: 80px"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="run in runs" :key="run.run_id">
                  <td>
                    <code>{{ run.run_id }}</code>
                    <v-chip v-if="run.status === 'unreadable'" size="x-small" color="error" variant="tonal" class="ml-1">
                      无法解析
                    </v-chip>
                    <v-chip v-if="selectedRunId === run.run_id" size="x-small" color="primary" variant="tonal" class="ml-1">
                      当前
                    </v-chip>
                  </td>
                  <td>{{ run.env || "-" }}</td>
                  <td class="text-caption">
                    passed {{ run.summary?.passed || 0 }} /
                    failed {{ run.summary?.failed || 0 }} /
                    blocked {{ run.summary?.blocked || 0 }} /
                    skipped {{ run.summary?.skipped || 0 }}
                  </td>
                  <td>
                    <v-btn
                      v-if="selectedRunId !== run.run_id"
                      size="x-small"
                      variant="tonal"
                      @click="selectRun(run.run_id)"
                    >
                      查看
                    </v-btn>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>

    <CaseDetailDialog v-model="caseDialog.open" :jira="jira" :case-id="caseDialog.caseId" />
    <ScreenshotViewer
      v-model="viewer.open"
      v-model:index="viewer.index"
      :images="viewer.images"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { mdiClipboardCheckOutline, mdiHelpCircleOutline, mdiRefresh, mdiMenuDown } from "@mdi/js";
import { getQa, getRequirement, runAction, fileCaseBug, triageQaCases } from "@/api/client";
import {
  jobTail,
  submitRerun,
  tallyStatuses,
  tallyMessage,
  tallyKind,
  findRunWithCases,
  BATCH_RERUN_CASE,
} from "@/composables/qaRerun";
import type { DocMeta, QaPage, QaReview, ShotItem } from "@/api/types";
import CaseDetailDialog from "@/components/CaseDetailDialog.vue";
import ReqDocTabs from "@/components/ReqDocTabs.vue";
import ScreenshotViewer from "@/components/ScreenshotViewer.vue";
import { assertionPassCount, caseShotItems, isQaJobActive, useQaActive } from "@/composables/qa";
import { useSnack } from "@/composables/snack";
import { runningJobs, watchJobs } from "@/state/jobs";

const route = useRoute();
const router = useRouter();
const snack = useSnack();
const jira = computed(() => String(route.params.jira || ""));
const payload = ref<QaPage | null>(null);
const docs = ref<DocMeta[]>([]);
const error = ref("");
const selectedRunId = ref("");
const newFailuresDismissed = ref(false);
const acting = ref("");
const feedbackText = ref("");
const rerunningCase = ref("");
const caseDialog = reactive({ open: false, caseId: "" });
const caseFromQuery = ref(false);
const viewer = reactive({ open: false, index: 0, images: [] as ShotItem[] });

const crumbs = computed(() => [
  { title: "需求", to: "/" },
  { title: jira.value, to: `/r/${jira.value}` },
  { title: "测试", disabled: true },
]);

const empty = computed(() => {
  if (!payload.value) return false;
  return !payload.value.cases.length && !payload.value.runs.length && !payload.value.meta;
});

const changes = computed(() => {
  const raw = payload.value?.meta?.changes;
  return Array.isArray(raw) ? raw : [];
});

const runs = computed(() => payload.value?.runs || []);

const review = computed<QaReview | null>(() => payload.value?.review || null);

const hasCases = computed(() => Boolean(payload.value?.cases.length));
const reviewApproved = computed(() => Boolean(review.value?.approved));

const openQuestions = computed(
  () => payload.value?.open_questions || { count: 0, body: "", exists: false },
);

const triagePending = computed(() => payload.value?.triage?.pending || []);
const triageAuto = computed(() => payload.value?.triage?.auto_recycled || []);
const phaseDrift = computed(() => Boolean(payload.value?.phase_drift));
const recordedPhase = computed(() => payload.value?.recorded_phase || "");
const triaging = ref(false);

// A design/run job still in flight keeps the case set in motion, so the review
// gate stays closed until it finishes.
const qaActive = useQaActive(
  jira,
  computed(() => payload.value?.active_jobs),
);

const reviewPanel = computed(
  () =>
    Boolean(payload.value?.cases.length) &&
    Boolean(review.value) &&
    !review.value?.approved &&
    !qaActive.value,
);

const designRunning = computed(
  () =>
    Boolean(payload.value?.cases.length) &&
    Boolean(review.value) &&
    !review.value?.approved &&
    qaActive.value,
);

const reviewLabel = computed(() => {
  if (review.value?.stale) return "需重审";
  if (review.value?.status === "rejected") return "已打回";
  return "待审核";
});

const reviewColor = computed(() =>
  review.value?.status === "rejected" || review.value?.stale ? "warning" : "info",
);

// Cases the host proved unverifiable (`qa/design-verify/`). They are skipped at
// run time, so the reviewer must see them here instead of approving blindly.
const verifyDetails = computed(() => {
  const verify = review.value?.verify;
  if (!verify?.present || verify.stale) return [];
  return verify.details ?? [];
});

const verifyCaseIds = computed(() => verifyDetails.value.map((d) => d.case).join("、"));

// Stale verify means the summary predates the current cases, so its verdict is
// no longer authoritative — surface that instead of silently hiding blockers.
const verifyStale = computed(
  () => Boolean(review.value?.verify?.present && review.value?.verify?.stale),
);

async function designCases() {
  acting.value = "design";
  error.value = "";
  try {
    await runAction(jira.value, "qa-design", {});
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

async function executeCases() {
  acting.value = "run";
  error.value = "";
  try {
    await runAction(jira.value, "qa-run", {});
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

async function approveCases() {
  acting.value = "approve";
  error.value = "";
  try {
    // Approval only marks the cases reviewed; running is a separate 执行用例.
    await runAction(jira.value, "qa-review", { approve: true });
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

async function rejectCases() {
  const feedback = feedbackText.value.trim();
  if (!feedback) return;
  acting.value = "reject";
  error.value = "";
  try {
    await runAction(jira.value, "qa-review", { redesign: true, feedback });
    feedbackText.value = "";
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

const selectedRun = computed(() => {
  const list = runs.value;
  return list.find((r) => r.run_id === selectedRunId.value) || list[0] || null;
});

// One-click batch: everything that did not pass the selected round. Only
// offered on the newest round, because `req_test` amends the newest run that
// contains the ids — batching an older round would re-run a different one.
const isNewestRun = computed(
  () => Boolean(selectedRun.value) && selectedRun.value === runs.value[0],
);
const failedCases = computed(() =>
  isNewestRun.value ? (selectedRun.value?.cases || []).filter((c) => c.status === "failed") : [],
);
const blockedCases = computed(() =>
  isNewestRun.value ? (selectedRun.value?.cases || []).filter((c) => c.status === "blocked") : [],
);
const rerunnableCases = computed(() => [...failedCases.value, ...blockedCases.value]);

const summary = computed(() => selectedRun.value?.summary || {});

const liveProgress = computed(() => selectedRun.value?.progress || null);

const envFault = computed(
  () => selectedRun.value?.env_fault || liveProgress.value?.env_fault || null,
);

const liveActive = computed(() => {
  if (!selectedRun.value || selectedRun.value !== runs.value[0]) return false;
  return Boolean(
    liveProgress.value?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state),
    ),
  );
});

const showSummary = computed(
  () => Boolean(summary.value && (summary.value.total || selectedRun.value?.cases.length)),
);

const livePools = computed(() => liveProgress.value?.pools || []);
const liveRunning = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "running") || [],
);
const liveReady = computed(
  () => liveProgress.value?.cases?.filter((c) => c.state === "ready") || [],
);

const runItems = computed(() =>
  runs.value.map((run) => ({
    title: `${run.run_id} · ${run.env || "-"} · ✓${run.summary?.passed || 0} ✗${run.summary?.failed || 0}${
      run.status === "unreadable" ? " · 无法解析" : ""
    }`,
    value: run.run_id,
  })),
);

const newFailures = computed(() => {
  const run = selectedRun.value;
  if (!run) return [];
  const idx = runs.value.findIndex((r) => r.run_id === run.run_id);
  const prev = idx >= 0 ? runs.value[idx + 1] : undefined;
  if (!prev) return [];
  return run.cases.filter((c) => {
    if (c.status !== "failed" && c.status !== "blocked") return false;
    const before = prev.cases.find((p) => p.case === c.case);
    return !before || (before.status !== "failed" && before.status !== "blocked");
  });
});

const hasActiveRun = computed(() =>
  Boolean(
    runs.value[0]?.progress?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state),
    ),
  ),
);

function isRunningCase(c: QaPage["runs"][number]["cases"][number]) {
  const live = liveProgress.value?.cases?.find((x) => x.id === c.case);
  return live?.state === "running";
}

function casePass(c: QaPage["runs"][number]["cases"][number]) {
  return assertionPassCount(c.assertions);
}

function failTitle(c: QaPage["runs"][number]["cases"][number]): string {
  return [c.failure?.step_desc, c.reason].filter(Boolean).join("\n");
}

function caseShots(c: QaPage["runs"][number]["cases"][number]): ShotItem[] {
  const runId = selectedRun.value?.run_id || "";
  if (!runId) return [];
  return caseShotItems(jira.value, runId, c.case, c.screenshots || [], c.failure?.evidence);
}

function openViewer(c: QaPage["runs"][number]["cases"][number], index: number) {
  viewer.images = caseShots(c);
  viewer.index = index;
  viewer.open = true;
}

function openCaseDetail(caseId: string, fromQuery = false) {
  if (!caseId) return;
  caseDialog.caseId = caseId;
  caseDialog.open = true;
  caseFromQuery.value = fromQuery;
}

function selectRun(runId: string) {
  selectedRunId.value = runId;
  newFailuresDismissed.value = false;
  if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
}

function statusColor(status: string) {
  if (status === "passed") return "success";
  if (status === "failed") return "error";
  if (status === "blocked") return "warning";
  if (status === "skipped") return "grey";
  return "info";
}

function canRerun(c: { status?: string }) {
  return c.status === "failed" || c.status === "blocked" || c.status === "passed";
}

function canFileBug(c: { status?: string }) {
  // 下 bug resolves the newest run server-side, so only offer it on the newest
  // round — filing from an older round would cite the wrong result.
  return isNewestRun.value && (c.status === "failed" || c.status === "blocked");
}

async function fileBug(caseId: string) {
  if (!caseId || acting.value) return;
  error.value = "";
  acting.value = `file-bug-${caseId}`;
  try {
    const out = await fileCaseBug(jira.value, caseId);
    snack.notify(`已下 bug 票 ${out.ticket_id}（${out.repo}）`, "success");
    await load();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    error.value = msg;
    snack.notify(msg, "error");
  } finally {
    acting.value = "";
  }
}

async function fileProductBugs() {
  if (!triagePending.value.length || triaging.value) return;
  error.value = "";
  triaging.value = true;
  try {
    const out = await triageQaCases(jira.value, true);
    const n = Object.keys(out.filed || {}).length;
    snack.notify(
      n ? `已下 ${n} 张 bug 票` : "没有可自动建票的 product 项（未分类请逐条下 bug）",
      n ? "success" : "info",
    );
    await load();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    error.value = msg;
    snack.notify(msg, "error");
  } finally {
    triaging.value = false;
  }
}

async function rerunCase(caseId: string) {
  await rerunCases([caseId]);
}

async function rerunCases(caseIds: string[]) {
  const ids = caseIds.map((c) => c.trim()).filter(Boolean);
  if (!ids.length || rerunningCase.value) return;
  error.value = "";
  rerunningCase.value = ids.length === 1 ? ids[0] : BATCH_RERUN_CASE;
  try {
    const { ids: done, label, job } = await submitRerun(jira.value, ids);
    await load();
    if (job.state === "error" || job.state === "cancelled") {
      const msg =
        jobTail(job.log) ||
        `${label} 重测${job.state === "cancelled" ? "已取消" : "失败"}`;
      error.value = msg;
      snack.notify(msg, "error");
      return;
    }
    const run = findRunWithCases(payload.value?.runs, done);
    const tally = tallyStatuses(run?.cases, done);
    snack.notify(tallyMessage(done, tally), tallyKind(tally));
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    snack.notify(error.value, "error");
  } finally {
    rerunningCase.value = "";
  }
}

function latestStatus(id: string) {
  const run = runs.value[0];
  if (!run) return "未跑";
  const live = run.progress?.cases?.find((c) => c.id === id);
  if (live?.state === "running") return `${live.model || ""} 在跑`.trim();
  const row = run.cases.find((c) => c.case === id);
  return row?.status || "未跑";
}

async function load() {
  error.value = "";
  try {
    const [qa, detail] = await Promise.all([getQa(jira.value), getRequirement(jira.value)]);
    payload.value = qa;
    docs.value = detail.docs || [];
    const ids = qa.runs.map((r) => r.run_id);
    if (!ids.includes(selectedRunId.value)) {
      selectedRunId.value = ids[0] || "";
      newFailuresDismissed.value = false;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

// Deep link: /r/:jira/qa?case=case-XX opens the dialog once.
watch(
  () => route.query.case,
  (value) => {
    const caseId = String(value || "");
    if (caseId) openCaseDetail(caseId, true);
  },
  { immediate: true },
);

watch(
  () => caseDialog.open,
  (open) => {
    if (open || !caseFromQuery.value) return;
    caseFromQuery.value = false;
    const query = { ...route.query };
    delete query.case;
    void router.replace({ query });
  },
);

// Auto-refresh: SSE for web jobs, interval for CLI-started runs.
let stopJobs: (() => void) | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;

// Reload when a QA job starts or finishes: the payload's `active_jobs` and the
// freshly designed cases must not go stale (which would pin the design hint).
watch(runningJobs, (jobs, prev) => {
  const had = isQaJobActive(prev || [], jira.value);
  const has = isQaJobActive(jobs, jira.value);
  if (has !== had) void load();
});

watch(
  hasActiveRun,
  (active) => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
    if (active) pollTimer = setInterval(() => void load(), 5000);
  },
  { immediate: true },
);

onMounted(() => {
  stopJobs = watchJobs();
  void load();
});
onUnmounted(() => {
  stopJobs?.();
  if (pollTimer) clearInterval(pollTimer);
});
watch(jira, load);
</script>

<style scoped>
.run-select {
  max-width: 460px;
  min-width: 280px;
}
.case-row:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}
.reason-cell {
  max-width: 100%;
  vertical-align: middle;
}
.shot-thumb {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.cursor-pointer {
  cursor: pointer;
}
.job-log {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  padding: 0.8rem 1rem;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
  margin: 0;
}
</style>
