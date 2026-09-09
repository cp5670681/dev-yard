from __future__ import annotations

from pathlib import Path

# Shared workspace files live under reqs/ but are not Jira dirs.
RESERVED_REQ_NAMES = frozenset({"docs"})


def _seg(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"invalid name {name!r}")
    return name


def is_reserved_req_name(name: str) -> bool:
    n = (name or "").strip()
    return (not n) or n.startswith(".") or n.lower() in RESERVED_REQ_NAMES


def is_req_dir(path: Path) -> bool:
    if not path.is_dir() or is_reserved_req_name(path.name):
        return False
    return (path / "STATUS.yaml").is_file() or (path / "REQUIREMENT.md").is_file()


def iter_req_dirs(root: Path) -> list[Path]:
    rd = reqs_dir(root)
    if not rd.exists():
        return []
    return sorted((p for p in rd.iterdir() if is_req_dir(p)), key=lambda p: p.name)


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


def context_md(root: Path) -> Path:
    return reqs_dir(root) / "CONTEXT.md"


def adr_dir(root: Path) -> Path:
    return reqs_dir(root) / "docs" / "adr"


def req_dir(root: Path, jira: str) -> Path:
    name = _seg(jira)
    if is_reserved_req_name(name):
        raise ValueError(f"{name!r} is reserved (glossary/ADR live under reqs/docs)")
    return reqs_dir(root) / name


def req_worktree(root: Path, jira: str, alias: str) -> Path:
    return req_dir(root, jira) / "worktrees" / _seg(alias)


def child_worktree(root: Path, jira: str, alias: str, ticket_id: str) -> Path:
    return root / ".yard-worktrees" / _seg(jira) / _seg(alias) / _seg(ticket_id)


def status_path(root: Path, jira: str) -> Path:
    return req_dir(root, jira) / "STATUS.yaml"
