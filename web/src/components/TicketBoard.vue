<template>
  <div>
    <template v-if="mdAndUp">
      <div class="board">
        <div v-for="col in columns" :key="col" class="col">
          <div class="d-flex align-center mb-2 text-caption text-medium-emphasis">
            <v-badge :color="dotColor(col)" dot inline class="mr-2" />
            {{ col }}
            <v-spacer />
            {{ (byState[col] || []).length }}
          </div>
          <TicketCard
            v-for="t in byState[col] || []"
            :key="t.id"
            class="mb-2"
            :ticket="t"
            @implement="$emit('implement', $event)"
            @review="$emit('review', $event)"
          />
          <div v-if="!(byState[col] || []).length" class="text-center text-medium-emphasis py-6">
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
defineEmits<{ implement: [id: string]; review: [id: string] }>();

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
  gap: 12px;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  padding-bottom: 8px;
}
.col {
  flex: 0 0 260px;
  scroll-snap-align: start;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  min-height: 12rem;
  max-height: calc(100vh - 280px);
  overflow-y: auto;
  padding: 0.7rem;
}
</style>
