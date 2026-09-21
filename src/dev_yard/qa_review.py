"""Human review gate for auto-designed UI cases.

`qa-design` writes cases, then a person reviews them before `req test` is
allowed to execute. The approval lives in `reqs/<JIRA>/qa/review.yaml` and is
bound to a fingerprint of the case files, so regenerating or editing cases
invalidates a previous approval instead of silently carrying it over.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

REVIEW_FILE = "review.yaml"
STATUS_AWAITING = "awaiting"
STATUS_REJECTED = "rejected"
STATUS_PASSED = "passed"
NO_CASES = "no-cases"


@dataclass(frozen=True)
class ReviewState:
    status: str
    fingerprint: str
    feedback: str = ""
    updated_at: str = ""
    # Set when the requirement docs changed after the cases were designed;
    # forces `approved=False` until a person re-approves or redesigns.
    stale_reason: str = ""


def _case_assets(qa: Path) -> list[Path]:
    """Every authored file that changes what a case executes.

    Includes `case-*.md`'s setup/cleanup scripts (spec §6.1) but excludes the
    `.replay.sh` that `qa-run` writes next to a case: that is run output, not a
    reviewed artifact, and must not invalidate an approval.
    """
    root = qa / "cases"
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.name.startswith(".")
        and not path.name.endswith(".replay.sh")
    ]


def cases_fingerprint(qa: Path) -> str:
    """Stable hash over every authored case asset's path and content."""
    h = hashlib.sha1()
    for path in _case_assets(qa):
        h.update(path.relative_to(qa).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def load_review(qa: Path) -> ReviewState | None:
    path = qa / REVIEW_FILE
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    status = str(data.get("status") or "").strip()
    fingerprint = str(data.get("fingerprint") or "").strip()
    if not status or not fingerprint:
        return None
    return ReviewState(
        status=status,
        fingerprint=fingerprint,
        feedback=str(data.get("feedback") or ""),
        updated_at=str(data.get("updated_at") or ""),
        stale_reason=str(data.get("stale_reason") or ""),
    )


def save_review(
    qa: Path,
    status: str,
    fingerprint: str,
    feedback: str = "",
    stale_reason: str = "",
) -> Path:
    qa.mkdir(parents=True, exist_ok=True)
    path = qa / REVIEW_FILE
    payload = {
        "status": status,
        "fingerprint": fingerprint,
        "feedback": feedback,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if stale_reason:
        payload["stale_reason"] = stale_reason
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def review_payload(qa: Path) -> dict[str, object]:
    """Review status as the board / web page should render it.

    `status` is `no-cases` before design, `passed` only when the approval
    matches the current cases, otherwise `awaiting`/`rejected`.
    """
    root = qa / "cases"
    has_cases = root.is_dir() and any(root.rglob("case-*.md"))
    current = cases_fingerprint(qa)
    state = load_review(qa)
    stale_reason = state.stale_reason if state else ""
    approved = bool(
        state
        and state.status == STATUS_PASSED
        and state.fingerprint == current
        and not stale_reason
    )
    stale = bool(
        state
        and state.status == STATUS_PASSED
        and state.fingerprint != current
        and not stale_reason
    )
    if not has_cases:
        status = NO_CASES
    elif stale_reason:
        status = STATUS_AWAITING
    elif approved:
        status = STATUS_PASSED
    elif state and state.status == STATUS_REJECTED:
        status = STATUS_REJECTED
    else:
        status = STATUS_AWAITING
    return {
        "status": status,
        "approved": approved,
        "stale": stale,
        "stale_reason": stale_reason,
        "feedback": state.feedback if state else "",
        "updated_at": state.updated_at if state else "",
        "fingerprint": current,
    }


def review_gate(qa: Path) -> tuple[bool, str]:
    """`(can_run, reason)` for the current case set."""
    payload = review_payload(qa)
    if payload["approved"]:
        return True, ""
    status = payload["status"]
    if status == NO_CASES:
        return False, "还没有用例"
    if payload.get("stale_reason"):
        return False, f"需求已变更（{payload['stale_reason']}），请复核用例"
    if payload["stale"]:
        return False, "用例已改动，需重新审核"
    if status == STATUS_REJECTED:
        return False, "用例已按意见重做，等待复审"
    return False, "用例待审核"


def approve_cases(qa: Path) -> dict[str, object]:
    """Mark the current case set approved; returns the fresh payload."""
    save_review(qa, STATUS_PASSED, cases_fingerprint(qa))
    return review_payload(qa)


def reject_cases(qa: Path, feedback: str) -> dict[str, object]:
    """Record a rejection with the reviewer's feedback."""
    save_review(qa, STATUS_REJECTED, cases_fingerprint(qa), feedback=feedback)
    return review_payload(qa)


def mark_stale(qa: Path, reason: str) -> dict[str, object]:
    """Flag the current cases as needing re-review after a doc change.

    Keeps any prior status/fingerprint but clears `approved` until a person
    re-approves (`approve_cases`) or redesigns (`reject_cases`).
    """
    state = load_review(qa)
    status = state.status if state else STATUS_AWAITING
    fingerprint = cases_fingerprint(qa)
    save_review(
        qa,
        status,
        fingerprint,
        feedback=state.feedback if state else "",
        stale_reason=reason,
    )
    return review_payload(qa)


def clear_stale(qa: Path) -> dict[str, object]:
    """Drop the doc-change flag while keeping the previous approval fingerprint."""
    state = load_review(qa)
    if not state or not state.stale_reason:
        return review_payload(qa)
    save_review(qa, state.status, state.fingerprint, feedback=state.feedback)
    return review_payload(qa)
