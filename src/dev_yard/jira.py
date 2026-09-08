from __future__ import annotations

import os


def jira_creds() -> tuple[str, str, str] | None:
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
    if not (base and user and secret):
        return None
    return base.rstrip("/"), user, secret


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
