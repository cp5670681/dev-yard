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


def test_merge_worktree(root: Path, jira: str, alias: str) -> Path:
    """Scratch detached worktree used to merge a freeze branch into a test branch.

    Lives under `.yard-worktrees/` (like ticket worktrees) so it never shows up
    as a repo alias under `reqs/<jira>/worktrees/`. The leading underscore keeps
    it out of ticket-worktree teardown bookkeeping.
    """
    return root / ".yard-worktrees" / _seg(jira) / _seg(alias) / "_test-merge"


def status_path(root: Path, jira: str) -> Path:
    return req_dir(root, jira) / "STATUS.yaml"


def qa_dir(root: Path, jira: str) -> Path:
    return req_dir(root, jira) / "qa"


def qa_reports_dir(root: Path, jira: str) -> Path:
    return qa_dir(root, jira) / "reports"


def qa_accounts_discover_sql(root: Path, jira: str) -> Path:
    """Read-only query the design agent writes to find candidate accounts."""
    return qa_dir(root, jira) / "accounts-discover.sql"


def qa_yaml(root: Path) -> Path:
    return root / "qa.yaml"


def req_accounts_yaml(root: Path, jira: str) -> Path:
    """Requirement-level accounts (plaintext, gitignored under .yard-qa/)."""
    return root / ".yard-qa" / "requirements" / _seg(jira) / "accounts.yaml"


def exports_dir(root: Path) -> Path:
    """Workspace scratch dir for `req export` bundles written by the web console."""
    return root / ".yard-exports"


def export_uploads_dir(root: Path) -> Path:
    """Staging area for bundles uploaded to the web import endpoint."""
    return exports_dir(root) / "uploads"


def export_bundle_path(root: Path, jira: str, job_id: str) -> Path:
    """The archive one export job writes.

    Unique per job so a later export can never clobber a download the user is
    still fetching; the job-scoped download URL pins the exact artifact.
    """
    return exports_dir(root) / _seg(jira) / f"{_seg(job_id)}.tar.gz"
