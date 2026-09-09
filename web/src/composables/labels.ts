export const PHASE_COLOR: Record<string, string> = {
  open: "grey",
  grill: "primary",
  spec: "info",
  tickets: "cyan",
  freeze: "purple",
  implement: "warning",
  review: "secondary",
  done: "success",
};

export const TICKET_COLOR: Record<string, string> = {
  pending: "grey",
  ready: "info",
  implementing: "primary",
  implemented: "purple",
  reviewing: "cyan",
  blocked: "error",
  done: "success",
};

export const STEP_LABELS: Record<string, string> = {
  open: "抽取",
  grill: "对齐",
  spec: "Spec",
  tickets: "拆票",
  freeze: "冻结",
  implement: "实现",
  review: "审查",
  done: "完成",
};

export const ACTION_LABELS: Record<string, string> = {
  ...STEP_LABELS,
  spec: "写 Spec",
  freeze: "冻结 worktree",
  contract: "契约审查",
  repo_add: "仓库 · clone",
};

export function phaseColor(phase: string) {
  return PHASE_COLOR[phase] || "grey";
}

export function ticketColor(state: string) {
  return TICKET_COLOR[state] || "grey";
}
