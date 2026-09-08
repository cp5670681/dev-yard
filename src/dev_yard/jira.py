from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def fetch_issue(key: str) -> tuple[str, str] | None:
    base = os.environ.get("JIRA_BASE_URL") or os.environ.get("JIRA_URL")
    email = os.environ.get("JIRA_EMAIL") or os.environ.get("JIRA_USER")
    token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_TOKEN")
    if not (base and email and token):
        return None
    url = f"{base.rstrip('/')}/rest/api/2/issue/{key}"
    req = urllib.request.Request(url)
    import base64

    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    fields = data.get("fields") or {}
    title = fields.get("summary") or key
    desc = fields.get("description") or ""
    if isinstance(desc, dict):
        desc = _adf_text(desc)
    return title, str(desc)


def _adf_text(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(_adf_text(x) for x in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text") or "")
        return _adf_text(node.get("content") or [])
    return ""
