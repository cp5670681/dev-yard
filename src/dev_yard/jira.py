from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dev_yard import paths
from dev_yard import status as st
from dev_yard.config import load_repos
from dev_yard.env import load_env
from dev_yard.tickets import HEADING, parse_tickets


def jira_creds(root: Path | None = None) -> tuple[str, str, str] | None:
    if root:
        load_env(root)
    base = os.environ.get("JIRA_BASE_URL") or os.environ.get("JIRA_URL")
    user = (
        os.environ.get("JIRA_EMAIL")
        or os.environ.get("JIRA_USER")
        or os.environ.get("JIRA_USERNAME")
    )
    secret = (
        os.environ.get("JIRA_API_TOKEN")
        or os.environ.get("JIRA_TOKEN")
        or os.environ.get("JIRA_PASSWORD")
    )
    if base and user and secret:
        return base.rstrip("/"), user, secret

    # Check MCP configs (same as pi / agy mcp-atlassian-pro in req open)
    candidates = [
        Path.home() / ".pi" / "agent" / "mcp.json",
        Path.home() / ".gemini" / "config" / "mcp_config.json",
        Path.home() / ".gemini" / "antigravity" / "mcp_config.json",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers") or {}
            atlassian = servers.get("mcp-atlassian-pro") or servers.get("atlassian") or {}
            headers = atlassian.get("headers") or {}
            j_url = headers.get("X-Jira-Url")
            auth = headers.get("Authorization") or ""
            if j_url and auth.startswith("Basic "):
                raw = base64.b64decode(auth[6:].strip()).decode("utf-8")
                if ":" in raw:
                    u, s = raw.split(":", 1)
                    return j_url.rstrip("/"), u, s
        except Exception:
            continue

    return None


def adf_text(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(adf_text(x) for x in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text") or "")
        return adf_text(node.get("content") or [])
    return ""


def _jira_request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    creds: tuple[str, str, str] | None = None,
    root: Path | None = None,
    timeout: int = 30,
) -> Any:
    creds = creds or jira_creds(root)
    if not creds:
        raise ValueError(
            "Jira 凭证未配置，请配置环境变量 JIRA_URL/JIRA_USER/JIRA_PASSWORD，"
            "或在 ~/.pi/agent/mcp.json / ~/.gemini/config/mcp_config.json 中配置 mcp-atlassian-pro"
        )
    base, user, secret = creds
    url = f"{base}/{path.lstrip('/')}"
    headers = {
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode(),
    }
    data_bytes: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        msg = f"Jira API HTTP {e.code} on {method} {path}"
        if err_body:
            try:
                parsed_err = json.loads(err_body)
                errors = parsed_err.get("errors") or parsed_err.get("errorMessages")
                if errors:
                    msg += f": {errors}"
            except Exception:
                msg += f": {err_body[:200]}"
        raise RuntimeError(msg) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Jira 请求失败: {e}") from e


def fetch_parent_issue(
    key: str, creds: tuple[str, str, str] | None = None, root: Path | None = None
) -> dict[str, Any]:
    data = _jira_request(
        f"rest/api/2/issue/{key}?fields=summary,project,assignee,components",
        method="GET",
        creds=creds,
        root=root,
    )
    fields = data.get("fields") or {}
    project = fields.get("project") or {}
    assignee = fields.get("assignee") or {}
    components = [c.get("name") for c in fields.get("components") or [] if c.get("name")]
    return {
        "key": data.get("key") or key,
        "id": data.get("id"),
        "summary": fields.get("summary") or key,
        "project_key": project.get("key"),
        "project_id": project.get("id"),
        "assignee_name": assignee.get("name") or assignee.get("key"),
        "assignee_display": assignee.get("displayName") or assignee.get("display_name"),
        "components": components,
    }


def extract_ticket_bodies(text: str) -> dict[str, str]:
    """Extract markdown body sections for each ticket in TICKETS.md."""
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current_id: str | None = None
    in_meta = True

    for line in lines:
        m = HEADING.match(line.strip())
        if m:
            current_id = m.group(1)
            sections[current_id] = []
            in_meta = True
            continue
        if current_id is None:
            continue
        if in_meta:
            if line.strip().startswith("-"):
                continue
            if not line.strip():
                continue
            in_meta = False
        sections[current_id].append(line)

    return {tid: "\n".join(body_lines).strip() for tid, body_lines in sections.items()}


def preview_dev_subtasks(root: Path, jira: str) -> dict[str, Any]:
    req = paths.req_dir(root, jira)
    p = req / "TICKETS.md"
    if not p.is_file():
        raise FileNotFoundError(f"未找到 {jira} 的 TICKETS.md，请先完成拆票（tickets 阶段）")

    text = p.read_text(encoding="utf-8")
    parsed = parse_tickets(text)
    dev_tickets = [t for t in parsed if t.repo]
    if not dev_tickets:
        raise ValueError(f"{jira} 的 TICKETS.md 中没有研发票（带 repo 的票）")

    bodies = extract_ticket_bodies(text)

    # Repo notes from repos.yaml
    try:
        repos_map = load_repos(root)
    except Exception:
        repos_map = {}

    # Status slots
    status_data = st.load(root, jira)
    slots = st.tickets_map(status_data.get("tickets"))

    # Creds & Parent issue
    creds = jira_creds(root)
    parent_info: dict[str, Any] = {}
    if creds:
        try:
            parent_info = fetch_parent_issue(jira, creds=creds, root=root)
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    for t in dev_tickets:
        repo_obj = repos_map.get(t.repo)
        repo_note = (repo_obj.note if repo_obj and repo_obj.note else t.repo) if t.repo else ""

        clean_title = t.title
        if repo_note and clean_title.startswith(repo_note):
            clean_title = clean_title[len(repo_note) :].lstrip("：:、 ")
        summary = f"【{repo_note}】{clean_title}" if repo_note else clean_title

        slot = slots.get(t.id) or {}
        existing_key = slot.get("jira_key")
        existing_url = slot.get("jira_url")

        items.append(
            {
                "id": t.id,
                "title": t.title,
                "summary": summary,
                "repo": t.repo,
                "repo_note": repo_note,
                "story_point": 0,
                "description": bodies.get(t.id, ""),
                "jira_key": existing_key,
                "jira_url": existing_url,
                "created": bool(existing_key),
            }
        )

    return {
        "jira": jira,
        "parent_summary": parent_info.get("summary") or status_data.get("title") or jira,
        "project_key": parent_info.get("project_key") or jira.split("-")[0],
        "default_assignee": parent_info.get("assignee_name"),
        "default_assignee_display": parent_info.get("assignee_display"),
        "items": items,
    }


def create_dev_subtasks(
    root: Path,
    jira: str,
    items: list[dict[str, Any]] | None = None,
    assignee: str | None = None,
) -> list[dict[str, Any]]:
    creds = jira_creds(root)
    if not creds:
        raise ValueError(
            "Jira 凭证未配置，请配置环境变量 JIRA_URL/JIRA_USER/JIRA_PASSWORD，"
            "或在 ~/.pi/agent/mcp.json / ~/.gemini/config/mcp_config.json 中配置 mcp-atlassian-pro"
        )

    base, _, _ = creds
    parent_info = fetch_parent_issue(jira, creds=creds, root=root)
    project_key = parent_info.get("project_key") or jira.split("-")[0]
    target_assignee = assignee or parent_info.get("assignee_name")

    preview = preview_dev_subtasks(root, jira)
    preview_items = {it["id"]: it for it in preview["items"]}

    if items is None:
        targets = [it for it in preview["items"] if not it.get("created")]
    else:
        targets = []
        for it in items:
            tid = it.get("id")
            if not tid or tid not in preview_items:
                continue
            base_it = dict(preview_items[tid])
            base_it.update(it)
            targets.append(base_it)

    created_results: list[dict[str, Any]] = []

    with st.jira_lock(jira):
        data = st.load(root, jira)
        slots = st.tickets_map(data.get("tickets"))

        for target in targets:
            tid = target["id"]
            slot = slots.setdefault(tid, {})
            if slot.get("jira_key") and not target.get("force"):
                continue

            summary = (target.get("summary") or "").strip() or target.get("title") or f"Subtask {tid}"
            if len(summary) > 250:
                summary = summary[:247] + "..."

            raw_sp = target.get("story_point", 0)
            try:
                sp: float | int = float(raw_sp or 0)
                if sp.is_integer():
                    sp = int(sp)
            except (ValueError, TypeError):
                sp = 0

            desc = target.get("description", "")
            item_assignee = target.get("assignee") or target_assignee

            payload: dict[str, Any] = {
                "fields": {
                    "project": {"key": project_key},
                    "parent": {"key": jira},
                    "summary": summary,
                    "issuetype": {"name": "子任务"},
                    "customfield_10106": sp,
                }
            }
            if item_assignee:
                payload["fields"]["assignee"] = {"name": item_assignee}

            try:
                res = _jira_request(
                    "rest/api/2/issue", method="POST", payload=payload, creds=creds, root=root
                )
            except RuntimeError as e:
                # If customfield_10106 fails (e.g. project does not have that customfield), retry without it
                if "customfield_10106" in str(e):
                    payload["fields"].pop("customfield_10106", None)
                    res = _jira_request(
                        "rest/api/2/issue", method="POST", payload=payload, creds=creds, root=root
                    )
                else:
                    raise

            new_key = res.get("key")
            new_id = res.get("id")
            subtask_url = f"{base}/browse/{new_key}"

            slot["jira_key"] = new_key
            slot["jira_url"] = subtask_url
            data["tickets"] = slots
            st.save(root, jira, data)

            # Post task description as comment if non-empty
            if desc and new_key:
                comment_body = (
                    f"### 任务说明（对应研发票 {tid}）\n- **代码仓**：{target.get('repo', '')}\n\n{desc}"
                )
                try:
                    _jira_request(
                        f"rest/api/2/issue/{new_key}/comment",
                        method="POST",
                        payload={"body": comment_body},
                        creds=creds,
                        root=root,
                    )
                except Exception:
                    pass

            created_results.append(
                {
                    "id": tid,
                    "jira_key": new_key,
                    "jira_id": new_id,
                    "url": subtask_url,
                    "summary": summary,
                }
            )

    return created_results
