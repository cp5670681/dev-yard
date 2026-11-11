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
          <v-card variant="outlined" class="mb-4 bg-surface-light">
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
                <th style="width: 60px">票号</th>
                <th style="width: 90px">代码仓</th>
                <th>子任务概要（可编辑）</th>
                <th style="width: 90px">估点</th>
                <th style="width: 140px">状态 / 链接</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in preview.items" :key="item.id" :class="{ 'row-created': item.created }">
                <td>
                  <v-checkbox-btn
                    v-model="selectedMap[item.id]"
                    :disabled="item.created || submitting"
                  />
                </td>
                <td>
                  <code class="text-caption font-weight-bold">{{ item.id }}</code>
                </td>
                <td>
                  <v-chip size="x-small" variant="tonal" color="primary">
                    {{ item.repo_note || item.repo }}
                  </v-chip>
                </td>
                <td>
                  <v-text-field
                    v-model="item.summary"
                    density="compact"
                    variant="underlined"
                    hide-details
                    :disabled="item.created || submitting"
                    class="pt-0 mt-0 text-body-2"
                  />
                </td>
                <td>
                  <v-text-field
                    v-model.number="item.story_point"
                    type="number"
                    density="compact"
                    variant="underlined"
                    hide-details
                    :disabled="item.created || submitting"
                    class="pt-0 mt-0 text-caption"
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
          <span v-if="createdCount"> · 已建 {{ createdCount }} 张</span>
          <span v-if="selectedCount"> · 已选 {{ selectedCount }} 项待建</span>
        </span>
        <v-spacer />
        <v-btn variant="text" :disabled="submitting" @click="emit('update:modelValue', false)">
          取消
        </v-btn>
        <v-btn
          color="primary"
          variant="flat"
          :loading="submitting"
          :disabled="selectedCount === 0 || submitting"
          @click="doCreate"
        >
          确认创建 ({{ selectedCount }} 项)
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { mdiClose, mdiLinkVariant, mdiRefresh } from "@mdi/js";
import { createJiraSubtasks, getJiraSubtasksPreview } from "@/api/client";
import type { JiraSubtasksPreview } from "@/api/types";

const props = defineProps<{
  modelValue: boolean;
  jira: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "created", items: Array<{ id: string; jira_key: string }>): void;
}>();

const loading = ref(false);
const submitting = ref(false);
const error = ref("");
const preview = ref<JiraSubtasksPreview | null>(null);
const selectedMap = reactive<Record<string, boolean>>({});

const uncreatedItems = computed(() => {
  return (preview.value?.items || []).filter((it) => !it.created);
});

const createdCount = computed(() => {
  return (preview.value?.items || []).filter((it) => it.created).length;
});

const selectedCount = computed(() => {
  return uncreatedItems.value.filter((it) => selectedMap[it.id]).length;
});

const allUncreatedSelected = computed(() => {
  return (
    uncreatedItems.value.length > 0 &&
    uncreatedItems.value.every((it) => selectedMap[it.id])
  );
});

const someUncreatedSelected = computed(() => {
  return uncreatedItems.value.some((it) => selectedMap[it.id]);
});

function toggleSelectAll(val: boolean | null) {
  const target = Boolean(val);
  for (const it of uncreatedItems.value) {
    selectedMap[it.id] = target;
  }
}

async function loadPreview() {
  if (!props.jira) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await getJiraSubtasksPreview(props.jira);
    preview.value = res;
    // Default select all uncreated items
    for (const it of res.items) {
      selectedMap[it.id] = !it.created;
    }
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function doCreate() {
  if (!preview.value) return;
  const toCreate = preview.value.items.filter((it) => !it.created && selectedMap[it.id]);
  if (!toCreate.length) return;

  submitting.value = true;
  error.value = "";
  try {
    const payload = {
      items: toCreate.map((it) => ({
        id: it.id,
        summary: it.summary?.trim() || it.title,
        story_point: Number(it.story_point) || 0,
        description: it.description || "",
      })),
      assignee: preview.value.default_assignee || undefined,
    };
    const res = await createJiraSubtasks(props.jira, payload);
    emit("created", res.created);
    emit("update:modelValue", false);
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : String(err);
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
    }
  },
);
</script>

<style scoped>
.subtasks-table tbody tr.row-created {
  opacity: 0.75;
}
</style>

