from __future__ import annotations

from pathlib import Path

from dev_yard import paths
from dev_yard.stages import StageSpec


def session_prompt_for(
    spec: StageSpec, root: Path, jira: str, extra: str = "", target: str = ""
) -> str:
    req = paths.req_dir(root, jira)
    ctx = paths.context_md(root)
    adr = paths.adr_dir(root)
    target_str = (target or jira).strip()
    if spec.name == "open":
        start = (
            f"Requirement target: `{target_str}`.\n"
            f"Autonomously inspect available tools/MCPs/fetchers to retrieve requirement details for `{target_str}`. "
            f"Write `{req / 'REQUIREMENT.md'}` and optional `{req / 'assets'}`."
        )
    else:
        start_file = req / (
            "SPEC.md" if spec.name in {"review", "contract"} else "REQUIREMENT.md"
        )
        start = (
            f"Read files with the read tool as needed, starting with {start_file}. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    return (
        f"Run skill `{spec.skill}` (already loaded via --skill) for {jira}.\n"
        f"Req dir: {req}\n"
        f"{start}\n"
        f"{spec.guidance}\n"
        f"Do not dump unrelated historical documents. Do not use ~/.pi/agent/skills copies.\n"
        f"{extra}"
    ).strip()


def session_prompt(
    root: Path, name: str, jira: str, extra: str = "", target: str = ""
) -> str:
    from dev_yard.stages import load_registry

    spec = load_registry(root).get(name)
    if spec is None:
        raise ValueError(f"unknown stage {name!r}; run: dev-yard stages")
    return session_prompt_for(spec, root, jira, extra=extra, target=target)
