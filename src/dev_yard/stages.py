from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RESERVED_STAGE_NAMES = frozenset(
    {
        "init",
        "repo",
        "req",
        "ticket",
        "web",
        "status",
        "push",
        "run",
        "stages",
        "review-override",
        "version",
    }
)
ALLOWED_TOOLS = ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp")
BUILTIN_PHASES = ("open", "frozen", "testing", "done")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class StageSpec:
    name: str
    skill: str
    bundles: tuple[str, ...]
    tools: tuple[str, ...]
    protects: tuple[str, ...] = ()
    requires_phase: str | None = None
    sets_phase: str | None = None
    lists_sources: bool = False
    order: int = 50
    builtin: bool = False
    guidance: str = ""
    skill_dir: Path | None = None
    title: str = ""


_GUIDANCE = {
    "open": "Write REQUIREMENT.md and optional assets/ for this requirement only.",
    "grill": (
        "Write only reqs/<REQ>/GRILL.md. If a term or ADR is settled, write "
        "reqs/CONTEXT.md and reqs/docs/adr/ (shared across requirements). "
        "Do not write workspace-root CONTEXT.md or docs/adr. "
        "Do not copy them into source clones or freeze worktrees. "
        "Do not write SPEC.md or TICKETS.md. "
        "Focus on P0/P1 decisions, apply sensible defaults for minor details, "
        "group questions by theme for large tasks, and cap grilling strictly within 1-3 rounds."
    ),
    "spec": "Write only SPEC.md from GRILL.md. Do not interview. Do not write TICKETS.md.",
    "tickets": "Write only TICKETS.md from SPEC.md.",
    "implement": (
        "Write code in the current worktree only. "
        "Do not add CONTEXT.md or docs/adr to the business repo."
    ),
    "review": "Do not implement; report Standards and Spec axes.",
    "contract": "Do not implement; report Spec contract gaps across worktrees.",
}


def _spec(
    name: str,
    skill: str,
    bundles: tuple[str, ...],
    tools: tuple[str, ...],
    protects: tuple[str, ...] = (),
    sets_phase: str | None = None,
    lists_sources: bool = False,
    order: int = 50,
) -> StageSpec:
    return StageSpec(
        name=name,
        skill=skill,
        bundles=bundles,
        tools=tools,
        protects=protects,
        sets_phase=sets_phase,
        lists_sources=lists_sources,
        order=order,
        builtin=True,
        guidance=_GUIDANCE[name],
    )


BUILTIN_STAGES: dict[str, StageSpec] = {
    s.name: s
    for s in (
        _spec(
            "open",
            "fetch-requirement",
            ("fetch-requirement",),
            ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp"),
            protects=("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml"),
            sets_phase="open",
            order=10,
        ),
        _spec(
            "grill",
            "grill-with-docs",
            ("grill-with-docs", "grilling", "domain-modeling"),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("SPEC.md", "TICKETS.md"),
            lists_sources=True,
            order=20,
        ),
        _spec(
            "spec",
            "to-spec",
            ("to-spec",),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("TICKETS.md",),
            lists_sources=True,
            order=30,
        ),
        _spec(
            "tickets",
            "to-tickets",
            ("to-tickets",),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml"),
            lists_sources=True,
            order=40,
        ),
        _spec(
            "implement",
            "implement",
            ("implement", "tdd", "codebase-design"),
            ("read", "bash", "grep", "find", "ls", "edit", "write"),
            order=50,
        ),
        _spec(
            "review",
            "code-review",
            ("code-review",),
            ("read", "grep", "find", "ls"),
            order=55,
        ),
        _spec(
            "contract",
            "code-review",
            ("code-review",),
            ("read", "grep", "find", "ls"),
            order=56,
        ),
    )
}


def load_registry(root: Path) -> dict[str, StageSpec]:
    """Built-in stages merged with yard.yaml plugins (plugins override by name).

    Plugin loading joins in a later task; for now this returns builtin copies.
    """
    return dict(BUILTIN_STAGES)
