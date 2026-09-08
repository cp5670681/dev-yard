from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dev_yard.skillbind import skill_dirs


@dataclass
class RunResult:
    ok: bool
    summary: str
    exit_code: int = 0


class Runner:
    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
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
    if bundle == "review":
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
    prompt: str,
    print_mode: bool = False,
    binary: str | None = None,
) -> list[str]:
    cmd = binary or agent_binary()
    # --no-skills: skip ~/.pi/agent/skills and extra project skills.
    # Explicit --skill still loads this command's bundle (project copies).
    # Do not --append-system-prompt AGENTS.md: pi already loads it from cwd.
    # Do not @-attach REQUIREMENT.md: large dumps break tool-call arguments.
    argv = [cmd, "--approve", "--no-skills"]
    provider = os.environ.get("YARD_PI_PROVIDER")
    model = os.environ.get("YARD_PI_MODEL")
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    argv.extend(["--tools", _tools_for(bundle)])
    for d in skill_dirs(root, bundle):
        argv.extend(["--skill", str(d)])
    if print_mode:
        argv.append("-p")
    argv.append(prompt)
    return argv


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

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
        if not shutil.which(self.binary) and not Path(self.binary).exists():
            return RunResult(
                ok=False,
                summary=f"pi not found (`{self.binary}`). Install pi or set YARD_PI to its path.",
                exit_code=127,
            )
        argv = pi_argv(
            root=self.root,
            bundle=self.bundle,
            prompt=prompt,
            print_mode=self.print_mode,
            binary=self.binary,
        )
        if self.print_mode:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            chunks: list[str] = []
            assert proc.stdout is not None
            for line in proc.stdout:
                chunks.append(line)
                sys.stdout.write(line)
                sys.stdout.flush()
            code = proc.wait()
            raw = "".join(chunks)
            blocked = code != 0 or "REVIEW_FAILED" in raw
            summary = raw.strip()[:4000]
            return RunResult(
                ok=not blocked,
                summary=summary,
                exit_code=code if code else (1 if blocked else 0),
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

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path]) -> RunResult:
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
