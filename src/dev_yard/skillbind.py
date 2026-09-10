from __future__ import annotations

from pathlib import Path

from dev_yard import paths

SKILL_NAMES = {
    "open": "fetch-requirement",
    "grill": "grill-with-docs",
    "spec": "to-spec",
    "tickets": "to-tickets",
    "implement": "implement",
    "review": "code-review",
    "tdd": "tdd",
}

# Extra primitives pi must load with the entry skill.
SKILL_BUNDLES: dict[str, list[str]] = {
    "open": ["fetch-requirement"],
    "grill": ["grill-with-docs", "grilling", "domain-modeling"],
    "spec": ["to-spec"],
    "tickets": ["to-tickets"],
    "implement": ["implement", "tdd", "codebase-design"],
    "review": ["code-review"],
}


def skills_root(root: Path) -> Path:
    return root / ".pi" / "skills"


def load_skill(root: Path, name: str) -> str:
    key = SKILL_NAMES.get(name, name)
    path = skills_root(root) / key / "SKILL.md"
    if not path.exists():
        return f"(bound skill {key} not found at {path})"
    return path.read_text()


STAGE_WRITE = {
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
}


def session_prompt(
    root: Path, name: str, jira: str, extra: str = "", target: str = ""
) -> str:
    req = paths.req_dir(root, jira)
    ctx = paths.context_md(root)
    adr = paths.adr_dir(root)
    entry = SKILL_NAMES.get(name, name)
    stage = STAGE_WRITE.get(name, "")
    target_str = (target or jira).strip()
    if name == "open":
        start = (
            f"Requirement target: `{target_str}`.\n"
            f"Autonomously inspect available tools/MCPs/fetchers to retrieve requirement details for `{target_str}`. "
            f"Write `{req / 'REQUIREMENT.md'}` and optional `{req / 'assets'}`."
        )
    else:
        start_file = req / ("SPEC.md" if name == "review" else "REQUIREMENT.md")
        start = (
            f"Read files with the read tool as needed, starting with {start_file}. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    return (
        f"Run skill `{entry}` (already loaded via --skill) for {jira}.\n"
        f"Req dir: {req}\n"
        f"{start}\n"
        f"{stage}\n"
        f"Do not dump unrelated historical documents. Do not use ~/.pi/agent/skills copies.\n"
        f"{extra}"
    ).strip()


def skill_dirs(root: Path, name: str) -> list[Path]:
    names = SKILL_BUNDLES.get(name, [SKILL_NAMES.get(name, name)])
    out: list[Path] = []
    for n in names:
        p = skills_root(root) / n
        if p.exists():
            out.append(p)
    return out
