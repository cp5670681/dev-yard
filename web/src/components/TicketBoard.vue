<template>
  <div>
    <template v-if="mdAndUp">
      <div class="board">
        <div v-for="col in columns" :key="col" class="col">
          <div class="d-flex align-center mb-2 px-1 text-caption">
            <v-badge :color="dotColor(col)" dot inline class="mr-2" />
            <span class="col-header text-truncate">{{ col }}</span>
            <v-spacer />
            <span class="col-count-badge">{{ (byState[col] || []).length }}</span>
          </div>
          <TicketCard
            v-for="t in byState[col] || []"
            :key="t.id"
            class="mb-2.5"
            :ticket="t"
            @implement="$emit('implement', $event)"
            @review="$emit('review', $event)"
            @diff="$emit('diff', $event)"
            @feedback="$emit('feedback', $event)"
          />
          <div v-if="!(byState[col] || []).length" class="text-center text-medium-emphasis py-6 text-caption">
            空
          </div>
        </div>
      </div>
    </template>
    <template v-else>
      <v-slide-group v-model="filter" mandatory show-arrows class="mb-3">
        <v-slide-group-item v-for="opt in filterOptions" :key="opt.id" :value="opt.id" v-slot="{ isSelected, toggle }">
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
        v-for="t in filtered"
        :key="t.id"
        class="mb-3"
        :ticket="t"
        show-state
        @implement="$emit('implement', $event)"
        @review="$emit('review', $event)"
        @diff="$emit('diff', $event)"
        @feedback="$emit('feedback', $event)"
      />
      <div v-if="!filtered.length" class="text-center text-medium-emphasis py-8">这一栏没有票</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useDisplay } from "vuetify";
import type { Ticket } from "@/api/types";
import TicketCard from "./TicketCard.vue";
import { ticketColor } from "@/composables/labels";

const props = defineProps<{ tickets: Ticket[] }>();
defineEmits<{
  implement: [id: string];
  review: [id: string];
  diff: [id: string];
  feedback: [ticket: Ticket];
}>();

const { mdAndUp } = useDisplay();
const columns = [
  "pending",
  "ready",
  "implementing",
  "implemented",
  "reviewing",
  "blocked",
  "done",
];
const filter = ref("active");

const byState = computed(() => {
  const map: Record<string, Ticket[]> = {};
  for (const c of columns) map[c] = [];
  for (const t of props.tickets) {
    const state = t.state || "pending";
    if (!map[state]) map[state] = [];
    map[state].push(t);
  }
  return map;
});

const filterOptions = computed(() => {
  const active = props.tickets.filter((t) => t.state !== "done").length;
  return [
    { id: "active", label: "进行中", count: active },
    ...columns.map((c) => ({
      id: c,
      label: c,
      count: (byState.value[c] || []).length,
    })),
  ];
});

const filtered = computed(() => {
  if (filter.value === "active") return props.tickets.filter((t) => t.state !== "done");
  return byState.value[filter.value] || [];
});

function dotColor(col: string) {
  return ticketColor(col);
}
</script>

<style scoped>
.board {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  padding-bottom: 8px;
}
.col {
  flex: 1 1 180px;
  min-width: 170px;
  max-width: 235px;
  scroll-snap-align: start;
  background: rgba(var(--v-theme-surface-variant), 0.75);
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  min-height: 16rem;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
  padding: 0.6rem 0.5rem;
}
.col-header {
  font-size: 11.5px;
  font-weight: 700;
  text-transform: uppercase;
  color: rgb(var(--v-theme-on-surface-variant));
  letter-spacing: 0.04em;
}
.col-count-badge {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 10px;
  background: rgba(var(--v-theme-surface), 0.9);
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
</style>

