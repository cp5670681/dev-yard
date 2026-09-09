import { ref } from "vue";

const KEY = "dev-yard.recent-jiras";
const MAX = 8;

function load(): string[] {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export const recentJiras = ref<string[]>(load());

export function touchRecent(jira: string) {
  const key = jira.trim();
  if (!key) return;
  const next = [key, ...recentJiras.value.filter((j) => j !== key)].slice(0, MAX);
  recentJiras.value = next;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}
