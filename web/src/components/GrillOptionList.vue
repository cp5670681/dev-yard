<template>
  <v-list class="grill-options mb-2 pa-0" bg-color="transparent">
    <v-list-item
      v-for="opt in options"
      :key="opt.id"
      :active="model === opt.id"
      :class="{ 'grill-opt--on': model === opt.id }"
      rounded="sm"
      class="grill-opt mb-2"
      @click="model = opt.id"
    >
      <template #prepend>
        <v-icon
          :icon="model === opt.id ? mdiRadioboxMarked : mdiRadioboxBlank"
          :color="model === opt.id ? 'primary' : undefined"
          size="22"
          class="grill-opt__mark"
        />
      </template>
      <v-list-item-title class="text-wrap text-body-2">
        <span class="font-weight-medium">{{ opt.id }}.</span>
        {{ opt.label }}
      </v-list-item-title>
    </v-list-item>
    <v-list-item
      :active="model === CUSTOM"
      :class="{ 'grill-opt--on': model === CUSTOM }"
      rounded="sm"
      class="grill-opt"
      @click="model = CUSTOM"
    >
      <template #prepend>
        <v-icon
          :icon="model === CUSTOM ? mdiRadioboxMarked : mdiRadioboxBlank"
          :color="model === CUSTOM ? 'primary' : undefined"
          size="22"
          class="grill-opt__mark"
        />
      </template>
      <v-list-item-title class="text-body-2">自定义</v-list-item-title>
    </v-list-item>
  </v-list>
</template>

<script lang="ts">
export const CUSTOM = "__custom__";
</script>

<script setup lang="ts">
import { mdiRadioboxBlank, mdiRadioboxMarked } from "@mdi/js";

defineProps<{
  options: { id: string; label: string }[];
}>();

const model = defineModel<string>({ required: true });
</script>

<style scoped>
.grill-opt {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  min-height: 48px;
}
.grill-opt--on {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.08);
}
.grill-opt__mark {
  width: 22px;
  min-width: 22px;
}
</style>
