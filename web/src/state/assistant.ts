import { ref } from "vue";

export const assistantOpen = ref(false);

export function openAssistant() {
  assistantOpen.value = true;
}

export function closeAssistant() {
  assistantOpen.value = false;
}
