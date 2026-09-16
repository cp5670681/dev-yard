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
      <v-btn icon="$close" variant="text" size="small" @click="closeAssistant" />
    </v-toolbar>
    <v-divider />
    <div ref="chatEl" class="asst-chat pa-4" @scroll="onScroll">
      <v-empty-state
        v-if="!entries.length"
        title="问问平台或当前需求"
        text="例如：下一步做什么、为什么不能实现、把前后端从远端拉最新。"
      />
      <v-card
        v-for="(entry, i) in entries"
        :key="i"
        class="mb-3"
        :variant="entry.role === 'user' ? 'tonal' : 'outlined'"
      >
        <v-card-text>
          <div class="text-caption text-medium-emphasis mb-2">{{ roleLabel(entry.role) }}</div>
          <div v-if="entry.text" class="asst-text">{{ entry.text }}</div>
          <v-expansion-panels
            v-if="entry.thinking"
            variant="accordion"
            class="mt-2"
          >
            <v-expansion-panel title="思考">
              <v-expansion-panel-text>
                <pre class="asst-pre">{{ entry.thinking }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
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
        :disabled="session?.state === 'streaming'"
        @keydown="onKey"
      />
      <div class="d-flex justify-end mt-2">
        <v-btn
          color="primary"
          size="small"
          :disabled="!draft.trim() || session?.state === 'streaming'"
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
  createAssistantSession,
  runAction,
  sendAssistantMessage,
  abortAssistant,
} from "@/api/client";
import type { AssistantSession, PiEntry, SuggestedAction } from "@/api/types";
import { ACTION_LABELS } from "@/composables/labels";
import { useSnack } from "@/composables/snack";
import { assistantOpen, closeAssistant } from "@/state/assistant";
import { closePi } from "@/state/pi";

const { smAndDown } = useDisplay();
const route = useRoute();
const router = useRouter();
const snack = useSnack();

const session = ref<AssistantSession | null>(null);
const draft = ref("");
const running = ref("");
const chatEl = ref<HTMLElement | null>(null);
let follow = true;
let es: EventSource | null = null;
const entries = computed(() => session.value?.entries || []);

const pageJira = computed(() =>
  typeof route.params.jira === "string" ? route.params.jira : "",
);
const subtitle = computed(() => {
  if (pageJira.value) return pageJira.value;
  const name = String(route.name || "");
  if (name === "repos") return "仓库";
  if (name === "settings") return "模型";
  return "平台";
});

function roleLabel(role?: string) {
  if (role === "user") return "你";
  if (role === "assistant") return "助手";
  if (role === "toolResult") return "工具";
  return role || "";
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

function listen(id: string) {
  stopEs();
  es = new EventSource(`/api/assistant/sessions/${encodeURIComponent(id)}/events`);
  es.addEventListener("snapshot", (e) => {
    session.value = JSON.parse((e as MessageEvent).data) as AssistantSession;
    scrollFollow();
  });
  es.addEventListener("entry", (e) => {
    const entry = JSON.parse((e as MessageEvent).data) as PiEntry;
    if (!session.value) return;
    session.value = {
      ...session.value,
      entries: [...session.value.entries, entry],
    };
    scrollFollow();
  });
  es.addEventListener("delta", (e) => {
    const entry = JSON.parse((e as MessageEvent).data) as PiEntry;
    if (!session.value) return;
    const cur = session.value.entries;
    const last = cur[cur.length - 1];
    const next =
      last && last.role === "assistant" && last.streaming
        ? [...cur.slice(0, -1), entry]
        : [...cur, entry];
    session.value = { ...session.value, entries: next };
    scrollFollow();
  });
  es.addEventListener("state", (e) => {
    const data = JSON.parse((e as MessageEvent).data) as {
      state?: string;
      error?: string | null;
    };
    if (!session.value) return;
    session.value = {
      ...session.value,
      state: data.state || session.value.state,
      error: data.error ?? session.value.error,
    };
  });
  es.addEventListener("done", (e) => {
    session.value = JSON.parse((e as MessageEvent).data) as AssistantSession;
    stopEs();
    scrollFollow();
  });
}

async function send() {
  const text = draft.value.trim();
  if (!text) return;
  draft.value = "";
  follow = true;
  try {
    const cur = await ensureSession();
    const snap = await sendAssistantMessage(cur.id, text, {
      route: route.path,
      jira: pageJira.value || null,
    });
    session.value = snap;
    if (snap.state === "streaming") listen(cur.id);
    scrollFollow();
  } catch (e) {
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
    await ensureSession();
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
</style>
