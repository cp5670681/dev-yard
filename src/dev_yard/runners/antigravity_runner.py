from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dev_yard.guards.command_guard import validate_tool_call
from dev_yard.guards.doc_align_guard import validate_doc_align_write
from dev_yard.guards.review_tool import (
    ANTIGRAVITY_REVIEW_PROMPT_GUIDANCE,
    ReviewVerdict,
    extract_review_verdict,
)
from dev_yard.runners import (
    MISSING_VERDICT_NOTE,
    REVIEW_NUDGE_PROMPT,
    STRUCTURED_REVIEW_STAGES,
    Runner,
    RunResult,
    clip_summary,
    recovered_summary,
)
from dev_yard.stages import StageSpec


def default_antigravity_sessions_dir(root: Path) -> Path:
    override = os.environ.get("YARD_ANTIGRAVITY_SESSIONS")
    if override:
        return Path(override)
    return root / ".yard-sessions"


class AntigravityRunner(Runner):
    """Runner implementation backed by Antigravity."""

    def __init__(
        self,
        root: Path,
        bundle: str,
        print_mode: bool = False,
        spec: StageSpec | None = None,
        model: str | None = None,
        on_spawn: Callable[[subprocess.Popen], None] | None = None,
        on_reap: Callable[[subprocess.Popen], None] | None = None,
    ) -> None:
        self.root = root
        self.bundle = bundle
        self.print_mode = print_mode
        self.spec = spec
        self.model = model or os.environ.get("YARD_AGENT_MODEL") or "default"
        self.on_spawn = on_spawn
        self.on_reap = on_reap
        self.tool_override: tuple[str, ...] | None = None
        self.on_tool: Callable[[str], None] | None = None
        self.on_note: Callable[[str], None] | None = None

    def start(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        repo: str | None = None,
    ) -> RunResult:
        # Check SDK availability or mock/fallback runner
        try:
            import google.antigravity  # type: ignore # noqa: F401
            has_sdk = True
        except ImportError:
            has_sdk = False

        session_id = f"ag_{self.bundle}_{uuid.uuid4().hex[:12]}"
        sessions_dir = default_antigravity_sessions_dir(self.root)
        session_file = sessions_dir / f"{session_id}.jsonl"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Write initial session header
        header = {
            "id": session_id,
            "cwd": str(cwd.resolve()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engine": "antigravity",
            "bundle": self.bundle,
            "model": self.model,
        }
        with session_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(header, ensure_ascii=False) + "\n")

        if not has_sdk:
            from dev_yard.pi_catalog import resolve_agy_binary

            agy_bin = resolve_agy_binary()
            if not agy_bin and not os.environ.get("YARD_TEST_MOCK_AGENT"):
                msg = (
                    "Antigravity engine selected, but neither `google-antigravity` Python SDK "
                    "nor `agy` CLI binary was found. "
                    "Install the SDK (`pip install google-antigravity`) or set YARD_ANTIGRAVITY_BIN."
                )
                return RunResult(ok=False, summary=msg, exit_code=127)

        # Run via the Antigravity engine session
        return self._execute_session(
            prompt=prompt,
            cwd=cwd,
            extra_read_paths=extra_read_paths,
            session_file=session_file,
            session_id=session_id,
        )

    def _execute_session(
        self,
        prompt: str,
        cwd: Path,
        extra_read_paths: list[Path],
        session_file: Path,
        session_id: str,
    ) -> RunResult:
        import sys
        from dev_yard.pi_catalog import resolve_agy_binary

        review_verdict = ReviewVerdict()
        allowed_tools = set(self.tool_override or (self.spec.tools if self.spec else ()))

        # Append user prompt message to session log
        self._append_message(
            session_file,
            role="user",
            content=[{"type": "text", "text": prompt}],
        )

        agy_bin = resolve_agy_binary()
        if not os.environ.get("YARD_TEST_MOCK_AGENT") and agy_bin:
            spec = self.spec
            if spec is None:
                from dev_yard.stages import load_registry

                spec = load_registry(self.root).get(self.bundle)

            actual_prompt = prompt
            if spec:
                from dev_yard.stages import spec_skill_dirs

                skill_texts = []
                for sdir in spec_skill_dirs(self.root, spec):
                    sf = sdir / "SKILL.md"
                    if sf.is_file():
                        try:
                            skill_texts.append(
                                f"# Skill: {sdir.name}\n\n{sf.read_text(encoding='utf-8')}"
                            )
                        except Exception:
                            pass
                if skill_texts:
                    actual_prompt = "\n\n".join(skill_texts) + "\n\n---\n\n" + prompt

            if extra_read_paths:
                from dev_yard.attachments import is_prompt_image

                img_attachments = [
                    f"@{p.resolve()}" for p in extra_read_paths if is_prompt_image(p)
                ]
                if img_attachments:
                    actual_prompt = (
                        actual_prompt + "\n\nAttached images:\n" + "\n".join(img_attachments)
                    )

            if self.bundle in STRUCTURED_REVIEW_STAGES:
                actual_prompt += ANTIGRAVITY_REVIEW_PROMPT_GUIDANCE

            cmd = [
                agy_bin,
                "-p",
                actual_prompt,
                "--model",
                self.model or "gemini-3.8-flash-high",
                "--output-format",
                "stream-json",
                "--dangerously-skip-permissions",
            ]

            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            if self.on_spawn:
                self.on_spawn(proc)

            full_text_chunks: list[str] = []
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        if self.print_mode:
                            sys.stdout.write(line if line.endswith("\n") else line + "\n")
                            sys.stdout.flush()
                        continue
                    event_type = data.get("event")
                    if event_type == "step_update":
                        su = data.get("step_update") or {}
                        stype = su.get("step_type")
                        if stype == "tool":
                            state = su.get("state")
                            tname = su.get("tool_name")
                            tinfo = su.get("tool_info") or {}
                            params = tinfo.get("parameters") or {}
                            if tname == "submit_review":
                                review_verdict.execute(params)
                            elif tname == "call_mcp_tool" and params.get("ToolName") == "submit_review":
                                review_verdict.execute(params.get("Arguments") or {})
                            if state == "ACTIVE" and self.print_mode:
                                brief = f"[{tname}]"
                                for k in (
                                    "CommandLine",
                                    "path",
                                    "file_path",
                                    "pattern",
                                    "query",
                                    "issue_key",
                                    "issueKey",
                                ):
                                    if params.get(k):
                                        brief += f" {str(params[k])[:80]}"
                                        break
                                sys.stdout.write(brief + "\n")
                                sys.stdout.flush()
                        else:
                            delta = su.get("text_delta")
                            if delta and isinstance(delta, str):
                                full_text_chunks.append(delta)
                                if self.print_mode:
                                    sys.stdout.write(delta)
                                    sys.stdout.flush()
                    elif event_type == "result":
                        res = data.get("result") or {}
                        resp = res.get("response")
                        if resp and isinstance(resp, str) and not full_text_chunks:
                            full_text_chunks.append(resp)
                            if self.print_mode:
                                sys.stdout.write(resp)
                                sys.stdout.flush()
                proc.wait()
            finally:
                if self.on_reap:
                    self.on_reap(proc)

            code = proc.returncode
            summary = "".join(full_text_chunks).strip()
            if not summary and code != 0:
                err = proc.stderr.read() if proc.stderr else ""
                summary = f"agy exited {code}: {err.strip()}"
            ok = (code == 0)
        else:
            summary = f"[Antigravity Engine] Stage `{self.bundle}` executed for model `{self.model}`."
            ok = True

        # Append assistant response to session log
        self._append_message(
            session_file,
            role="assistant",
            content=[{"type": "text", "text": summary}],
        )

        verdict: str | None = None
        findings: list[Any] = []
        verdict_missing = False

        if self.bundle in STRUCTURED_REVIEW_STAGES:
            verdict, findings = extract_review_verdict(
                tool_verdict=review_verdict,
                cwd=cwd,
                text=summary,
            )
            if verdict is None:
                verdict_missing = True
                summary = (summary + f"\n\n{MISSING_VERDICT_NOTE}").strip()

        return RunResult(
            ok=ok,
            summary=clip_summary(summary, self.bundle),
            exit_code=0 if ok else 1,
            verdict=verdict,
            findings=findings,
            verdict_missing=verdict_missing,
        )

    def _append_message(self, session_file: Path, role: str, content: list[dict[str, Any]]) -> None:
        rec = {
            "type": "message",
            "message": {
                "role": role,
                "content": content,
            },
        }
        with session_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
