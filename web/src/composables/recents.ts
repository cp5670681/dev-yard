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

function persist(next: string[]) {
  recentJiras.value = next;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}

export function touchRecent(jira: string) {
  const key = jira.trim();
  if (!key) return;
  persist([key, ...recentJiras.value.filter((j) => j !== key)].slice(0, MAX));
}

export function forgetRecent(jira: string) {
  const key = jira.trim();
  if (!key) return;
  const next = recentJiras.value.filter((j) => j !== key);
  if (next.length === recentJiras.value.length) return;
  persist(next);
}

export function pruneRecents(valid: Iterable<string>) {
  const keep = new Set(valid);
  const next = recentJiras.value.filter((j) => keep.has(j));
  if (next.length === recentJiras.value.length) return;
  persist(next);
}
