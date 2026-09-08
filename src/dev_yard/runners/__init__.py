from __future__ import annotations

import os
import shutil
import subprocess
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


def pi_argv(
    *,
    root: Path,
    bundle: str,
    prompt: str,
    extra_read_paths: list[Path] | None = None,
    print_mode: bool = False,
    binary: str | None = None,
) -> list[str]:
    cmd = binary or agent_binary()
    argv = [cmd, "--approve"]
    agents = root / "AGENTS.md"
    if agents.exists():
        argv.extend(["--append-system-prompt", str(agents)])
    for d in skill_dirs(root, bundle):
        argv.extend(["--skill", str(d)])
    for p in extra_read_paths or []:
        if p.exists():
            argv.append(f"@{p}")
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
            extra_read_paths=extra_read_paths,
            print_mode=self.print_mode,
            binary=self.binary,
        )
        if self.print_mode:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
            summary = (r.stdout or r.stderr or "").strip()[:4000]
            return RunResult(ok=r.returncode == 0, summary=summary, exit_code=r.returncode)
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
