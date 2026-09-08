from __future__ import annotations

from pathlib import Path


def _seg(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid name {name!r}")
    return name


def find_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "repos.yaml").exists():
            return p
    raise FileNotFoundError("repos.yaml not found; run `dev-yard init` first")


def repos_yaml(root: Path) -> Path:
    return root / "repos.yaml"


def repos_dir(root: Path) -> Path:
    return root / ".repos"


def reqs_dir(root: Path) -> Path:
    return root / "reqs"


def req_dir(root: Path, jira: str) -> Path:
    return reqs_dir(root) / _seg(jira)


def req_worktree(root: Path, jira: str, alias: str) -> Path:
    return req_dir(root, jira) / "worktrees" / _seg(alias)


def child_worktree(root: Path, jira: str, alias: str, ticket_id: str) -> Path:
    return root / ".yard-worktrees" / _seg(jira) / _seg(alias) / _seg(ticket_id)


def status_path(root: Path, jira: str) -> Path:
    return req_dir(root, jira) / "STATUS.yaml"
