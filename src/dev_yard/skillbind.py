from __future__ import annotations

from pathlib import Path

from dev_yard import paths
from dev_yard.config import load_dev_settings
from dev_yard.stages import StageSpec


def session_prompt_for(
    spec: StageSpec, root: Path, jira: str, extra: str = "", target: str = ""
) -> str:
    req = paths.req_dir(root, jira)
    ctx = paths.context_md(root)
    adr = paths.adr_dir(root)
    target_str = (target or jira).strip()
    dev_settings = load_dev_settings(root)
    tdd_extra = ""
    if not dev_settings.tdd:
        if spec.name == "implement":
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). Do NOT write test files (e.g. RSpec, Jest, unit tests) "
                "and do NOT run test suites unless SPEC.md explicitly demands testing. "
                "Directly implement the business logic and verify via static code walkthrough/syntax checking."
            )
        elif spec.name in {"review", "contract"}:
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). Do NOT require test files or test execution in review; "
                "missing tests is NOT a defect or violation unless SPEC.md explicitly demands testing."
            )

    if spec.name == "open":
        start = (
            f"Requirement target: `{target_str}`.\n"
            f"Autonomously inspect available tools/MCPs/fetchers to retrieve requirement details for `{target_str}`. "
            f"Write `{req / 'REQUIREMENT.md'}` and optional `{req / 'assets'}`."
        )
    elif spec.name == "implement":
        start = (
            f"Read files with the read tool as needed, starting with `{req / 'SPEC.md'}` and `{req / 'TICKETS.md'}`. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    elif spec.name in {"review", "contract", "tickets"}:
        start_file = req / "SPEC.md"
        start = (
            f"Read files with the read tool as needed, starting with {start_file}. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    else:
        start_file = req / "REQUIREMENT.md"
        start = (
            f"Read files with the read tool as needed, starting with {start_file}. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    parts = [
        f"Run skill `{spec.skill}` (already loaded via --skill) for {jira}.",
        f"Req dir: {req}",
        start,
        spec.guidance,
        "Do not dump unrelated historical documents. Do not use ~/.pi/agent/skills copies.",
    ]
    if tdd_extra:
        parts.append(tdd_extra)
    if extra:
        parts.append(extra)
    return "\n".join(parts).strip()


def session_prompt(
    root: Path, name: str, jira: str, extra: str = "", target: str = ""
) -> str:
    from dev_yard.stages import load_registry

    spec = load_registry(root).get(name)
    if spec is None:
        raise ValueError(f"unknown stage {name!r}; run: dev-yard stages")
    return session_prompt_for(spec, root, jira, extra=extra, target=target)
