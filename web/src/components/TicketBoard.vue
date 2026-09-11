<template>
  <div>
    <template v-if="mdAndUp">
      <div class="ghx-board">
        <div class="ghx-column-headers">
          <div v-for="col in columns" :key="col" class="ghx-column-header">
            <span class="ghx-column-title">{{ stateLabel(col) }}</span>
            <span class="ghx-column-count">{{ (byState[col] || []).length }}</span>
          </div>
        </div>
        <div class="ghx-columns">
          <div v-for="col in columns" :key="col" class="ghx-column">
            <TicketCard
              v-for="t in byState[col] || []"
              :key="t.id"
              class="ghx-card-gap"
              :ticket="t"
              @implement="$emit('implement', $event)"
              @review="$emit('review', $event)"
              @diff="$emit('diff', $event)"
              @feedback="$emit('feedback', $event)"
            />
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
import { TICKET_STATE_LABELS } from "@/composables/labels";

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
      label: stateLabel(c),
      count: (byState.value[c] || []).length,
    })),
  ];
});

const filtered = computed(() => {
  if (filter.value === "active") return props.tickets.filter((t) => t.state !== "done");
  return byState.value[filter.value] || [];
});

function stateLabel(col: string) {
  return TICKET_STATE_LABELS[col] || col;
}

</script>

<style scoped>
.ghx-board {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 220px);
  overflow-x: auto;
  background: #fff;
  padding: 0 2px 8px;
}
:global(.v-theme--dark) .ghx-board {
  background: rgb(var(--v-theme-surface));
}
.ghx-column-headers,
.ghx-columns {
  display: grid;
  grid-template-columns: repeat(7, minmax(196px, 1fr));
  column-gap: 12px;
}
.ghx-column-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 10px 10px;
  border-bottom: 2px solid #c1c7d0;
  background: #fff;
}
.ghx-column-title {
  font-size: 14px;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface));
}
.ghx-column-count {
  font-size: 14px;
  color: rgb(var(--v-theme-on-surface-variant));
}
.ghx-column {
  background: #f4f5f7;
  min-height: 12rem;
  padding: 8px;
  border-radius: 0 0 2px 2px;
}
:global(.v-theme--dark) .ghx-column {
  background: #22272b;
}
:global(.v-theme--dark) .ghx-column-header {
  background: rgb(var(--v-theme-surface));
  border-bottom-color: rgba(255, 255, 255, 0.18);
}
.ghx-card-gap {
  margin-bottom: 8px;
}
.ghx-card-gap:last-child {
  margin-bottom: 0;
}
</style>
