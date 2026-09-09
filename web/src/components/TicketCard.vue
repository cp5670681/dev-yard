<template>
  <v-card variant="outlined" class="ticket-card">
    <v-card-text class="pa-3">
      <div class="d-flex align-center ga-2 mb-1">
        <span class="text-caption text-primary">{{ ticket.id }}</span>
        <v-chip v-if="showState" size="x-small" :color="dotColor" variant="tonal">
          {{ ticket.state }}
        </v-chip>
        <v-chip v-if="ticket.parallel" size="x-small" variant="text">parallel</v-chip>
      </div>
      <div class="font-weight-medium">{{ ticket.title || ticket.id }}</div>
      <div class="text-caption text-medium-emphasis mt-1">
        {{ ticket.repo
        }}{{ ticket.depends_on?.length ? " · 依赖 " + ticket.depends_on.join(", ") : "" }}
      </div>
      <div v-if="ticket.last_summary" class="text-caption mt-2 summary">
        {{ ticket.last_summary.slice(0, 240) }}
      </div>
      <div class="d-flex ga-2 mt-3">
        <v-btn
          size="small"
          variant="tonal"
          :disabled="!ticket.can_implement"
          @click="$emit('implement', ticket.id)"
        >
          实现
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          :disabled="!ticket.can_review"
          @click="$emit('review', ticket.id)"
        >
          审查
        </v-btn>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { Ticket } from "@/api/types";

const props = defineProps<{ ticket: Ticket; showState?: boolean }>();
defineEmits<{ implement: [id: string]; review: [id: string] }>();

const colors: Record<string, string> = {
  pending: "grey",
  ready: "info",
  implementing: "primary",
  implemented: "purple",
  reviewing: "cyan",
  blocked: "error",
  done: "success",
};

const dotColor = computed(() => colors[props.ticket.state] || "grey");
</script>

<style scoped>
.summary {
  white-space: pre-wrap;
  max-height: 4.5rem;
  overflow: hidden;
}
</style>
