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


def scan_constants(root: Path, jira: str, alias: str) -> list[str]:
    """Class names and CONST = literals under app/models in a freeze worktree."""
    wt = paths.req_worktree(root, jira, alias)
    models = wt / "app" / "models"
    if not models.is_dir():
        return []
    names: set[str] = set()
    for path in models.rglob("*.rb"):
        text = _read_worktree_text(path)
        if not text:
            continue
        for m in _CLASS.finditer(text):
            names.add(m.group(1))
        cls = _CLASS.search(text)
        prefix = cls.group(1) if cls else ""
        for m in _CONST.finditer(text):
            names.add(m.group(1))
            if prefix:
                names.add(f"{prefix}::{m.group(1)}")
    return sorted(names)


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
    worktrees: dict[str, Any] = {}
    for alias in aliases:
        consts = scan_constants(root, jira, alias)
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
