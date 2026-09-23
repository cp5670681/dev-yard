export const PHASE_COLOR: Record<string, string> = {
  open: "grey",
  grill: "primary",
  spec: "info",
  tickets: "secondary",
  freeze: "secondary",
  frozen: "secondary",
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
  assistant: "助手",
};

export const ACTION_LABELS: Record<string, string> = {
  ...STEP_LABELS,
  open: "重新抽取",
  spec: "写规约",
  freeze: "冻结 worktree",
  contract: "契约审查",
  change: "轻量变更",
  "fix-contract": "按契约修",
  "submit-test": "提测",
  "qa-design": "设计用例",
  "qa-review": "审核用例",
  "qa-run": "执行用例",
  "fill-test-report": "提 bug",
  "fix-test": "修 bug",
  push: "推送到远端",
  sync: "同步远端",
  "reset-phase": "重置阶段",
  "reset-grill": "重置对齐",
  repo_add: "仓库 · clone",
};

// Display order for the action groups on the requirement page. Mirrors the
// pipeline steps, plus "utility" for cross-stage maintenance actions
// (push/sync/change/reset-phase) and plugin stages with no builtin peer.
export const STAGE_ORDER = [
  "open",
  "grill",
  "spec",
  "tickets",
  "freeze",
  "implement",
  "review",
  "testing",
  "done",
  "utility",
];

export const STAGE_LABELS: Record<string, string> = {
  open: "抽取",
  grill: "对齐",
  spec: "规约",
  tickets: "拆票",
  freeze: "冻结",
  implement: "实现",
  review: "审查",
  testing: "提测",
  done: "完成",
  utility: "工具",
};

export const QA_STATE_LABELS: Record<string, string> = {
  pending: "待测试",
  ready: "就绪",
  running: "测试中",
  passed: "通过",
  failed: "失败",
  blocked: "阻塞",
  skipped: "跳过",
};

export const QA_STATE_COLOR: Record<string, string> = {
  pending: "grey",
  ready: "info",
  running: "primary",
  passed: "success",
  failed: "error",
  blocked: "warning",
  skipped: "grey-darken-1",
};

export function qaStateColor(state: string) {
  return QA_STATE_COLOR[state] || "grey";
}

export function phaseColor(phase: string) {
  return PHASE_COLOR[phase] || "grey";
}

export function ticketColor(state: string) {
  return TICKET_COLOR[state] || "grey";
}
