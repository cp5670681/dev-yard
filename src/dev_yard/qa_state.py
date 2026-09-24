"""Persistent QA state: one authority for phase, triage and pool health.

The evidence files stay authoritative for *facts* (``review.yaml``,
``design-verify/``, ``evidence/<run>/progress.yaml``); this module is the index
over them. It records the phase/triage/pool metadata so the CLI, the web board
and restart recovery share one view, and derives the live phase from the
evidence so a hand-edited workspace can never report a stale one.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard import status as st

STATE_FILE = "state.yaml"

# Phases the machine can be in. `missing` means no requirement dir at all.
PHASES = (
    "missing",
    "open",
    "frozen",
    "designing",
    "verifying",
    "awaiting_review",
    "approved",
    "running",
    "awaiting_triage",
    "recycled",
    "closed",
)

# Human-readable next step per phase, shared by the CLI and the board.
NEXT_ACTION = {
    "missing": "先创建需求：dev-yard req open <JIRA>",
    "open": "先对齐/拆票：dev-yard grill|spec|tickets",
    "frozen": "先提测：dev-yard req submit-test <JIRA>",
    "designing": "设计用例：dev-yard req test <JIRA> --design-only",
    "verifying": "数据核实中：dev-yard req test <JIRA> --verify-only",
    "awaiting_review": "审核用例：dev-yard req test <JIRA> --approve",
    "approved": "执行用例：dev-yard req test <JIRA> --run-only",
    "running": "继续执行：dev-yard req test <JIRA> --resume",
    "awaiting_triage": "判定失败：在失败用例上「下 bug」，或 --redesign 修用例",
    "recycled": "修复 B 票后重测：dev-yard implement <JIRA> --from-test",
    "closed": "已完成",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_path(root: Path, jira: str) -> Path:
    return paths.qa_dir(root, jira) / STATE_FILE


def load(root: Path, jira: str) -> dict[str, Any]:
    """The recorded state, or {} when absent/unreadable."""
    path = state_path(root, jira)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def save(root: Path, jira: str, data: dict[str, Any]) -> Path:
    path = state_path(root, jira)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**data, "version": 1, "jira": jira, "updated_at": _now()}
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    # Unique temp + atomic rename, like status.save: a concurrent writer (e.g.
    # a pool-trip callback racing a restart resume) must not collide on one
    # fixed temp name or truncate the live file.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def record(root: Path, jira: str, **fields: Any) -> dict[str, Any]:
    """Merge fields into the state document (never raises)."""
    with st.jira_lock(jira):
        data = load(root, jira)
        for key, value in fields.items():
            if value is None:
                continue
            data[key] = value
        try:
            save(root, jira, data)
        except OSError:
            pass
        return data


def record_pools(root: Path, jira: str, pools: dict[str, str]) -> dict[str, Any]:
    """Record per-pool health (`healthy` / `quarantined`), merging by id."""
    with st.jira_lock(jira):
        data = load(root, jira)
        prev = data.get("pools") if isinstance(data.get("pools"), dict) else {}
        merged = dict(prev)
        for pid, state in pools.items():
            entry = dict(merged.get(pid) or {}) if isinstance(merged.get(pid), dict) else {}
            entry["state"] = state
            merged[pid] = entry
        data["pools"] = merged
        try:
            save(root, jira, data)
        except OSError:
            pass
        return data


def record_triage(
    root: Path,
    jira: str,
    pending: list[str],
    auto_recycled: list[str],
    filed: dict[str, str] | None = None,
) -> dict[str, Any]:
    with st.jira_lock(jira):
        data = load(root, jira)
        prev = data.get("triage") if isinstance(data.get("triage"), dict) else {}
        merged_filed = prev.get("filed") if isinstance(prev.get("filed"), dict) else {}
        if filed:
            merged_filed = {**merged_filed, **filed}
        data["triage"] = {
            "pending": sorted(set(pending)),
            "auto_recycled": sorted(set(auto_recycled)),
            "filed": merged_filed,
        }
        try:
            save(root, jira, data)
        except OSError:
            pass
        return data


def triage(root: Path, jira: str) -> dict[str, Any]:
    data = load(root, jira)
    t = data.get("triage")
    if not isinstance(t, dict):
        return {"pending": [], "auto_recycled": [], "filed": {}}
    return {
        "pending": list(t.get("pending") or []),
        "auto_recycled": list(t.get("auto_recycled") or []),
        "filed": t.get("filed") if isinstance(t.get("filed"), dict) else {},
    }


def derive_phase(root: Path, jira: str) -> str:
    """Compute the live phase from STATUS + evidence (never trusts a stale write)."""
    from dev_yard import status as st

    req = paths.req_dir(root, jira)
    if not req.is_dir():
        return "missing"
    data = st.load(root, jira)
    phase = str(data.get("phase") or "open")
    if phase == "open":
        return "open"
    if phase == "frozen":
        return "frozen"
    if phase == "done":
        return "closed"
    # phase == "testing"
    from dev_yard.qa import discover_cases, find_incomplete_run
    from dev_yard.qa_review import cases_fingerprint, review_payload
    from dev_yard.qa_verify import verify_view

    qa = paths.qa_dir(root, jira)
    cases = discover_cases(qa) if qa.is_dir() else []
    if not cases:
        return "designing"
    ids = {c.id for c in cases}
    if find_incomplete_run(qa, ids, None) is not None:
        return "running"
    review = review_payload(qa)
    if not review.get("approved"):
        # `no-cases` is impossible here (cases were discovered above).
        return "awaiting_review"
    # Approved: a recorded run decides between triage / recycle / closed.
    from dev_yard.qa_board import latest_run_summary

    run = latest_run_summary(qa)
    if not run:
        # Approved but never run (or verify still pending).
        v = verify_view(qa, cases_fingerprint(qa))
        if v.get("present") and v.get("blocked"):
            return "verifying"
        return "approved"
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    failed = int(summary.get("failed") or 0)
    blocked = int(summary.get("blocked") or 0)
    if failed or blocked:
        t = triage(root, jira)
        if t["pending"]:
            return "awaiting_triage"
        if t["auto_recycled"] or t.get("filed"):
            return "recycled"
        # Env-only blocks are retryable; report as still running.
        return "running"
    if int(summary.get("total") or 0) == 0:
        return "approved"
    return "closed"


def status_payload(root: Path, jira: str) -> dict[str, Any]:
    """Everything `qa status` / the board needs, from one place."""
    from dev_yard.qa_review import review_payload

    qa = paths.qa_dir(root, jira)
    review = review_payload(qa) if qa.is_dir() else {"status": "no-cases"}
    t = triage(root, jira)
    recorded = load(root, jira)
    phase = derive_phase(root, jira)
    return {
        "jira": jira,
        "phase": phase,
        "next": NEXT_ACTION.get(phase, ""),
        "review": review,
        "triage": t,
        "pools": recorded.get("pools") if isinstance(recorded.get("pools"), dict) else {},
        "attempts": recorded.get("attempts") if isinstance(recorded.get("attempts"), dict) else {},
        "last_run_id": recorded.get("last_run_id"),
        "updated_at": recorded.get("updated_at"),
    }
