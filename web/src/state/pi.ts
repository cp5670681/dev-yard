import { ref } from "vue";
import { assistantOpen } from "./assistant";

export const piTarget = ref<{ jobId: string; run: number } | null>(null);

export function openPi(jobId: string, run = 0) {
  assistantOpen.value = false;
  piTarget.value = { jobId, run };
}

export function closePi() {
  piTarget.value = null;
}
