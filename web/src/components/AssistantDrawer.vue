<template>
  <v-navigation-drawer
    v-if="assistantOpen"
    :model-value="true"
    location="right"
    temporary
    :width="smAndDown ? '100%' : 480"
    @update:model-value="onToggle"
  >
    <v-toolbar density="compact" color="surface">
      <v-toolbar-title>
        <div class="text-subtitle-1">助手</div>
        <div class="text-caption text-medium-emphasis text-truncate">
          {{ subtitle }}
        </div>
      </v-toolbar-title>
      <v-btn
        v-if="session?.state === 'streaming'"
        size="small"
        variant="text"
        @click="abort"
      >
        停止
      </v-btn>
      <v-btn
        size="small"
        variant="text"
        :disabled="!canReset"
        :loading="resetting"
        @click="newSession"
      >
        新会话
      </v-btn>
      <v-btn icon="$close" variant="text" size="small" @click="closeAssistant" />
    </v-toolbar>
    <v-divider />
    <div ref="chatEl" class="asst-chat pa-4" @scroll="onScroll">
      <v-empty-state
        v-if="!messages.length"
        title="问问平台或当前需求"
        text="例如：下一步做什么、为什么不能实现、把前后端从远端拉最新。"
      />
      <div v-if="hiddenCount" class="d-flex justify-center mb-2">
        <v-btn size="small" variant="text" @click="loadOlder">
          载入更早的 {{ Math.min(WINDOW, hiddenCount) }} 条
        </v-btn>
      </div>
      <v-card
        v-for="(entry, i) in visible"
        :key="entry.id || i"
        class="mb-3"
        :variant="entry.role === 'user' ? 'tonal' : 'outlined'"
      >
        <v-card-text>
          <div class="d-flex align-center ga-2 mb-2">
            <span class="text-caption text-medium-emphasis">{{
              roleLabel(entry.role)
            }}</span>
            <v-progress-circular
              v-if="entry.streaming"
              indeterminate
              size="11"
              width="2"
            />
          </div>

          <template v-if="entry.role === 'user'">
            <div v-if="entry.text" class="asst-text">{{ entry.text }}</div>
          </template>

          <template v-else>
            <div v-if="entry.thinking" class="asst-section">
              <button
                class="asst-toggle"
                type="button"
                @click="togglePanel('think', entry, i)"
              >
                <v-icon :icon="mdiLightbulbOnOutline" size="15" />
                <span>思考</span>
                <span v-if="entry.thinking_ms" class="text-medium-emphasis">
                  · {{ formatMs(entry.thinking_ms) }} 秒
                </span>
                <span class="flex-grow-1" />
                <v-icon
                  :icon="
                    isPanelOpen('think', entry, i) ? mdiChevronUp : mdiChevronDown
                  "
                  size="16"
                />
              </button>
              <v-expand-transition>
                <pre
                  v-if="isPanelOpen('think', entry, i)"
                  class="asst-pre asst-pre-clamp"
                >{{ entry.thinking }}</pre>
              </v-expand-transition>
            </div>

            <div v-if="entry.steps?.length" class="asst-section">
              <button
                class="asst-toggle"
                type="button"
                @click="togglePanel('proc', entry, i)"
              >
                <v-icon
                  :icon="stepsRunning(entry) ? mdiProgressClock : mdiCheckAll"
                  size="15"
                  :color="stepsRunning(entry) ? undefined : 'success'"
                />
                <span>{{ stepsTitle(entry) }}</span>
                <span class="flex-grow-1" />
                <v-icon
                  :icon="
                    isPanelOpen('proc', entry, i) ? mdiChevronUp : mdiChevronDown
                  "
                  size="16"
                />
              </button>
              <v-expand-transition>
                <div v-if="isPanelOpen('proc', entry, i)" class="asst-process">
                  <ToolStep
                    v-for="step in entry.steps"
                    :key="step.id"
                    :step="step"
                  />
                </div>
              </v-expand-transition>
            </div>

            <div v-if="entry.html" class="markdown asst-md" v-html="entry.html" />
            <div v-else-if="entry.text" class="asst-text">{{ entry.text }}</div>
            <div
              v-else-if="entry.streaming && !entry.steps?.length"
              class="asst-typing"
            >
              正在思考…
            </div>

            <div
              v-if="entry.suggested_actions?.length"
              class="mt-3 d-flex flex-column ga-2"
            >
              <v-card
                v-for="(act, ai) in entry.suggested_actions"
                :key="ai"
                variant="tonal"
                color="primary"
              >
                <v-card-text class="pb-0">
                  <div class="font-weight-medium">
                    {{ ACTION_LABELS[act.action] || act.action }}
                    <span v-if="act.jira" class="text-caption"> · {{ act.jira }}</span>
                  </div>
                  <div v-if="act.reason" class="text-caption mt-1">{{ act.reason }}</div>
                </v-card-text>
                <v-card-actions>
                  <v-spacer />
                  <v-btn
                    size="small"
                    color="primary"
                    :loading="running === keyOf(act)"
                    @click="runSuggested(act)"
                  >
                    确认执行
                  </v-btn>
                </v-card-actions>
              </v-card>
            </div>
          </template>
        </v-card-text>
      </v-card>
    </div>
    <v-divider />
    <div class="pa-3">
      <v-textarea
        v-model="draft"
        variant="outlined"
        density="compact"
        rows="2"
        auto-grow
        max-rows="6"
        hide-details
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        :disabled="composeLocked"
        @keydown="onKey"
      />
      <div class="d-flex justify-end mt-2">
        <v-btn
          color="primary"
          size="small"
          :disabled="!draft.trim() || composeLocked"
          :loading="session?.state === 'streaming'"
          @click="send"
        >
          发送
        </v-btn>
      </div>
    </div>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useDisplay } from "vuetify";
import {
  mdiCheckAll,
  mdiChevronDown,
  mdiChevronUp,
  mdiLightbulbOnOutline,
  mdiProgressClock,
} from "@mdi/js";
import {
  ApiError,
  createAssistantSession,
  dropAssistant,
  runAction,
  sendAssistantMessage,
  abortAssistant,
} from "@/api/client";
import type { AssistantSession, PiEntry, SuggestedAction } from "@/api/types";
import { ACTION_LABELS } from "@/composables/labels";
import { formatMs, stepsRunning, stepsTitle } from "@/composables/tools";
import { useSnack } from "@/composables/snack";
import { assistantOpen, closeAssistant } from "@/state/assistant";
import { closePi } from "@/state/pi";
import ToolStep from "@/components/ToolStep.vue";

const WINDOW = 40;

const { smAndDown } = useDisplay();
const route = useRoute();
const router = useRouter();
const snack = useSnack();

const session = ref<AssistantSession | null>(null);
const draft = ref("");
const running = ref("");
const resetting = ref(false);
const chatEl = ref<HTMLElement | null>(null);
const windowSize = ref(WINDOW);
const openPanels = ref<Record<string, boolean>>({});
const prevStreaming = new Map<string, boolean>();
let follow = true;
let es: EventSource | null = null;
let epoch = 0;

const entries = computed(() => session.value?.entries || []);
// Tool results are folded into their owning turn by the hub; drop any stray
// `toolResult` rows so they can never render as standalone cards.
const messages = computed(() =>
  entries.value.filter((entry) => entry.role !== "toolResult"),
);
const hiddenCount = computed(() =>
  Math.max(0, messages.value.length - windowSize.value),
);
const visible = computed(() =>
  hiddenCount.value ? messages.value.slice(hiddenCount.value) : messages.value,
);
const composeLocked = computed(
  () => resetting.value || session.value?.state === "streaming",
);
const canReset = computed(
  () =>
    Boolean(session.value) &&
    !resetting.value &&
    (entries.value.length > 0 || session.value?.state === "streaming"),
);

const pageJira = computed(() =>
  typeof route.params.jira === "string" ? route.params.jira : "",
);
const subtitle = computed(() => {
  if (pageJira.value) return pageJira.value;
  const name = String(route.name || "");
  if (name === "repos") return "仓库";
  if (name === "settings") return "配置";
  return "平台";
});

function roleLabel(role?: string) {
  if (role === "user") return "你";
  if (role === "assistant") return "助手";
  if (role === "toolResult") return "工具";
  return role || "";
}

function panelKey(kind: string, entry: PiEntry, i: number) {
  return `${kind}:${entry.id || `idx-${i}`}`;
}

function isPanelOpen(kind: string, entry: PiEntry, i: number) {
  return Boolean(openPanels.value[panelKey(kind, entry, i)]);
}

function togglePanel(kind: string, entry: PiEntry, i: number) {
  const key = panelKey(kind, entry, i);
  openPanels.value = { ...openPanels.value, [key]: !openPanels.value[key] };
}

function loadOlder() {
  windowSize.value += WINDOW;
}

function onToggle(open: boolean) {
  if (!open) closeAssistant();
}

function onScroll() {
  const el = chatEl.value;
  if (!el) return;
  follow = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}

function stopEs() {
  es?.close();
  es = null;
}

function scrollFollow() {
  if (!follow) return;
  void nextTick(() => {
    const el = chatEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

async function ensureSession() {
  if (session.value) {
    return session.value;
  }
  stopEs();
  session.value = await createAssistantSession(route.path, pageJira.value);
  return session.value;
}

function still(id: string, mine: number) {
  return mine === epoch && session.value?.id === id;
}

function upsertTurn(entry: PiEntry) {
  if (!session.value) return;
  const cur = session.value.entries;
  if (entry.id) {
    const idx = cur.findIndex((e) => e.id === entry.id);
    if (idx >= 0) {
      const next = cur.slice();
      next[idx] = { ...next[idx], ...entry };
      session.value = { ...session.value, entries: next };
      scrollFollow();
      return;
    }
  }
  session.value = { ...session.value, entries: [...cur, entry] };
  scrollFollow();
}

// Open the process/thinking panels while a turn is streaming, then collapse
// them when it settles — only the final answer stays in view.
watch(messages, (list) => {
  for (const entry of list) {
    if (entry.role !== "assistant" || !entry.id) continue;
    const now = Boolean(entry.streaming);
    if (prevStreaming.get(entry.id) === now) continue;
    prevStreaming.set(entry.id, now);
    const next = { ...openPanels.value };
    next[`think:${entry.id}`] = now;
    next[`proc:${entry.id}`] = now;
    openPanels.value = next;
  }
});

watch(
  () => session.value?.id,
  () => {
    windowSize.value = WINDOW;
    openPanels.value = {};
    prevStreaming.clear();
  },
);

function listen(id: string, mine: number) {
  stopEs();
  es = new EventSource(`/api/assistant/sessions/${encodeURIComponent(id)}/events`);
  es.addEventListener("snapshot", (e) => {
    if (!still(id, mine)) return;
    session.value = JSON.parse((e as MessageEvent).data) as AssistantSession;
    scrollFollow();
  });
  es.addEventListener("turn", (e) => {
    if (!still(id, mine)) return;
    upsertTurn(JSON.parse((e as MessageEvent).data) as PiEntry);
  });
  es.addEventListener("state", (e) => {
    const data = JSON.parse((e as MessageEvent).data) as {
      state?: string;
      error?: string | null;
    };
    if (!still(id, mine) || !session.value) return;
    session.value = {
      ...session.value,
      state: data.state || session.value.state,
      error: data.error ?? session.value.error,
    };
  });
  es.addEventListener("done", (e) => {
    if (!still(id, mine)) return;
    session.value = JSON.parse((e as MessageEvent).data) as AssistantSession;
    stopEs();
    scrollFollow();
  });
}

async function send() {
  const text = draft.value.trim();
  if (!text || composeLocked.value) return;
  draft.value = "";
  follow = true;
  const mine = epoch;
  try {
    const cur = await ensureSession();
    if (mine !== epoch) return;
    const snap = await sendAssistantMessage(cur.id, text, {
      route: route.path,
      jira: pageJira.value || null,
    });
    if (mine !== epoch || session.value?.id !== cur.id) return;
    session.value = snap;
    if (snap.state === "streaming" && still(cur.id, mine)) listen(cur.id, mine);
    scrollFollow();
  } catch (e) {
    if (mine !== epoch) return;
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  }
}

function onKey(ev: KeyboardEvent) {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    void send();
  }
}

async function abort() {
  if (!session.value) return;
  try {
    session.value = await abortAssistant(session.value.id);
  } catch (e) {
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  }
}

async function newSession() {
  const old = session.value;
  if (!old || resetting.value) return;
  resetting.value = true;
  epoch += 1;
  stopEs();
  session.value = null;
  follow = true;
  try {
    try {
      await dropAssistant(old.id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        session.value = old;
        throw e;
      }
    }
    await ensureSession();
  } catch (e) {
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  } finally {
    resetting.value = false;
  }
}

function keyOf(act: SuggestedAction) {
  return [act.action, act.jira, act.ticket_id, (act.repos || []).join(",")].join(":");
}

async function runSuggested(act: SuggestedAction) {
  const jira = act.jira || pageJira.value || session.value?.jira;
  if (!jira) {
    snack.notify("这条建议没有需求号", "error");
    return;
  }
  running.value = keyOf(act);
  try {
    const out = await runAction(jira, act.action, {
      ticket_id: act.ticket_id,
      repos: act.repos,
      strategy: act.strategy,
      force: act.force,
    });
    const job = out.jobs[0]?.id;
    snack.notify(`已启动 ${ACTION_LABELS[act.action] || act.action}`, "success");
    await router.push({
      path: `/r/${encodeURIComponent(jira)}`,
      query: job ? { job } : {},
    });
  } catch (e) {
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  } finally {
    running.value = "";
  }
}

watch(assistantOpen, async (open) => {
  if (!open) {
    stopEs();
    return;
  }
  closePi();
  follow = true;
  try {
    const mine = epoch;
    const cur = await ensureSession();
    if (mine !== epoch || session.value?.id !== cur.id) return;
    // Hiding the drawer stops the EventSource, so reopening it mid-turn must
    // reattach the stream; otherwise turns that arrived while hidden never show
    // and the session looks stuck until an abort forces a fresh snapshot.
    if (cur.state === "streaming" && (!es || es.readyState === EventSource.CLOSED)) {
      listen(cur.id, mine);
    }
  } catch (e) {
    snack.notify(e instanceof Error ? e.message : String(e), "error");
  }
});
</script>

<style scoped>
.asst-chat {
  height: calc(100vh - 220px);
  overflow: auto;
}
.asst-text {
  white-space: pre-wrap;
  line-height: 1.5;
  font-size: 0.88rem;
}
.asst-pre {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.75rem;
}
.asst-pre-clamp {
  max-height: 16rem;
  overflow: auto;
}
.asst-typing {
  font-size: 0.82rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}
.asst-section {
  margin-bottom: 0.4rem;
}
.asst-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 2px 0;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-surface), 0.68);
}
.asst-toggle:hover {
  color: rgb(var(--v-theme-on-surface));
}
.asst-process {
  margin-top: 0.2rem;
  padding: 0.1rem 0.6rem 0.3rem;
  border-left: 2px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.asst-md {
  line-height: 1.55;
  font-size: 0.88rem;
  overflow-wrap: anywhere;
}
.asst-md :deep(> :first-child) {
  margin-top: 0;
}
.asst-md :deep(> :last-child) {
  margin-bottom: 0;
}
.asst-md :deep(h1),
.asst-md :deep(h2),
.asst-md :deep(h3),
.asst-md :deep(h4) {
  margin: 0.85rem 0 0.4rem;
  line-height: 1.3;
  font-size: 1rem;
}
.asst-md :deep(p),
.asst-md :deep(ul),
.asst-md :deep(ol),
.asst-md :deep(blockquote) {
  margin: 0.4rem 0;
}
.asst-md :deep(ul),
.asst-md :deep(ol) {
  padding-left: 1.25rem;
}
.asst-md :deep(ul) {
  list-style: disc;
}
.asst-md :deep(ol) {
  list-style: decimal;
}
.asst-md :deep(li) {
  margin: 0.15rem 0;
}
.asst-md :deep(blockquote) {
  border-left: 3px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding-left: 0.7rem;
  color: rgba(var(--v-theme-on-surface), 0.72);
}
.asst-md :deep(a) {
  color: rgb(var(--v-theme-primary));
}
.asst-md :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.85em;
  background: rgba(var(--v-theme-surface-variant), 0.6);
  padding: 0.1em 0.35em;
  border-radius: 3px;
}
.asst-md :deep(pre) {
  background: rgba(var(--v-theme-surface-variant), 0.6);
  color: rgb(var(--v-theme-on-surface));
  padding: 0.7rem 0.85rem;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.8rem;
}
.asst-md :deep(pre code) {
  background: none;
  padding: 0;
}
.asst-md :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5rem 0;
  font-size: 0.82rem;
}
.asst-md :deep(th),
.asst-md :deep(td) {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding: 0.3rem 0.5rem;
}
.asst-md :deep(img) {
  max-width: 100%;
  border-radius: 6px;
}
</style>
