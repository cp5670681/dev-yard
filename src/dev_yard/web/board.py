from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dev_yard import grill_round, paths, status as st
from dev_yard.config import load_repos
from dev_yard.service import GRILL_SKELETON, REQ_SKELETON, SPEC_SKELETON, TICKETS_SKELETON
from dev_yard.tickets import load_tickets

DOC_FILES = {
    "requirement": "REQUIREMENT.md",
    "grill": "GRILL.md",
    "spec": "SPEC.md",
    "tickets": "TICKETS.md",
    "test-report": "TEST-REPORT.md",
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

_SKELETONS = {
    "REQUIREMENT.md": lambda jira: REQ_SKELETON.format(key=jira, title=jira, body=""),
    "GRILL.md": lambda jira: GRILL_SKELETON.format(key=jira),
    "SPEC.md": lambda jira: SPEC_SKELETON.format(key=jira),
    "TICKETS.md": lambda jira: TICKETS_SKELETON.format(key=jira),
}

_IMPLEMENT_STATES = {"ready", "blocked", "implementing"}
_REVIEW_STATES = {"implemented", "reviewing"}
_BEYOND_IMPLEMENT = {"implemented", "reviewing", "done"}


@dataclass
class Action:
    id: str
    label: str
    enabled: bool
    reason: str = ""


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
    contract: str | None
    contract_summary: str | None
    test: dict | None = None


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
    assets = _list_assets(req)
    awaiting = _grill_awaiting(req)
    test = data.get("test") if isinstance(data.get("test"), dict) else None
    next_label = _next_label(
        phase, docs, tickets, awaiting, data.get("contract_review"), test
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
        contract=data.get("contract_review"),
        contract_summary=data.get("contract_summary"),
        test=test,
    )
    detail.actions = available_actions(detail)
    return detail


def available_actions(detail: ReqDetail) -> list[Action]:
    has_tickets = bool(detail.tickets)
    frozen = detail.phase in {"frozen", "done", "testing"}
    any_implement = any(t.can_implement for t in detail.tickets)
    any_review = any(t.can_review for t in detail.tickets)
    tickets_done = bool(detail.tickets) and all(t.state == "done" for t in detail.tickets)
    contract_ok = detail.contract == "passed"
    test = detail.test or {}
    can_submit = (
        tickets_done
        and contract_ok
        and detail.phase != "testing"
        and not st.test_passed({"test": detail.test, "phase": detail.phase})
    )
    can_fill = detail.phase == "testing"
    can_fix_test = detail.phase == "testing" and test.get("latest_verdict") == "failed"
    return [
        Action(
            "open",
            "重新抽取",
            True,
            "phase 已过 open 时需勾选重置，否则拒绝；重置会删 assets/",
        ),
        Action("grill", "对齐", True),
        Action("spec", "写规约", True),
        Action("tickets", "拆票", True),
        Action(
            "freeze",
            "冻结 worktree",
            has_tickets,
            "" if has_tickets else "TICKETS.md 里还没有带 repo 的票",
        ),
        Action(
            "implement",
            "实现 ready 票",
            frozen and any_implement,
            "" if frozen and any_implement else "需要先 freeze，且有 ready/blocked 票",
        ),
        Action(
            "review",
            "审查 implemented",
            any_review,
            "" if any_review else "没有处于 implemented 的票",
        ),
        Action(
            "contract",
            "契约审查",
            frozen,
            "" if frozen else "需要先 freeze",
        ),
        Action(
            "fix-contract",
            "按契约修",
            frozen and bool(detail.contract_summary),
            ""
            if frozen and detail.contract_summary
            else "需要先 freeze，且已有契约审查摘要",
        ),
        Action(
            "submit-test",
            "提测",
            can_submit,
            ""
            if can_submit
            else (
                "已在提测阶段"
                if detail.phase == "testing"
                else "测试报告已通过"
                if st.test_passed({"test": detail.test})
                else "需要全部票 done 且契约审查 passed"
            ),
        ),
        Action(
            "fill-test-report",
            "填写测试报告",
            can_fill,
            "" if can_fill else "需要处于提测阶段",
        ),
        Action(
            "fix-test",
            "按测试报告修",
            can_fix_test,
            "" if can_fix_test else "需要提测阶段且最新报告为 failed",
        ),
    ]


def save_doc(root: Path, jira: str, slug: str, text: str) -> Path:
    if slug not in DOC_FILES:
        raise ValueError(f"unknown doc {slug}")
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise FileNotFoundError(f"missing {req}")
    path = req / DOC_FILES[slug]
    path.write_text(text if text.endswith("\n") else text + "\n")
    return path


def doc_path(root: Path, jira: str, slug: str) -> Path:
    if slug not in DOC_FILES:
        raise ValueError(f"unknown doc {slug}")
    return paths.req_dir(root, jira) / DOC_FILES[slug]


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
            }
        )
    return out


def _doc_view(req: Path, slug: str, filename: str, jira: str) -> DocView:
    path = req / filename
    exists = path.is_file()
    text = path.read_text() if exists else ""
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
) -> str:
    by_slug = {d.slug: d for d in docs}
    if st.pipeline_complete({"phase": phase, "test": test}):
        return "done"
    if phase == "testing":
        if (test or {}).get("latest_verdict") == "failed":
            return "fix-test"
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
