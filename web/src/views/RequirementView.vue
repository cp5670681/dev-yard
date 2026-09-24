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
            :color="contractTone(detail.contract).color"
            variant="tonal"
            class="jira-lozenge font-weight-bold cursor-pointer"
            :prepend-icon="
              detail.contract === 'passed'
                ? mdiCheckCircleOutline
                : detail.contract === 'inconclusive'
                  ? mdiHelpCircleOutline
                  : mdiAlertCircleOutline
            "
            @click="contractDialog = true"
          >
            {{ contractTone(detail.contract).label }}
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
    <v-alert
      v-if="runEndBanner.show"
      :type="runEndBanner.color"
      variant="tonal"
      border="start"
      closable
      class="mb-4"
      @click:close="runEndBanner.show = false"
    >
      <div class="d-flex flex-wrap align-center ga-2">
        <span class="font-weight-medium">{{ runEndBanner.text }}</span>
        <router-link class="text-caption" :to="`/r/${jira}/qa`">打开测试页 ›</router-link>
      </div>
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

      <div
        class="d-flex flex-wrap align-center ga-2"
        :class="actionGroups.length > 1 ? 'mb-2' : 'mb-4'"
      >
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
        <v-tooltip
          v-for="a in activeActions"
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
      </div>

      <div v-if="actionGroups.length > 1" class="d-flex flex-wrap align-center ga-1 mb-4">
        <span class="text-caption text-medium-emphasis mr-1">阶段</span>
        <v-chip
          v-for="g in actionGroups"
          :key="g.stage"
          size="small"
          class="cursor-pointer"
          :variant="g.stage === currentStage ? 'flat' : 'tonal'"
          :color="g.stage === currentStage ? 'primary' : g.enabledCount ? undefined : 'grey'"
          @click="pickedStage = g.stage"
        >
          {{ g.label }}
          <span class="ml-1 text-caption font-weight-bold">{{ g.enabledCount }}</span>
        </v-chip>
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
        v-else-if="detail.contract === 'inconclusive'"
        type="warning"
        variant="tonal"
        border="start"
        class="mb-4"
        :icon="mdiHelpCircleOutline"
      >
        <div class="d-flex flex-column flex-sm-row justify-space-between align-sm-center ga-3">
          <div>
            <div class="text-subtitle-1 font-weight-bold">
              跨仓契约审查没有结论
            </div>
            <div class="text-body-2 text-medium-emphasis mt-0.5">
              这次没有交回审查结论，没有判定通过或失败，也不会派生修复票。可以重跑契约审查。
            </div>
          </div>
          <div class="d-flex flex-wrap align-center ga-2 flex-shrink-0">
            <v-btn
              v-if="detail.contract_summary"
              size="small"
              variant="outlined"
              color="warning"
              :prepend-icon="mdiEyeOutline"
              @click="contractDialog = true"
            >
              查看契约报告
            </v-btn>
            <v-btn
              size="small"
              color="warning"
              variant="flat"
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

      <!-- 设计中：用例还在生成/核实，先不亮审核门 -->
      <v-alert
        v-if="qaReviewPending && qaActive"
        type="info"
        variant="tonal"
        border="start"
        class="mb-4"
        :icon="mdiProgressClock"
      >
        <div class="d-flex flex-column flex-sm-row justify-space-between align-sm-center ga-3">
          <div>
            <div class="text-subtitle-1 font-weight-bold">用例处理中</div>
            <div class="text-body-2 text-medium-emphasis mt-0.5">
              用例生成 / 审核进行中，完成后这里会更新；此期间的审批会审到还在变动的用例。
            </div>
          </div>
          <v-btn size="small" variant="outlined" :to="`/r/${jira}/qa`" class="flex-shrink-0">
            查看进度
          </v-btn>
        </div>
      </v-alert>

      <!-- 用例审核门：通过后「执行用例」才会真正执行 -->
      <v-alert
        v-else-if="qaReviewPending"
        :type="qaReview?.stale || qaReview?.stale_reason || qaReview?.status === 'rejected' ? 'warning' : 'info'"
        variant="tonal"
        border="start"
        class="mb-4"
        :icon="mdiClipboardCheckOutline"
      >
        <div class="d-flex flex-column flex-sm-row justify-space-between align-sm-center ga-3">
          <div>
            <div class="text-subtitle-1 font-weight-bold">
              测试用例待审核（{{ qaReviewLabel }}）
            </div>
            <div class="text-body-2 text-medium-emphasis mt-0.5">
              <template v-if="qaReview?.stale_reason">
                需求已变更（{{ qaReview.stale_reason }}），请复核用例后重新审核。
              </template>
              <QaFeedbackDetails v-else-if="qaReview?.feedback" :review="qaReview" />
              <template v-else-if="qaReview?.stale">用例在通过之后又改动过，需要重新审核。</template>
              <template v-else>通过后「执行用例」才会开始执行；改预期等于洗白失败。</template>
            </div>
          </div>
          <div class="d-flex flex-wrap align-center ga-2 flex-shrink-0">
            <v-btn
              size="small"
              variant="outlined"
              :to="`/r/${jira}/qa`"
            >
              查看用例
            </v-btn>
            <v-btn
              size="small"
              color="success"
              variant="flat"
              :prepend-icon="mdiClipboardCheckOutline"
              :loading="acting === 'qa-review'"
              @click="openQaReview"
            >
              审核用例
            </v-btn>
          </div>
        </div>
      </v-alert>

      <v-card
        v-if="liveProgress && liveHasActive"
        class="mb-4"
        variant="tonal"
        color="warning"
      >
        <v-card-title class="text-subtitle-1">测试执行中</v-card-title>
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
        :rerunning-case="rerunningCase"
        @implement="(id) => confirmAction('implement', id)"
        @review="(id) => confirmAction('review', id)"
        @diff="(id) => openDiff(id)"
        @feedback="(ticket) => openReview(ticket)"
        @delete="(ticket) => confirmDeleteTicket(ticket)"
        @preview-screenshot="openPreview"
        @fill-bug="confirmAction('fill-test-report')"
        @open-case="openCaseDetail"
        @rerun-case="rerunCase"
        @rerun-cases="rerunCases"
        @file-bug="fileCaseBugAsTicket"
      />
      <v-card v-if="detail.changes?.length" variant="outlined" class="mt-4">
        <v-card-title class="text-subtitle-2">需求变更记录</v-card-title>
        <v-card-text>
          <div v-for="c in detail.changes" :key="c.id" class="mb-3">
            <div class="d-flex align-center ga-2 flex-wrap">
              <v-chip size="x-small" variant="tonal">{{ c.id }}</v-chip>
              <span class="text-caption text-medium-emphasis">{{ c.at }}</span>
              <v-chip v-if="c.ticket" size="x-small" color="primary" variant="tonal">
                {{ c.ticket }}
              </v-chip>
              <v-chip v-if="c.contract_touched" size="x-small" color="warning" variant="tonal">
                触及契约
              </v-chip>
              <v-chip v-if="c.stale?.qa" size="x-small" color="warning" variant="tonal">
                用例待复核
              </v-chip>
            </div>
            <div class="text-body-2 mt-1">{{ c.note }}</div>
          </div>
        </v-card-text>
      </v-card>
      <v-card variant="outlined" class="mt-4">
        <v-card-title class="text-subtitle-2 d-flex align-center ga-2">
          附件
          <v-spacer />
          <v-btn
            size="small"
            variant="tonal"
            color="primary"
            :prepend-icon="mdiPaperclip"
            :loading="uploading"
            @click="pickAttachments"
          >
            上传附件
          </v-btn>
        </v-card-title>
        <v-card-text>
          <input
            ref="attachInput"
            type="file"
            multiple
            class="d-none"
            @change="onAttachPicked"
          />
          <p class="text-caption text-medium-emphasis mb-2">
            人工上传（HTML 原型、文档等）存到
            <code>reqs/&lt;JIRA&gt;/uploads/</code>，重抽需求不会清空；文档里用
            <code>uploads/&lt;名字&gt;</code> 引用。
          </p>
          <v-list v-if="detail.uploads.length" density="compact">
            <v-list-item v-for="name in detail.uploads" :key="name">
              <v-list-item-title class="text-break font-weight-regular">
                <a :href="attachmentUrl(jira, name)" target="_blank" rel="noopener">{{ name }}</a>
              </v-list-item-title>
              <template #append>
                <v-btn
                  icon
                  size="x-small"
                  variant="text"
                  @click="copy(`uploads/${name}`, '已复制相对路径')"
                >
                  <v-icon :icon="mdiContentCopy" size="16" />
                </v-btn>
                <v-btn
                  icon
                  size="x-small"
                  variant="text"
                  color="error"
                  @click="removeAttachment(name)"
                >
                  <v-icon :icon="mdiDeleteOutline" size="16" />
                </v-btn>
              </template>
            </v-list-item>
          </v-list>
          <v-empty-state
            v-else
            title="暂无附件"
            text="点右上角上传，或在文档里用 uploads/<名字> 引用。"
          />
        </v-card-text>
      </v-card>
      <v-card variant="outlined" class="mt-4">
        <v-card-title class="text-subtitle-2 d-flex align-center ga-2">
          测试账号
          <v-spacer />
          <v-btn
            size="small"
            variant="tonal"
            color="primary"
            :prepend-icon="mdiAccountSearch"
            :loading="accountsBusy"
            :disabled="!reqAccounts?.discover_sql"
            @click="fillAccounts()"
          >
            发现并填充
          </v-btn>
          <v-btn
            size="small"
            variant="text"
            :prepend-icon="mdiRefresh"
            :loading="accountsBusy"
            @click="refreshAccounts()"
          >
            刷新登录态
          </v-btn>
        </v-card-title>
        <v-card-text>
          <p class="text-caption text-medium-emphasis mb-2">
            写入 <code>.yard-qa/requirements/{{ jira }}/accounts.yaml</code>（不动全局账号）。
            按 <code>qa/accounts-discover.sql</code> 的
            <code>username | account_key</code> 发现，用例 frontmatter 用
            <code>account: &lt;account_key&gt;</code> 引用；权限漂移时重跑即改绑。
          </p>
          <div v-if="reqAccounts?.accounts.length" class="d-flex flex-wrap ga-2">
            <v-chip
              v-for="a in reqAccounts.accounts"
              :key="a.name"
              size="small"
              :color="a.name === reqAccounts.default ? 'primary' : undefined"
              variant="tonal"
            >
              {{ a.name }} · {{ a.username }}
              <span v-if="a.name === reqAccounts.default" class="ml-1">default</span>
            </v-chip>
          </div>
          <p v-else class="text-caption text-medium-emphasis">未配置账号，用全局默认。</p>
          <p v-if="reqAccounts && !reqAccounts.discover_sql" class="text-caption text-warning mt-2">
            缺少 <code>qa/accounts-discover.sql</code>（qa-design 产出），暂无法自动发现。
          </p>
          <p v-if="accountsMsg" class="text-caption text-success mt-2">{{ accountsMsg }}</p>
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
                :color="contractTone(detail.contract).color"
                variant="tonal"
                class="font-weight-bold"
              >
                {{ contractTone(detail.contract).summary }}
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
              最近执行
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

    <v-dialog v-model="ticketDelete.open" max-width="480">
      <v-card>
        <v-card-title>删除 bug 票？</v-card-title>
        <v-card-text>
          将从 <code>TICKETS.md</code> 删除
          <strong>{{ ticketDelete.ticket?.id }}</strong>
          （{{ ticketDelete.ticket?.title || ticketDelete.ticket?.id }}）整块，并从状态里移除。
          仅未开工的 bug 票可删；此操作不可恢复。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="ticketDelete.open = false">取消</v-btn>
          <v-btn
            color="error"
            :loading="acting === `delete-ticket-${ticketDelete.ticket?.id}`"
            @click="doDeleteTicket"
          >
            删除
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="confirm.open" max-width="420">
      <v-card>
        <v-card-title>确认操作</v-card-title>
        <v-card-text>
          <p class="mb-2">{{ confirm.text }}</p>
          <v-select
            v-if="confirm.action === 'qa-run' && qaEnvs.length"
            v-model="confirm.env"
            :items="qaEnvs"
            label="环境（qa.yaml envs）"
            variant="outlined"
            density="comfortable"
            hide-details
            class="mb-2"
          />
          <v-checkbox
            v-if="confirm.action === 'qa-run' && incompleteRun"
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

    <ContractReviewDialog
      v-model="contractDialog"
      :jira="jira"
      :contract="detail?.contract || null"
      :summary="detail?.contract_summary || null"
      :summary-html="detail?.contract_summary_html"
      @reviewed="onContractReviewed"
    />

    <v-dialog v-model="changeOpen" max-width="560">
      <v-card>
        <v-card-title>轻量变更</v-card-title>
        <v-card-text>
          <p class="text-body-2 mb-3">
            只改描述 / 规则 / 验收等非契约内容；涉及接口契约请另开需求。
            流程：追加变更记录 →（可选）对齐 → 写规约 → 追加一张轻量票。
          </p>
          <v-textarea
            v-model="changeForm.note"
            label="变更说明"
            rows="3"
            auto-grow
            density="compact"
            variant="outlined"
            hide-details
          />
          <v-select
            v-model="changeForm.repo"
            :items="frozenRepos"
            label="受影响仓库（本需求已冻结的仓）"
            density="compact"
            variant="outlined"
            class="mt-3"
            hide-details
          />
          <v-checkbox
            v-model="changeForm.grill"
            density="compact"
            hide-details
            label="需要澄清（先跑一轮对齐）"
            class="mt-2"
          />
          <v-checkbox
            v-model="changeForm.run"
            density="compact"
            hide-details
            label="创建后立即实现这张票"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="changeOpen = false">取消</v-btn>
          <v-btn
            color="primary"
            :disabled="!changeForm.note.trim() || !changeForm.repo"
            @click="runChange"
          >
            开始
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="qaReviewOpen" max-width="560">
      <v-card>
        <v-card-title class="d-flex align-center ga-2">
          <v-icon :icon="mdiClipboardCheckOutline" size="20" />
          测试用例审核
          <v-chip size="small" variant="tonal">{{ qaReviewLabel }}</v-chip>
        </v-card-title>
        <v-card-text>
          <QaFeedbackDetails
            v-if="qaReview?.feedback"
            :review="qaReview"
            label="上一轮审核意见"
            class="mb-2"
          />
          <p v-else-if="qaReview?.stale" class="text-body-2 mb-2">
            用例在通过之后又改动过，需要重新审核。
          </p>
          <p v-else class="text-body-2 mb-2">通过后「执行用例」才会开始执行；改预期等于洗白失败。</p>
          <v-textarea
            v-model="qaReviewFeedback"
            label="打回意见（打回时必填；会交给 qa-design 重做用例）"
            rows="3"
            auto-grow
            density="compact"
            variant="outlined"
            hide-details
          />
          <p class="text-caption text-medium-emphasis mt-2 mb-0">
            想先看用例内容？<router-link :to="`/r/${jira}/qa`">打开测试页</router-link>
          </p>
        </v-card-text>
        <v-card-actions>
          <v-btn
            color="warning"
            variant="tonal"
            :disabled="!qaReviewFeedback.trim()"
            @click="rejectQa"
          >
            打回重做
          </v-btn>
          <v-spacer />
          <v-btn variant="text" @click="qaReviewOpen = false">取消</v-btn>
          <v-btn color="success" @click="approveQa">通过</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <ScreenshotViewer
      v-model="viewer.open"
      v-model:index="viewer.index"
      :images="viewer.images"
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
  mdiAccountSearch,
  mdiAlertCircleOutline,
  mdiCheckCircleOutline,
  mdiClipboardCheckOutline,
  mdiCloudUploadOutline,
  mdiContentCopy,
  mdiDeleteOutline,
  mdiEyeOutline,
  mdiFileDocumentAlertOutline,
  mdiFileDocumentCheckOutline,
  mdiHelpCircleOutline,
  mdiPaperclip,
  mdiProgressClock,
  mdiRefresh,
  mdiWrench,
} from "@mdi/js";
import {
  attachmentUrl,
  autoFillReqAccounts,
  deleteAttachment,
  deleteRequirement,
  deleteTicket,
  fileCaseBug,
  getReqAccounts,
  getRequirement,
  refreshReqAccounts,
  runAction,
  submitTestReport,
  uploadAttachments,
} from "@/api/client";
import type {
  Action,
  JobSnapshot,
  QaProgress,
  ReqAccounts,
  ReqDetail,
  ShotItem,
  Ticket,
} from "@/api/types";
import ContractReviewDialog from "@/components/ContractReviewDialog.vue";
import JobPanel from "@/components/JobPanel.vue";
import QaFeedbackDetails from "@/components/QaFeedbackDetails.vue";
import ReqDocTabs from "@/components/ReqDocTabs.vue";
import ScreenshotViewer from "@/components/ScreenshotViewer.vue";
import TicketBoard from "@/components/TicketBoard.vue";
import CaseDetailDialog from "@/components/CaseDetailDialog.vue";
import TicketDiffDialog from "@/components/TicketDiffDialog.vue";
import TicketReviewDialog from "@/components/TicketReviewDialog.vue";
import { runningJobs, watchJobs } from "@/state/jobs";
import {
  ACTION_LABELS,
  contractTone,
  phaseColor,
  STAGE_LABELS,
  STAGE_ORDER,
  STEP_LABELS,
} from "@/composables/labels";
import { isQaJobActive, QA_GATE_ACTIONS, useQaActive } from "@/composables/qa";
import { jobTail, submitRerun, tallyStatuses, tallyKind, BATCH_RERUN_CASE } from "@/composables/qaRerun";
import { useSnack } from "@/composables/snack";

const { mdAndUp } = useDisplay();
const snack = useSnack();
const route = useRoute();
const router = useRouter();
const jira = computed(() => String(route.params.jira || ""));
const detail = ref<ReqDetail | null>(null);
const error = ref("");
const acting = ref("");
const attachInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const reqAccounts = ref<ReqAccounts | null>(null);
const accountsBusy = ref(false);
const accountsMsg = ref("");
const viewer = reactive({ open: false, index: 0, images: [] as ShotItem[] });
const runEndBanner = reactive({
  show: false,
  text: "",
  color: "success" as "success" | "error" | "warning",
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
const qaReview = computed(() => detail.value?.qa?.review || null);
// While a design/run job is in flight the case set is still moving, so the
// review gate must stay closed: approving now would bless unseen cases.
const qaActive = useQaActive(
  jira,
  computed(() => detail.value?.qa?.active_jobs),
);
const qaReviewPending = computed(
  () => Boolean(detail.value?.qa?.has_cases) && !qaReview.value?.approved,
);
const qaReviewLabel = computed(() => {
  if (qaReview.value?.stale_reason) return "需求已变更";
  if (qaReview.value?.stale) return "需重审";
  if (qaReview.value?.status === "rejected") return "已打回";
  return "待审核";
});
const qaReviewOpen = ref(false);
const qaReviewFeedback = ref("");
const changeOpen = ref(false);
const changeForm = reactive({ note: "", repo: "", grill: false, run: false });
const frozenRepos = computed(() =>
  (detail.value?.worktrees || [])
    .map((p) => p.split("/").filter(Boolean).pop() || "")
    .filter(Boolean)
    .sort(),
);
const rerunningCase = ref("");
const deleteOpen = ref(false);
const ticketDelete = reactive({ open: false, ticket: null as Ticket | null });
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

async function fileCaseBugAsTicket(caseId: string) {
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
// Group every action by pipeline stage so the page shows one stage's buttons at
// a time instead of a flat wall. The primary next-action is rendered separately
// and stripped from its group's inline row to avoid showing it twice.
const actionGroups = computed(() => {
  const map = new Map<string, Action[]>();
  for (const a of detail.value?.actions || []) {
    const stage = a.stage || "utility";
    const bucket = map.get(stage);
    if (bucket) bucket.push(a);
    else map.set(stage, [a]);
  }
  const order = [...STAGE_ORDER];
  for (const stage of map.keys()) if (!order.includes(stage)) order.push(stage);
  return order
    .filter((stage) => map.has(stage))
    .map((stage) => {
      const all = map.get(stage) || [];
      return {
        stage,
        label: STAGE_LABELS[stage] || stage,
        actions: all.filter((a) => a.id !== nextAction.value?.id),
        enabledCount: all.filter((a) => a.enabled).length,
      };
    });
});

const pickedStage = ref("");
const currentStage = computed(() => {
  const stages = actionGroups.value.map((g) => g.stage);
  if (pickedStage.value && stages.includes(pickedStage.value)) return pickedStage.value;
  const next = nextAction.value;
  if (next?.stage && stages.includes(next.stage)) return next.stage;
  const firstEnabled = actionGroups.value.find((g) => g.enabledCount > 0);
  return firstEnabled?.stage || actionGroups.value[0]?.stage || "";
});
const activeActions = computed(
  () => actionGroups.value.find((g) => g.stage === currentStage.value)?.actions || [],
);

// A stage pick belongs to the requirement it was made on; clear it on navigation
// so the next requirement opens on its own current stage.
watch(jira, () => {
  pickedStage.value = "";
  rerunBannerCases = [];
  suppressRunBanner = false;
  lastBannerRunId = "";
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
  if (!QA_GATE_ACTIONS.has(job.action)) return;
  if (job.state === "ok" || job.state === "error" || job.state === "cancelled") {
    // Drop the last progress snapshot on terminal states — it can still show
    // active cases, which would pin liveHasActive (and the poll loop) forever.
    jobProgress.value = null;
    return;
  }
  if (job.qa_progress) jobProgress.value = job.qa_progress;
}

// While a run is in flight, poll so CLI-started runs (no web job) still update
// the board; when it settles, surface the run-end banner once.
let boardPoll: ReturnType<typeof setInterval> | undefined;
// Held only for the rerun that is on screen. A later run, or another
// requirement, must not keep painting that case.
let rerunBannerCases: string[] = [];
let suppressRunBanner = false;
let lastBannerRunId = "";

watch(
  liveHasActive,
  (now, was) => {
    if (boardPoll) {
      clearInterval(boardPoll);
      boardPoll = undefined;
    }
    if (now) {
      if (!rerunningCase.value) {
        rerunBannerCases = [];
        suppressRunBanner = false;
      }
      boardPoll = setInterval(() => void load(), 5000);
      return;
    }
    if (was) void load().then(showRunEndBanner);
  },
  { immediate: true },
);

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
  await loadReqAccounts();
}

async function loadReqAccounts() {
  try {
    reqAccounts.value = await getReqAccounts(jira.value);
  } catch {
    reqAccounts.value = null;
  }
}

async function fillAccounts() {
  accountsBusy.value = true;
  accountsMsg.value = "";
  try {
    const res = await autoFillReqAccounts(jira.value, false);
    reqAccounts.value = res;
    const bits: string[] = [];
    if (res.added?.length) bits.push(`新增 ${res.added.join("、")}`);
    if (res.changed?.length) bits.push(`改绑 ${res.changed.join("、")}`);
    accountsMsg.value = bits.join("；") || "账号已刷新";
    snack.notify(`已写入 ${res.accounts.length} 个账号`, "success");
  } catch (err) {
    snack.notify(err instanceof Error ? err.message : String(err), "error");
  } finally {
    accountsBusy.value = false;
  }
}

async function refreshAccounts() {
  accountsBusy.value = true;
  accountsMsg.value = "";
  try {
    const res = await refreshReqAccounts(jira.value);
    reqAccounts.value = res;
    accountsMsg.value = res.dropped?.length
      ? `已清缓存登录态：${res.dropped.join("、")}`
      : "无可清缓存";
    snack.notify("已刷新登录态", "success");
  } catch (err) {
    snack.notify(err instanceof Error ? err.message : String(err), "error");
  } finally {
    accountsBusy.value = false;
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

function openPreview(nameOrUrl: string) {
  if (!nameOrUrl) return;
  viewer.images = [{ url: previewImgSrc(nameOrUrl), caption: nameOrUrl }];
  viewer.index = 0;
  viewer.open = true;
}

async function copy(text: string, msg = "已复制路径") {
  try {
    await navigator.clipboard.writeText(text);
    snack.notify(msg, "success");
  } catch {
    snack.notify("复制失败", "error");
  }
}

function pickAttachments() {
  attachInput.value?.click();
}

async function onAttachPicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length) return;
  uploading.value = true;
  try {
    const res = await uploadAttachments(jira.value, files);
    snack.notify(`已上传 ${res.added.length} 个附件`, "success");
    await load();
  } catch (err) {
    snack.notify(err instanceof Error ? err.message : String(err), "error");
  } finally {
    uploading.value = false;
  }
}

async function removeAttachment(name: string) {
  try {
    await deleteAttachment(jira.value, name);
    snack.notify("已删除附件", "success");
    await load();
  } catch (err) {
    snack.notify(err instanceof Error ? err.message : String(err), "error");
  }
}

function confirmAction(action: string, ticketId?: string, act?: Action) {
  const meta = act || detail.value?.actions.find((a) => a.id === action);
  if (meta && !meta.enabled) {
    snack.notify(meta.reason || "当前不能执行", "info");
    return;
  }
  if (action === "change") {
    changeForm.note = "";
    changeForm.repo = frozenRepos.value[0] || "";
    changeForm.grill = false;
    changeForm.run = false;
    changeOpen.value = true;
    return;
  }
  if (action === "qa-review") {
    openQaReview();
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
  const resetPhase = action === "reset-phase";
  const resetGrill = action === "reset-grill";
  const freeze = action === "freeze";
  const push = action === "push";
  const pull = action === "pull";
  const sync = action === "sync";
  const runTest = action === "qa-run";
  const designTest = action === "qa-design";
  if (resetPhase || resetGrill || freeze || push || pull || sync || runTest || designTest) {
    confirm.action = action;
    confirm.ticketId = ticketId || "";
    const branch = detail.value?.branch || `req/${jira.value}`;
    confirm.text = push
      ? `将把各个仓库的 worktree 分支 (${branch}) 推送到远端。确认继续？`
      : pull
      ? `将 fetch 远端，并把已冻结的 worktree 快进到各自的远端分支 (${branch})。若本地与远端已分叉，默认快进会失败。确认继续？`
      : sync
      ? "将 fetch 远端，并把已冻结 worktree 更新到 origin/<default_base>（默认快进）。确认继续？"
      : freeze
      ? `冻结后会创建分支 ${branch} 并切 worktree。确认继续？`
      : designTest
      ? "将按 qa.yaml 设计 UI 用例并做数据核实；完成后停下等审核。确认继续？"
      : runTest
      ? "将执行已审核的 UI 用例；失败会拆 B 票。确认继续？"
      : resetGrill
      ? "将停止正在等待的对齐任务，丢弃当前待答轮次，并把 GRILL.md 清回空白：下次「对齐」从头生成问题。阶段/票/契约保留。确认继续？"
      : "将把需求重置回 open 阶段：拆掉 worktree 和本地分支，清空票/契约/测试状态。文档与截图保留。确认继续？";
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
  const env = confirm.action === "qa-run" ? confirm.env : undefined;
  const resume = confirm.action === "qa-run" ? confirm.resume : undefined;
  void onAction(confirm.action, confirm.ticketId || undefined, env, resume);
}

function runChange() {
  changeOpen.value = false;
  void onAction("change", undefined, undefined, undefined, {
    note: changeForm.note.trim(),
    repo: changeForm.repo,
    grill: changeForm.grill,
    run: changeForm.run,
  });
}

function openQaReview() {
  qaReviewFeedback.value = "";
  qaReviewOpen.value = true;
}

async function approveQa() {
  qaReviewOpen.value = false;
  await onAction("qa-review", undefined, undefined, undefined, { approve: true });
}

async function rejectQa() {
  const feedback = qaReviewFeedback.value.trim();
  if (!feedback) return;
  qaReviewOpen.value = false;
  await onAction("qa-review", undefined, undefined, undefined, {
    redesign: true,
    feedback,
  });
}

async function rerunCase(caseId: string) {
  await rerunCases([caseId]);
}

async function rerunCases(caseIds: string[]) {
  const ids = caseIds.map((c) => c.trim()).filter(Boolean);
  if (!ids.length || rerunningCase.value) return;
  error.value = "";
  rerunningCase.value = ids.length === 1 ? ids[0] : BATCH_RERUN_CASE;
  rerunBannerCases = ids;
  try {
    const { ids: done, label, job } = await submitRerun(jira.value, ids, (jobId) =>
      router.replace({ query: { ...route.query, job: jobId } }),
    );
    await load();
    if (job.state === "error" || job.state === "cancelled") {
      rerunBannerCases = [];
      suppressRunBanner = true;
      runEndBanner.show = false;
      const msg =
        jobTail(job.log) ||
        `${label} 重测${job.state === "cancelled" ? "已取消" : "失败"}`;
      error.value = msg;
      snack.notify(msg, "error");
      return;
    }
    snack.notify(runEndBanner.text, showRerunBanner(done));
  } catch (e) {
    rerunBannerCases = [];
    suppressRunBanner = true;
    error.value = e instanceof Error ? e.message : String(e);
    snack.notify(error.value, "error");
  } finally {
    rerunningCase.value = "";
  }
}

async function doDelete() {
  error.value = "";
  acting.value = "delete";
  try {
    await deleteRequirement(jira.value);
    deleteOpen.value = false;
    snack.notify(`已删除 ${jira.value}`, "success");
    await router.push("/");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    acting.value = "";
  }
}

function confirmDeleteTicket(ticket: Ticket) {
  ticketDelete.ticket = ticket;
  ticketDelete.open = true;
}

async function doDeleteTicket() {
  const t = ticketDelete.ticket;
  if (!t) return;
  error.value = "";
  acting.value = `delete-ticket-${t.id}`;
  try {
    await deleteTicket(jira.value, t.id);
    ticketDelete.open = false;
    ticketDelete.ticket = null;
    snack.notify(`已删除 ${t.id}`, "success");
    await load();
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

async function onAction(
  action: string,
  ticketId?: string,
  env?: string,
  resume?: boolean,
  extra?: {
    approve?: boolean;
    redesign?: boolean;
    feedback?: string;
    note?: string;
    repo?: string;
    grill?: boolean;
    run?: boolean;
  },
) {
  error.value = "";
  acting.value = action;
  if (action === "qa-run" || action === "qa-design" || action === "qa-review") {
    rerunBannerCases = [];
    suppressRunBanner = false;
  }
  try {
    const out = await runAction(jira.value, action, {
      ticket_id: ticketId,
      env: env || undefined,
      resume: action === "qa-run" ? Boolean(resume) : undefined,
      approve: extra?.approve,
      redesign: extra?.redesign,
      feedback: extra?.feedback,
      note: extra?.note,
      repo: extra?.repo,
      grill: extra?.grill,
      run: extra?.run,
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

function onJobDone(job?: JobSnapshot) {
  if (job && QA_GATE_ACTIONS.has(job.action)) jobProgress.value = null;
  void load().then(() => {
    // `qa-review` and `qa-design` never execute cases, so they must not
    // surface a run banner.
    if (job?.action === "qa-run") showRunEndBanner();
  });
}

// Paint the banner for the cases just re-run and return the matching snack kind.
// A batch whose cases never produced a verdict must not read as a clean pass.
function showRerunBanner(caseIds: string[]): "success" | "error" | "info" {
  const cases = detail.value?.qa?.latest_run?.cases;
  if (caseIds.length === 1) {
    const row = cases?.find((c) => c.case === caseIds[0]);
    const status = row?.status || "未知";
    const reason = row?.reason ? `：${row.reason}` : "";
    runEndBanner.text = `${caseIds[0]} 重测 ${status}${reason}`;
    runEndBanner.color =
      status === "passed" ? "success" : status === "failed" ? "error" : "warning";
    runEndBanner.show = true;
    return status === "passed" ? "success" : status === "failed" ? "error" : "info";
  }
  const tally = tallyStatuses(cases, caseIds);
  const text = [
    `重测 ${caseIds.length} 条：${tally.passed} 通过 / ${tally.failed} 失败 / ${tally.blocked} 阻塞`,
  ];
  if (tally.other) text.push(`${tally.other} 未出结果`);
  runEndBanner.text = text.join("，");
  runEndBanner.color =
    tally.failed > 0 ? "error" : tally.blocked > 0 || tally.other > 0 ? "warning" : "success";
  runEndBanner.show = true;
  return tallyKind(tally);
}

function showRunEndBanner() {
  if (suppressRunBanner) return;
  if (rerunBannerCases.length) {
    showRerunBanner(rerunBannerCases);
    return;
  }
  const run = detail.value?.qa?.latest_run;
  if (!run?.run_id || run.run_id === lastBannerRunId) return;
  lastBannerRunId = run.run_id;
  const s = run.summary || {};
  // No summary means no run actually produced results (e.g. the review gate
  // stopped it, or the run is still incomplete). Reporting "0 通过 / 0 失败 /
  // 0 阻塞" here reads as a clean pass when nothing was tested.
  if (!run.summary || typeof s.total !== "number") return;
  const failed = s.failed || 0;
  const blocked = s.blocked || 0;
  const bugs = detail.value?.tickets.filter((t) => t.source === "test").length || 0;
  const text = [`本轮 ${s.passed || 0} 通过 / ${failed} 失败 / ${blocked} 阻塞`];
  if (bugs > 0) text.push(`已拆 ${bugs} 张 B 票`);
  runEndBanner.text = text.join(" → ");
  runEndBanner.color = failed > 0 ? "error" : blocked > 0 ? "warning" : "success";
  runEndBanner.show = true;
  snack.notify(runEndBanner.text, failed > 0 ? "error" : "success");
}

onMounted(() => {
  stop = watchJobs();
  void load();
});
onUnmounted(() => {
  stop?.();
  if (boardPoll) clearInterval(boardPoll);
});
watch(jira, () => void load());
watch(runningJobs, (jobs, prev) => {
  // A finished QA job invalidates the payload's `active_jobs`: refetch so the
  // review gate (and the freshly designed cases) come back without a reload.
  if (isQaJobActive(prev || [], jira.value) && !isQaJobActive(jobs, jira.value)) {
    void load();
  } else if (!detail.value) {
    void load();
  }
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
