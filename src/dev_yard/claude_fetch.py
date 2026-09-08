from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from dev_yard.runners import RunResult


def claude_binary() -> str:
    return os.environ.get("YARD_CLAUDE") or "claude"


def fetch_prompt(jira: str, dest: Path) -> str:
    return f"""You have Atlassian MCP (Jira + Confluence). Analyze issue {jira} and write the **current** product requirement only.

Write:
- `{dest / "REQUIREMENT.md"}`
- optional images under `{dest / "assets"}/` (only images that belong to this ticket's own product page)

Steps:
1. MCP `jira_get_issue` for {jira} with fields `*all` (need custom fields like Product Document) and comments.
2. Find Confluence links: Product Document custom field, description, remote links.
3. Prefer the page that is **this ticket's product doc** (title often contains the Jira key, or the Product Document URL). Fetch it with `confluence_get_page`.
4. Do **not** dump parent/historical wiki (e.g. a module overview that lists many old Jiras). At most one sentence + URL for those.
5. Do **not** follow every `ri:page` / 更新记录 old rows. Stay on this change: background, this-ticket 功能说明, acceptance, UI/API notes, this-ticket screenshots.
6. Images: `confluence_get_page_images` only for the current-ticket page. Save referenced screenshots as files under assets/ and link them in REQUIREMENT.md. Skip images that are clearly from old versions.
7. REQUIREMENT.md structure:
   # {jira}
   title
   ## Jira (summary, type, status, description, key custom fields)
   ## Product document (this ticket only)
   ## Out of scope / not fetched (list skipped historical URLs)

Use MCP. Do not crawl the whole space. Do not run bulk HTTP downloads of every attachment on parent pages.
When done, REQUIREMENT.md must exist and be about {jira} only.
"""


def claude_argv(dest: Path, binary: str | None = None) -> list[str]:
    cmd = binary or claude_binary()
    # Prompt goes on stdin. Do not put it after --add-dir (variadic; it swallows the prompt).
    return [
        cmd,
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "text",
        "--add-dir",
        str(dest),
        "-p",
    ]


def run_claude_fetch(jira: str, dest: Path, dry_run: bool = False) -> RunResult:
    argv = claude_argv(dest)
    prompt = fetch_prompt(jira, dest)
    if dry_run:
        return RunResult(ok=True, summary=" ".join(argv) + "\n(stdin prompt)", exit_code=0)
    cmd = argv[0]
    if not shutil.which(cmd) and not Path(cmd).exists():
        return RunResult(
            ok=False,
            summary=f"claude not found (`{cmd}`). Install Claude Code or set YARD_CLAUDE.",
            exit_code=127,
        )
    r = subprocess.run(argv, cwd=dest, input=prompt, text=True)
    return RunResult(
        ok=r.returncode == 0,
        summary=f"claude exit {r.returncode}",
        exit_code=r.returncode,
    )
