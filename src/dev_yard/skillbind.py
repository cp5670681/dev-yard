from __future__ import annotations

from pathlib import Path

SKILL_NAMES = {
    "grill": "grill-with-docs",
    "spec": "to-spec",
    "tickets": "to-tickets",
    "implement": "implement",
    "review": "code-review",
    "tdd": "tdd",
}

# Extra primitives pi must load with the entry skill.
SKILL_BUNDLES: dict[str, list[str]] = {
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
    "grill": "Write only GRILL.md (and CONTEXT.md / docs/adr if a term or ADR is settled). Do not write SPEC.md or TICKETS.md.",
    "spec": "Write only SPEC.md from GRILL.md. Do not interview. Do not write TICKETS.md.",
    "tickets": "Write only TICKETS.md from SPEC.md.",
    "implement": "Write code in the current worktree only.",
    "review": "Do not implement; report Standards and Spec axes.",
}


def session_prompt(root: Path, name: str, jira: str, extra: str = "") -> str:
    req = root / "reqs" / jira
    entry = SKILL_NAMES.get(name, name)
    stage = STAGE_WRITE.get(name, "")
    start = req / ("SPEC.md" if name == "review" else "REQUIREMENT.md")
    return (
        f"Run skill `{entry}` (already loaded via --skill) for {jira}.\n"
        f"Req dir: {req}\n"
        f"Read files with the read tool as needed, starting with {start}.\n"
        f"{stage}\n"
        f"Do not dump historical Confluence. Do not use ~/.pi/agent/skills copies.\n"
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
