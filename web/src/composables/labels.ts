export const PHASE_COLOR: Record<string, string> = {
  open: "grey",
  grill: "primary",
  spec: "info",
  tickets: "secondary",
  freeze: "secondary",
  implement: "primary",
  review: "secondary",
  testing: "warning",
  done: "success",
};

export const TICKET_COLOR: Record<string, string> = {
  pending: "grey",
  ready: "info",
  implementing: "primary",
  implemented: "secondary",
  reviewing: "secondary",
  blocked: "error",
  done: "success",
};

export const TICKET_STATE_LABELS: Record<string, string> = {
  pending: "待办",
  ready: "就绪",
  implementing: "实现中",
  implemented: "已实现",
  reviewing: "审查中",
  blocked: "阻塞",
  done: "完成",
};

export const STEP_LABELS: Record<string, string> = {
  open: "抽取",
  grill: "对齐",
  spec: "规约",
  tickets: "拆票",
  freeze: "冻结",
  implement: "实现",
  review: "审查",
  contract: "契约审查",
  testing: "提测",
  done: "完成",
};

export const ACTION_LABELS: Record<string, string> = {
  ...STEP_LABELS,
  spec: "写规约",
  freeze: "冻结 worktree",
  contract: "契约审查",
  "fix-contract": "按契约修",
  "submit-test": "提测",
  "fill-test-report": "填写测试报告",
  "fix-test": "按测试报告修",
  push: "推送到远端",
  repo_add: "仓库 · clone",
};

export function phaseColor(phase: string) {
  return PHASE_COLOR[phase] || "grey";
}

export function ticketColor(state: string) {
  return TICKET_COLOR[state] || "grey";
}
