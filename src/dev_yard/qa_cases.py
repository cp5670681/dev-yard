"""Case documents shared by design and execution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard import status as st
from dev_yard.parse import as_name_list
from dev_yard.qa_config import TestRejected
from dev_yard.qa_review import case_id_fallback, iter_case_markdown
from dev_yard.qa_schedule import CaseJob
from dev_yard.tickets import load_tickets

_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _meta_routes(qa: Path) -> dict[str, str]:
    meta = qa / "meta.yaml"
    if not meta.is_file():
        return {}
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return {}
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, dict):
        return {}
    return {str(k): str(v) for k, v in routes.items()}


def discover_cases(qa: Path) -> list[CaseJob]:
    cases_root = qa / "cases"
    if not cases_root.is_dir():
        return []
    out: list[CaseJob] = []
    seen: dict[str, Path] = {}
    for path in iter_case_markdown(cases_root):
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = split_frontmatter(text)
        except yaml.YAMLError as e:
            raise TestRejected(f"unreadable case frontmatter in {path}: {e}") from e
        cid = str(meta.get("id") or case_id_fallback(path)).strip()
        if not _CASE_ID.match(cid):
            raise TestRejected(
                f"invalid case id {cid!r} in {path}; use letters/digits/._- and start alphanumeric"
            )
        if cid in seen:
            raise TestRejected(f"duplicate case id {cid!r}: {seen[cid]} and {path}")
        seen[cid] = path
        deps = as_name_list(meta.get("depends_on"))
        covers = as_name_list(meta.get("covers"))
        module = path.parent.name
        data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
        if (
            "writes" in data
            and data.get("writes") is not None
            and not isinstance(data.get("writes"), list)
        ):
            raise TestRejected(f"case {cid} data.writes must be a list of table.column")
        writes_raw = data.get("writes") or []
        writes = [str(w).strip() for w in writes_raw if str(w).strip()]
        raw_driver = str(meta.get("driver") or data.get("driver") or "browser").strip().lower()
        if raw_driver in {"", "browser"}:
            driver = "browser"
        elif raw_driver == "host":
            driver = "host"
        else:
            raise TestRejected(f"case {cid} driver {raw_driver!r} must be browser or host")
        out.append(
            CaseJob(
                id=cid,
                title=str(meta.get("title") or cid),
                repo=str(meta.get("repo") or "").strip(),
                depends_on=[str(d).strip() for d in deps if str(d).strip()],
                priority=str(meta.get("priority") or "P1"),
                body=body,
                path=str(path),
                covers=[str(c).strip() for c in covers if str(c).strip()],
                module=module,
                account=str(meta.get("account") or "").strip(),
                setup=str(data.get("setup") or "").strip(),
                cleanup=str(data.get("cleanup") or "").strip(),
                verify=str(data.get("verify") or "").strip(),
                writes=writes,
                identity=str(data.get("identity") or "").strip(),
                db=str(data.get("db") or "").strip(),
                driver=driver,
            )
        )
    return out


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    rest = text[3:].lstrip("\n")
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    raw = rest[:end]
    body = rest[end + 4 :].lstrip("\n")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        return {}, body
    return data, body


def uncovered_changes(qa: Path, cases: list[CaseJob]) -> list[str]:
    """Change ids (meta.yaml `changes`) no case declares in `covers`."""
    meta = qa / "meta.yaml"
    if not meta.is_file():
        return []
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    changes = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(changes, list):
        return []
    ids = [
        str(c.get("id")).strip()
        for c in changes
        if isinstance(c, dict) and str(c.get("id") or "").strip()
    ]
    if not ids:
        return []
    covered = {c for job in cases for c in job.covers}
    return [cid for cid in ids if cid not in covered]


def _involved_aliases(root: Path, jira: str) -> list[str]:
    req = paths.req_dir(root, jira)
    aliases: set[str] = set()
    for t in load_tickets(req):
        if t.repo:
            aliases.add(t.repo)
    data = st.load(root, jira)
    for a in data.get("repos") or []:
        if a:
            aliases.add(str(a))
    for slot in (data.get("tickets") or {}).values():
        repo = (slot or {}).get("repo")
        if repo:
            aliases.add(str(repo))
    return sorted(aliases)
