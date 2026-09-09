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
        v-for="(entry, i) in entries"
        :key="i"
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
import { nextTick, ref, watch } from "vue";
import { useDisplay } from "vuetify";
import type { PiEntry } from "@/api/types";
import { closePi, piTarget } from "@/state/pi";

const { smAndDown } = useDisplay();

const entries = ref<PiEntry[]>([]);
const meta = ref("");
const empty = ref("连接对话流…");
const chatEl = ref<HTMLElement | null>(null);
let follow = true;
let es: EventSource | null = null;

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
}

function stop() {
  es?.close();
  es = null;
}

watch(
  piTarget,
  (target) => {
    stop();
    entries.value = [];
    meta.value = "";
    empty.value = "连接对话流…";
    follow = true;
    if (!target) return;
    const url = `/api/jobs/${encodeURIComponent(target.jobId)}/pi/${encodeURIComponent(String(target.run))}/events`;
    es = new EventSource(url);
    es.addEventListener("snapshot", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { cwd?: string; found?: boolean };
      meta.value = `${data.cwd || ""}${data.found ? "" : " · 等待 session…"}`;
      if (data.found) empty.value = "";
    });
    es.addEventListener("entry", (e) => {
      empty.value = "";
      entries.value.push(JSON.parse((e as MessageEvent).data) as PiEntry);
      if (follow) {
        void nextTick(() => {
          const el = chatEl.value;
          if (el) el.scrollTop = el.scrollHeight;
        });
      }
    });
    es.addEventListener("done", () => {
      stop();
      if (!entries.value.length) empty.value = "没有找到对话记录";
    });
  },
  { immediate: true },
);
</script>

<style scoped>
.pi-chat {
  height: calc(100vh - 88px);
  overflow: auto;
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
