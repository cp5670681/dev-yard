from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dev_yard.config import resolve_pi_choice
from dev_yard.skillbind import skill_dirs


_SUMMARY_MAX = 4000
_REVIEW_SUMMARY_MAX = 32000


@dataclass
class RunResult:
    ok: bool
    summary: str
    exit_code: int = 0


def clip_summary(raw: str, bundle: str = "") -> str:
    text = raw.strip()
    if not text:
        return ""
    limit = _REVIEW_SUMMARY_MAX if bundle in {"review", "contract"} else _SUMMARY_MAX
    if len(text) <= limit:
        return text
    # Findings and REVIEW_FAILED sit at the end of pi -p output.
    if bundle in {"review", "contract"}:
        return text[-limit:]
    return text[:limit]


class Runner:
    def start(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        repo: str | None = None,
    ) -> RunResult:
        raise NotImplementedError


def agent_binary() -> str:
    return os.environ.get("YARD_PI") or "pi"


# pi leaves grep/find/ls off unless listed.
# grill/spec/tickets write docs only; git on source clones is the CLI's job.
# review is read-only — CLI injects the diff, no bash.
# open needs mcp (Atlassian) and bash to save screenshot files from attachment URLs.
_REVIEW_TOOLS = "read,grep,find,ls"
_DOC_TOOLS = "read,grep,find,ls,edit,write"
_OPEN_TOOLS = "read,bash,grep,find,ls,edit,write,mcp"
_IMPLEMENT_TOOLS = "read,bash,grep,find,ls,edit,write"


def _tools_for(bundle: str) -> str:
    if bundle in {"review", "contract"}:
        return _REVIEW_TOOLS
    if bundle == "open":
        return _OPEN_TOOLS
    if bundle in {"grill", "spec", "tickets"}:
        return _DOC_TOOLS
    return _IMPLEMENT_TOOLS


def pi_argv(
    *,
    root: Path,
    bundle: str,
    prompt: str | None = None,
    print_mode: bool = False,
    binary: str | None = None,
    repo: str | None = None,
) -> list[str]:
    cmd = binary or agent_binary()
    # --no-skills: skip ~/.pi/agent/skills and extra project skills.
    # Explicit --skill still loads this command's bundle (project copies).
    # Do not --append-system-prompt AGENTS.md: pi already loads it from cwd.
    # Do not @-attach REQUIREMENT.md: large dumps break tool-call arguments.
    argv = [cmd, "--approve", "--no-skills"]
    provider, model = resolve_pi_choice(root, bundle, repo=repo)
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    argv.extend(["--tools", _tools_for(bundle)])
    for d in skill_dirs(root, bundle):
        argv.extend(["--skill", str(d)])
    if print_mode:
        argv.append("-p")
    if prompt is not None:
        argv.append(prompt)
    return argv


def run_pi_print(
    argv: list[str],
    cwd: Path,
    prompt: str,
    on_line: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run `pi -p` with the prompt on stdin so large diffs do not hit ARG_MAX."""
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as e:
        if e.errno == errno.E2BIG:
            return 1, f"[Errno {errno.E2BIG}] Argument list too long: {argv[0]!r}"
        raise
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        proc.stdin.write(prompt)
    finally:
        proc.stdin.close()
    chunks: list[str] = []
    for line in proc.stdout:
        chunks.append(line)
        if on_line is not None:
            on_line(line)
    code = proc.wait()
    return code, "".join(chunks)


class PiRunner(Runner):
    def __init__(
        self,
        root: Path,
        bundle: str,
        print_mode: bool = False,
        binary: str | None = None,
    ) -> None:
        self.root = root
        self.bundle = bundle
        self.print_mode = print_mode
        self.binary = binary or agent_binary()

    def start(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        repo: str | None = None,
    ) -> RunResult:
        if not shutil.which(self.binary) and not Path(self.binary).exists():
            return RunResult(
                ok=False,
                summary=f"pi not found (`{self.binary}`). Install pi or set YARD_PI to its path.",
                exit_code=127,
            )
        if self.print_mode:
            argv = pi_argv(
                root=self.root,
                bundle=self.bundle,
                prompt=None,
                print_mode=True,
                binary=self.binary,
                repo=repo,
            )

            def _echo(line: str) -> None:
                sys.stdout.write(line)
                sys.stdout.flush()

            code, raw = run_pi_print(argv, cwd, prompt, on_line=_echo)
            blocked = code != 0 or "REVIEW_FAILED" in raw
            summary = clip_summary(raw, self.bundle)
            return RunResult(
                ok=not blocked,
                summary=summary,
                exit_code=code if code else (1 if blocked else 0),
            )
        argv = pi_argv(
            root=self.root,
            bundle=self.bundle,
            prompt=prompt,
            print_mode=False,
            binary=self.binary,
            repo=repo,
        )
        r = subprocess.run(argv, cwd=cwd)
        return RunResult(
            ok=r.returncode == 0,
            summary=f"pi exit {r.returncode}",
            exit_code=r.returncode,
        )


class DryRunRunner(Runner):
    def __init__(self, argv: list[str] | None = None) -> None:
        self.argv = argv or []

    def start(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        repo: str | None = None,
    ) -> RunResult:
        extra = f" argv={self.argv}" if self.argv else ""
        return RunResult(
            ok=True,
            summary=f"dry-run cwd={cwd} reads={len(extra_read_paths)} prompt_chars={len(prompt)}{extra}",
        )


def get_runner(
    root: Path,
    bundle: str,
    dry_run: bool = False,
    print_mode: bool = False,
) -> Runner:
    if dry_run:
        return DryRunRunner(
            argv=pi_argv(root=root, bundle=bundle, prompt="(dry-run)", print_mode=print_mode)
        )
    return PiRunner(root, bundle, print_mode=print_mode)
