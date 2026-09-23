from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dev_yard.attachments import is_prompt_image
from dev_yard.config import resolve_pi_choice
from dev_yard.stages import (
    STRUCTURED_REVIEW_STAGES,
    StageSpec,
    load_registry,
    resolve_extension_path,
    resolve_review_extension_path,
    resolve_skill_dir,
    spec_skill_dirs,
)

_SUMMARY_MAX = 4000
_REVIEW_SUMMARY_MAX = 32000


@dataclass
class RunResult:
    ok: bool
    summary: str
    exit_code: int = 0
    # Set by submit_review. None means the process never produced a verdict.
    verdict: str | None = None
    findings: list[Any] | None = None


class JobCancelled(RuntimeError):
    """Raised inside a run path after cancel(); unwinds the calling service loop."""


def clip_summary(raw: str, bundle: str = "") -> str:
    text = raw.strip()
    if not text:
        return ""
    limit = _REVIEW_SUMMARY_MAX if bundle in {"review", "contract"} else _SUMMARY_MAX
    if len(text) <= limit:
        return text
    # Review prose is already separated from the verdict event. Keep the tail
    # so a long report's closing section is what the board shows.
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


def guard_args(root: Path) -> list[str]:
    """`--extension <yard-guard.ts>` when the safety extension is available."""
    guard = resolve_extension_path(root)
    return ["--extension", str(guard)] if guard is not None else []


def extension_args(root: Path, bundle: str) -> list[str]:
    """Safety extension, plus the review verdict tool on review/contract."""
    args = guard_args(root)
    if bundle in STRUCTURED_REVIEW_STAGES:
        review = resolve_review_extension_path(root)
        if review is not None:
            args.extend(["--extension", str(review)])
    return args


class ReviewStream:
    """Split a `pi --mode json` review into prose and a submit_review verdict.

    Assistant text is the report. The verdict is taken only from a successful
    `submit_review` tool event. Report text is never inspected for a marker.
    """

    def __init__(self) -> None:
        self._log_buf = ""
        self._texts: list[str] = []
        self._noise: list[str] = []
        self._pending: dict[str, dict[str, Any]] = {}
        self.verdict: str | None = None
        self.findings: list[Any] = []
        # Set when an assistant turn ends in error/aborted. JSON mode still
        # exits 0 for those, so the process code alone cannot see them.
        self.failure: str | None = None

    def feed(self, line: str) -> str:
        """Consume one stdout line. Return human text to show, or empty."""
        stripped = line.strip()
        if not stripped.startswith("{"):
            if stripped:
                self._noise.append(line if line.endswith("\n") else line + "\n")
            return line
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            self._noise.append(line if line.endswith("\n") else line + "\n")
            return line
        if not isinstance(event, dict):
            return ""
        kind = event.get("type")
        if kind == "message_update":
            inner = event.get("assistantMessageEvent") or {}
            if isinstance(inner, dict) and inner.get("type") == "text_delta":
                return self._push_delta(str(inner.get("delta") or ""))
            return ""
        if kind == "message_end":
            self._take_message(event.get("message"))
            return ""
        if kind == "tool_execution_start" and event.get("toolName") == "submit_review":
            args = event.get("args")
            if isinstance(args, dict):
                self._pending[str(event.get("toolCallId") or "")] = args
            return ""
        if kind == "tool_execution_end" and event.get("toolName") == "submit_review":
            call_id = str(event.get("toolCallId") or "")
            started = self._pending.pop(call_id, None)
            if event.get("isError"):
                return ""
            result = event.get("result")
            details = result.get("details") if isinstance(result, dict) else None
            # details is what the tool executed. Start args are only a fallback
            # when an older event omitted them.
            self._accept(details if isinstance(details, dict) else started)
            return ""
        return ""

    def flush_log(self) -> str:
        text = self._log_buf
        self._log_buf = ""
        return text

    @property
    def prose(self) -> str:
        text = "\n\n".join(part for part in self._texts if part.strip())
        if text:
            return text
        return "".join(self._noise).strip()

    def _push_delta(self, delta: str) -> str:
        self._log_buf += delta
        if "\n" not in self._log_buf:
            return ""
        done, _, self._log_buf = self._log_buf.rpartition("\n")
        return done + "\n"

    def _take_message(self, message: Any) -> None:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return
        parts: list[str] = []
        for block in message.get("content") or []:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
                continue
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                parts.append(str(block["text"]).strip())
        if parts:
            self._texts.append("\n".join(parts))
        # A tool call in the message has not run yet. pi can then emit
        # tool_execution_end with isError (truncated args, validation) and
        # still leave the process exit code at 0 in JSON mode.
        stop = message.get("stopReason")
        if stop in {"error", "aborted"}:
            self.failure = str(message.get("errorMessage") or f"Request {stop}")

    def _accept(self, args: Any) -> None:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return
        if not isinstance(args, dict):
            return
        verdict = str(args.get("verdict") or "").strip().lower()
        if verdict not in {"passed", "failed"}:
            return
        self.verdict = verdict
        findings = args.get("findings")
        self.findings = findings if isinstance(findings, list) else []


def finish_pi_run(bundle: str, code: int, raw: str) -> RunResult:
    """Build a RunResult from a finished `pi -p` stream.

    Review and contract runs use `--mode json`. Their pass/fail comes from
    `submit_review`. Other stages still follow the process exit code.
    """
    if bundle in STRUCTURED_REVIEW_STAGES:
        stream = ReviewStream()
        for line in raw.splitlines(keepends=True):
            stream.feed(line)
        prose = clip_summary(stream.prose, bundle)
        if stream.verdict == "passed":
            return RunResult(
                ok=True,
                summary=prose,
                exit_code=0,
                verdict="passed",
                findings=stream.findings,
            )
        if stream.verdict == "failed":
            return RunResult(
                ok=False,
                summary=prose,
                exit_code=code or 1,
                verdict="failed",
                findings=stream.findings,
            )
        if code != 0 or stream.failure:
            # Provider errors and aborts are not a review rejection. Leaving
            # verdict unset keeps the next implement run from treating them
            # as a failed review.
            summary = prose
            if stream.failure:
                summary = f"{prose}\n\n{stream.failure}".strip() if prose else stream.failure
            return RunResult(
                ok=False,
                summary=summary or f"pi exit {code}",
                exit_code=code or 1,
                verdict=None,
            )
        note = "评审没有调用 submit_review，没有单独结论，按未通过处理。"
        summary = f"{prose}\n\n{note}".strip() if prose else note
        return RunResult(
            ok=False,
            summary=summary,
            exit_code=1,
            verdict="failed",
            findings=[],
        )
    summary = clip_summary(raw, bundle) or f"pi exit {code}"
    return RunResult(ok=code == 0, summary=summary, exit_code=code)


def attachment_args(paths: list[Path]) -> list[str]:
    """`@<image>` args so files arrive as user-message images, not tool results.

    pi's `read` puts images in a `function_call_output`, which the grok-cli
    Responses upstream serializes as text and counts the base64 as tokens (one
    1MB screenshot ~= 780k -> `input_too_large`). `@` inputs ride the user
    message, where the same image costs ~1.2k tokens. Non-image paths are left
    alone: the stage reads markdown with the read tool, not by dumping it in.
    """
    return [f"@{p}" for p in paths if is_prompt_image(p)]


def pi_argv(
    *,
    root: Path,
    bundle: str,
    prompt: str | None = None,
    print_mode: bool = False,
    binary: str | None = None,
    repo: str | None = None,
    spec: StageSpec | None = None,
    provider: str | None = None,
    model: str | None = None,
    attach: list[Path] | None = None,
) -> list[str]:
    cmd = binary or agent_binary()
    # --no-skills: skip ~/.pi/agent/skills and extra project skills.
    # Explicit --skill still loads this command's bundle (project copies).
    # Do not --append-system-prompt AGENTS.md: pi already loads it from cwd.
    # Do not @-attach REQUIREMENT.md: large dumps break tool-call arguments.
    if spec is None:
        spec = load_registry(root).get(bundle)
    if spec is None:
        raise ValueError(f"unknown stage {bundle!r}; run: dev-yard stages")
    argv = [cmd, "--approve", "--no-skills"]
    if provider is None and model is None:
        provider, model = resolve_pi_choice(root, bundle, repo=repo)
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    argv.extend(["--tools", ",".join(spec.tools)])
    for d in spec_skill_dirs(root, spec):
        argv.extend(["--skill", str(d)])
    argv.extend(extension_args(root, bundle))
    if print_mode:
        argv.append("-p")
        if bundle in STRUCTURED_REVIEW_STAGES:
            argv.extend(["--mode", "json"])
    if attach:
        argv.extend(attachment_args(attach))
    if prompt is not None:
        argv.append(prompt)
    return argv


ASSISTANT_TOOLS = ("read", "grep", "find", "ls")


def assistant_pi_argv(
    *,
    root: Path,
    session_id: str,
    session_dir: Path,
    binary: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[str]:
    cmd = binary or agent_binary()
    if provider is None and model is None:
        provider, model = resolve_pi_choice(root, "assistant")
    argv = [cmd, "--approve", "--no-skills", "--mode", "rpc"]
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    argv.extend(["--tools", ",".join(ASSISTANT_TOOLS)])
    skill = resolve_skill_dir(root, "assistant")
    if skill is not None:
        argv.extend(["--skill", str(skill)])
    argv.extend(guard_args(root))
    argv.extend(
        [
            "--session-dir",
            str(session_dir),
            "--session-id",
            session_id,
            "--name",
            "yard-assistant",
        ]
    )
    return argv


def kill_proc_group(proc: subprocess.Popen) -> None:
    """Kill pi and every descendant sharing its session.

    Children inherit the stdout pipe, so a lone proc.kill() leaves the reader
    blocked on EOF until they exit on their own. The process group (set via
    start_new_session) lets one signal take them all down.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_pi_print(
    argv: list[str],
    cwd: Path,
    prompt: str,
    on_line: Callable[[str], None] | None = None,
    timeout: float | None = None,
    on_spawn: Callable[[subprocess.Popen], None] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run `pi -p` with the prompt on stdin so large diffs do not hit ARG_MAX.

    A hung worker would otherwise pin its scheduler slot forever, so the process
    is killed after `YARD_PI_TIMEOUT` seconds (default 3600; 0 disables).
    `on_spawn` fires right after the process starts so callers can kill it early.
    """
    import threading

    limit = timeout
    if limit is None:
        try:
            limit = float(os.environ.get("YARD_PI_TIMEOUT", "3600") or "0")
        except ValueError:
            limit = 3600.0
    child_env = None
    if env:
        child_env = {**os.environ, **env}
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=child_env,
        )
    except OSError as e:
        if e.errno == errno.E2BIG:
            return 1, f"[Errno {errno.E2BIG}] Argument list too long: {argv[0]!r}"
        raise
    if on_spawn is not None:
        on_spawn(proc)
    assert proc.stdin is not None
    assert proc.stdout is not None
    timed_out = {"hit": False}
    timer: threading.Timer | None = None
    if limit and limit > 0:
        def _kill() -> None:
            timed_out["hit"] = True
            kill_proc_group(proc)

        timer = threading.Timer(limit, _kill)
        timer.daemon = True
        timer.start()
    try:
        try:
            proc.stdin.write(prompt)
        except BrokenPipeError:
            # The proc was killed right after spawn (cancel raced the write).
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        chunks: list[str] = []
        for line in proc.stdout:
            chunks.append(line)
            if on_line is not None:
                on_line(line)
    finally:
        if timer is not None:
            timer.cancel()
    code = proc.wait()
    if timed_out["hit"]:
        return 124, "".join(chunks) + f"\n[timed out after {int(limit)}s; killed]\n"
    return code, "".join(chunks)


def run_pi_print_tracked(
    argv: list[str],
    cwd: Path,
    prompt: str,
    *,
    on_line: Callable[[str], None] | None = None,
    timeout: float | None = None,
    on_spawn: Callable[[subprocess.Popen[str]], None] | None = None,
    on_reap: Callable[[subprocess.Popen[str]], None] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """`run_pi_print` plus spawn/reap callbacks around the live subprocess.

    `on_spawn` fires with the proc so a caller can kill it on cancel; `on_reap`
    fires for every proc once the run returns, so the caller can drop it and a
    later cancel never targets a finished (possibly recycled) pid.
    """
    spawned: list[subprocess.Popen[str]] = []

    def _spawn(proc: subprocess.Popen[str]) -> None:
        spawned.append(proc)
        if on_spawn is not None:
            on_spawn(proc)

    try:
        extra = {"env": env} if env else {}
        return run_pi_print(
            argv,
            cwd,
            prompt,
            on_line=on_line,
            timeout=timeout,
            on_spawn=_spawn,
            **extra,
        )
    finally:
        if on_reap is not None:
            for proc in spawned:
                on_reap(proc)


class PiRunner(Runner):
    def __init__(
        self,
        root: Path,
        bundle: str,
        print_mode: bool = False,
        binary: str | None = None,
        spec: StageSpec | None = None,
        provider: str | None = None,
        model: str | None = None,
        on_spawn: Callable[[subprocess.Popen], None] | None = None,
        on_reap: Callable[[subprocess.Popen], None] | None = None,
    ) -> None:
        self.root = root
        self.bundle = bundle
        self.print_mode = print_mode
        self.binary = binary or agent_binary()
        self.spec = spec
        # An explicit pair (e.g. qa.yaml design) wins over resolve_pi_choice.
        self.provider = provider
        self.model = model
        # Cancellation bridge: `on_spawn` fires with the live subprocess so the
        # caller can kill it; `on_reap` drops it once the run returns.
        self.on_spawn = on_spawn
        self.on_reap = on_reap

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
                spec=self.spec,
                provider=self.provider,
                model=self.model,
                attach=extra_read_paths,
            )

            stream = (
                ReviewStream() if self.bundle in STRUCTURED_REVIEW_STAGES else None
            )

            def _echo(line: str) -> None:
                shown = stream.feed(line) if stream is not None else line
                if shown:
                    sys.stdout.write(shown)
                    sys.stdout.flush()

            code, raw = run_pi_print_tracked(
                argv,
                cwd,
                prompt,
                on_line=_echo,
                on_spawn=self.on_spawn,
                on_reap=self.on_reap,
            )
            if stream is not None:
                tail = stream.flush_log()
                if tail:
                    sys.stdout.write(tail if tail.endswith("\n") else tail + "\n")
                    sys.stdout.flush()
            return finish_pi_run(self.bundle, code, raw)
        argv = pi_argv(
            root=self.root,
            bundle=self.bundle,
            prompt=prompt,
            print_mode=False,
            binary=self.binary,
            repo=repo,
            spec=self.spec,
            provider=self.provider,
            model=self.model,
            attach=extra_read_paths,
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
    spec: StageSpec | None = None,
    provider: str | None = None,
    model: str | None = None,
    on_spawn: Callable[[subprocess.Popen], None] | None = None,
    on_reap: Callable[[subprocess.Popen], None] | None = None,
) -> Runner:
    if dry_run:
        return DryRunRunner(
            argv=pi_argv(
                root=root,
                bundle=bundle,
                prompt="(dry-run)",
                print_mode=print_mode,
                spec=spec,
                provider=provider,
                model=model,
            )
        )
    return PiRunner(
        root,
        bundle,
        print_mode=print_mode,
        spec=spec,
        provider=provider,
        model=model,
        on_spawn=on_spawn,
        on_reap=on_reap,
    )
