"""What the requirement branch added versus what the live site has.

Pure functions so tests can drive them without `qa_ready.ENABLED`. A failed
probe or a missing worktree is `None` (undetermined), never a hard fail.
"""

from __future__ import annotations

import re
from pathlib import Path

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.config import load_repos
from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_exec import run_sql_lines

_ADD_COL = re.compile(
    r"\badd_column\s+(?::(?P<t1>\w+)|['\"](?P<t2>\w+)['\"])\s*,\s*"
    r"(?::(?P<c1>\w+)|['\"](?P<c2>\w+)['\"])",
    re.I,
)
_REMOVE_COL = re.compile(
    r"\bremove_column\s+(?::(?P<t1>\w+)|['\"](?P<t2>\w+)['\"])\s*,\s*"
    r"(?::(?P<c1>\w+)|['\"](?P<c2>\w+)['\"])",
    re.I,
)
_CREATE_TABLE = re.compile(
    r"\bcreate_table\s+(?::(?P<t1>\w+)|['\"](?P<t2>\w+)['\"])[^\n]*?"
    r"do\s+\|(?P<blk>\w+)\|(?P<body>.*?)end",
    re.I | re.S,
)
_T_COL = re.compile(r"^\s*(?P<blk>\w+)\.(?P<kind>\w+)\s+(?::(?P<c1>\w+)|['\"](?P<c2>\w+)['\"])", re.M)
_SKIP_KINDS = frozenset({"timestamps", "index", "indexes", "check"})
_INCLUDE_FREEZE = re.compile(r"\binclude\s+(?:\w+::)*FreezeModelConcern\b")
_CLASS = re.compile(r"^class\s+(\w+)", re.M)
_COL_PROBE = (
    "SELECT 1 FROM information_schema.columns "
    "WHERE LOWER(table_name) = '{table}' AND LOWER(column_name) = '{col}'"
)


def _ident(*groups: str | None) -> str:
    for g in groups:
        if g:
            return g
    return ""


def _diff_base(root: Path, jira: str, alias: str) -> str | None:
    wt = paths.req_worktree(root, jira, alias)
    if not (wt / ".git").exists():
        return None
    repos = load_repos(root)
    repo = repos.get(alias)
    base = repo.default_base if repo else ""
    data = st.load(root, jira) if (paths.req_dir(root, jira) / "STATUS.yaml").is_file() else {}
    saved = data.get("base_shas")
    saved = saved.get(alias) if isinstance(saved, dict) else None
    try:
        return gitops.freeze_base(wt, base, saved) or None
    except gitops.GitError:
        return None


def requirement_migrations(root: Path, jira: str, alias: str) -> list[str] | None:
    """Relative paths of migrate files this requirement *added*. `None` if unknown."""
    wt = paths.req_worktree(root, jira, alias)
    if not wt.is_dir() or not (wt / ".git").exists():
        return None
    base = _diff_base(root, jira, alias)
    if not base:
        return None
    try:
        out = gitops.run(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=A",
                f"{base}...HEAD",
                "--",
                "db/migrate",
            ],
            cwd=wt,
        )
    except gitops.GitError:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def parse_migration_text(text: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return (added, removed) column sets from one Rails migration body."""
    added: dict[str, set[str]] = {}
    removed: dict[str, set[str]] = {}

    def _add(store: dict[str, set[str]], table: str, col: str) -> None:
        if table and col:
            store.setdefault(table, set()).add(col)

    for m in _ADD_COL.finditer(text):
        _add(added, _ident(m.group("t1"), m.group("t2")), _ident(m.group("c1"), m.group("c2")))
    for m in _REMOVE_COL.finditer(text):
        _add(removed, _ident(m.group("t1"), m.group("t2")), _ident(m.group("c1"), m.group("c2")))
    for m in _CREATE_TABLE.finditer(text):
        table = _ident(m.group("t1"), m.group("t2"))
        blk = m.group("blk") or "t"
        body = m.group("body") or ""
        for col_m in _T_COL.finditer(body):
            if col_m.group("blk") != blk:
                continue
            kind = (col_m.group("kind") or "").lower()
            if kind in _SKIP_KINDS:
                continue
            col = _ident(col_m.group("c1"), col_m.group("c2"))
            if kind == "references" and col:
                _add(added, table, f"{col}_id")
            else:
                _add(added, table, col)
    return added, removed


def branch_new_columns(migrations: list[Path | str]) -> dict[str, set[str]]:
    """Net new columns after replaying add/create/remove in filename order."""
    net: dict[str, set[str]] = {}
    files = sorted((Path(p) for p in migrations), key=lambda p: p.name)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        added, removed = parse_migration_text(text)
        for table, cols in added.items():
            net.setdefault(table, set()).update(cols)
        for table, cols in removed.items():
            if table in net:
                net[table] -= cols
                if not net[table]:
                    net.pop(table, None)
    return net


def frozen_models(root: Path, jira: str, alias: str) -> set[str]:
    """Model class names that `include FreezeModelConcern` (or a namespaced twin)."""
    wt = paths.req_worktree(root, jira, alias)
    if not wt.is_dir():
        return set()
    names: set[str] = set()
    for path in wt.rglob("*.rb"):
        try:
            rel = path.relative_to(wt).as_posix()
        except ValueError:
            rel = path.as_posix()
        if any(part in f"/{rel}/" for part in ("/vendor/", "/node_modules/", "/tmp/")):
            continue
        if path.name.lower() in {"freeze_model_concern.rb", "freeze_model.rb"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not _INCLUDE_FREEZE.search(text):
            continue
        found = _CLASS.search(text)
        if found:
            names.add(found.group(1))
    return names


def catalogs_for_alias(root: Path, jira: str, cfg: QaConfig, alias: str) -> list[str]:
    repos = load_repos(root)
    repo = repos.get(alias)
    declared = list(repo.databases) if repo and repo.databases else []
    if declared:
        return declared
    names = [c.name for c in getattr(cfg.env, "db_catalogs", ()) or ()]
    if names:
        return names
    if cfg.env.db_default:
        return [cfg.env.db_default]
    return []


def _column_on_site(cfg: QaConfig, table: str, col: str, catalogs: list[str]) -> bool | None:
    """True if any catalog has the column; False if all succeed without it; None on probe failure."""
    saw_ok = False
    sql = _COL_PROBE.format(table=table.lower(), col=col.lower())
    for name in catalogs:
        try:
            rows = run_sql_lines(cfg, sql, catalog=name)
        except (TestRejected, TypeError):
            try:
                rows = run_sql_lines(cfg, sql)
            except (TestRejected, TypeError):
                continue
        saw_ok = True
        if rows:
            return True
    if not saw_ok:
        return None
    return False


def missing_on_site(
    root: Path, jira: str, cfg: QaConfig, alias: str
) -> tuple[list[str], list[str]] | None:
    """`(migration files, table.col)` missing on site, or `None` if we cannot tell."""
    rels = requirement_migrations(root, jira, alias)
    if rels is None:
        return None
    wt = paths.req_worktree(root, jira, alias)
    files = [wt / rel for rel in rels]
    wanted = branch_new_columns(files)
    if not wanted:
        return [], []
    catalogs = catalogs_for_alias(root, jira, cfg, alias)
    if not catalogs:
        return None
    missing_cols: list[str] = []
    missing_files: list[str] = []
    for table, cols in sorted(wanted.items()):
        for col in sorted(cols):
            present = _column_on_site(cfg, table, col, catalogs)
            if present is None:
                return None
            if not present:
                missing_cols.append(f"{table}.{col}")
    if missing_cols:
        missing_files = list(rels)
    return missing_files, missing_cols


def undeployed_columns(
    root: Path, jira: str, cfg: QaConfig, aliases: list[str]
) -> list[tuple[str, str, str]]:
    """Rows of (alias, table.col, migration_rel) for context.md.

    Live probe miss and probe-undetermined both list branch-new columns so
    design still keeps those assertions. Empty only when every alias is
    deployed or has no new columns.
    """
    rows: list[tuple[str, str, str]] = []
    for alias in aliases:
        rels = requirement_migrations(root, jira, alias)
        if rels is None:
            continue
        wt = paths.req_worktree(root, jira, alias)
        wanted = branch_new_columns([wt / rel for rel in rels])
        if not wanted:
            continue
        file_hint = rels[0] if rels else ""
        missing = missing_on_site(root, jira, cfg, alias)
        if missing is None:
            cols = [
                f"{table}.{col}"
                for table, names in sorted(wanted.items())
                for col in sorted(names)
            ]
        else:
            _files, cols = missing
        for col in cols:
            rows.append((alias, col, file_hint))
    return rows
