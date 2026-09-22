from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dev_yard import attachments, grill_round, paths
from dev_yard import status as st
from dev_yard.actions import ACTION_LABELS, BOARD_ACTION_IDS
from dev_yard.config import load_repos, resolve_freeze_branch
from dev_yard.service import GRILL_SKELETON, REQ_SKELETON, SPEC_SKELETON, TICKETS_SKELETON
from dev_yard.tickets import load_tickets

DOC_FILES = {
    "requirement": "REQUIREMENT.md",
    "grill": "GRILL.md",
    "spec": "SPEC.md",
    "tickets": "TICKETS.md",
}

PIPELINE = (
    "open",
    "grill",
    "spec",
    "tickets",
    "freeze",
    "implement",
    "review",
    "testing",
    "done",
)
BUILTIN_ACTION_IDS = BOARD_ACTION_IDS

_SKELETONS = {
    "REQUIREMENT.md": lambda jira: REQ_SKELETON.format(key=jira, title=jira, body=""),
    "GRILL.md": lambda jira: GRILL_SKELETON.format(key=jira),
    "SPEC.md": lambda jira: SPEC_SKELETON.format(key=jira),
    "TICKETS.md": lambda jira: TICKETS_SKELETON.format(key=jira),
}

_IMPLEMENT_STATES = {"ready", "blocked", "implementing"}
_REVIEW_STATES = {"implemented", "reviewing", "blocked"}
_BEYOND_IMPLEMENT = {"implemented", "reviewing", "done"}


@dataclass
class Action:
    id: str
    label: str
    enabled: bool
    reason: str = ""
    stage: str = ""


# Which pipeline stage each board action belongs to, so the web UI can group the
# action row instead of dumping every button in one flat list. Anything not
# listed here (push/sync/change/reset-phase, plugin stages) falls back to
# "utility": cross-stage actions that don't belong to one pipeline step.
ACTION_STAGES = {
    "open": "open",
    "grill": "grill",
    "reset-grill": "grill",
    "spec": "spec",
    "tickets": "tickets",
    "freeze": "freeze",
    "implement": "implement",
    "review": "review",
    "contract": "review",
    "fix-contract": "review",
    "submit-test": "testing",
    "qa-review": "testing",
    "run-test": "testing",
    "fill-test-report": "testing",
    "fix-test": "testing",
}

# Plugin stages gate on a *phase* (open/frozen/testing/done), which is a coarser
# vocabulary than the pipeline stages above. Translate so plugin buttons group
# with the matching builtin stage instead of spawning orphan buckets like
# "frozen" that the UI has no label or ordering for.
PHASE_TO_STAGE = {
    "open": "open",
    "frozen": "freeze",
    "testing": "testing",
    "done": "done",
}


@dataclass
class DocView:
    slug: str
    filename: str
    exists: bool
    filled: bool
    text: str


@dataclass
class TicketView:
    id: str
    title: str
    repo: str
    state: str
    depends_on: list[str]
    parallel: bool
    child_worktree: str | None
    last_summary: str | None
    can_implement: bool
    can_review: bool
    worktree: str | None = None
    source: str = ""
    finding: str = ""


@dataclass
class Step:
    id: str
    done: bool
    current: bool = False


@dataclass
class ReqSummary:
    jira: str
    phase: str
    next_label: str
    title: str | None = None
    ticket_counts: dict[str, int] = field(default_factory=dict)
    ticket_total: int = 0
    ticket_done: int = 0
    contract: str | None = None


@dataclass
class ReqDetail:
    jira: str
    phase: str
    next_label: str
    title: str | None
    docs: list[DocView]
    tickets: list[TicketView]
    actions: list[Action]
    steps: list[Step]
    worktrees: list[str]
    assets: list[str]
    uploads: list[str]
    contract: str | None
    contract_summary: str | None
    test: dict | None = None
    repos: list[str] = field(default_factory=list)
    stage_runs: dict = field(default_factory=dict)
    qa: dict | None = None
    branch: str = ""
    changes: list[dict] = field(default_factory=list)


def parse_requirement_title(text: str, jira: str) -> str | None:
    """Prefer the Jira Summary table cell; else the lede under `# KEY`."""
    summary: str | None = None
    lede: str | None = None
    after_h1 = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if (
                len(cells) >= 2
                and cells[0].lower() == "summary"
                and cells[1]
                and cells[1] not in {"---", "内容"}
                and cells[1] != jira
            ):
                summary = cells[1]
            continue
        if line.startswith("#") and not line.startswith("##"):
            after_h1 = True
            heading = line.lstrip("#").strip()
            if heading and heading != jira and lede is None:
                lede = heading
            continue
        if after_h1 and line.startswith("##"):
            after_h1 = False
            continue
        if after_h1 and line != jira and lede is None:
            lede = line
    return summary or lede


def list_requirements(root: Path) -> list[ReqSummary]:
    out: list[ReqSummary] = []
    for p in paths.iter_req_dirs(root):
        detail = requirement_detail(root, p.name)
        if detail is None:
            continue
        counts: dict[str, int] = {}
        for t in detail.tickets:
            counts[t.state] = counts.get(t.state, 0) + 1
        out.append(
            ReqSummary(
                jira=detail.jira,
                phase=detail.phase,
                next_label=detail.next_label,
                title=detail.title,
                ticket_counts=counts,
                ticket_total=len(detail.tickets),
                ticket_done=sum(1 for t in detail.tickets if t.state == "done"),
                contract=detail.contract,
            )
        )
    return out


def requirement_detail(root: Path, jira: str) -> ReqDetail | None:
    if paths.is_reserved_req_name(jira):
        return None
    try:
        req = paths.req_dir(root, jira)
    except ValueError:
        return None
    if not paths.is_req_dir(req):
        return None
    data = st.load(root, jira)
    parsed = load_tickets(req)
    data = st.sync_tickets(data, parsed)
    st.refresh_ready(data)
    slots = st.tickets_map(data.get("tickets"))
    tickets: list[TicketView] = []
    for t in parsed:
        slot = slots.get(t.id) or {}
        state = slot.get("state") or "pending"
        tickets.append(
            TicketView(
                id=t.id,
                title=t.title,
                repo=t.repo,
                state=state,
                depends_on=t.depends_on,
                parallel=t.parallel,
                child_worktree=slot.get("child_worktree"),
                last_summary=slot.get("last_summary"),
                can_implement=state in _IMPLEMENT_STATES,
                can_review=state in _REVIEW_STATES,
                worktree=slot.get("worktree"),
                source=t.source,
                finding=t.finding,
            )
        )
    docs = [_doc_view(req, slug, filename, jira) for slug, filename in DOC_FILES.items()]
    req_doc = next((d for d in docs if d.slug == "requirement"), None)
    title = parse_requirement_title(req_doc.text if req_doc else "", jira)
    phase = data.get("phase") or "open"
    worktrees: list[str] = []
    wt_root = req / "worktrees"
    if wt_root.is_dir():
        worktrees = sorted(str(p) for p in wt_root.iterdir() if p.is_dir())
    sample_wt = next((Path(p) for p in worktrees if (Path(p) / ".git").exists()), None)
    assets = _list_assets(req)
    awaiting = _grill_awaiting(req)
    test = data.get("test") if isinstance(data.get("test"), dict) else None
    from dev_yard.qa_board import qa_detail_summary

    qa = qa_detail_summary(root, jira)
    next_label = _next_label(
        phase,
        docs,
        tickets,
        awaiting,
        data.get("contract_review"),
        test,
        has_qa_run=bool(qa.get("latest_run")),
    )
    steps = _steps(phase, docs, tickets, awaiting, data.get("contract_review"), test)
    detail = ReqDetail(
        jira=jira,
        phase=phase,
        next_label=next_label,
        title=title,
        docs=docs,
        tickets=tickets,
        actions=[],
        steps=steps,
        worktrees=worktrees,
        assets=assets,
        uploads=attachments.list_names(root, jira),
        contract=data.get("contract_review"),
        contract_summary=data.get("contract_summary"),
        test=test,
        repos=list(data.get("repos") or []),
        stage_runs=dict(data.get("stage_runs") or {}),
        qa=qa,
        branch=resolve_freeze_branch(root, jira, data, sample_wt),
        changes=st.changes(data),
    )
    detail.actions = available_actions(detail, root)
    return detail


def available_actions(detail: ReqDetail, root: Path) -> list[Action]:
    has_tickets = bool(detail.tickets)
    frozen = detail.phase in {"frozen", "done", "testing"}
    any_implement = any(t.can_implement for t in detail.tickets)
    any_review = any(t.can_review for t in detail.tickets)
    tickets_done = bool(detail.tickets) and all(t.state == "done" for t in detail.tickets)
    contract_ok = detail.contract == "passed"
    passed = st.test_passed({"test": detail.test, "phase": detail.phase})
    in_testing = detail.phase == "testing"
    resubmit_ok = False
    has_eligible = False
    if in_testing and not passed:
        from dev_yard import test_integrate

        proxy = {
            "branch": detail.branch,
            "test": detail.test,
            "tickets": {t.id: {"repo": t.repo} for t in detail.tickets},
        }
        has_eligible = bool(test_integrate.eligible_repos(root, proxy))
        resubmit_ok = has_eligible and test_integrate.has_new_changes(
            root, detail.jira, proxy
        )
    can_submit = (
        tickets_done
        and contract_ok
        and not passed
        and (not in_testing or resubmit_ok)
    )
    can_fill = (
        contract_ok
        and detail.phase in {"testing", "frozen"}
        and not st.test_passed({"test": detail.test})
    )
    can_fix_test = any(
        t.source == "test" and t.can_implement for t in detail.tickets
    )
    has_worktrees = bool(detail.worktrees)
    from dev_yard.qa_board import qa_config_reason

    qa_reason = qa_config_reason(root)
    qa_summary = detail.qa or {}
    review = qa_summary.get("review") or {}
    review_pending = bool(qa_summary.get("has_cases") and not review.get("approved"))
    # Same gates as run-test minus the review gate itself: the review action is
    # exactly what clears `review_pending`, so it must not require it.
    can_review_qa = (
        can_fill
        and has_worktrees
        and not qa_reason
        and detail.phase == "testing"
        and tickets_done
        and review_pending
    )
    if not can_fill:
        review_qa_reason = (
            "测试已通过"
            if st.test_passed({"test": detail.test})
            else "需要契约审查 passed，且已 freeze 或提测"
        )
    elif detail.phase != "testing":
        review_qa_reason = "先提测（dev-yard req submit-test）"
    elif not has_worktrees:
        review_qa_reason = "需要 freeze worktree"
    elif qa_reason:
        review_qa_reason = qa_reason
    elif not tickets_done:
        review_qa_reason = "还有未完成的票，先处理测试 bug"
    elif not qa_summary.get("has_cases"):
        review_qa_reason = "还没有用例，先点「自动测」设计"
    elif not review_pending:
        review_qa_reason = "用例已审核通过，可直接「自动测」"
    else:
        review_qa_reason = ""
    can_run_test = (
        can_fill
        and has_worktrees
        and not qa_reason
        and detail.phase == "testing"
        and tickets_done
    )
    if not can_fill:
        run_reason = (
            "测试已通过"
            if st.test_passed({"test": detail.test})
            else "需要契约审查 passed，且已 freeze 或提测"
        )
    elif detail.phase != "testing":
        run_reason = "先提测（dev-yard req submit-test）"
    elif not has_worktrees:
        run_reason = "需要 freeze worktree"
    elif qa_reason:
        run_reason = qa_reason
    elif not tickets_done:
        run_reason = "还有未完成的票，先处理测试 bug"
    elif review_pending:
        run_reason = "用例待审核：去测试页通过，或 dev-yard req test --approve"
    else:
        run_reason = ""
    can_fix_contract = frozen and (
        detail.contract == "failed"
        or any(t.source == "contract" and t.can_implement for t in detail.tickets)
    )
    can_push = (detail.phase in {"frozen", "done", "testing"}) and has_worktrees
    registered = load_repos(root)
    can_sync = bool(registered)
    sync_reason = (
        ""
        if can_sync
        else "先登记仓库"
    )
    builtin = [
        Action(
            "open",
            ACTION_LABELS["open"],
            detail.phase == "open",
            ""
            if detail.phase == "open"
            else f"当前 phase={detail.phase}，请先「重置阶段」",
        ),
        Action(
            "reset-phase",
            ACTION_LABELS["reset-phase"],
            detail.phase != "open",
            ""
            if detail.phase != "open"
            else "已在 open 阶段，无需重置",
        ),
        Action(
            "change",
            ACTION_LABELS["change"],
            has_worktrees and detail.phase in {"frozen", "testing"},
            ""
            if has_worktrees and detail.phase in {"frozen", "testing"}
            else (
                "需要先 freeze 创建 worktree"
                if not has_worktrees
                else "已完成的需求改动属于方案级，本次不支持"
                if detail.phase == "done"
                else "在 open 阶段直接改文档后重跑对齐/写规约/拆票"
            ),
        ),
        Action("grill", ACTION_LABELS["grill"], True),
        Action(
            "reset-grill",
            ACTION_LABELS["reset-grill"],
            detail.phase == "open",
            ""
            if detail.phase == "open"
            else "对齐只在 open 阶段；如需回到 open 用「重置阶段」",
        ),
        Action("spec", ACTION_LABELS["spec"], True),
        Action("tickets", ACTION_LABELS["tickets"], True),
        Action(
            "freeze",
            ACTION_LABELS["freeze"],
            has_tickets and detail.phase not in {"testing", "done"},
            ""
            if has_tickets and detail.phase not in {"testing", "done"}
            else (
                "TICKETS.md 里还没有带 repo 的票"
                if not has_tickets
                else "提测/完成后回退 freeze 请用 CLI --force"
            ),
        ),
        Action(
            "implement",
            ACTION_LABELS["implement"],
            frozen and any_implement,
            "" if frozen and any_implement else "需要先 freeze，且有 ready/blocked 票",
        ),
        Action(
            "review",
            ACTION_LABELS["review"],
            any_review,
            "" if any_review else "没有处于可审查状态的票",
        ),
        Action(
            "contract",
            ACTION_LABELS["contract"],
            frozen,
            "" if frozen else "需要先 freeze",
        ),
        Action(
            "fix-contract",
            ACTION_LABELS["fix-contract"],
            can_fix_contract,
            ""
            if can_fix_contract
            else "需要契约审查 failed，或已有就绪的契约 bug 票",
        ),
        Action(
            "submit-test",
            "重新提测" if in_testing and can_submit else ACTION_LABELS["submit-test"],
            can_submit,
            ""
            if can_submit
            else (
                "没有新的改动"
                if in_testing and has_eligible
                else "已在提测阶段"
                if in_testing
                else "测试报告已通过"
                if passed
                else "需要全部票 done 且契约审查 passed"
            ),
        ),
        Action(
            "run-test",
            ACTION_LABELS["run-test"],
            can_run_test,
            run_reason,
        ),
        Action(
            "qa-review",
            ACTION_LABELS["qa-review"],
            can_review_qa,
            review_qa_reason,
        ),
        Action(
            "fill-test-report",
            ACTION_LABELS["fill-test-report"],
            can_fill,
            ""
            if can_fill
            else (
                "测试已通过"
                if st.test_passed({"test": detail.test})
                else "需要契约审查 passed，且已 freeze 或提测"
            ),
        ),
        Action(
            "fix-test",
            ACTION_LABELS["fix-test"],
            can_fix_test,
            "" if can_fix_test else "没有就绪的测试 bug 票",
        ),
        Action(
            "push",
            ACTION_LABELS["push"],
            can_push,
            "" if can_push else "需要先 freeze 创建 worktree",
        ),
        Action(
            "sync",
            ACTION_LABELS["sync"],
            can_sync,
            sync_reason
            if not can_sync
            else (
                "fetch 登记仓；已冻结则把 worktree 快进/合并到 origin/<default_base>"
            ),
        ),
    ]
    for a in builtin:
        a.stage = ACTION_STAGES.get(a.id, "utility")

    from dev_yard.stages import load_registry

    for spec in sorted(load_registry(root).values(), key=lambda s: s.order):
        if spec.builtin or spec.name in BUILTIN_ACTION_IDS:
            continue
        enabled = (detail.phase == spec.requires_phase) if spec.requires_phase else True
        reason = (
            ""
            if enabled
            else f"需要 phase={spec.requires_phase}（当前 {detail.phase}）"
        )
        builtin.append(
            Action(
                spec.name,
                spec.title or spec.name,
                enabled,
                reason,
                PHASE_TO_STAGE.get(spec.requires_phase or "", "utility"),
            )
        )
    return builtin


def save_doc(root: Path, jira: str, slug: str, text: str) -> Path:
    if slug not in DOC_FILES:
        raise ValueError(f"unknown doc {slug}")
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}")
    path = req / DOC_FILES[slug]
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def asset_file(root: Path, jira: str, name: str) -> Path:
    req = paths.req_dir(root, jira)
    assets = (req / "assets").resolve()
    target = (assets / name).resolve()
    try:
        target.relative_to(assets)
    except ValueError as e:
        raise ValueError(f"invalid asset {name!r}") from e
    if not target.is_file():
        raise FileNotFoundError(name)
    return target


def attachment_file(root: Path, jira: str, name: str) -> Path:
    return attachments.resolve(root, jira, name)


def list_repos(root: Path) -> list[dict[str, str]]:
    out = []
    for alias, repo in load_repos(root).items():
        out.append(
            {
                "alias": alias,
                "url": repo.url,
                "default_base": repo.default_base,
                "role": repo.role,
                "path": str(repo.path) if repo.path else "",
                "provider": repo.provider or "",
                "model": repo.model or "",
                "test_branch": repo.test_branch or "",
            }
        )
    return out


def _doc_view(req: Path, slug: str, filename: str, jira: str) -> DocView:
    path = req / filename
    exists = path.is_file()
    text = path.read_text(encoding="utf-8") if exists else ""
    make_skel = _SKELETONS.get(filename)
    skeleton = make_skel(jira).strip() if make_skel else ""
    filled = exists and text.strip() != skeleton and bool(text.strip())
    if filename == "TICKETS.md" and filled:
        filled = bool(load_tickets(req))
    return DocView(slug=slug, filename=filename, exists=exists, filled=filled, text=text)


def _list_assets(req: Path) -> list[str]:
    d = req / "assets"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def _grill_awaiting(req: Path) -> bool:
    rnd = grill_round.load_round(req)
    return rnd is not None and rnd.awaiting()


def _next_label(
    phase: str,
    docs: list[DocView],
    tickets: list[TicketView],
    grill_awaiting: bool = False,
    contract: str | None = None,
    test: dict | None = None,
    has_qa_run: bool = False,
) -> str:
    by_slug = {d.slug: d for d in docs}
    if st.pipeline_complete({"phase": phase, "test": test}):
        return "done"
    ready_bugs = any(
        t.source == "test" and t.state in _IMPLEMENT_STATES for t in tickets
    )
    open_bugs = any(t.source == "test" and t.state != "done" for t in tickets)
    if ready_bugs:
        return "fix-test"
    if open_bugs:
        return "implement"
    if phase == "testing":
        if not has_qa_run:
            return "run-test"
        return "fill-test-report"
    if tickets and all(t.state == "done" for t in tickets):
        return "submit-test" if contract == "passed" else "contract"
    if any(t.state in _REVIEW_STATES for t in tickets):
        return "review"
    if phase in {"frozen", "done", "testing"}:
        return "implement"
    if tickets:
        return "freeze"
    if grill_awaiting:
        return "grill"
    if by_slug["spec"].filled:
        return "tickets"
    if by_slug["grill"].filled:
        return "spec"
    return "grill"


def _steps(
    phase: str,
    docs: list[DocView],
    tickets: list[TicketView],
    grill_awaiting: bool = False,
    contract: str | None = None,
    test: dict | None = None,
) -> list[Step]:
    by_slug = {d.slug: d for d in docs}
    tickets_done = bool(tickets) and all(t.state == "done" for t in tickets)
    qa_passed = st.test_passed({"test": test})
    flags = {
        "open": by_slug["requirement"].exists,
        "grill": by_slug["grill"].filled and not grill_awaiting,
        "spec": by_slug["spec"].filled,
        "tickets": bool(tickets),
        "freeze": phase in {"frozen", "done", "testing"},
        "implement": bool(tickets) and all(t.state in _BEYOND_IMPLEMENT for t in tickets),
        "review": tickets_done,
        "testing": phase == "testing" or (phase == "done" and qa_passed),
        "done": phase == "done" and qa_passed,
    }
    current = "done"
    for sid in PIPELINE:
        if not flags[sid]:
            current = sid
            break
    if tickets_done and contract != "passed" and phase not in {"testing", "done"}:
        current = "review"
    if phase == "testing":
        current = "testing"
    if tickets_done and contract == "passed" and phase in {"frozen", "done"} and not qa_passed:
        current = "testing"
    return [
        Step(id=sid, done=flags[sid], current=(sid == current and not flags["done"]))
        for sid in PIPELINE
    ]
