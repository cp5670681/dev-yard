<template>
  <v-dialog
    :model-value="modelValue"
    max-width="960"
    scrollable
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card class="d-flex flex-column">
      <v-card-title class="d-flex align-center flex-wrap ga-2 py-2 px-4 bg-surface-variant">
        <v-icon :icon="mdiLinkVariant" color="primary" />
        <span class="text-subtitle-1 font-weight-bold">{{ jira }}</span>
        <span class="text-subtitle-1 font-weight-medium">创建 Jira 子任务</span>
        <v-spacer />
        <v-btn
          :icon="mdiRefresh"
          size="small"
          variant="text"
          :loading="loading"
          @click="loadPreview"
        />
        <v-btn
          :icon="mdiClose"
          size="small"
          variant="text"
          @click="emit('update:modelValue', false)"
        />
      </v-card-title>

      <v-card-text class="pa-4">
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4" closable @click:close="error = ''">
          {{ error }}
        </v-alert>

        <div v-if="loading && !preview" class="d-flex justify-center align-center py-8">
          <v-progress-circular indeterminate color="primary" size="36" />
        </div>

        <template v-else-if="preview">
          <!-- Parent requirement info -->
          <v-card variant="outlined" class="mb-3 bg-surface-light">
            <v-card-text class="py-2 px-3">
              <div class="d-flex flex-wrap align-center ga-3 text-caption text-medium-emphasis">
                <div>
                  <strong>父需求：</strong>
                  <span class="font-weight-bold text-high-emphasis">{{ preview.jira }}</span>
                  <span v-if="preview.parent_summary"> · {{ preview.parent_summary }}</span>
                </div>
                <div>
                  <strong>项目：</strong>
                  <span>{{ preview.project_key }}</span>
                </div>
                <div>
                  <strong>默认经办人：</strong>
                  <span>{{ preview.default_assignee_display || preview.default_assignee || '无' }}</span>
                </div>
                <div>
                  <strong>默认 Story Point：</strong>
                  <span>0</span>
                </div>
              </div>
            </v-card-text>
          </v-card>

          <!-- Granularity Mode Selector -->
          <div class="d-flex flex-wrap justify-space-between align-center ga-2 mb-3 px-1">
            <div class="d-flex align-center ga-1 text-caption text-medium-emphasis">
              <span class="font-weight-bold">子任务聚合粒度：</span>
            </div>
            <v-btn-toggle
              v-model="groupMode"
              mandatory
              density="compact"
              color="primary"
              variant="outlined"
            >
              <v-btn value="repo" size="small">
                按代码仓合并（推荐）
              </v-btn>
              <v-btn value="requirement" size="small">
                整需求单任务
              </v-btn>
              <v-btn value="ticket" size="small">
                按 Ticket 细分
              </v-btn>
            </v-btn-toggle>
          </div>

          <!-- Tickets Table -->
          <v-table density="compact" class="subtasks-table">
            <thead>
              <tr>
                <th style="width: 44px">
                  <v-checkbox-btn
                    :model-value="allUncreatedSelected"
                    :indeterminate="someUncreatedSelected && !allUncreatedSelected"
                    :disabled="uncreatedItems.length === 0"
                    @update:model-value="toggleSelectAll"
                  />
                </th>
                <template v-if="groupMode === 'ticket'">
                  <th style="width: 70px">票号</th>
                  <th style="width: 100px">代码仓</th>
                </template>
                <template v-else>
                  <th style="width: 110px">代码仓</th>
                  <th style="width: 170px">包含票号</th>
                </template>
                <th>子任务概要（可编辑）</th>
                <th style="width: 90px">估点</th>
                <th style="width: 140px">状态 / 链接</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in displayItems" :key="item.key" :class="{ 'row-created': item.created }">
                <td>
                  <v-checkbox-btn
                    v-model="selectedMap[item.key]"
                    :disabled="item.created || submitting"
                  />
                </td>
                <template v-if="groupMode === 'ticket'">
                  <td>
                    <code class="text-caption font-weight-bold">{{ item.ticket_ids[0] }}</code>
                  </td>
                  <td>
                    <v-chip size="x-small" variant="tonal" color="primary">
                      {{ item.repo_note || item.repo }}
                    </v-chip>
                  </td>
                </template>
                <template v-else>
                  <td>
                    <v-chip size="x-small" variant="tonal" color="primary" class="font-weight-medium">
                      {{ item.repo_note || item.repo }}
                    </v-chip>
                  </td>
                  <td>
                    <div class="d-flex flex-wrap align-center ga-1 py-1">
                      <v-chip
                        v-for="t in item.tickets"
                        :key="t.id"
                        size="x-small"
                        variant="outlined"
                        :title="`${t.id}: ${t.title}`"
                        class="font-mono text-caption"
                      >
                        {{ t.id }}
                      </v-chip>
                      <span class="text-caption text-medium-emphasis">
                        ({{ item.tickets.length }} 张)
                      </span>
                    </div>
                  </td>
                </template>
                <td>
                  <v-text-field
                    :model-value="item.summary"
                    density="compact"
                    variant="underlined"
                    hide-details
                    :disabled="item.created || submitting"
                    class="pt-0 mt-0 text-body-2"
                    @update:model-value="(v: string) => updateSummary(item, v)"
                  />
                </td>
                <td>
                  <v-text-field
                    :model-value="item.story_point"
                    type="number"
                    density="compact"
                    variant="underlined"
                    hide-details
                    :disabled="item.created || submitting"
                    class="pt-0 mt-0 text-caption"
                    @update:model-value="(v: string | number) => updateStoryPoint(item, v)"
                  />
                </td>
                <td>
                  <v-chip
                    v-if="item.created && item.jira_key"
                    size="x-small"
                    color="success"
                    variant="tonal"
                    :href="item.jira_url || `http://jira.rccchina.com/browse/${item.jira_key}`"
                    target="_blank"
                    class="font-weight-medium"
                    @click.stop
                  >
                    已创建 · {{ item.jira_key }}
                  </v-chip>
                  <v-chip v-else size="x-small" variant="outlined" class="text-medium-emphasis">
                    待创建
                  </v-chip>
                </td>
              </tr>
            </tbody>
          </v-table>
        </template>
      </v-card-text>

      <v-divider />

      <v-card-actions class="px-4 py-3">
        <span v-if="preview" class="text-caption text-medium-emphasis">
          共 {{ preview.items.length }} 张研发票
          <span v-if="groupMode !== 'ticket'">
            · 聚合为 {{ displayItems.length }} 个子任务
          </span>
          <span v-if="createdCount"> · 已建 {{ createdCount }} 个</span>
          <span v-if="selectedCount"> · 已选 {{ selectedCount }} 项待建</span>
        </span>
        <v-spacer />
        <v-btn variant="text" :disabled="submitting" @click="emit('update:modelValue', false)">
          取消
        </v-btn>
        <v-btn
          color="primary"
          variant="flat"
          :disabled="selectedCount === 0 || submitting"
          @click="promptConfirm"
        >
          确认创建 ({{ selectedCount }} 项)
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- Secondary Confirmation Dialog -->
  <v-dialog v-model="confirmOpen" max-width="560">
    <v-card>
      <v-card-title class="d-flex align-center ga-2 py-3 px-4 bg-surface-variant">
        <v-icon :icon="mdiAlertCircleOutline" color="primary" />
        <span class="text-subtitle-1 font-weight-bold">确认在 Jira 创建子任务</span>
      </v-card-title>
      <v-card-text class="pa-4">
        <p class="text-body-2 mb-3">
          即将向 Jira <strong>{{ preview?.project_key }}</strong> 项目为父需求 <strong>{{ jira }}</strong> 创建以下 <strong>{{ toCreateItems.length }}</strong> 个子任务：
        </p>
        <v-list density="compact" class="bg-surface-light rounded-sm pa-2 mb-3 border">
          <v-list-item v-for="(it, idx) in toCreateItems" :key="it.key" class="px-2 py-1">
            <template #prepend>
              <span class="text-caption font-weight-bold text-medium-emphasis mr-2">{{ idx + 1 }}.</span>
            </template>
            <v-list-item-title class="text-body-2 font-weight-bold text-high-emphasis text-wrap">
              {{ it.summary }}
            </v-list-item-title>
            <v-list-item-subtitle class="text-caption text-medium-emphasis mt-0.5">
              <span v-if="it.repo_note || it.repo">代码仓：{{ itemRepoNote(it) }} · </span>
              <span>关联票：{{ it.ticket_ids.join(', ') }} ({{ it.ticket_ids.length }} 张)</span>
              <span v-if="it.story_point"> · 估点：{{ it.story_point }}</span>
            </v-list-item-subtitle>
          </v-list-item>
        </v-list>
        <div v-if="preview?.default_assignee_display || preview?.default_assignee" class="text-caption text-medium-emphasis">
          默认经办人：<strong class="text-high-emphasis">{{ preview.default_assignee_display || preview.default_assignee }}</strong>
        </div>
      </v-card-text>
      <v-divider />
      <v-card-actions class="px-4 py-3">
        <v-spacer />
        <v-btn variant="text" :disabled="submitting" @click="confirmOpen = false">
          返回修改
        </v-btn>
        <v-btn
          color="primary"
          variant="flat"
          :loading="submitting"
          @click="executeCreate"
        >
          确认提交创建
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { mdiAlertCircleOutline, mdiClose, mdiLinkVariant, mdiRefresh } from "@mdi/js";
import { createJiraSubtasks, getJiraSubtasksPreview } from "@/api/client";
import type { JiraSubtaskPreviewItem, JiraSubtasksPreview } from "@/api/types";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "created", items: Array<{ id: string; jira_key: string }>): void;
}>();

type GroupMode = "repo" | "requirement" | "ticket";

const loading = ref(false);
const submitting = ref(false);
const confirmOpen = ref(false);
const error = ref("");
const preview = ref<JiraSubtasksPreview | null>(null);
const groupMode = ref<GroupMode>("repo");

const editState = reactive<Record<string, { summary?: string; story_point?: number }>>({});
const selectedMap = reactive<Record<string, boolean>>({});

interface DisplaySubtaskItem {
  key: string;
  ticket_ids: string[];
  repo?: string;
  repo_note?: string;
  summary: string;
  story_point: number;
  description: string;
  created: boolean;
  jira_key?: string | null;
  jira_url?: string | null;
  tickets: JiraSubtaskPreviewItem[];
}

const displayItems = computed<DisplaySubtaskItem[]>(() => {
  if (!preview.value) return [];
  const rawItems = preview.value.items || [];
  const parentTitle = (preview.value.parent_summary || preview.value.jira || "").trim();

  if (groupMode.value === "repo") {
    const map = new Map<string, JiraSubtaskPreviewItem[]>();
    for (const item of rawItems) {
      const r = item.repo || "other";
      if (!map.has(r)) {
        map.set(r, []);
      }
      map.get(r)!.push(item);
    }

    const res: DisplaySubtaskItem[] = [];
    for (const [repo, tList] of map.entries()) {
      const first = tList[0];
      const repoNote = first.repo_note || repo;
      const key = `repo:${repo}`;
      const defaultSummary = `【${repoNote}】${parentTitle}`;
      const edited = editState[key];
      const summary = edited?.summary !== undefined ? edited.summary : defaultSummary;
      const defaultSp = 0;
      const storyPoint = edited?.story_point !== undefined ? edited.story_point : defaultSp;

      const allCreated = tList.length > 0 && tList.every((t) => t.created);
      const createdKey = tList.find((t) => t.jira_key)?.jira_key || null;
      const createdUrl = tList.find((t) => t.jira_url)?.jira_url || null;

      res.push({
        key,
        ticket_ids: tList.map((t) => t.id),
        repo: repo === "other" ? "" : repo,
        repo_note: repoNote,
        summary,
        story_point: storyPoint,
        description: "",
        created: allCreated,
        jira_key: createdKey,
        jira_url: createdUrl,
        tickets: tList,
      });
    }
    return res;
  } else if (groupMode.value === "requirement") {
    const key = `req:${preview.value.jira}`;
    const defaultSummary = `【研发】${parentTitle}`;
    const edited = editState[key];
    const summary = edited?.summary !== undefined ? edited.summary : defaultSummary;
    const defaultSp = 0;
    const storyPoint = edited?.story_point !== undefined ? edited.story_point : defaultSp;
    const allCreated = rawItems.length > 0 && rawItems.every((t) => t.created);
    const createdKey = rawItems.find((t) => t.jira_key)?.jira_key || null;
    const createdUrl = rawItems.find((t) => t.jira_url)?.jira_url || null;

    return [
      {
        key,
        ticket_ids: rawItems.map((t) => t.id),
        repo: "全部代码仓",
        repo_note: "研发整体",
        summary,
        story_point: storyPoint,
        description: "",
        created: allCreated,
        jira_key: createdKey,
        jira_url: createdUrl,
        tickets: rawItems,
      },
    ];
  } else {
    return rawItems.map((t) => {
      const key = `ticket:${t.id}`;
      const edited = editState[key];
      const summary = edited?.summary !== undefined ? edited.summary : t.summary;
      const storyPoint = edited?.story_point !== undefined ? edited.story_point : t.story_point || 0;

      return {
        key,
        ticket_ids: [t.id],
        repo: t.repo,
        repo_note: t.repo_note,
        summary,
        story_point: storyPoint,
        description: t.description || "",
        created: t.created,
        jira_key: t.jira_key,
        jira_url: t.jira_url,
        tickets: [t],
      };
    });
  }
});

const uncreatedItems = computed(() => {
  return displayItems.value.filter((it) => !it.created);
});

const createdCount = computed(() => {
  return displayItems.value.filter((it) => it.created).length;
});

const selectedCount = computed(() => {
  return uncreatedItems.value.filter((it) => selectedMap[it.key]).length;
});

const toCreateItems = computed(() => {
  return displayItems.value.filter((it) => !it.created && selectedMap[it.key]);
});

const allUncreatedSelected = computed(() => {
  return (
    uncreatedItems.value.length > 0 &&
    uncreatedItems.value.every((it) => selectedMap[it.key])
  );
});

const someUncreatedSelected = computed(() => {
  return uncreatedItems.value.some((it) => selectedMap[it.key]);
});

function itemRepoNote(it: DisplaySubtaskItem) {
  return it.repo_note || it.repo || "全代码仓";
}

function toggleSelectAll(val: boolean | null) {
  const target = Boolean(val);
  for (const it of uncreatedItems.value) {
    selectedMap[it.key] = target;
  }
}

function updateSummary(item: DisplaySubtaskItem, val: string) {
  if (!editState[item.key]) {
    editState[item.key] = {};
  }
  editState[item.key].summary = val;
}

function updateStoryPoint(item: DisplaySubtaskItem, val: number | string) {
  if (!editState[item.key]) {
    editState[item.key] = {};
  }
  editState[item.key].story_point = Number(val) || 0;
}

function initSelections() {
  for (const it of displayItems.value) {
    if (selectedMap[it.key] === undefined) {
      selectedMap[it.key] = !it.created;
    }
  }
}

async function loadPreview() {
  if (!props.jira) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await getJiraSubtasksPreview(props.jira);
    preview.value = res;
    initSelections();
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

watch(groupMode, () => {
  initSelections();
});

function promptConfirm() {
  if (!toCreateItems.value.length) return;
  confirmOpen.value = true;
}

async function executeCreate() {
  if (!preview.value || !toCreateItems.value.length) return;

  submitting.value = true;
  error.value = "";
  try {
    const payload = {
      items: toCreateItems.value.map((it) => ({
        id: it.key,
        ticket_ids: it.ticket_ids,
        summary: it.summary?.trim() || it.ticket_ids.join(","),
        story_point: Number(it.story_point) || 0,
        description: it.description || "",
        repo: it.repo || undefined,
      })),
      assignee: preview.value.default_assignee || undefined,
    };
    const res = await createJiraSubtasks(props.jira, payload);
    confirmOpen.value = false;
    emit("created", res.created);
    emit("update:modelValue", false);
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : String(err);
    confirmOpen.value = false;
    void loadPreview();
  } finally {
    submitting.value = false;
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      loadPreview();
    } else {
      error.value = "";
      confirmOpen.value = false;
    }
  },
);
</script>

<style scoped>
.subtasks-table tbody tr.row-created {
  opacity: 0.75;
}
</style>
