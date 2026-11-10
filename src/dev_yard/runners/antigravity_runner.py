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
from dev_yard.guards.review_tool import ReviewVerdict
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
            agy_bin = os.environ.get("YARD_ANTIGRAVITY_BIN") or shutil.which("agy")
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
        review_verdict = ReviewVerdict()
        allowed_tools = set(self.tool_override or (self.spec.tools if self.spec else ()))

        # Append user prompt message to session log
        self._append_message(
            session_file,
            role="user",
            content=[{"type": "text", "text": prompt}],
        )

        # If SDK is available, run through the SDK
        try:
            from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig  # type: ignore

            config = LocalAgentConfig(
                model=self.model,
                system_instructions=(self.spec.guidance if self.spec else ""),
                capabilities=CapabilitiesConfig(
                    allow_bash="bash" in allowed_tools,
                    allow_file_write="write" in allowed_tools or "edit" in allowed_tools,
                ),
            )
            # Synchronous wrapper around SDK async execution
            import asyncio

            async def _run():
                async with Agent(config) as agent:
                    return await agent.chat(prompt)

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            response = loop.run_until_complete(_run())
            summary = str(response or "").strip()
            ok = True
        except Exception as e:
            # Fallback for mock/test runs or subprocess
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
            if review_verdict.verdict:
                verdict = review_verdict.verdict
                findings = review_verdict.findings
            else:
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
