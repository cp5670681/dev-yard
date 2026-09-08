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


def session_prompt(root: Path, name: str, jira: str, extra: str = "") -> str:
    req = root / "reqs" / jira
    entry = SKILL_NAMES.get(name, name)
    return (
        f"Execute the loaded skill `{entry}` for requirement {jira}.\n"
        f"Req dir: {req}\n"
        f"Do not use a separate mattpocock plugin copy.\n"
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
