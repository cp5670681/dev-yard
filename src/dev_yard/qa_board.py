from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.qa import discover_cases, incomplete_run_payload, split_frontmatter
from dev_yard.qa_config import TestRejected, load_qa_config, qa_env_choices

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_UNREADABLE = "unreadable"


def qa_config_reason(root: Path) -> str:
    try:
        load_qa_config(root)
        return ""
    except TestRejected as e:
        return str(e)


def _load_yaml(path: Path) -> tuple[Any, bool]:
    if not path.is_file():
        return None, False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None, True
    return data, False


def latest_progress(qa: Path) -> dict[str, Any] | None:
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return None
    for run_dir in sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True):
        data, bad = _load_yaml(run_dir / "progress.yaml")
        if bad or not isinstance(data, dict):
            continue
        cases = data.get("cases") or []
        if any(
            isinstance(c, dict) and str(c.get("state") or "") in {"pending", "ready", "running"}
            for c in cases
        ):
            return data
        return None
    return None


def latest_run_summary(qa: Path) -> dict[str, Any] | None:
    runs = list_runs(qa)
    if not runs:
        return None
    top = runs[0]
    out = {
        "run_id": top.get("run_id"),
        "env": top.get("env"),
        "summary": top.get("summary") or {},
        "cases": top.get("cases") or [],
    }
    if top.get("env_fault"):
        out["env_fault"] = top["env_fault"]
    return out


def qa_detail_summary(root: Path, jira: str) -> dict[str, Any]:
    qa = paths.qa_dir(root, jira)
    cases = discover_cases(qa) if qa.is_dir() else []
    has_cases = bool(cases)
    has_meta = (qa / "meta.yaml").is_file()
    progress = latest_progress(qa) if qa.is_dir() else None
    env_names, active_env = qa_env_choices(root)
    # Same source the run uses for `--resume`, so the page never advertises a
    # different run than the one that would actually be resumed.
    incomplete = (
        incomplete_run_payload(
            qa, {c.id for c in cases} or None, active_env or None
        )
        if qa.is_dir()
        else None
    )
    latest_run = latest_run_summary(qa) if qa.is_dir() else None
    case_payloads = list_case_payloads(qa) if qa.is_dir() else []

    run_cases_map: dict[str, dict[str, Any]] = {}
    if latest_run and isinstance(latest_run.get("cases"), list):
        for rc in latest_run["cases"]:
            if isinstance(rc, dict) and rc.get("case"):
                run_cases_map[str(rc["case"])] = rc

    prog_cases_map: dict[str, dict[str, Any]] = {}
    if progress and isinstance(progress.get("cases"), list):
        for pc in progress["cases"]:
            if isinstance(pc, dict) and pc.get("id"):
                prog_cases_map[str(pc["id"])] = pc

    merged_cases: list[dict[str, Any]] = []
    for cp in case_payloads:
        cid = cp["id"]
        prog = prog_cases_map.get(cid)
        rc = run_cases_map.get(cid)

        state = "pending"
        model = str(cp.get("model") or "")
        reason = ""
        failure = None
        assertions: list[dict[str, Any]] = []
        screenshots: list[str] = []

        if prog:
            state = str(prog.get("state") or "pending")
            model = str(prog.get("model") or "")
            reason = str(prog.get("reason") or "")
        elif rc:
            state = str(rc.get("status") or "pending")
            model = str(rc.get("model") or "")
            reason = str(rc.get("reason") or "")
            failure = rc.get("failure")
            assertions = list(rc.get("assertions") or [])
            screenshots = list(rc.get("screenshots") or [])
        elif not cp.get("depends_on"):
            state = "ready"

        run_id = (
            str(progress.get("run_id"))
            if progress and progress.get("run_id")
            else (
                str(latest_run.get("run_id"))
                if latest_run and latest_run.get("run_id")
                else ""
            )
        )

        merged_cases.append(
            {
                "id": cid,
                "title": cp.get("title") or cid,
                "module": cp.get("module") or "",
                "priority": cp.get("priority") or "P1",
                "repo": cp.get("repo") or "",
                "covers": cp.get("covers") or [],
                "depends_on": cp.get("depends_on") or [],
                "state": state,
                "model": model,
                "reason": reason,
                "failure": failure,
                "assertions": assertions,
                "screenshots": screenshots,
                "run_id": run_id,
            }
        )

    return {
        "has_cases": has_cases,
        "has_meta": has_meta,
        "envs": env_names,
        "active_env": active_env,
        "latest_run": latest_run,
        "progress": progress,
        "incomplete_run": incomplete,
        "cases": merged_cases,
    }


def list_case_payloads(qa: Path) -> list[dict[str, Any]]:
    cases_root = qa / "cases"
    if not cases_root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(cases_root.rglob("case-*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = split_frontmatter(text)
        except Exception:
            out.append(
                {
                    "id": path.stem,
                    "module": path.parent.name,
                    "path": str(path),
                    "status": _UNREADABLE,
                    "body": text,
                }
            )
            continue
        deps = meta.get("depends_on") or []
        if isinstance(deps, str):
            deps = [x.strip() for x in deps.replace(",", " ").split() if x.strip()]
        elif not isinstance(deps, list):
            deps = []
        covers = meta.get("covers") or []
        if isinstance(covers, str):
            covers = [x.strip() for x in covers.replace(",", " ").split() if x.strip()]
        elif not isinstance(covers, list):
            covers = []
        out.append(
            {
                "id": str(meta.get("id") or path.stem),
                "module": path.parent.name,
                "title": str(meta.get("title") or path.stem),
                "priority": str(meta.get("priority") or ""),
                "repo": str(meta.get("repo") or ""),
                "covers": [str(c) for c in covers],
                "depends_on": [str(d) for d in deps],
                "body": body,
                "path": str(path),
            }
        )
    return out


def list_runs(qa: Path) -> list[dict[str, Any]]:
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for run_dir in sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True):
        result, bad_result = _load_yaml(run_dir / "result.yaml")
        progress, _ = _load_yaml(run_dir / "progress.yaml")
        if bad_result:
            runs.append(
                {
                    "run_id": run_dir.name,
                    "status": _UNREADABLE,
                    "env": "",
                    "summary": {},
                    "workers": [],
                    "progress": progress if isinstance(progress, dict) else None,
                    "cases": [],
                }
            )
            continue
        run = result if isinstance(result, dict) else {}
        case_rows = []
        listed = run.get("cases") if isinstance(run.get("cases"), list) else []
        seen: set[str] = set()
        for item in listed:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("case") or item.get("id") or "").strip()
            if not cid:
                continue
            seen.add(cid)
            case_rows.append(_case_run_row(run_dir, cid, item))
        for child in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            if child.name in {"repo-baseline", "_root_png"} or child.name in seen:
                continue
            if (child / "result.yaml").is_file():
                case_rows.append(_case_run_row(run_dir, child.name, {}))
        row = {
            "run_id": str(run.get("run_id") or run_dir.name),
            "env": str(run.get("env") or ""),
            "summary": run.get("summary") if isinstance(run.get("summary"), dict) else {},
            "workers": run.get("workers") if isinstance(run.get("workers"), list) else [],
            "progress": progress if isinstance(progress, dict) else None,
            "cases": case_rows,
        }
        fault = run.get("env_fault")
        if not isinstance(fault, dict) and isinstance(progress, dict):
            fault = progress.get("env_fault")
        if isinstance(fault, dict) and fault:
            row["env_fault"] = fault
        runs.append(row)
    return runs


def _assertion_rows(raw: Any) -> list[dict[str, Any]]:
    """Normalize agent-written assertion lists into a stable string shape."""
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "type": str(item.get("type") or ""),
                "expected": str(item.get("expected") or ""),
                "actual": str(item.get("actual") or ""),
                "status": str(item.get("status") or ""),
                "carrier": item.get("carrier"),
            }
        )
    return rows


def _case_run_row(run_dir: Path, cid: str, listed: dict[str, Any]) -> dict[str, Any]:
    case_dir = run_dir / cid
    raw, bad = _load_yaml(case_dir / "result.yaml")
    if bad:
        return {
            "case": cid,
            "status": _UNREADABLE,
            "repo": listed.get("repo") or "",
            "model": listed.get("model") or "",
            "reason": "结果文件无法解析",
            "failure": None,
            "assertions": [],
            "screenshots": _screenshots(case_dir),
        }
    data = raw if isinstance(raw, dict) else {}
    failure = data.get("failure") if isinstance(data.get("failure"), dict) else None
    return {
        "case": cid,
        "status": str(data.get("status") or listed.get("status") or ""),
        "repo": str(data.get("repo") or listed.get("repo") or ""),
        "model": str(data.get("model") or listed.get("model") or ""),
        "reason": str(data.get("reason") or listed.get("reason") or ""),
        "failure": failure,
        "assertions": _assertion_rows(data.get("assertions")),
        "screenshots": _screenshots(case_dir),
        "title": str(data.get("title") or ""),
    }


def _screenshots(case_dir: Path) -> list[str]:
    shots = case_dir / "screenshots"
    if not shots.is_dir():
        return []
    return sorted(
        p.name
        for p in shots.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXT
    )


def qa_page_payload(root: Path, jira: str) -> dict[str, Any]:
    qa = paths.qa_dir(root, jira)
    if not qa.is_dir():
        return {"meta": None, "cases": [], "runs": []}
    meta, bad_meta = _load_yaml(qa / "meta.yaml")
    if bad_meta:
        meta_out: Any = {"status": _UNREADABLE}
    else:
        meta_out = meta if isinstance(meta, dict) else None
    return {
        "meta": meta_out,
        "cases": list_case_payloads(qa),
        "runs": list_runs(qa),
    }


def qa_case_detail(root: Path, jira: str, case_id: str) -> dict[str, Any] | None:
    """One case: definition + newest run row + live state for the detail dialog."""
    qa = paths.qa_dir(root, jira)
    if not qa.is_dir():
        return None
    # Match by exact id against discovered payloads — never join case_id into a path.
    cp = next((c for c in list_case_payloads(qa) if c.get("id") == case_id), None)
    if cp is None:
        return None
    progress = latest_progress(qa)
    progress_cases: list[Any] = []
    if isinstance(progress, dict) and isinstance(progress.get("cases"), list):
        progress_cases = progress["cases"]
    prog = next(
        (p for p in progress_cases if isinstance(p, dict) and p.get("id") == case_id),
        None,
    )
    latest: dict[str, Any] | None = None
    for run in list_runs(qa):  # newest first: first run that actually has this case
        run_cases = run.get("cases") if isinstance(run.get("cases"), list) else []
        rc = next(
            (r for r in run_cases if isinstance(r, dict) and r.get("case") == case_id),
            None,
        )
        if rc is None:
            continue
        # While the case is still in flight, its failure/screenshots on disk are
        # stale (previous run) — hide them and let the live strip speak instead.
        live_active = bool(prog) and str(prog.get("state") or "") in {
            "pending",
            "ready",
            "running",
        }
        latest = {
            "run_id": str(run.get("run_id") or ""),
            "env": str(run.get("env") or ""),
            "state": str((prog or {}).get("state") or rc.get("status") or ""),
            "model": str((prog or {}).get("model") or rc.get("model") or ""),
            "reason": str((prog or {}).get("reason") or rc.get("reason") or ""),
            "failure": None if live_active else rc.get("failure"),
            "assertions": [] if live_active else list(rc.get("assertions") or []),
            "screenshots": [] if live_active else list(rc.get("screenshots") or []),
        }
        break
    live = None
    if prog:
        live = {
            "run_id": str((progress or {}).get("run_id") or ""),
            "state": str(prog.get("state") or ""),
            "model": prog.get("model"),
            "reason": str(prog.get("reason") or ""),
        }
    return {
        "case": {
            k: cp.get(k)
            for k in ("id", "title", "module", "priority", "repo", "covers", "depends_on", "path")
        },
        "body": cp.get("body") or "",
        "latest_run": latest,
        "live": live,
    }


def screenshot_file(
    root: Path, jira: str, run_id: str, case_id: str, name: str
) -> Path:
    for part, label in (
        (run_id, "run_id"),
        (case_id, "case_id"),
        (name, "name"),
    ):
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
        ):
            raise FileNotFoundError(label)
    suffix = Path(name).suffix.lower()
    if suffix not in _IMAGE_EXT:
        raise FileNotFoundError(name)
    evidence = (paths.qa_dir(root, jira) / "evidence").resolve()
    target = (evidence / run_id / case_id / "screenshots" / name).resolve()
    try:
        target.relative_to(evidence)
    except ValueError as e:
        raise FileNotFoundError(name) from e
    if not target.is_file():
        raise FileNotFoundError(name)
    return target
