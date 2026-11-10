from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

