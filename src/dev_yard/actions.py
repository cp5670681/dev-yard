"""Single source of truth for built-in board actions and job dispatch.

Before this module the action ids were spread across `web.app.ACTIONS`,
`web.jobs._HOST_JOB_ACTIONS`, `web.jobs._TICKET_ACTIONS` and
`web.board.BUILTIN_ACTION_IDS`, and they had already drifted. Everything now
derives from the tables below.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    id: str
    label: str


# Every built-in board button, in pipeline order. Includes `fill-test-report`,
# which is a UI flow (POST /test-report) rather than a background job.
BOARD_ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("open", "重新抽取"),
    ActionSpec("import", "外部导入"),
    ActionSpec("change", "轻量变更"),
    ActionSpec("grill", "对齐"),
    ActionSpec("reset-grill", "重置对齐"),
    ActionSpec("spec", "写规约"),
    ActionSpec("tickets", "拆票"),
    ActionSpec("freeze", "冻结 worktree"),
    ActionSpec("implement", "实现 ready 票"),
    ActionSpec("review", "审查"),
    ActionSpec("contract", "契约审查"),
    ActionSpec("fix-contract", "按契约修"),
    ActionSpec("submit-test", "提测"),
    ActionSpec("qa-design", "设计用例"),
    ActionSpec("qa-review", "审核用例"),
    ActionSpec("qa-run", "执行用例"),
    ActionSpec("fill-test-report", "提 bug"),
    ActionSpec("fix-test", "修 bug"),
    ActionSpec("push", "推送到远端"),
    ActionSpec("sync", "同步远端"),
    ActionSpec("reset-phase", "重置阶段"),
)

BOARD_ACTION_IDS: frozenset[str] = frozenset(a.id for a in BOARD_ACTIONS)
ACTION_LABELS: dict[str, str] = {a.id: a.label for a in BOARD_ACTIONS}

# Actions that can be dispatched as a background Job. `fill-test-report` is a
# UI flow, not a job.
JOB_ACTIONS: frozenset[str] = BOARD_ACTION_IDS - {"fill-test-report"}

# Job actions with a dedicated branch in `jobs.default_execute`.
HOST_JOB_ACTIONS: frozenset[str] = JOB_ACTIONS

# Job actions that operate on a subset of tickets.
TICKET_ACTIONS: frozenset[str] = frozenset(
    {"implement", "review", "fix-contract", "fix-test"}
)
