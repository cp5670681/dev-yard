<template>
  <v-form class="mt-4" @submit.prevent="submit">
    <div class="text-subtitle-1 mb-2">第 {{ grill.round }} 轮</div>
    <p v-if="grill.intro" class="text-medium-emphasis">{{ grill.intro }}</p>
    <v-card
      v-for="q in grill.questions"
      :key="q.id"
      variant="tonal"
      class="mb-3 pa-3"
    >
      <div class="font-weight-medium mb-2">{{ q.id }} {{ q.title || "" }}</div>
      <div v-if="q.body" class="text-body-2 text-medium-emphasis mb-3" style="white-space: pre-wrap">
        {{ q.body }}
      </div>
      <v-radio-group v-if="q.options?.length" v-model="picked[q.id]" hide-details>
        <v-radio
          v-for="opt in q.options"
          :key="opt.id"
          :value="opt.id"
          :label="`${opt.id}. ${opt.label}`"
        />
        <v-radio value="__custom__" label="自定义" />
      </v-radio-group>
      <v-textarea
        v-model="texts[q.id]"
        :placeholder="q.options?.length ? '选「自定义」时填写' : '填写或改写建议'"
        rows="3"
        auto-grow
        hide-details
        class="mt-2"
      />
    </v-card>
    <div class="d-flex ga-2">
      <v-btn type="submit" color="primary" :loading="busy">提交本轮</v-btn>
      <v-btn variant="tonal" :disabled="busy" @click="acceptSuggested">按建议提交</v-btn>
    </div>
  </v-form>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { submitAnswers } from "@/api/client";
import type { GrillRound } from "@/api/types";

const props = defineProps<{ jobId: string; grill: GrillRound }>();
const emit = defineEmits<{ submitted: [] }>();

const picked = reactive<Record<string, string>>({});
const texts = reactive<Record<string, string>>({});
const busy = ref(false);

for (const q of props.grill.questions) {
  picked[q.id] = q.suggested || (q.options?.length ? q.options[0].id : "__custom__");
  texts[q.id] = q.options?.length ? "" : q.suggested_text || "";
}

function collect() {
  return props.grill.questions.map((q) => ({
    id: q.id,
    option: picked[q.id] || q.suggested || "__custom__",
    text: (texts[q.id] || "").trim(),
  }));
}

function acceptSuggested() {
  for (const q of props.grill.questions) {
    if (q.suggested) picked[q.id] = q.suggested;
    if (!q.options?.length) texts[q.id] = q.suggested_text || "";
  }
  void submit();
}

async function submit() {
  busy.value = true;
  try {
    await submitAnswers(props.jobId, collect());
    emit("submitted");
  } finally {
    busy.value = false;
  }
}
</script>
