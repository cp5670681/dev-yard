<template>
  <div class="boards-container">
    <!-- ========================================== -->
    <!-- 1. 需求开发任务 (Dev / Requirement Tickets) -->
    <!-- ========================================== -->
    <div class="board-section mb-5">
      <div class="board-section-header cursor-pointer select-none" @click="toggleDev()">
        <div class="d-flex align-center ga-2 flex-wrap">
          <v-icon
            :icon="isDevExpanded ? mdiChevronDown : mdiChevronRight"
            size="20"
            class="section-chevron"
          />
          <v-icon :icon="mdiCodeBraces" size="20" color="primary" />
          <span class="section-title">需求开发任务</span>
          <v-chip size="x-small" variant="flat" color="primary" class="font-weight-bold">
            {{ devTickets.length }} 张票
          </v-chip>
          <span v-if="devStats.done > 0" class="text-caption text-success font-weight-medium">
            {{ devStats.done }} 已完成
          </span>
          <span v-if="devStats.active > 0" class="text-caption text-medium-emphasis">
            · {{ devStats.active }} 进行中
          </span>
          <span v-if="devStats.blocked > 0" class="text-caption text-error font-weight-medium">
            · {{ devStats.blocked }} 阻塞
          </span>
        </div>
        <div class="d-flex align-center ga-2">
          <v-btn
            variant="text"
            size="x-small"
            class="text-medium-emphasis"
            :prepend-icon="isDevExpanded ? mdiChevronUp : mdiChevronDown"
            @click.stop="toggleDev()"
          >
            {{ isDevExpanded ? '收起' : '展开' }}
          </v-btn>
        </div>
      </div>

      <v-expand-transition>
        <div v-show="isDevExpanded" class="board-section-body">
          <template v-if="devTickets.length">
            <!-- Desktop Kanban -->
            <template v-if="mdAndUp">
              <div class="ghx-board">
                <div class="ghx-column-headers">
                  <div v-for="col in columns" :key="col" class="ghx-column-header">
                    <span class="ghx-column-title">{{ stateLabel(col) }}</span>
                    <span class="ghx-column-count">{{ (devByState[col] || []).length }}</span>
                  </div>
                </div>
                <div class="ghx-columns">
                  <div v-for="col in columns" :key="col" class="ghx-column">
                    <TicketCard
                      v-for="t in devByState[col] || []"
                      :key="t.id"
                      class="ghx-card-gap"
                      :ticket="t"
                      :jira="jira"
                      @implement="$emit('implement', $event)"
                      @review="$emit('review', $event)"
                      @diff="$emit('diff', $event)"
                      @feedback="$emit('feedback', $event)"
                      @open-case="$emit('open-case', $event)"
                    />
                  </div>
                </div>
              </div>
            </template>
            <!-- Mobile Slide Group -->
            <template v-else>
              <v-slide-group v-model="devFilter" mandatory show-arrows class="mb-3">
                <v-slide-group-item
                  v-for="opt in devFilterOptions"
                  :key="opt.id"
                  :value="opt.id"
                  v-slot="{ isSelected, toggle }"
                >
                  <v-chip
                    class="ma-1"
                    :color="isSelected ? 'primary' : undefined"
                    :variant="isSelected ? 'flat' : 'outlined'"
                    @click="toggle"
                  >
                    {{ opt.label }} {{ opt.count }}
                  </v-chip>
                </v-slide-group-item>
              </v-slide-group>
              <TicketCard
                v-for="t in filteredDevTickets"
                :key="t.id"
                class="mb-3"
                :ticket="t"
                :jira="jira"
                show-state
                @implement="$emit('implement', $event)"
                @review="$emit('review', $event)"
                @diff="$emit('diff', $event)"
                @feedback="$emit('feedback', $event)"
                @open-case="$emit('open-case', $event)"
              />
              <div v-if="!filteredDevTickets.length" class="text-center text-medium-emphasis py-6 text-caption">
                这一栏没有票
              </div>
            </template>
          </template>
          <div v-else class="empty-section-placeholder text-center py-6 text-medium-emphasis">
            <v-icon :icon="mdiCodeBraces" size="28" class="mb-1 text-disabled" />
            <div class="text-caption">暂无需求开发任务 (T票拆解后将在此显示)</div>
          </div>
        </div>
      </v-expand-transition>
    </div>

    <!-- ========================================== -->
    <!-- 2. 自动化测试 (Automated QA / Test Tickets) -->
    <!-- ========================================== -->
    <div class="board-section mb-5">
      <div class="board-section-header cursor-pointer select-none" @click="toggleQa()">
        <div class="d-flex align-center ga-2 flex-wrap">
          <v-icon
            :icon="isQaExpanded ? mdiChevronDown : mdiChevronRight"
            size="20"
            class="section-chevron"
          />
          <v-icon :icon="mdiFlaskOutline" size="20" color="info" />
          <span class="section-title">自动化测试</span>
          <v-chip size="x-small" variant="flat" color="info" class="font-weight-bold">
            {{ allQaCases.length }} 个用例
          </v-chip>

          <!-- Live Running Chip -->
          <v-chip
            v-if="qaLiveHasActive"
            size="x-small"
            color="warning"
            variant="flat"
            class="font-weight-bold pulse-chip"
          >
            <v-progress-circular indeterminate size="10" width="2" color="white" class="mr-1" />
            正在测试 ({{ qaLiveRunning.length }}/{{ allQaCases.length }})
          </v-chip>

          <span v-if="qaStats.passed > 0" class="text-caption text-success font-weight-medium">
            {{ qaStats.passed }} 通过
          </span>
          <span v-if="qaStats.failed > 0" class="text-caption text-error font-weight-medium">
            · {{ qaStats.failed }} 失败
          </span>
          <span v-if="qaStats.blocked > 0" class="text-caption text-warning font-weight-medium">
            · {{ qaStats.blocked }} 阻塞
          </span>
          <span v-if="qaStats.running > 0 && !qaLiveHasActive" class="text-caption text-primary font-weight-medium">
            · {{ qaStats.running }} 运行中
          </span>
          <span v-if="qaStats.ready > 0" class="text-caption text-medium-emphasis">
            · {{ qaStats.ready }} 就绪
          </span>
        </div>

        <div class="d-flex align-center ga-2">
          <v-btn
            v-if="allQaCases.length || jira"
            variant="text"
            size="x-small"
            color="primary"
            :to="`/r/${jira}/qa`"
            class="text-caption"
            @click.stop
          >
            测试详情页 ›
          </v-btn>
          <v-btn
            variant="text"
            size="x-small"
            class="text-medium-emphasis"
            :prepend-icon="isQaExpanded ? mdiChevronUp : mdiChevronDown"
            @click.stop="toggleQa()"
          >
            {{ isQaExpanded ? '收起' : '展开' }}
          </v-btn>
        </div>
      </div>

      <v-expand-transition>
        <div v-show="isQaExpanded" class="board-section-body">
          <template v-if="allQaCases.length">
            <!-- Desktop QA Kanban -->
            <template v-if="mdAndUp">
              <div class="ghx-board ghx-board--qa">
                <div class="ghx-column-headers ghx-qa-headers">
                  <div v-for="col in qaColumns" :key="col" class="ghx-column-header">
                    <span class="ghx-column-title">{{ qaStateLabel(col) }}</span>
                    <span class="ghx-column-count">{{ (qaByState[col] || []).length }}</span>
                  </div>
                </div>
                <div class="ghx-columns ghx-qa-columns">
                  <div v-for="col in qaColumns" :key="col" class="ghx-column">
                    <QaTestCard
                      v-for="c in qaByState[col] || []"
                      :key="c.id"
                      class="ghx-card-gap"
                      :test-case="c"
                      :jira="jira"
                      :rerunning="rerunningCase === c.id"
                      :disabled="rerunningCase !== ''"
                      @preview-screenshot="$emit('preview-screenshot', $event)"
                      @open-case="$emit('open-case', $event)"
                      @rerun-case="$emit('rerun-case', $event)"
                    />
                  </div>
                </div>
              </div>
            </template>
            <!-- Mobile QA Slide Group -->
            <template v-else>
              <v-slide-group v-model="qaFilter" mandatory show-arrows class="mb-3">
                <v-slide-group-item
                  v-for="opt in qaFilterOptions"
                  :key="opt.id"
                  :value="opt.id"
                  v-slot="{ isSelected, toggle }"
                >
                  <v-chip
                    class="ma-1"
                    :color="isSelected ? 'info' : undefined"
                    :variant="isSelected ? 'flat' : 'outlined'"
                    @click="toggle"
                  >
                    {{ opt.label }} {{ opt.count }}
                  </v-chip>
                </v-slide-group-item>
              </v-slide-group>
              <QaTestCard
                v-for="c in filteredQaCases"
                :key="c.id"
                class="mb-3"
                :test-case="c"
                :jira="jira"
                show-state
                :rerunning="rerunningCase === c.id"
                :disabled="rerunningCase !== ''"
                @preview-screenshot="$emit('preview-screenshot', $event)"
                @open-case="$emit('open-case', $event)"
                @rerun-case="$emit('rerun-case', $event)"
              />
              <div v-if="!filteredQaCases.length" class="text-center text-medium-emphasis py-6 text-caption">
                这一栏没有测试用例
              </div>
            </template>
          </template>
          <div v-else class="empty-section-placeholder text-center py-6 text-medium-emphasis">
            <v-icon :icon="mdiFlaskOutline" size="28" class="mb-1 text-disabled" />
            <div class="text-caption">尚未生成测试用例。提测后点击「自动测」将自动设计并执行测试票。</div>
          </div>
        </div>
      </v-expand-transition>
    </div>

    <!-- ========================================== -->
    <!-- 3. 缺陷与 Bug 修复 (Bug Fix Tickets - B1..Bn) -->
    <!-- ========================================== -->
    <div class="board-section mb-2">
      <div class="board-section-header cursor-pointer select-none" @click="toggleBug()">
        <div class="d-flex align-center ga-2 flex-wrap">
          <v-icon
            :icon="isBugExpanded ? mdiChevronDown : mdiChevronRight"
            size="20"
            class="section-chevron"
          />
          <v-icon :icon="mdiBugOutline" size="20" color="error" />
          <span class="section-title">缺陷修复 (Bug 票)</span>
          <v-chip
            size="x-small"
            variant="flat"
            :color="bugTickets.length > 0 ? 'error' : 'grey'"
            class="font-weight-bold"
          >
            {{ bugTickets.length }} 个 Bug
          </v-chip>
          <span v-if="bugStats.done > 0" class="text-caption text-success font-weight-medium">
            {{ bugStats.done }} 已解决
          </span>
          <span v-if="bugStats.active > 0" class="text-caption text-error font-weight-medium">
            · {{ bugStats.active }} 待修复
          </span>
          <span v-if="bugStats.blocked > 0" class="text-caption text-warning font-weight-medium">
            · {{ bugStats.blocked }} 阻塞
          </span>
        </div>

        <div class="d-flex align-center ga-2">
          <v-btn
            variant="tonal"
            size="x-small"
            color="error"
            :prepend-icon="mdiPlus"
            class="text-caption font-weight-medium"
            @click.stop="$emit('fill-bug')"
          >
            提 Bug
          </v-btn>
          <v-btn
            variant="text"
            size="x-small"
            class="text-medium-emphasis"
            :prepend-icon="isBugExpanded ? mdiChevronUp : mdiChevronDown"
            @click.stop="toggleBug()"
          >
            {{ isBugExpanded ? '收起' : '展开' }}
          </v-btn>
        </div>
      </div>

      <v-expand-transition>
        <div v-show="isBugExpanded" class="board-section-body">
          <template v-if="bugTickets.length">
            <!-- Desktop Bug Kanban -->
            <template v-if="mdAndUp">
              <div class="ghx-board ghx-board--bug">
                <div class="ghx-column-headers">
                  <div v-for="col in columns" :key="col" class="ghx-column-header">
                    <span class="ghx-column-title">{{ stateLabel(col) }}</span>
                    <span class="ghx-column-count">{{ (bugByState[col] || []).length }}</span>
                  </div>
                </div>
                <div class="ghx-columns">
                  <div v-for="col in columns" :key="col" class="ghx-column">
                    <TicketCard
                      v-for="t in bugByState[col] || []"
                      :key="t.id"
                      class="ghx-card-gap"
                      :ticket="t"
                      :jira="jira"
                      :case-info="bugCaseInfo(t)"
                      @implement="$emit('implement', $event)"
                      @review="$emit('review', $event)"
                      @diff="$emit('diff', $event)"
                      @feedback="$emit('feedback', $event)"
                      @delete="$emit('delete', $event)"
                      @open-case="$emit('open-case', $event)"
                      @preview-screenshot="$emit('preview-screenshot', $event)"
                    />
                  </div>
                </div>
              </div>
            </template>
            <!-- Mobile Bug Slide Group -->
            <template v-else>
              <v-slide-group v-model="bugFilter" mandatory show-arrows class="mb-3">
                <v-slide-group-item
                  v-for="opt in bugFilterOptions"
                  :key="opt.id"
                  :value="opt.id"
                  v-slot="{ isSelected, toggle }"
                >
                  <v-chip
                    class="ma-1"
                    :color="isSelected ? 'error' : undefined"
                    :variant="isSelected ? 'flat' : 'outlined'"
                    @click="toggle"
                  >
                    {{ opt.label }} {{ opt.count }}
                  </v-chip>
                </v-slide-group-item>
              </v-slide-group>
              <TicketCard
                v-for="t in filteredBugTickets"
                :key="t.id"
                class="mb-3"
                :ticket="t"
                :jira="jira"
                :case-info="bugCaseInfo(t)"
                show-state
                @implement="$emit('implement', $event)"
                @review="$emit('review', $event)"
                @diff="$emit('diff', $event)"
                @feedback="$emit('feedback', $event)"
                @delete="$emit('delete', $event)"
                @open-case="$emit('open-case', $event)"
                @preview-screenshot="$emit('preview-screenshot', $event)"
              />
              <div v-if="!filteredBugTickets.length" class="text-center text-medium-emphasis py-6 text-caption">
                这一栏没有 Bug 票
              </div>
            </template>
          </template>
          <div v-else class="empty-section-placeholder text-center py-6 text-medium-emphasis">
            <v-icon :icon="mdiBugCheckOutline" size="28" class="mb-1 text-success" />
            <div class="text-caption">当前没有发现 Bug 或缺陷。提测或契约审查发现问题时将在此自动拆票。</div>
          </div>
        </div>
      </v-expand-transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useDisplay } from "vuetify";
import {
  mdiChevronDown,
  mdiChevronRight,
  mdiChevronUp,
  mdiCodeBraces,
  mdiFlaskOutline,
  mdiBugOutline,
  mdiBugCheckOutline,
  mdiPlus,
} from "@mdi/js";
import type { Ticket, QaCaseItem, QaProgress } from "@/api/types";
import TicketCard from "./TicketCard.vue";
import QaTestCard from "./QaTestCard.vue";
import { TICKET_STATE_LABELS, QA_STATE_LABELS } from "@/composables/labels";
import { findingCaseId } from "@/composables/qa";

const props = withDefaults(
  defineProps<{
    tickets: Ticket[];
    qaCases?: QaCaseItem[];
    qaProgress?: QaProgress | null;
    jira: string;
    phase?: string;
    rerunningCase?: string;
  }>(),
  {
    qaCases: () => [],
    qaProgress: null,
    phase: "open",
    rerunningCase: "",
  }
);

defineEmits<{
  implement: [id: string];
  review: [id: string];
  diff: [id: string];
  feedback: [ticket: Ticket];
  delete: [ticket: Ticket];
  "preview-screenshot": [url: string];
  "fill-bug": [];
  "open-case": [caseId: string];
  "rerun-case": [caseId: string];
}>();

const { mdAndUp } = useDisplay();

// Collapsible states. Defaults follow phase/live state; once the user touches
// a section we stop overriding it (no fighting manual toggles on every refresh).
const isDevExpanded = ref(true);
const isQaExpanded = ref(true);
const isBugExpanded = ref(true);
const touched = { dev: false, qa: false, bug: false };

function toggleDev() {
  touched.dev = true;
  isDevExpanded.value = !isDevExpanded.value;
}
function toggleQa() {
  touched.qa = true;
  isQaExpanded.value = !isQaExpanded.value;
}
function toggleBug() {
  touched.bug = true;
  isBugExpanded.value = !isBugExpanded.value;
}

const qaLiveActive = computed(
  () =>
    props.qaProgress?.cases?.some((c) => ["pending", "ready", "running"].includes(c.state)) ??
    false,
);

const qaHasTerminal = computed(() =>
  allQaCases.value.some((c) =>
    ["passed", "failed", "blocked", "skipped"].includes(c.state),
  ),
);

function applyDefaults() {
  if (!touched.dev) isDevExpanded.value = props.phase !== "testing";
  if (!touched.qa) {
    isQaExpanded.value =
      props.phase === "testing" || qaLiveActive.value || qaHasTerminal.value;
  }
  if (!touched.bug) isBugExpanded.value = bugTickets.value.length > 0;
}

onMounted(applyDefaults);
watch(
  () => props.phase,
  () => {
    touched.dev = false;
    touched.qa = false;
    touched.bug = false;
    applyDefaults();
  },
);

// Standard Ticket Columns (7 columns)
const columns = [
  "pending",
  "ready",
  "implementing",
  "implemented",
  "reviewing",
  "blocked",
  "done",
];

// QA Test Columns (6 columns)
const qaColumns = [
  "pending",
  "ready",
  "running",
  "passed",
  "failed",
  "blocked",
];

// -------------------------------------------------------------
// 1. Dev Tickets (T1..Tn)
// -------------------------------------------------------------
const devTickets = computed(() => {
  return props.tickets.filter((t) => !t.id.startsWith("B") && t.source !== "test" && t.source !== "contract");
});

const devFilter = ref("active");

const devByState = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const c of columns) map[c] = [];
  for (const t of devTickets.value) {
    const state = t.state || "pending";
    if (!map[state]) map[state] = [];
    map[state].push(t);
  }
  return map;
});

const devStats = computed(() => {
  const total = devTickets.value.length;
  const done = (devByState.value["done"] || []).length;
  const blocked = (devByState.value["blocked"] || []).length;
  const active = total - done;
  return { total, done, active, blocked };
});

const devFilterOptions = computed(() => {
  const active = devTickets.value.filter((t) => t.state !== "done").length;
  return [
    { id: "active", label: "进行中", count: active },
    ...columns.map((c) => ({
      id: c,
      label: stateLabel(c),
      count: (devByState.value[c] || []).length,
    })),
  ];
});

const filteredDevTickets = computed(() => {
  if (devFilter.value === "active") {
    return devTickets.value.filter((t) => t.state !== "done");
  }
  return devByState.value[devFilter.value] || [];
});

// -------------------------------------------------------------
// 2. QA Test Cases / Tickets (Case-01..n)
// -------------------------------------------------------------
const allQaCases = computed(() => {
  const baseCases = props.qaCases || [];
  const progCases = props.qaProgress?.cases || [];

  if (!progCases.length) {
    return baseCases;
  }

  // Merge live progress state into base cases
  const progMap = new Map<string, typeof progCases[0]>();
  for (const pc of progCases) {
    if (pc.id) progMap.set(pc.id, pc);
  }

  return baseCases.map((c) => {
    const prog = progMap.get(c.id);
    if (prog) {
      return {
        ...c,
        state: prog.state || c.state,
        model: prog.model || c.model,
        reason: prog.reason || c.reason,
      };
    }
    return c;
  });
});

const qaLiveHasActive = computed(() => {
  return (
    props.qaProgress?.cases?.some((c) =>
      ["pending", "ready", "running"].includes(c.state)
    ) ?? false
  );
});

const qaLiveRunning = computed(() => {
  return allQaCases.value.filter((c) => c.state === "running");
});

const qaFilter = ref("all");

const qaByState = computed(() => {
  const map: Record<string, QaCaseItem[]> = {};
  for (const c of qaColumns) map[c] = [];
  for (const item of allQaCases.value) {
    const state = item.state || "pending";
    if (!map[state]) map[state] = [];
    map[state].push(item);
  }
  return map;
});

const qaStats = computed(() => {
  const total = allQaCases.value.length;
  const passed = (qaByState.value["passed"] || []).length;
  const failed = (qaByState.value["failed"] || []).length;
  const blocked = (qaByState.value["blocked"] || []).length;
  const running = (qaByState.value["running"] || []).length;
  const ready = (qaByState.value["ready"] || []).length;
  const pending = (qaByState.value["pending"] || []).length;
  return { total, passed, failed, blocked, running, ready, pending };
});

const qaFilterOptions = computed(() => {
  return [
    { id: "all", label: "全部", count: allQaCases.value.length },
    ...qaColumns.map((c) => ({
      id: c,
      label: qaStateLabel(c),
      count: (qaByState.value[c] || []).length,
    })),
  ];
});

const filteredQaCases = computed(() => {
  if (qaFilter.value === "all") return allQaCases.value;
  return qaByState.value[qaFilter.value] || [];
});

// -------------------------------------------------------------
// 3. Bug Fix Tickets (B1..Bn)
// -------------------------------------------------------------
const bugTickets = computed(() => {
  return props.tickets.filter((t) => t.id.startsWith("B") || t.source === "test" || t.source === "contract");
});

// Live-merged QA case info keyed by id, so B tickets can show their failure summary.
const qaCaseById = computed(
  () => new Map(allQaCases.value.map((c) => [c.id, c] as const)),
);

function bugCaseInfo(t: Ticket): QaCaseItem | null {
  const cid = findingCaseId(t.finding || "");
  return cid ? qaCaseById.value.get(cid) ?? null : null;
}

const bugFilter = ref("active");

const bugByState = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const c of columns) map[c] = [];
  for (const t of bugTickets.value) {
    const state = t.state || "pending";
    if (!map[state]) map[state] = [];
    map[state].push(t);
  }
  return map;
});

const bugStats = computed(() => {
  const total = bugTickets.value.length;
  const done = (bugByState.value["done"] || []).length;
  const blocked = (bugByState.value["blocked"] || []).length;
  const active = total - done;
  return { total, done, active, blocked };
});

const bugFilterOptions = computed(() => {
  const active = bugTickets.value.filter((t) => t.state !== "done").length;
  return [
    { id: "active", label: "待解决", count: active },
    ...columns.map((c) => ({
      id: c,
      label: stateLabel(c),
      count: (bugByState.value[c] || []).length,
    })),
  ];
});

const filteredBugTickets = computed(() => {
  if (bugFilter.value === "active") {
    return bugTickets.value.filter((t) => t.state !== "done");
  }
  return bugByState.value[bugFilter.value] || [];
});

// -------------------------------------------------------------
// Helper functions
// -------------------------------------------------------------
function stateLabel(col: string) {
  return TICKET_STATE_LABELS[col] || col;
}

function qaStateLabel(col: string) {
  return QA_STATE_LABELS[col] || col;
}
</script>

<style scoped>
.boards-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.board-section {
  background: #ffffff;
  border: 1px solid #dfe1e6;
  border-radius: 4px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(9, 30, 66, 0.08);
}

:global(.v-theme--dark) .board-section {
  background: #1d2125;
  border-color: rgba(255, 255, 255, 0.12);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}

.board-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #fafbfc;
  border-bottom: 1px solid #dfe1e6;
  transition: background-color 0.1s ease;
}

.board-section-header:hover {
  background: #f4f5f7;
}

:global(.v-theme--dark) .board-section-header {
  background: #22272b;
  border-bottom-color: rgba(255, 255, 255, 0.08);
}

:global(.v-theme--dark) .board-section-header:hover {
  background: #282e33;
}

.section-chevron {
  color: #6b778c;
  transition: transform 0.15s ease;
}

.section-title {
  font-size: 14px;
  font-weight: 700;
  color: #172b4d;
  letter-spacing: 0.1px;
}

:global(.v-theme--dark) .section-title {
  color: #dee4ea;
}

.board-section-body {
  padding: 10px;
}

/* Kanban Board Styling */
.ghx-board {
  display: flex;
  flex-direction: column;
  overflow-x: auto;
  background: #fff;
  padding: 0 2px 4px;
}

:global(.v-theme--dark) .ghx-board {
  background: rgb(var(--v-theme-surface));
}

.ghx-column-headers,
.ghx-columns {
  display: grid;
  grid-template-columns: repeat(7, minmax(180px, 1fr));
  column-gap: 10px;
}

.ghx-qa-headers,
.ghx-qa-columns {
  grid-template-columns: repeat(6, minmax(180px, 1fr)) !important;
}

.ghx-column-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 8px 8px;
  border-bottom: 2px solid #c1c7d0;
  background: #fff;
}

:global(.v-theme--dark) .ghx-column-header {
  background: rgb(var(--v-theme-surface));
  border-bottom-color: rgba(255, 255, 255, 0.18);
}

.ghx-column-title {
  font-size: 13px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.ghx-column-count {
  font-size: 12px;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface-variant));
}

.ghx-column {
  background: #f4f5f7;
  min-height: 8rem;
  padding: 6px;
  border-radius: 0 0 3px 3px;
}

:global(.v-theme--dark) .ghx-column {
  background: #22272b;
}

.ghx-card-gap {
  margin-bottom: 8px;
}

.ghx-card-gap:last-child {
  margin-bottom: 0;
}

.empty-section-placeholder {
  background: #fafbfc;
  border-radius: 4px;
  border: 1px dashed #dfe1e6;
  margin: 4px 0;
}

:global(.v-theme--dark) .empty-section-placeholder {
  background: #22272b;
  border-color: rgba(255, 255, 255, 0.08);
}

.pulse-chip {
  animation: pulse-glow 2s infinite ease-in-out;
}

@keyframes pulse-glow {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(255, 139, 0, 0.5);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(255, 139, 0, 0);
  }
}
</style>
