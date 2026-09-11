from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths, status as st
from dev_yard.tickets import Ticket, load_tickets

KINDS = ("contract", "test")


@dataclass
class SpawnResult:
    ids: list[str]
    tickets: list[Ticket] = field(default_factory=list)


def _as_list(val: Any) -> list[str]:
    if val is None or val is False:
        return []
    if isinstance(val, str):
        return [x.strip() for x in val.replace(",", " ").split() if x.strip()]
    if isinstance(val, (list, tuple)):
        out: list[str] = []
        for x in val:
            out.extend(_as_list(x))
        return out
    return [str(val)]


def normalize_findings(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or f"F{i + 1}")
        parallel = item.get("parallel")
        if isinstance(parallel, str):
            parallel = parallel.lower() in {"true", "yes", "1"}
        out.append(
            {
                "id": fid,
                "title": str(item.get("title") or fid).strip() or fid,
                "detail": str(item.get("detail") or "").strip(),
                "repo": str(item.get("repo") or "").strip(),
                "depends_on": _as_list(item.get("depends_on")),
                "parallel": parallel,
            }
        )
    return out


def last_done_ticket_for_repo(
    tickets: list[Ticket], repo: str, data: dict[str, Any]
) -> str | None:
    last: str | None = None
    slots = st.tickets_map(data.get("tickets"))
    for t in tickets:
        if t.repo == repo and (slots.get(t.id) or {}).get("state") == "done":
            last = t.id
    return last


def _next_ids(existing: list[str], n: int) -> list[str]:
    used = set(existing)
    max_b = 0
    for tid in existing:
        if tid.startswith("B") and tid[1:].isdigit():
            max_b = max(max_b, int(tid[1:]))
    i = max_b
    out: list[str] = []
    while len(out) < n:
        i += 1
        cand = f"B{i}"
        if cand not in used:
            out.append(cand)
    return out


def parse_findings_from_summary(text: str) -> list[dict[str, Any]]:
    """Pull a YAML `findings:` list out of a contract/test agent summary."""
    if not (text or "").strip():
        return []
    idx = text.find("\nfindings:")
    if idx < 0:
        idx = 0 if text.lstrip().startswith("findings:") else text.find("findings:")
        if idx < 0:
            return []
        if idx > 0 and text[idx - 1] not in "\n":
            return []
    blob = text[idx:].lstrip()
    cut = blob.find("\n## ")
    if cut > 0:
        blob = blob[:cut]
    try:
        loaded = yaml.safe_load(blob)
    except yaml.YAMLError:
        return []
    if isinstance(loaded, dict) and loaded.get("findings") is not None:
        raw = loaded["findings"]
    elif isinstance(loaded, list):
        raw = loaded
    else:
        return []
    if not isinstance(raw, list):
        return []
    return normalize_findings(raw)


def _repo_list(data: dict[str, Any], tickets: list[Ticket]) -> list[str]:
    repos = list(data.get("repos") or [])
    if repos:
        return [str(r) for r in repos if r]
    return sorted({t.repo for t in tickets if t.repo})


def _synthetic_findings(
    kind: str, data: dict[str, Any], tickets: list[Ticket]
) -> list[dict[str, Any]]:
    summary = (data.get("contract_summary") or "").strip() if kind == "contract" else ""
    title = "契约缺口" if kind == "contract" else "测试缺陷"
    return [
        {
            "id": f"repo:{repo}",
            "title": f"{title} ({repo})",
            "detail": summary,
            "repo": repo,
            "depends_on": [],
            "parallel": True,
        }
        for repo in _repo_list(data, tickets)
    ]


def expand_findings_repos(
    findings: list[dict[str, Any]],
    repos: list[str],
) -> list[dict[str, Any]]:
    """Keep findings that name a repo. Empty repo is dropped (ingest should reject it)."""
    known = set(repos)
    out: list[dict[str, Any]] = []
    for f in findings:
        repo = (f.get("repo") or "").strip()
        if not repo:
            continue
        if known and repo not in known:
            continue
        out.append(f)
    return out


def _findings_for_kind(
    kind: str,
    data: dict[str, Any],
    tickets: list[Ticket],
) -> list[dict[str, Any]]:
    repos = _repo_list(data, tickets)
    if kind == "test":
        slot = data.get("test") if isinstance(data.get("test"), dict) else {}
        findings = normalize_findings(slot.get("findings") or [])
    else:
        findings = normalize_findings(data.get("contract_findings") or [])
        if not findings:
            findings = parse_findings_from_summary((data.get("contract_summary") or ""))
    findings = expand_findings_repos(findings, repos)
    if findings:
        return findings
    if kind == "test":
        return []
    if data.get("contract_review") == "failed":
        return _synthetic_findings(kind, data, tickets)
    return []


def _render_ticket(
    tid: str,
    title: str,
    repo: str,
    depends_on: list[str],
    parallel: bool,
    kind: str,
    finding_id: str,
    detail: str,
) -> str:
    deps = " ".join(depends_on)
    lines = [
        f"## {tid}: {title}",
        f"- repo: {repo}",
        f"- depends_on: {deps}".rstrip(),
        f"- parallel: {'true' if parallel else 'false'}",
        f"- source: {kind}",
        f"- finding: {finding_id}",
        "",
        "### 做什么",
        f"1. 只修本条缺陷（{finding_id}），不要改其它 bug 票范围。",
    ]
    if detail:
        lines.append("")
        lines.append(detail)
    lines += [
        "",
        "### 验收",
        "- 本条缺陷关闭；其它票的代码与范围保持不动。",
        "",
    ]
    return "\n".join(lines)


def spawn_fix_tickets(
    root: Path, jira: str, kind: str, persist: bool = True
) -> list[str]:
    """Append independent bug tickets for contract/test findings. Idempotent per finding id."""
    return spawn_fix_tickets_result(root, jira, kind, persist=persist).ids


def spawn_fix_tickets_result(
    root: Path, jira: str, kind: str, persist: bool = True
) -> SpawnResult:
    if kind not in KINDS:
        raise ValueError(f"unknown fix kind {kind!r}")
    req = paths.req_dir(root, jira)
    md_path = req / "TICKETS.md"
    with st.jira_lock(jira):
        existing = load_tickets(req) if md_path.exists() else []
        data = st.sync_tickets(st.load(root, jira), existing)
        findings = _findings_for_kind(kind, data, existing)
        by_id = {t.id: t for t in existing}
        known = {
            (t.source, t.finding): t.id
            for t in existing
            if t.source and t.finding
        }
        pending: list[dict[str, Any]] = []
        for f in findings:
            if not f.get("repo"):
                continue
            if (kind, f["id"]) in known:
                continue
            pending.append(f)
        matched_ids = [known[(kind, f["id"])] for f in findings if (kind, f["id"]) in known]
        if not pending:
            if persist:
                st.refresh_ready(data)
                st.save(root, jira, data)
            return SpawnResult(
                ids=matched_ids,
                tickets=[by_id[tid] for tid in matched_ids if tid in by_id],
            )

        new_ids = _next_ids([t.id for t in existing], len(pending))
        assigned: dict[str, str] = {}
        for f, tid in zip(pending, new_ids):
            assigned[f["id"]] = tid
            known[(kind, f["id"])] = tid

        blocks: list[str] = []
        created: list[Ticket] = []
        snapshot = list(existing)
        for f, tid in zip(pending, new_ids):
            deps: list[str] = []
            for dep in f["depends_on"]:
                if dep in assigned:
                    deps.append(assigned[dep])
                elif any(t.id == dep for t in snapshot):
                    deps.append(dep)
                elif (kind, dep) in known:
                    deps.append(known[(kind, dep)])
            last = last_done_ticket_for_repo(existing, f["repo"], data)
            if last and last not in deps:
                deps.append(last)
            has_new_dep = any(d in new_ids for d in deps)
            if f["parallel"] is None:
                parallel = not has_new_dep
            else:
                parallel = bool(f["parallel"]) and not has_new_dep
            block = _render_ticket(
                tid,
                f["title"],
                f["repo"],
                deps,
                parallel,
                kind,
                f["id"],
                f["detail"],
            )
            blocks.append(block)
            ticket = Ticket(
                id=tid,
                title=f["title"],
                repo=f["repo"],
                depends_on=deps,
                parallel=parallel,
                source=kind,
                finding=f["id"],
            )
            snapshot.append(ticket)
            created.append(ticket)

        if persist:
            text = md_path.read_text(encoding="utf-8") if md_path.exists() else f"# Tickets — {jira}\n"
            if text and not text.endswith("\n"):
                text += "\n"
            md_path.write_text(text + "\n" + "\n".join(blocks), encoding="utf-8")
            parsed = load_tickets(req)
            data = st.sync_tickets(st.load(root, jira), parsed)
            st.refresh_ready(data)
            st.save(root, jira, data)
        return SpawnResult(ids=[t.id for t in created], tickets=created)


def fix_ticket_ids(
    tickets: dict[str, Ticket],
    ids: list[str] | None,
    data: dict[str, Any],
    kind: str,
) -> list[str]:
    if ids:
        return [tid for tid in ids if tid in tickets]
    runnable = {"ready", "blocked", "implementing"}
    slots = st.tickets_map(data.get("tickets"))
    out: list[str] = []
    for tid, t in tickets.items():
        if t.source != kind:
            continue
        if (slots.get(tid) or {}).get("state") in runnable:
            out.append(tid)
    return out
