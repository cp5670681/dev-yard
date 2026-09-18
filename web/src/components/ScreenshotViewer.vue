<template>
  <v-dialog
    :model-value="modelValue"
    max-width="1200"
    scrollable
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card v-if="current" class="d-flex flex-column screenshot-viewer">
      <div class="d-flex align-center ga-2 pa-2 bg-surface-variant">
        <span class="text-caption text-truncate viewer-caption" :title="current.caption">
          {{ current.caption }}
        </span>
        <span class="text-caption text-medium-emphasis flex-shrink-0">
          {{ safeIndex + 1 }} / {{ images.length }}
        </span>
        <v-spacer />
        <v-btn
          :icon="zoomed ? mdiArrowCollapse : mdiArrowExpand"
          size="small"
          variant="text"
          :title="zoomed ? '适应窗口' : '原始尺寸'"
          @click="zoomed = !zoomed"
        />
        <v-btn
          :icon="mdiOpenInNew"
          size="small"
          variant="text"
          title="新窗口打开"
          :href="current.url"
          target="_blank"
        />
        <v-btn :icon="mdiClose" size="small" variant="text" title="关闭" @click="close" />
      </div>
      <v-divider />
      <div class="viewer-body" :class="{ 'viewer-body--zoomed': zoomed }">
        <v-img v-if="!zoomed" :src="current.url" :alt="current.caption" contain max-height="74vh" />
        <img v-else :src="current.url" :alt="current.caption" class="viewer-zoomed-img" />
      </div>
      <v-divider v-if="images.length > 1" />
      <div v-if="images.length > 1" class="d-flex align-center ga-1 pa-2">
        <v-btn
          :icon="mdiChevronLeft"
          variant="tonal"
          size="small"
          density="comfortable"
          :disabled="safeIndex === 0"
          title="上一张 (←)"
          @click="step(-1)"
        />
        <div v-if="images.length > 3" class="d-flex ga-1 flex-grow-1 overflow-x-auto py-1">
          <v-img
            v-for="(img, i) in images"
            :key="img.url"
            :src="img.url"
            :alt="img.caption || ''"
            width="48"
            height="32"
            cover
            class="cursor-pointer viewer-thumb"
            :class="{ 'viewer-thumb--active': i === safeIndex }"
            @click="go(i)"
          />
        </div>
        <v-btn
          :icon="mdiChevronRight"
          variant="tonal"
          size="small"
          density="comfortable"
          :disabled="safeIndex >= images.length - 1"
          title="下一张 (→)"
          @click="step(1)"
        />
      </div>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import {
  mdiArrowCollapse,
  mdiArrowExpand,
  mdiChevronLeft,
  mdiChevronRight,
  mdiClose,
  mdiOpenInNew,
} from "@mdi/js";
import type { ShotItem } from "@/api/types";

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    images: ShotItem[];
    index?: number;
  }>(),
  { index: 0 },
);

const emit = defineEmits<{
  "update:modelValue": [boolean];
  "update:index": [number];
}>();

const localIndex = ref(props.index ?? 0);
const zoomed = ref(false);

const safeIndex = computed(() =>
  Math.min(Math.max(localIndex.value, 0), Math.max(props.images.length - 1, 0)),
);
const current = computed(() => props.images[safeIndex.value] || null);

watch(
  () => props.index,
  (v) => {
    if (typeof v === "number") localIndex.value = v;
  },
);
watch(
  () => props.images,
  (imgs) => {
    if (localIndex.value >= imgs.length) localIndex.value = 0;
    // Self-close on an emptied list instead of rendering a blank overlay.
    if (!imgs.length && props.modelValue) close();
  },
);
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      window.addEventListener("keydown", onKey);
    } else {
      window.removeEventListener("keydown", onKey);
      zoomed.value = false;
    }
  },
);
onUnmounted(() => window.removeEventListener("keydown", onKey));

function go(i: number) {
  localIndex.value = i;
  emit("update:index", i);
}

function step(delta: number) {
  go(safeIndex.value + delta);
}

function close() {
  emit("update:modelValue", false);
}

function onKey(e: KeyboardEvent) {
  if (!props.modelValue) return;
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    step(-1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    step(1);
  }
}
</script>

<style scoped>
.screenshot-viewer {
  max-height: 92vh;
  overflow: hidden;
}
.viewer-caption {
  max-width: 45%;
}
.viewer-body {
  display: flex;
  justify-content: center;
  background: rgba(0, 0, 0, 0.04);
  min-height: 240px;
}
:global(.v-theme--dark) .viewer-body {
  background: rgba(0, 0, 0, 0.3);
}
.viewer-body--zoomed {
  max-height: 74vh;
  overflow: auto;
  justify-content: flex-start;
}
.viewer-zoomed-img {
  max-width: none;
}
.viewer-thumb {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 3px;
  opacity: 0.7;
  flex-shrink: 0;
}
.viewer-thumb--active {
  opacity: 1;
  border-color: rgb(var(--v-theme-primary));
}
.cursor-pointer {
  cursor: pointer;
}
</style>
