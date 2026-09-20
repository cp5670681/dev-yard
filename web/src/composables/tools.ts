import {
  mdiConsoleLine,
  mdiFileDocumentOutline,
  mdiFolderOutline,
  mdiFolderSearchOutline,
  mdiMagnify,
  mdiPencilOutline,
  mdiRobotOutline,
  mdiTuneVariant,
  mdiWeb,
} from "@mdi/js";
import type { PiEntry } from "@/api/types";

const TOOL_ICONS: Record<string, string> = {
  read: mdiFileDocumentOutline,
  write: mdiPencilOutline,
  edit: mdiPencilOutline,
  grep: mdiMagnify,
  find: mdiFolderSearchOutline,
  glob: mdiFolderSearchOutline,
  ls: mdiFolderOutline,
  bash: mdiConsoleLine,
  shell: mdiConsoleLine,
  web_fetch: mdiWeb,
  webfetch: mdiWeb,
  fetch: mdiWeb,
  task: mdiRobotOutline,
  agent: mdiRobotOutline,
};

const TOOL_LABELS: Record<string, string> = {
  read: "读取",
  write: "写入",
  edit: "编辑",
  grep: "搜索",
  find: "查找",
  glob: "匹配",
  ls: "列目录",
  bash: "命令",
  shell: "命令",
  web_fetch: "抓取",
  webfetch: "抓取",
  fetch: "抓取",
  task: "子任务",
  agent: "子任务",
};

export function toolIcon(name?: string) {
  return TOOL_ICONS[name || ""] || mdiTuneVariant;
}

export function toolLabel(name?: string) {
  return TOOL_LABELS[name || ""] || name || "工具";
}

export function stepsRunning(entry: PiEntry) {
  return Boolean(entry.steps?.some((step) => step.status === "running"));
}

export function stepsTitle(entry: PiEntry) {
  const count = entry.steps?.length || 0;
  return stepsRunning(entry) ? `正在执行 · ${count} 步` : `过程 · ${count} 步`;
}

export function formatMs(ms?: number) {
  if (!ms || ms < 0) return "";
  const sec = ms / 1000;
  if (sec < 1) return "<1";
  if (sec < 10) return sec.toFixed(1);
  return String(Math.round(sec));
}
