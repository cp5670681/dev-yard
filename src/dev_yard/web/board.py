from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dev_yard import paths, status as st
from dev_yard.config import load_repos
from dev_yard.service import GRILL_SKELETON, REQ_SKELETON, SPEC_SKELETON, TICKETS_SKELETON
from dev_yard.tickets import load_tickets

DOC_FILES = {
    "requirement": "REQUIREMENT.md",
    "grill": "GRILL.md",
    "spec": "SPEC.md",
    "tickets": "TICKETS.md",
}

PIPELINE = ("open", "grill", "spec", "tickets", "freeze", "implement", "review", "done")

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
    ticket_counts: dict[str, int] = field(default_factory=dict)
    ticket_total: int = 0
    ticket_done: int = 0


@dataclass
class ReqDetail:
    jira: str
    phase: str
    next_label: str
    docs: list[DocView]
    tickets: list[TicketView]
    actions: list[Action]
    steps: list[Step]
    worktrees: list[str]
    assets: list[str]
    contract: str | None
    contract_summary: str | None


def list_requirements(root: Path) -> list[ReqSummary]:
    rd = paths.reqs_dir(root)
    if not rd.exists():
        return []
    out: list[ReqSummary] = []
    for p in sorted(rd.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name.startswith("."):
            continue
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
                ticket_counts=counts,
                ticket_total=len(detail.tickets),
                ticket_done=sum(1 for t in detail.tickets if t.state == "done"),
            )
        )
    return out


def requirement_detail(root: Path, jira: str) -> ReqDetail | None:
    req = paths.req_dir(root, jira)
    if not req.is_dir():
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
    phase = data.get("phase") or "open"
    worktrees: list[str] = []
    wt_root = req / "worktrees"
    if wt_root.is_dir():
        worktrees = sorted(str(p) for p in wt_root.iterdir() if p.is_dir())
    assets = _list_assets(req)
    next_label = _next_label(phase, docs, tickets)
    steps = _steps(phase, docs, tickets)
    detail = ReqDetail(
        jira=jira,
        phase=phase,
        next_label=next_label,
        docs=docs,
        tickets=tickets,
        actions=[],
        steps=steps,
        worktrees=worktrees,
        assets=assets,
        contract=data.get("contract_review"),
        contract_summary=data.get("contract_summary"),
    )
    detail.actions = available_actions(detail)
    return detail


def available_actions(detail: ReqDetail) -> list[Action]:
    has_tickets = bool(detail.tickets)
    frozen = detail.phase in {"frozen", "done"}
    any_implement = any(t.can_implement for t in detail.tickets)
    any_review = any(t.can_review for t in detail.tickets)
    return [
        Action(
            "open",
            "重新抽取",
            True,
            "phase 已过 open 时需勾选重置，否则拒绝；重置会删 assets/",
        ),
        Action("grill", "Grill", True),
        Action("spec", "写 Spec", True),
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
            }
        )
    return out


def _doc_view(req: Path, slug: str, filename: str, jira: str) -> DocView:
    path = req / filename
    exists = path.is_file()
    text = path.read_text() if exists else ""
    skeleton = _SKELETONS[filename](jira).strip()
    filled = exists and text.strip() != skeleton and bool(text.strip())
    if filename == "TICKETS.md" and filled:
        filled = bool(load_tickets(req))
    return DocView(slug=slug, filename=filename, exists=exists, filled=filled, text=text)


def _list_assets(req: Path) -> list[str]:
    d = req / "assets"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def _next_label(phase: str, docs: list[DocView], tickets: list[TicketView]) -> str:
    by_slug = {d.slug: d for d in docs}
    if phase == "done":
        return "done"
    if tickets and all(t.state == "done" for t in tickets):
        return "contract" if phase != "done" else "done"
    if any(t.state in _REVIEW_STATES for t in tickets):
        return "review"
    if phase in {"frozen", "done"}:
        return "implement"
    if tickets:
        return "freeze"
    if by_slug["spec"].filled:
        return "tickets"
    if by_slug["grill"].filled:
        return "spec"
    return "grill"


def _steps(phase: str, docs: list[DocView], tickets: list[TicketView]) -> list[Step]:
    by_slug = {d.slug: d for d in docs}
    flags = {
        "open": by_slug["requirement"].exists,
        "grill": by_slug["grill"].filled,
        "spec": by_slug["spec"].filled,
        "tickets": bool(tickets),
        "freeze": phase in {"frozen", "done"},
        "implement": bool(tickets) and all(t.state in _BEYOND_IMPLEMENT for t in tickets),
        "review": bool(tickets) and all(t.state == "done" for t in tickets),
        "done": phase == "done",
    }
    current = "done"
    for sid in PIPELINE:
        if not flags[sid]:
            current = sid
            break
    return [Step(id=sid, done=flags[sid], current=(sid == current and not flags["done"])) for sid in PIPELINE]
