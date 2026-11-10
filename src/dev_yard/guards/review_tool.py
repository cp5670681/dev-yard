from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SUBMIT_REVIEW_SCHEMA = {
    "name": "submit_review",
    "description": (
        "Submit the review verdict. Call exactly once, after the written report. "
        "This call is the only pass/fail signal; the report text is not scanned."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["passed", "failed"],
                "description": "passed when the review accepts the change, failed when it must be reworked",
            },
            "findings": {
                "type": "array",
                "description": "Spec or contract gaps. Pass an empty list when there are none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Stable finding id, e.g. F1"},
                        "title": {"type": "string", "description": "Short title"},
                        "repo": {"type": "string", "description": "repos.yaml alias"},
                        "detail": {"type": "string", "description": "What is missing or wrong"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Repo-relative path",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Other finding id",
                        },
                        "gap": {
                            "type": "string",
                            "enum": ["missing", "partial", "contradicts", "unrequested"],
                            "description": "missing: not in code; partial: incomplete; contradicts: conflicts with spec; unrequested: diff has unasked work",
                        },
                        "ref": {
                            "type": "string",
                            "description": "SPEC id this gap cites: FR-001, US1, US1/AC1, or C-001",
                        },
                    },
                    "required": ["id", "title", "repo", "detail"],
                },
            },
        },
        "required": ["verdict"],
    },
}


@dataclass
class ReviewVerdict:
    verdict: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, arguments: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        verdict = str(arguments.get("verdict") or "").strip().lower()
        if verdict not in ("passed", "failed"):
            return False, "verdict must be passed or failed", {}
        findings = arguments.get("findings")
        self.verdict = verdict
        self.findings = findings if isinstance(findings, list) else []
        return True, f"recorded {verdict}", {"verdict": verdict, "findings": self.findings}


ANTIGRAVITY_REVIEW_PROMPT_GUIDANCE = (
    "\n\n---\n\n"
    "## Verdict Submission (Antigravity Engine)\n"
    "At the end of your review report, submit the review verdict by calling `submit_review` "
    "or by outputting a JSON block:\n"
    "```json\n"
    "submit_review({\n"
    '  "verdict": "passed",\n'
    '  "findings": []\n'
    "})\n"
    "```\n"
    'verdict must be "passed" or "failed". findings is a list of contract/spec gap objects.\n'
)


def parse_review_verdict_from_text(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract review verdict and findings from text.

    Supports:
    1. Function-style call: `submit_review({"verdict": "passed", "findings": []})`
       or `submit_review(verdict="passed", findings=[])`
    2. Code blocks (```json ... ``` or ```yaml ... ```) containing `"verdict": "passed"`
    3. Trailing JSON/YAML objects with `verdict` key
    """
    if not (text or "").strip():
        return None, []

    # 1. Look for submit_review(...) - check matches in reverse order (latest wins)
    # First attempt: parse JSON directly from `{` after `submit_review(` (handles nested parens)
    call_starts = list(re.finditer(r"submit_review\s*\(", text))
    for match in reversed(call_starts):
        start = match.end()
        idx = text.find("{", start)
        if idx != -1:
            try:
                data, _ = json.JSONDecoder().raw_decode(text[idx:])
                if isinstance(data, dict):
                    v = str(data.get("verdict") or "").strip().lower()
                    if v in ("passed", "failed"):
                        findings = data.get("findings")
                        return v, findings if isinstance(findings, list) else []
            except Exception:
                pass

    # Regex fallback for submit_review(verdict="passed"...) or YAML-style arguments
    matches = list(re.finditer(r"submit_review\s*\((.*?)\)", text, re.DOTALL))
    for match in reversed(matches):
        inner = match.group(1).strip()
        try:
            data = json.loads(inner)
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                if v in ("passed", "failed"):
                    findings = data.get("findings")
                    return v, findings if isinstance(findings, list) else []
        except Exception:
            pass
        try:
            data = yaml.safe_load(inner)
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                if v in ("passed", "failed"):
                    findings = data.get("findings")
                    return v, findings if isinstance(findings, list) else []
        except Exception:
            pass
        v_match = re.search(r"verdict\s*[:=]\s*[\x27\"]?(passed|failed)[\x27\"]?", inner, re.IGNORECASE)
        if v_match:
            return v_match.group(1).lower(), []

    # 2. Look for ```json ... ``` or ```yaml ... ``` code blocks
    code_blocks = list(re.finditer(r"```(?:json|yaml)?\s*(\{.*?\})\s*```", text, re.DOTALL))
    for match in reversed(code_blocks):
        inner = match.group(1).strip()
        try:
            data = json.loads(inner)
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                if v in ("passed", "failed"):
                    findings = data.get("findings")
                    return v, findings if isinstance(findings, list) else []
        except Exception:
            pass
        try:
            data = yaml.safe_load(inner)
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                if v in ("passed", "failed"):
                    findings = data.get("findings")
                    return v, findings if isinstance(findings, list) else []
        except Exception:
            pass

    # 3. Look for standalone JSON objects with verdict
    json_objs = list(re.finditer(r"\{[^{}]*\"verdict\"\s*:\s*\"(passed|failed)\"[^{}]*\}", text, re.DOTALL | re.IGNORECASE))
    for match in reversed(json_objs):
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                if v in ("passed", "failed"):
                    findings = data.get("findings")
                    return v, findings if isinstance(findings, list) else []
        except Exception:
            pass

    return None, []


def check_and_consume_verdict_file(cwd: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Check for `.yard-review.json` written by CLI `submit_review` and consume it."""
    vf = cwd / ".yard-review.json"
    if vf.is_file():
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                v = str(data.get("verdict") or "").strip().lower()
                findings = data.get("findings")
                f_list = findings if isinstance(findings, list) else []
                vf.unlink(missing_ok=True)
                if v in ("passed", "failed"):
                    return v, f_list
        except Exception:
            pass
        vf.unlink(missing_ok=True)
    return None, []


def extract_review_verdict(
    *,
    tool_verdict: ReviewVerdict | None = None,
    cwd: Path | None = None,
    text: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract review verdict and findings with fallback chain:
    1. Tool call result (ReviewVerdict object)
    2. Written verdict file (.yard-review.json) in cwd
    3. Parsed structured block in report text
    """
    if tool_verdict is not None and tool_verdict.verdict:
        return tool_verdict.verdict, tool_verdict.findings
    if cwd is not None:
        file_v, file_f = check_and_consume_verdict_file(cwd)
        if file_v:
            return file_v, file_f
    if text is not None:
        text_v, text_f = parse_review_verdict_from_text(text)
        if text_v:
            return text_v, text_f
    return None, []


