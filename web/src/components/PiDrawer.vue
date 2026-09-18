<template>
  <v-navigation-drawer
    v-if="piTarget"
    :model-value="true"
    location="right"
    temporary
    :width="smAndDown ? '100%' : 480"
    @update:model-value="onToggle"
  >
    <v-toolbar density="compact" color="surface">
      <v-toolbar-title>
        <div class="text-subtitle-1">pi 对话</div>
        <div class="text-caption text-medium-emphasis text-truncate">{{ meta }}</div>
      </v-toolbar-title>
      <v-btn icon="$close" variant="text" size="small" @click="closePi" />
    </v-toolbar>
    <v-divider />
    <div ref="chatEl" class="pi-chat pa-4" @scroll="onScroll">
      <v-empty-state v-if="!entries.length" :title="empty" />
      <v-card
        v-for="entry in visible"
        :key="entry.seq"
        class="mb-3"
        :variant="entry.role === 'user' ? 'tonal' : 'outlined'"
      >
        <v-card-text>
          <div class="text-caption text-medium-emphasis mb-2">{{ roleLabel(entry.role) }}</div>
          <v-expansion-panels v-if="entry.thinking" variant="accordion" class="mb-2">
            <v-expansion-panel title="思考">
              <v-expansion-panel-text>
                <pre class="pi-pre">{{ entry.thinking }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
          <template v-if="entry.role === 'toolResult'">
            <v-expansion-panels variant="accordion">
              <v-expansion-panel :title="(entry.is_error ? 'error · ' : '') + (entry.tool_name || 'result')">
                <v-expansion-panel-text>
                  <pre class="pi-pre">{{ entry.text }}</pre>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </template>
          <div v-else-if="entry.text" class="pi-text">{{ entry.text }}</div>
          <v-expansion-panels
            v-for="(t, ti) in entry.tools || []"
            :key="ti"
            variant="accordion"
            class="mt-2"
          >
            <v-expansion-panel :title="t.name || 'tool'">
              <v-expansion-panel-text>
                <pre class="pi-pre">{{ t.args }}</pre>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
      </v-card>
    </div>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useDisplay } from "vuetify";
import type { PiEntry } from "@/api/types";
import { closePi, piTarget } from "@/state/pi";

const { smAndDown } = useDisplay();

type ChatRow = PiEntry & { seq: number };

const WINDOW = 100;

const entries = ref<ChatRow[]>([]);
const start = ref(0);
const visible = computed(() => entries.value.slice(start.value));
const meta = ref("");
const empty = ref("连接对话流…");
const chatEl = ref<HTMLElement | null>(null);
let follow = true;
let es: EventSource | null = null;
let buffer: ChatRow[] = [];
let rafId = 0;
let nextSeq = 1;
let prepending = false;
let finished = false;

function roleLabel(role?: string) {
  if (role === "user") return "user";
  if (role === "assistant") return "assistant";
  if (role === "toolResult") return "tool";
  return role || "";
}

function onToggle(open: boolean) {
  if (!open) closePi();
}

function onScroll() {
  const el = chatEl.value;
  if (!el) return;
  follow = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  if (!follow && el.scrollTop < 48) void loadOlder();
}

function stop() {
  es?.close();
  es = null;
}

// 同步:缓冲的 SSE 帧一次性并入 entries,每帧最多一次响应式更新 + 一次滚动。
function flush() {
  rafId = 0;
  if (!buffer.length) return;
  entries.value.push(...buffer.splice(0));
  empty.value = "";
  if (!follow) return;
  start.value = Math.max(0, entries.value.length - WINDOW);
  void nextTick(() => {
    const el = chatEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

// 向上加载更早消息;程序化写 scrollTop 不触发 scroll 事件,需有界循环防卡顶。
async function loadOlder() {
  if (prepending || start.value <= 0) return;
  prepending = true;
  try {
    for (;;) {
      const el = chatEl.value;
      if (!el || !el.isConnected) return;
      const anchor = el.firstElementChild;
      const before = anchor ? anchor.getBoundingClientRect().top : 0;
      start.value = Math.max(0, start.value - WINDOW);
      await nextTick();
      const el2 = chatEl.value;
      if (!el2 || !el2.isConnected) return;
      if (anchor && anchor.isConnected) {
        el2.scrollTop += anchor.getBoundingClientRect().top - before;
      }
      if (start.value <= 0 || el2.scrollTop >= 48) return;
    }
  } finally {
    prepending = false;
  }
}

watch(
  piTarget,
  (target) => {
    stop();
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    buffer.length = 0;
    prepending = false;
    finished = false;
    nextSeq = 1;
    entries.value = [];
    start.value = 0;
    meta.value = "";
    empty.value = "连接对话流…";
    follow = true;
    if (!target) return;
    const url = `/api/jobs/${encodeURIComponent(target.jobId)}/pi/${encodeURIComponent(String(target.run))}/events`;
    const source = new EventSource(url);
    es = source;
    source.addEventListener("snapshot", (e) => {
      if (es !== source) return;
      const data = JSON.parse((e as MessageEvent).data) as { cwd?: string; found?: boolean };
      meta.value = `${data.cwd || ""}${data.found ? "" : " · 等待 session…"}`;
      if (data.found) empty.value = "";
    });
    source.addEventListener("entry", (e) => {
      if (es !== source) return;
      const row = JSON.parse((e as MessageEvent).data) as ChatRow;
      row.seq = nextSeq++;
      buffer.push(row);
      if (!rafId) rafId = requestAnimationFrame(flush);
    });
    source.addEventListener("done", () => {
      if (es !== source) return;
      finished = true;
      flush();
      stop();
      if (!entries.value.length) empty.value = "没有找到对话记录";
    });
    source.onerror = () => {
      if (finished || es !== source) return;
      if (source.readyState === EventSource.CONNECTING) {
        if (entries.value.length) {
          flush();
          stop();
        }
        return;
      }
      flush();
      stop();
      if (!entries.value.length) empty.value = "对话流连接失败";
    };
  },
  { immediate: true },
);
</script>

<style scoped>
.pi-chat {
  height: calc(100vh - 88px);
  overflow: auto;
  overflow-anchor: none;
}
.pi-text {
  white-space: pre-wrap;
  line-height: 1.5;
  font-size: 0.88rem;
}
.pi-pre {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.75rem;
}
</style>
