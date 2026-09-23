from __future__ import annotations

from pathlib import Path

from dev_yard import attachments, paths
from dev_yard.config import load_dev_settings
from dev_yard.stages import StageSpec


def _attachments_hint(root: Path, jira: str, req: Path) -> str:
    """Point every downstream stage at human-added uploads/ files.

    They are linked from REQUIREMENT.md, but nothing else makes the agent open
    them; without this the prototype's encoded logic never reaches GRILL/SPEC/TICKETS.
    """
    names = attachments.list_names(root, jira)
    if not names:
        return ""
    listing = "\n".join(f"- {req / attachments.UPLOADS_DIRNAME / n}" for n in names)
    return (
        "Human-added attachments are part of the requirement. Read the text/HTML "
        "ones (HTML prototypes carry the real UI logic: state machines, field "
        "linkage, validation) and fold decision-bearing behavior into your output, "
        "noting that it came from an attachment. Do NOT read image files "
        "(png/jpg/jpeg/gif/webp/bmp): they reach the stage as prompt attachments "
        "when needed, and the read tool blocks them. Large "
        "files: grep/skim instead of dumping. Never delete or modify anything under "
        "uploads/.\n"
        f"{listing}"
    )


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
        if spec.name == "spec":
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). Skip test-seam planning. "
                "In Testing Decisions write only that this requirement does not require test files. "
                "Do not list RSpec, Jest, or unit-test cases, and do not copy test-seam bullets into SPEC.md."
            )
        elif spec.name == "tickets":
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). Acceptance describes observable behavior only. "
                "Do not require spec files, unit tests, or phrases like 验收：spec."
            )
        elif spec.name == "implement":
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). This overrides any earlier instruction in this prompt. "
                "Do NOT write test files (e.g. RSpec, Jest, unit tests) and do NOT run test suites. "
                "Ignore test-file demands anywhere in SPEC.md or TICKETS.md, including previous-review "
                "or contract text that calls them Spec gaps. Directly implement the business logic and "
                "verify via static code walkthrough/syntax checking."
            )
        elif spec.name in {"review", "contract"}:
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). This overrides any earlier instruction in this prompt. "
                "Test-file demands anywhere in SPEC.md or TICKETS.md are not Spec gaps and must not "
                "become findings. A missing, broken, or unrun test is NOT a defect. "
                "Call submit_review with verdict passed unless production behavior is wrong. "
                "Judge production behavior only."
            )
        elif spec.name == "resolve-merge":
            tdd_extra = (
                "TDD mode is OFF (dev.tdd=false). After resolving conflicts, do NOT add test files and do "
                "NOT run test suites; only do a static check (syntax / obvious broken references) and stop."
            )
    elif spec.name == "resolve-merge":
        tdd_extra = (
            "TDD mode is ON (dev.tdd=true). After resolving conflicts, run the affected test suites using "
            "the repo's existing conventions and keep them green before you finish."
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
    elif spec.name == "contract":
        start = (
            f"Read files with the read tool as needed, starting with {req / 'REQUIREMENT.md'} "
            f"(product source of truth) and {req / 'SPEC.md'} (implementation contract). "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    elif spec.name == "resolve-merge":
        start = (
            f"Resolve the git merge conflict in the current worktree only. "
            f"Read {req / 'REQUIREMENT.md'} (product source of truth), "
            f"{req / 'SPEC.md'} (implementation contract), and {req / 'TICKETS.md'} as needed. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    elif spec.name in {"review", "tickets"}:
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
    hint = _attachments_hint(root, jira, req)
    if hint:
        parts.append(hint)
    if extra:
        parts.append(extra)
    if tdd_extra:
        parts.append(tdd_extra)
    return "\n".join(parts).strip()


def session_prompt(
    root: Path, name: str, jira: str, extra: str = "", target: str = ""
) -> str:
    from dev_yard.stages import load_registry

    spec = load_registry(root).get(name)
    if spec is None:
        raise ValueError(f"unknown stage {name!r}; run: dev-yard stages")
    return session_prompt_for(spec, root, jira, extra=extra, target=target)
