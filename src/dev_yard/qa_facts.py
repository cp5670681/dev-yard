"""Machine-readable site facts for qa-design lint (constants, freeze, dialect)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.config import load_repos
from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_deploy import _read_worktree_text, frozen_models
from dev_yard.qa_exec import run_sql_lines
from dev_yard.qa_verify import dump_live_column_types

_CLASS = re.compile(r"^\s*(?:class|module)\s+([A-Za-z0-9_:]+)", re.M)
_CONST = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=", re.M)
_CONST_INT = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+)\b", re.M)
_DEPT_NAME = re.compile(r"DEPARTMENT")
_MAX_ID_LOOKUPS = 40
_NESTED = re.compile(r"\b([A-Z]\w*)::([A-Z][A-Z0-9_]*)\b")
_MUTATE = re.compile(
    r"\b([A-Z]\w*)\.(?:new|create|create!|save|save!|update|update!|delete_all|destroy|destroy_all)\b"
)


def constants_in_ruby(text: str) -> set[str]:
    """Class/module names and CONST = literals in one Ruby file."""
    names: set[str] = set()
    if not text:
        return names
    for m in _CLASS.finditer(text):
        names.add(m.group(1))
    cls = _CLASS.search(text)
    prefix = cls.group(1) if cls else ""
    for m in _CONST.finditer(text):
        names.add(m.group(1))
        if prefix:
            names.add(f"{prefix}::{m.group(1)}")
    return names


def scan_constants(root: Path, jira: str, alias: str) -> list[str]:
    """Class names and CONST = literals under app/models in a freeze worktree."""
    wt = paths.req_worktree(root, jira, alias)
    models = wt / "app" / "models"
    if not models.is_dir():
        return []
    names: set[str] = set()
    for path in models.rglob("*.rb"):
        names |= constants_in_ruby(_read_worktree_text(path))
    return sorted(names)


def scan_diff_constants(root: Path, jira: str, alias: str) -> list[str]:
    """Constants in Ruby files the freeze diff touches, outside app/models.

    Command and service classes never live under app/models. A case that names
    one of them should not spend a design round on a whitelist miss.
    """
    from dev_yard import gitops
    from dev_yard import status as st

    wt = paths.req_worktree(root, jira, alias)
    if not wt.is_dir():
        return []
    try:
        repos = load_repos(root)
        repo = repos.get(alias)
        if repo is None:
            return []
        saved = st.load(root, jira).get("base_shas")
        saved = saved if isinstance(saved, dict) else {}
        base = gitops.freeze_base(wt, repo.default_base, saved.get(alias))
        changed = gitops.changed_files(wt, base) | gitops.untracked_files(wt)
    except Exception:  # noqa: BLE001 — a missing diff is not a fact
        return []
    names: set[str] = set()
    for rel in sorted(changed):
        if not rel.endswith(".rb"):
            continue
        if rel.startswith("app/models/") or "/app/models/" in f"/{rel}":
            continue
        path = wt / rel
        if not path.is_file():
            continue
        names |= constants_in_ruby(_read_worktree_text(path))
    return sorted(names)


_CREATE_TABLE = re.compile(
    r"create_table\s+(?::([A-Za-z_]\w*)|['\"]([A-Za-z_]\w*)['\"])",
    re.I,
)
_ROLE_LITERAL = re.compile(
    r"(?:name_en|role_name)\s*[:=]\s*['\"]([a-z][a-z0-9_]{5,})['\"]"
)
_MAX_SYMBOLS = 12
_MISSING_CONST = re.compile(
    r"常量 ([A-Za-z_]\w*::[A-Z][A-Z0-9_]*) 不在 facts.yaml constants"
)


def symbols_in_source(text: str) -> tuple[list[str], list[str]]:
    """Role name_en literals and create_table names in source text."""
    roles: list[str] = []
    tables: list[str] = []
    for m in _ROLE_LITERAL.finditer(text or ""):
        roles.append(m.group(1))
    for m in _CREATE_TABLE.finditer(text or ""):
        tables.append(m.group(1) or m.group(2))
    return roles, tables


def collect_diff_symbols(
    root: Path, jira: str, aliases: list[str]
) -> tuple[list[str], list[str]]:
    """Role names and new tables named in the freeze diff, capped."""
    from dev_yard import gitops
    from dev_yard import status as st

    roles: list[str] = []
    tables: list[str] = []
    try:
        repos = load_repos(root)
        saved = st.load(root, jira).get("base_shas")
        saved = saved if isinstance(saved, dict) else {}
    except Exception:  # noqa: BLE001
        return [], []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        repo = repos.get(alias)
        if repo is None or not wt.is_dir():
            continue
        try:
            base = gitops.freeze_base(wt, repo.default_base, saved.get(alias))
            changed = gitops.changed_files(wt, base) | gitops.untracked_files(wt)
        except Exception:  # noqa: BLE001
            continue
        for rel in sorted(changed):
            if not rel.endswith((".rb", ".sql")):
                continue
            text = _read_worktree_text(wt / rel)
            found_roles, found_tables = symbols_in_source(text)
            roles.extend(found_roles)
            tables.extend(found_tables)
    return _unique_cap(roles), _unique_cap(tables)


def _unique_cap(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= _MAX_SYMBOLS:
            break
    return out


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def probe_role_names(
    cfg: QaConfig, catalog: str | None, names: list[str]
) -> list[dict[str, Any]]:
    """How many active employees hold each role name. Failure returns []."""
    names = _unique_cap([n for n in names if n])
    if not names:
        return []
    listed = ", ".join(_sql_quote(n) for n in names)
    sql = (
        "SELECT r.name_en, COUNT(e.id) AS active_employees "
        "FROM right_roles r "
        "LEFT JOIN right_job_title_roles jtr "
        "ON jtr.right_role_id = r.id AND jtr.roleable_type = 'Employee' "
        "LEFT JOIN employees e ON e.id = jtr.roleable_id AND e.active = 1 "
        f"WHERE r.name_en IN ({listed}) "
        "GROUP BY r.name_en"
    )
    try:
        rows = run_sql_lines(cfg, sql, catalog=catalog)
    except (TestRejected, TypeError, ValueError):
        return []
    found: dict[str, int] = {}
    for line in rows:
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) < 2:
            parts = str(line).split()
        if len(parts) < 2:
            continue
        try:
            count = int(parts[-1])
        except ValueError:
            continue
        found[parts[0]] = count
    return [
        {
            "name": name,
            "exists": name in found,
            "active_employees": found.get(name, 0),
        }
        for name in names
    ]


def probe_table_schemas(
    cfg: QaConfig, catalog: str | None, tables: list[str]
) -> list[dict[str, Any]]:
    """schema of each table name. Failure returns []."""
    tables = _unique_cap([t for t in tables if t])
    if not tables:
        return []
    listed = ", ".join(_sql_quote(t) for t in tables)
    sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        f"WHERE table_name IN ({listed})"
    )
    try:
        rows = run_sql_lines(cfg, sql, catalog=catalog)
    except (TestRejected, TypeError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in rows:
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) < 2:
            continue
        key = (parts[0], parts[1])
        if key in seen or not key[0] or not key[1]:
            continue
        seen.add(key)
        out.append({"schema": key[0], "table": key[1]})
    return out


def _ruby_text_defines(text: str, qualified: str) -> bool:
    """True when this file declares the class or module and assigns that CONST.

    The assignment must be a whole token (`ID =`, not `VALID_ID =`) in the same
    file as `class`/`module`. A hit in another file is not a definition.
    """
    if "::" not in qualified or not text:
        return False
    klass, const = qualified.rsplit("::", 1)
    last = klass.split("::")[-1]
    if not last or not const:
        return False
    if not re.search(
        rf"(?m)^\s*(?:class|module)\s+{re.escape(last)}\b(?!::)",
        text,
    ):
        return False
    return (
        re.search(
            rf"(?m)^(?![ \t]*#).*(?<![A-Za-z0-9_]){re.escape(const)}\s*=(?!=)",
            text,
        )
        is not None
    )


def _ruby_defines(wt: Path, qualified: str) -> bool:
    """True when one Ruby file in this worktree defines Class::CONST."""
    from dev_yard import gitops

    if "::" not in qualified or not wt.is_dir():
        return False
    last = qualified.rsplit("::", 1)[0].split("::")[-1]
    if not last:
        return False
    rels: set[str] = set()
    try:
        out = gitops.run(
            [
                "git",
                "grep",
                "-l",
                "-E",
                rf"(class|module)[[:space:]]+{re.escape(last)}([^A-Za-z0-9_:]|$)",
                "--",
                "*.rb",
            ],
            cwd=wt,
        )
        rels.update(ln.strip() for ln in out.splitlines() if ln.strip())
    except Exception:  # noqa: BLE001 — git grep exit 1 means "not here"
        pass
    try:
        rels.update(rel for rel in gitops.untracked_files(wt) if rel.endswith(".rb"))
    except Exception:  # noqa: BLE001 — not a git checkout
        pass
    root = wt.resolve()
    for rel in sorted(rels):
        path = (wt / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        if _ruby_text_defines(_read_worktree_text(path), qualified):
            return True
    return False


def absorb_worktree_constants(root: Path, jira: str, problems: list[str]) -> list[str]:
    """Add Const::NAME hits that already exist in a freeze worktree to facts.yaml.

    Returns the names that were added. The caller re-runs lint so those hits
    do not open a design round.
    """
    wanted = []
    for problem in problems:
        m = _MISSING_CONST.search(problem or "")
        if m:
            wanted.append(m.group(1))
    if not wanted:
        return []
    qa = paths.qa_dir(root, jira)
    data = load_facts(qa)
    worktrees = data.get("worktrees") if isinstance(data, dict) else None
    if not isinstance(worktrees, dict) or not worktrees:
        return []
    found: list[str] = []
    for name in sorted(set(wanted)):
        owners = [
            alias
            for alias in worktrees
            if _ruby_defines(paths.req_worktree(root, jira, str(alias)), name)
        ]
        if not owners:
            continue
        found.append(name)
        for alias in owners:
            entry = worktrees.get(alias)
            if not isinstance(entry, dict):
                continue
            consts = list(entry.get("constants") or [])
            if name not in consts:
                consts.append(name)
                consts.sort()
                entry["constants"] = consts
    if not found:
        return []
    (qa / "facts.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return found


def scan_department_constants(root: Path, jira: str, alias: str) -> list[dict[str, Any]]:
    """Integer constants whose name says DEPARTMENT, with the literal value."""
    wt = paths.req_worktree(root, jira, alias)
    models = wt / "app" / "models"
    if not models.is_dir():
        return []
    found: dict[str, int] = {}
    for path in models.rglob("*.rb"):
        text = _read_worktree_text(path)
        if not text:
            continue
        cls = _CLASS.search(text)
        prefix = cls.group(1) if cls else ""
        for m in _CONST_INT.finditer(text):
            if not _DEPT_NAME.search(m.group(1)):
                continue
            name = f"{prefix}::{m.group(1)}" if prefix else m.group(1)
            found[name] = int(m.group(2))
    return [{"name": name, "value": found[name]} for name in sorted(found)]


def probe_department_ids(
    cfg: QaConfig, catalog: str | None, constants: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ask the live DB which department-id constants actually have a row.

    A missing fact returns [] so lint does not invent a failure. Existence is
    not a people-count; the prompt only forbids using values that are absent.
    """
    values = []
    for item in constants[:_MAX_ID_LOOKUPS]:
        try:
            values.append(int(item["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    uniq = sorted(set(values))
    if not uniq:
        return []
    sql = "SELECT id FROM departments WHERE id IN (" + ",".join(str(v) for v in uniq) + ")"
    try:
        rows = run_sql_lines(cfg, sql, catalog=catalog)
    except (TestRejected, TypeError, ValueError):
        return []
    present: set[int] = set()
    for row in rows:
        token = str(row).strip().split()[0] if str(row).strip() else ""
        try:
            present.add(int(token))
        except ValueError:
            continue
    out: list[dict[str, Any]] = []
    for item in constants[:_MAX_ID_LOOKUPS]:
        try:
            value = int(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            {
                "name": item.get("name"),
                "value": value,
                "table": "departments",
                "exists": value in present,
            }
        )
    return out


def probe_dialect(cfg: QaConfig, catalog: str | None) -> tuple[str, str]:
    """Return (dialect, version) or empty strings when the probe fails."""
    try:
        rows = run_sql_lines(cfg, "SELECT version()", catalog=catalog)
    except (TestRejected, TypeError):
        try:
            rows = run_sql_lines(
                cfg,
                "SELECT banner FROM v$version WHERE ROWNUM = 1",
                catalog=catalog,
            )
        except (TestRejected, TypeError):
            return "", ""
        text = " ".join(rows).lower()
        ver = ""
        m = re.search(r"(\d+\.\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+)", text)
        if m:
            ver = m.group(1)
        return "oracle", ver
    text = " ".join(rows).lower()
    if "postgres" in text:
        m = re.search(r"postgresql\s+(\d+(?:\.\d+)?)", text)
        return "postgres", (m.group(1) if m else "")
    if "oracle" in text:
        m = re.search(r"(\d+\.\d+\.\d+)", text)
        return "oracle", (m.group(1) if m else "")
    if "mysql" in text or "mariadb" in text:
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
        return "mysql", (m.group(1) if m else "")
    # Plain version string from MySQL/MariaDB
    m = re.search(r"^(\d+\.\d+(?:\.\d+)?)", text.strip())
    if m:
        return "mysql", m.group(1)
    return "", ""


def _catalog_for_alias(repos: dict, cfg: QaConfig, alias: str) -> str | None:
    """The one catalog a repo's department ids are checked against."""
    repo = repos.get(alias)
    names = list(getattr(repo, "databases", ()) or ())
    if names:
        return str(names[0])
    default = getattr(cfg.env, "db_default", "") or ""
    return default or None


def _attach_department_lookups(
    root: Path,
    jira: str,
    cfg: QaConfig,
    aliases: list[str],
    repos: dict,
    catalogs: dict[str, Any],
) -> None:
    """Probe department-id constants once per declared catalog and store them there."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for alias in aliases:
        catalog = _catalog_for_alias(repos, cfg, alias)
        if not catalog or catalog not in catalogs:
            continue
        rows = scan_department_constants(root, jira, alias)
        if rows:
            grouped.setdefault(catalog, []).extend(rows)
    single = not list(getattr(cfg.env, "db_catalogs", ()) or ())
    for catalog, rows in grouped.items():
        seen: set[tuple[str, int]] = set()
        unique: list[dict[str, Any]] = []
        for row in rows:
            key = (str(row.get("name")), int(row["value"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        lookups = probe_department_ids(cfg, None if single else catalog, unique)
        if lookups:
            catalogs[catalog]["id_lookups"] = lookups


def _attach_diff_lookups(
    root: Path,
    jira: str,
    cfg: QaConfig,
    aliases: list[str],
    repos: dict,
    catalogs: dict[str, Any],
) -> None:
    """Probe role coverage and table schemas named in the freeze diff.

    A failed catalog is skipped. The design prompt reads these instead of
    guessing that a role has employees or that a table lives in public.
    """
    roles, tables = collect_diff_symbols(root, jira, aliases)
    if not roles and not tables:
        return
    single = not list(getattr(cfg.env, "db_catalogs", ()) or ())
    probed: set[str] = set()
    for alias in aliases:
        catalog = _catalog_for_alias(repos, cfg, alias)
        if not catalog or catalog not in catalogs or catalog in probed:
            continue
        probed.add(catalog)
        target = None if single else catalog
        if roles:
            rows = probe_role_names(cfg, target, roles)
            if rows:
                catalogs[catalog]["role_lookups"] = rows
        if tables:
            schemas = probe_table_schemas(cfg, target, tables)
            if schemas:
                catalogs[catalog]["table_schemas"] = schemas


def _lookup_lines(data: dict[str, Any]) -> str:
    catalogs = data.get("catalogs") if isinstance(data, dict) else None
    if not isinstance(catalogs, dict):
        return ""
    lines: list[str] = []
    for name, entry in catalogs.items():
        if not isinstance(entry, dict):
            continue
        for row in entry.get("role_lookups") or []:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {name} 角色 {row.get('name')}: exists={row.get('exists')} "
                f"active_employees={row.get('active_employees')}"
            )
        for row in entry.get("table_schemas") or []:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {name} 表 {row.get('table')}: schema={row.get('schema')}"
            )
    if not lines:
        return ""
    return (
        "现场探针（写 verify.sql 前先看；active_employees=0 的角色不要写成能查到人，"
        "记未覆盖。表的 schema 以这里为准，不要写死 public）：\n"
        + "\n".join(lines)
        + "\n\n"
    )


def build_facts(root: Path, jira: str, cfg: QaConfig, aliases: list[str]) -> dict[str, Any]:
    repos = load_repos(root)
    catalogs: dict[str, Any] = {}
    db_cats = list(getattr(cfg.env, "db_catalogs", ()) or ())
    if not db_cats and getattr(cfg.env, "db_url", ""):
        dialect, version = probe_dialect(cfg, None)
        tables: dict[str, dict[str, str]] = {}
        if dialect:
            tables = dump_live_column_types(cfg, [], None) or {}
        entry: dict[str, Any] = {}
        if dialect:
            entry["dialect"] = dialect
        if version:
            entry["version"] = version
        if tables:
            entry["tables"] = tables
        if entry:
            catalogs[cfg.env.db_default or "default"] = entry
    else:
        for cat in db_cats:
            dialect, version = probe_dialect(cfg, cat.name)
            tables = {}
            if dialect:
                # Optional column dump; empty when the live probe fails.
                tables = dump_live_column_types(cfg, [], cat.name) or {}
            entry = {}
            if dialect:
                entry["dialect"] = dialect
            if version:
                entry["version"] = version
            if tables:
                entry["tables"] = tables
            catalogs[cat.name] = entry
    _attach_department_lookups(root, jira, cfg, aliases, repos, catalogs)
    _attach_diff_lookups(root, jira, cfg, aliases, repos, catalogs)
    worktrees: dict[str, Any] = {}
    for alias in aliases:
        consts = sorted(
            set(scan_constants(root, jira, alias)) | set(scan_diff_constants(root, jira, alias))
        )
        frozen = sorted(frozen_models(root, jira, alias))
        worktrees[alias] = {
            "constants": consts,
            "frozen_models": frozen,
        }
    sites: dict[str, Any] = {}
    spec = getattr(cfg.env, "exec_cfg", None)
    for s in list(getattr(spec, "sites", ()) or ()):
        sites[s.name] = {
            "runner": s.runner or "",
            "rails": s.rails or "",
        }
        repo = repos.get(s.name)
        if repo and repo.exec:
            sites[s.name]["exec"] = repo.exec
    return {"catalogs": catalogs, "worktrees": worktrees, "sites": sites}


def write_facts_yaml(root: Path, jira: str, cfg: QaConfig, aliases: list[str]) -> Path:
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    path = qa / "facts.yaml"
    data = build_facts(root, jira, cfg, aliases)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def facts_prompt_block(qa: Path, *, limit: int = 8000) -> str:
    """Inject facts.yaml into the design prompt so the agent cannot skip the file."""
    data = load_facts(qa)
    if not data:
        path = qa / "facts.yaml"
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
        else:
            return ""
    else:
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    if not text:
        return ""
    summary = _lookup_lines(data) if data else ""
    if len(text) > limit:
        text = text[:limit] + "\n… (truncated)"
    return (
        "\n\n# qa/facts.yaml（宿主生成的现场事实；写 setup/verify 前必读）\n\n"
        "常量存在 ≠ 语义正确（数字 ID 可能不是部门 id）。"
        "id_lookups 里 exists=false 的值禁止当作该表的 id；"
        "部门过滤只用 exists=true 的部门常量。"
        "freeze_models 写入必须 skip_freeze。"
        "dialect/version 决定 SQL：oracle 11 用 ROWNUM，不要 FETCH FIRST；"
        "AR 2.x 用 find(:all, :conditions => …)，不要 Model.where。\n\n"
        f"{summary}"
        f"```yaml\n{text}\n```\n"
    )


def load_facts(qa: Path) -> dict[str, Any]:
    path = qa / "facts.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


_STDLIB_MODULES = {
    "ActionController",
    "ActionMailer",
    "ActionView",
    "ActiveModel",
    "ActiveRecord",
    "ActiveResource",
    "ActiveSupport",
    "Benchmark",
    "BigDecimal",
    "Date",
    "DateTime",
    "Digest",
    "ERB",
    "Exception",
    "File",
    "GC",
    "JSON",
    "Kernel",
    "Logger",
    "Net",
    "ObjectSpace",
    "OpenSSL",
    "Process",
    "Rails",
    "SecureRandom",
    "StandardError",
    "Time",
    "Timeout",
    "URI",
    "YAML",
}


def referenced_constants(blob: str) -> set[str]:
    return {f"{a}::{b}" for a, b in _NESTED.findall(blob or "")}


def lint_facts_blob(blob: str, constants: list[str], frozen: set[str]) -> list[str]:
    """Rule 2 (unknown Const::NAME) and rule 3 (frozen write needs skip_freeze)."""
    out: list[str] = []
    if constants:
        known = set(constants)
        for name in sorted(referenced_constants(blob)):
            prefix = name.split("::", 1)[0]
            if prefix in _STDLIB_MODULES:
                continue
            if name not in known and prefix not in known:
                out.append(f"常量 {name} 不在 facts.yaml constants")
    if frozen:
        if "skip_freeze" not in blob:
            for m in _MUTATE.finditer(blob):
                model = m.group(1)
                if model in frozen:
                    out.append(f"{model} 带 freeze_model_concern，需显式 skip_freeze")
                    break
    return out
