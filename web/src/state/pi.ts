import { ref } from "vue";

export const piTarget = ref<{ jobId: string; run: number } | null>(null);

export function openPi(jobId: string, run = 0) {
  piTarget.value = { jobId, run };
}

export function closePi() {
  piTarget.value = null;
}
