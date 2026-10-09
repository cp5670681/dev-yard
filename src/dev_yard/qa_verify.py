"""Design-time data verification for yard-qa cases (spec M1/M3/M5).

A case's "the data exists" claim used to be prose the host never checked, so a
gap only surfaced mid-run as `case-defect`. Here the claim becomes an executable
read-only `data.verify` query the host runs against `qa.yaml`'s db.url, and the
result is written to `qa/design-verify/`. The review gate then refuses approval
while a case's data cannot be proven, and the design loop feeds failures back to
the agent instead of failing the run.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dev_yard import paths
from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_exec import (
    assert_readonly_sql,
    case_script_path,
    run_case_script,
    run_sql_count,
    run_sql_first_row,
    run_sql_lines,
    run_sql_value,
)
from dev_yard.qa_schedule import CaseJob, now_iso
from dev_yard.runners import JobCancelled

VERIFY_DIR = "design-verify"
SUMMARY_FILE = "summary.yaml"
BLOCKED_FILE = "BLOCKED.md"
LOCK_DIR = Path(".yard-qa") / "locks"
_STDOUT_CAP = 4000
_MAX_ATTEMPTS_REASON = 500

# A case body line declaring a DB expectation, e.g. `- DB: 落库 hire_type=1`.
_DB_HINT = re.compile(r"^\s*[-*+]\s*DB\s*[:：]", re.I | re.M)

# SQL words that are not table/column names; identifiers left after removing
# them are what the lint looks for in the case body.
_SQL_STOP = frozenset(
    {
        "select", "from", "where", "and", "or", "not", "is", "null", "in", "as",
        "on", "join", "left", "right", "inner", "outer", "full", "cross", "group",
        "order", "by", "having", "limit", "offset", "distinct", "case", "when",
        "then", "else", "end", "with", "union", "all", "exists", "between", "like",
        "ilike", "asc", "desc", "true", "false", "count", "sum", "min", "max",
        "avg", "coalesce", "cast", "int", "integer", "text", "varchar", "boolean",
        "date", "timestamp", "interval", "now", "current_date", "current_timestamp",
        "values", "table", "show", "explain", "database", "set", "to", "for", "if",
        "any", "some", "over", "partition", "row_number", "rank", "filter", "nulls",
        "first", "last", "using", "natural", "lateral", "fetch", "next", "rows",
        "only", "recursive", "returning", "tables", "columns", "information_schema",
    }
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FROM_TABLE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.I)
_NUMBER = re.compile(r"^\d+$")
_HARD_PK = re.compile(r"\bid\s*=\s*\d+", re.I)
_COL_MISSING = re.compile(
    r"column\s+(?:(?P<qual>[A-Za-z_][\w]*)\.)?(?P<col>[A-Za-z_][\w]*)\s+does not exist",
    re.I,
)

# Host-internal tables live in a reserved namespace and are exempt from the
# "every FROM/JOIN table must be named in the case body" rule. Seed ids belong
# in `design-verify/seeds.yaml` (setup stdout `QA_SEED`), not a business-DB
# table; leftover `_qa_*` names in old verify.sql are still ignored here.
_INTERNAL_TABLE_PREFIX = "_qa_"


def _truncate(text: str, cap: int = _STDOUT_CAP) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap] + f"\n...({len(text) - cap} more)"


_LINT_NAMES = re.compile(r"引用了表 (.+)，但|的列 (.+) 未出现")
_HOST_NOTE = re.compile(r"<!-- host-verify: .*? -->")
# Rails runner boots with pages of warnings before the real exception. The
# design repair only needs the exception, so drop the boot noise.
_BOOT_NOISE = (
    "warn --",
    "warning:",
    "has_rdoc",
    "already initialized constant",
    "fatal: not a git repository",
    "bootsnap",
    "gem::specification",
    "character class has",
)


def salient_error(text: str, limit: int = _MAX_ATTEMPTS_REASON) -> str:
    """The part of a setup/verify failure the repair pass can act on."""
    raw = (text or "").replace("\\n", "\n").replace("\\t", " ")
    kept: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("w, [") or any(noise in low for noise in _BOOT_NOISE):
            continue
        kept.append(s)
    hit = [
        s
        for s in kept
        if "error" in s.lower()
        or "exception" in s.lower()
        or "不存在" in s
        or "验证失败" in s
        or "recordinvalid" in s.lower()
        or "validation failed" in s.lower()
    ]
    chosen = hit[-3:] if hit else kept[-6:]
    out = "\n".join(chosen).strip()
    if len(out) > limit:
        out = out[-limit:]
    if out:
        return out
    tail = (text or "").strip()
    return tail[-limit:] if len(tail) > limit else tail


def _lint_names(lint: dict[str, Any]) -> list[str]:
    detail = str(lint.get("detail") or "")
    found = _LINT_NAMES.search(detail)
    if not found:
        return []
    raw = found.group(1) or found.group(2) or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def note_lint_names(job: CaseJob, lint: dict[str, Any]) -> list[str]:
    """Write missing table/column names into the case so lint can proceed.

    A missing name is not a data failure. The host records it and keeps going;
    setup errors and zero-row queries are what come back to design.
    """
    names = _lint_names(lint)
    if not names or not job.path:
        return []
    path = Path(job.path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    prev: list[str] = []
    old = _HOST_NOTE.search(job.body or "")
    if old:
        raw = old.group(0).split(":", 1)[-1].removesuffix("-->")
        prev = [part.strip() for part in raw.split(",") if part.strip()]
    merged: list[str] = []
    for name in [*prev, *names]:
        if name not in merged:
            merged.append(name)
    note = "<!-- host-verify: " + ", ".join(merged) + " -->"
    text = _HOST_NOTE.sub("", text).rstrip() + "\n" + note + "\n"
    path.write_text(text, encoding="utf-8")
    body = _HOST_NOTE.sub("", job.body or "").rstrip()
    job.body = body + "\n" + note + "\n"
    return names


@dataclass
class VerifyResult:
    """Outcome of verifying one case's declared data prerequisites."""

    case: str
    status: str = "skipped"  # passed | failed | blocked | skipped
    reason: str = ""
    setup_ok: bool = True
    setup_stdout: str = ""
    verify_sql: str = ""
    rows: int = 0
    lint: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    # Set to "env" when the check could not run for infrastructure reasons
    # (usql/DB unreachable) rather than a real data gap. A blocked case must
    # not be fed back to design as a seed bug (M5).
    blocked_class: str = ""
    # sha1 of the case set this result belongs to; the gate ignores stale ones.
    fingerprint: str = ""
    # Row id this case's setup bound, plus the columns it writes. Empty when
    # the case does not declare `data.writes`.
    identity: str = ""
    writes: list[str] = field(default_factory=list)
    # Host-filled repair hint (real columns, invented-PK guidance). Empty on pass.
    hint: str = ""

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "case": self.case,
            "status": self.status,
            "reason": self.reason,
            "setup": {"code": 0 if self.setup_ok else 1, "stdout": self.setup_stdout},
            "verify_sql": self.verify_sql,
            "rows": self.rows,
            "lint": self.lint,
            "error": self.error,
            "fingerprint": self.fingerprint,
        }
        if self.blocked_class:
            out["blocked_class"] = self.blocked_class
        if self.hint:
            out["hint"] = self.hint
        return out


# Signals that a verify query failed for infrastructure reasons, not because the
# case's data is missing. Kept next to the SQL executor so design-loop and gate
# agree on what "env" means.
_ENV_VERIFY_HINTS = (
    "usql not found",
    "no db.url",
    "timed out",
    "cannot run",
    "connection",
    "could not connect",
    "no route",
    "unreachable",
    "password authentication",
    "too many connections",
    "server closed the connection",
)
_ENV_VERIFY_CLASSES = frozenset(
    {"unreachable", "auth", "pod_not_found", "timeout", "runner", "config"}
)


def verify_env_error(e: TestRejected) -> bool:
    """True when a verify failure is environmental, not a case data gap."""
    cls = str(getattr(e, "error_class", "") or "").lower()
    if cls in _ENV_VERIFY_CLASSES:
        return True
    low = str(e).lower()
    return any(h in low for h in _ENV_VERIFY_HINTS)


def _sql_identifiers(sql: str) -> set[str]:
    return {
        t.lower()
        for t in _IDENT.findall(sql)
        if t.lower() not in _SQL_STOP and not _NUMBER.match(t)
    }


def _sql_tables(sql: str) -> set[str]:
    """Names in `FROM`/`JOIN` clauses (schema prefix stripped)."""
    out: set[str] = set()
    for m in _FROM_TABLE.finditer(sql):
        name = m.group(1).split(".")[-1].lower()
        if name not in _SQL_STOP:
            out.add(name)
    return out


def lint_verify(job: CaseJob, sql: str) -> dict[str, Any]:
    """Check that `verify.sql` actually references what the case asserts.

    The tables in `FROM`/`JOIN` must be named in the case body, so a query
    cannot pass by matching only a generic column name. A trivially-empty query
    (`SELECT 1`) is allowed but marked `empty`, so the gate can surface it as an
    explicit exemption rather than let it hide a real data assertion.
    """
    idents = _sql_identifiers(sql)
    tables = _sql_tables(sql)
    body = (job.body or "").lower()
    if not idents and not tables:
        return {"ok": True, "empty": True, "matched": []}
    # Every business table must be named in the case body (a generic column name
    # cannot carry a query on its own), and at least one column must appear too —
    # so `SELECT id FROM unrelated_table` cannot pass by matching an identifier.
    # Host-internal `_qa_*` tables (the seed registry) are exempt: they are never
    # a business object a body would name.
    business_tables = [t for t in tables if not t.startswith(_INTERNAL_TABLE_PREFIX)]
    missing_tables = sorted(t for t in business_tables if t not in body)
    if missing_tables:
        return {
            "ok": False,
            "empty": False,
            "matched": sorted(t for t in tables if t in body),
            "detail": (
                "verify.sql 引用了表 "
                + ", ".join(missing_tables)
                + "，但用例正文从未提及；请把断言对象写进正文，或改用真查该数据的 verify.sql"
            ),
        }
    if tables and not business_tables:
        # Only the seed registry: it proves setup registered its rows, not that
        # the business data is in place. Surface it as an exemption for a human
        # to eyeball, like a `SELECT 1`, rather than a clean pass.
        return {"ok": True, "empty": True, "matched": sorted(tables)}
    columns = sorted(idents - tables)
    hit = [c for c in columns if c in body]
    if columns and not hit:
        return {
            "ok": False,
            "empty": False,
            "matched": sorted(tables),
            "detail": (
                "verify.sql 的列 "
                + ", ".join(columns)
                + " 未出现在用例正文；请把断言字段写进正文（- DB: <表.字段=值>）"
            ),
        }
    return {"ok": True, "empty": False, "matched": sorted(set(tables) | set(hit))}


def _safe_ident(name: str) -> str | None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        return name.lower()
    return None


def _agg_cells(cfg: QaConfig, sql: str, catalog: str | None = None) -> str:
    try:
        return (run_sql_value(cfg, sql, catalog=catalog) or "").strip()
    except (TestRejected, TypeError):
        try:
            return (run_sql_value(cfg, sql) or "").strip()
        except TestRejected:
            return ""


def _table_columns(cfg: QaConfig, table: str, catalog: str | None = None) -> list[str]:
    ident = _safe_ident(table)
    if not ident:
        return []
    cell = _agg_cells(
        cfg,
        "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
        "FROM information_schema.columns "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
        f"AND table_name = '{ident}'",
        catalog,
    )
    names = [p.strip() for p in cell.split(",") if p.strip()]
    return names[:40]


def _sample_ids(cfg: QaConfig, table: str, catalog: str | None = None) -> str:
    ident = _safe_ident(table)
    if not ident:
        return ""
    return _agg_cells(
        cfg,
        "SELECT string_agg(id::text, ',') FROM ("
        f"SELECT id FROM {ident} LIMIT 5"
        ") qa_sample",
        catalog,
    )


def missing_column(error: str) -> str:
    found = _COL_MISSING.search(error or "")
    return (found.group("col") if found else "") or ""


def invented_pk_sql(sql: str) -> bool:
    return bool(_HARD_PK.search(sql or ""))


_SCHEMA_DUMP_SQL = (
    "SELECT table_name || ': ' || string_agg(column_name, ', ' ORDER BY ordinal_position) "
    "FROM information_schema.columns "
    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
    "AND table_name NOT LIKE '_qa_%' "
    "GROUP BY table_name ORDER BY table_name"
)
_SCHEMA_DUMP_MAX_LINES = 250
_SCHEMA_DUMP_MAX_CHARS = 80000
_SCHEMA_FILES = ("db/schema.rb", "db/structure.sql", "prisma/schema.prisma")


def dump_live_schema(cfg: QaConfig, names: list[str] | None = None) -> list[str]:
    """Table: columns from information_schema, per named catalog.

    Empty if no DSN or every probe fails. A single catalog keeps the historical
    unprefixed `- table: cols` lines so existing tests and prompts stay stable.
    """
    catalogs = list(getattr(cfg.env, "db_catalogs", ()) or ())
    if names is not None:
        wanted = [n for n in names if n]
        catalogs = [c for c in catalogs if c.name in wanted]
        if not catalogs:
            return []
    elif not catalogs:
        url = getattr(cfg.env, "db_url", "")
        if not url:
            return []
        catalogs = []  # fall through to a nameless dump
    lines: list[str] = []
    size = 0
    targets = catalogs or [None]
    multi = len(catalogs) > 1

    def _append(line: str) -> bool:
        nonlocal size
        size += len(line) + 1
        if size > _SCHEMA_DUMP_MAX_CHARS:
            lines.append("- …(truncated)")
            return False
        lines.append(line)
        return True

    for cat in targets:
        catalog_name = cat.name if cat is not None else None
        if multi and catalog_name:
            if lines:
                lines.append("")
            heading = f"Live columns (`{catalog_name}`):"
            if not _append(heading):
                break
        try:
            rows = run_sql_lines(cfg, _SCHEMA_DUMP_SQL, catalog=catalog_name)
        except (TestRejected, TypeError):
            # TypeError: tests stub run_sql_lines without catalog=.
            try:
                rows = run_sql_lines(cfg, _SCHEMA_DUMP_SQL)
            except TestRejected:
                continue
        for row in rows[:_SCHEMA_DUMP_MAX_LINES]:
            if not _append(f"- {row}"):
                return lines
    return lines


def worktree_schema_files(root: Path, jira: str, aliases: list[str]) -> list[str]:
    out: list[str] = []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        for rel in _SCHEMA_FILES:
            path = wt / rel
            if path.is_file():
                out.append(f"- schema file ({alias}): `{path}`")
    return out


_NUMERIC_TYPES = frozenset(
    {
        "number",
        "numeric",
        "integer",
        "int",
        "int2",
        "int4",
        "int8",
        "bigint",
        "smallint",
        "decimal",
        "float",
        "double",
        "real",
        "binary_float",
        "binary_double",
        "pls_integer",
    }
)


def dump_live_column_types(
    cfg: QaConfig, tables: list[str], catalog: str | None = None
) -> dict[str, dict[str, str]]:
    """`{table: {col: data_type}}` for the named tables only. Empty on probe failure.

    Not rendered into context.md; lint uses it to catch string literals on
    numeric keys without dumping the whole catalog.
    """
    wanted = [t.strip().lower() for t in tables if t and t.strip()]
    if not wanted:
        return {}
    in_list = ", ".join(f"'{t}'" for t in wanted)
    sql = (
        "SELECT LOWER(table_name) || '.' || LOWER(column_name) || ':' || data_type "
        "FROM information_schema.columns "
        "WHERE LOWER(table_name) IN (" + in_list + ") "
        "AND table_schema NOT IN ('pg_catalog', 'information_schema')"
    )
    try:
        rows = run_sql_lines(cfg, sql, catalog=catalog)
    except (TestRejected, TypeError):
        try:
            rows = run_sql_lines(cfg, sql)
        except (TestRejected, TypeError):
            return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if ":" not in row or "." not in row.split(":", 1)[0]:
            continue
        left, typ = row.split(":", 1)
        table, col = left.split(".", 1)
        out.setdefault(table.strip(), {})[col.strip()] = typ.strip()
    return out


def is_numeric_type(typ: str) -> bool:
    head = (typ or "").split("(")[0].strip().lower().replace(" ", "_")
    return head in _NUMERIC_TYPES


def _branch_added_column(root: Path | None, jira: str | None, alias: str, col: str) -> bool:
    if root is None or not jira or not alias:
        return False
    from dev_yard.qa_deploy import branch_new_columns, requirement_migrations

    rels = requirement_migrations(root, jira, alias)
    if not rels:
        return False
    wt = paths.req_worktree(root, jira, alias)
    wanted = branch_new_columns([wt / rel for rel in rels])
    needle = col.lower()
    for table, cols in wanted.items():
        if needle == table.lower() + "." + col.lower():
            return True
        if any(c.lower() == needle or f"{table}.{c}".lower() == needle for c in cols):
            return True
    return False


def enrich_verify_hint(
    cfg: QaConfig,
    job: CaseJob,
    result: VerifyResult,
    *,
    root: Path | None = None,
    jira: str | None = None,
) -> str:
    """Tell the repair pass how to rewrite setup/verify, not just that it failed."""
    if result.status != "failed":
        return ""
    parts: list[str] = []
    sql = result.verify_sql or ""
    err = result.error or ""
    tables = sorted(_sql_tables(sql))
    col = missing_column(err)
    if col:
        if _branch_added_column(root, jira, job.repo, col):
            parts.append(
                f"列 {col} 属本需求新增、现场未部署，保留断言并在用例备注标注待部署。"
                "不要从 setup / verify 删掉该列。"
            )
        else:
            parts.append(
                f"列 {col} 不存在。代码常量（如 NEED_RENOVATION_TYPE）不是表字段；"
                "列名从 worktree schema.rb / ORM 读。"
            )
        for table in tables:
            cols = _table_columns(cfg, table, job.db or None)
            if cols:
                parts.append(f"表 {table} 现有列: {', '.join(cols)}")
    if result.rows < 1 and (err == "0 rows" or "0 rows" in err):
        if invented_pk_sql(sql) and not job.setup:
            parts.append(
                "verify 用了编造主键（id=数字）且无 setup。不要再换一个假 id："
                "写幂等 setup INSERT（verify 查这批种子），或按业务条件"
                "（last/version/名称）SELECT 库里已有行 LIMIT 1。"
            )
        elif invented_pk_sql(sql):
            parts.append(
                "setup 已跑但仍 0 行：核对 setup 是否插入了 verify 里的 id，"
                "或改 verify 按 setup 写入的属性查找，不要硬编码猜测主键。"
            )
        else:
            parts.append(
                "查询 0 行：先放宽条件，改选库里已有的行，不要先造数。"
                "只有这种业务状态不可能已存在时才写 setup，并同时写 cleanup。"
            )
        for table in tables[:2]:
            sample = _sample_ids(cfg, table, job.db or None)
            if sample:
                parts.append(f"表 {table} 样例 id: {sample}")
    if "已有行满足 verify.sql" in err:
        parts.append(
            "删掉 data.setup、data.cleanup、data.writes 和 data.identity，"
            "前置改成 verify.sql 选出的已有行。步骤和预期不要改。"
        )
    elif "验证失败" in err or "RecordInvalid" in err:
        parts.append(
            "造数被模型校验拦住。优先删掉 setup，verify.sql 按业务条件选出一条已有行，"
            "SELECT 列别名对应步骤里的 `<seed.别名>`。"
            "只有该状态不可能已存在时才保留造数：读模型里所有未写 optional: true 的 "
            "belongs_to 和 presence 校验，一次填齐，并写 cleanup。"
            "不要按这一次报错补一个字段再重跑。"
        )
    elif _SETUP_CRASH.search(err):
        parts.append(
            "造数在保存时崩溃。删掉 setup，以及 data.writes 和 data.identity。"
            "verify.sql 按业务条件选出已有行，"
            "SELECT 列别名对应步骤里的 `<seed.别名>`。"
            "回调里对 nil 调方法（写了尚无对应行的外键，例如 projectid）时，"
            "不要编这个外键。"
            "只有这种状态不可能已存在时才保留造数，并一次填齐未写 optional: true 的 "
            "belongs_to 和 presence 校验。不要按这一次堆栈补一个字段再重跑。"
        )
    hint = " ".join(parts).strip()
    result.hint = hint
    return hint


_AND_SPLIT = re.compile(r"\s+AND\s+", re.I)
_MAX_DELTA = 8


def _where_conjuncts(sql: str) -> tuple[str, list[str], str] | None:
    """Split a simple `... WHERE a AND b ...` into (head, conjuncts, tail)."""
    m = re.search(r"\bWHERE\b", sql, re.I)
    if not m:
        return None
    head = sql[: m.end()]
    rest = sql[m.end() :]
    order = re.search(
        r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|FETCH|OFFSET)\b", rest, re.I
    )
    body = rest[: order.start()] if order else rest
    tail = rest[order.start() :] if order else ""
    parts = [p.strip() for p in _AND_SPLIT.split(body) if p.strip()]
    if len(parts) < 2:
        return None
    return head, parts, tail


def attribute_zero_rows(
    cfg: QaConfig,
    sql: str,
    catalog: str | None,
    *,
    on_log=None,
) -> str:
    """Drop WHERE conjuncts one at a time; report the first that flips 0→n."""
    parsed = _where_conjuncts(sql)
    if parsed is None:
        return ""
    head, parts, tail = parsed
    if len(parts) > _MAX_DELTA:
        parts = parts[:_MAX_DELTA]
    for i, dropped in enumerate(parts):
        remain = [p for j, p in enumerate(parts) if j != i]
        trial = head + " " + " AND ".join(remain) + " " + tail
        try:
            n = run_sql_count(cfg, trial, on_log=on_log, catalog=catalog, verify=True)
        except TestRejected:
            continue
        if n >= 1:
            return f"断言 {dropped} 使结果为 0 行：去掉该项即 {n} 行"
    return ""


def needs_verify(job: CaseJob) -> bool:
    """Whether the case declares data prerequisites that must be proven.

    A pure-UI case (no seed, no DB expectation) is exempt; anything else — a
    setup/cleanup script or a `- DB:` expectation — needs a `data.verify`.
    """
    return bool(
        job.setup
        or job.cleanup
        or job.verify
        or _DB_HINT.search(job.body or "")
    )


def _write_result(qa: Path, result: VerifyResult) -> Path:
    out = qa / VERIFY_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.case}.yaml"
    path.write_text(
        yaml.safe_dump(result.to_payload(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def read_existing_seeds(
    cfg: QaConfig,
    job: CaseJob,
    seeds: dict[str, dict[str, str]],
    *,
    on_log: Callable[[str], None] | None = None,
) -> tuple[str | None, str, list[str]]:
    """Fill `seeds` from one live row when this case has no setup.

    Returns `(error, kind, blank_aliases)`. `kind` is `env` or `case`.
    This queries the database now. It does not read design-verify/seeds.yaml,
    which would be stale by the time the browser opens.
    A placeholder the verify.sql itself still needs is left for the caller:
    that query cannot be executed to discover its own bind values.
    """
    if job.setup:
        return None, "", []
    if job.path:
        try:
            body = Path(job.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = job.body or ""
    else:
        body = job.body or ""
    verify_sql = ""
    if job.verify:
        try:
            verify_sql = case_script_path(job, job.verify, "verify").read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError, TestRejected) as e:
            return f"verify.sql 不可读：{e}", "case", []
    from dev_yard.qa_seeds import (
        apply_seed_placeholders,
        has_seed_placeholders,
        seed_action_text,
        seeds_from_columns,
    )

    if not has_seed_placeholders(body) and not has_seed_placeholders(verify_sql):
        return None, "", []
    _, missing_body = apply_seed_placeholders(seed_action_text(body), seeds)
    if not missing_body:
        return None, "", []
    if not verify_sql.strip():
        return "页面步骤有 <seed.> 但没有 data.verify，无法从已有行取值", "case", []
    query, missing = apply_seed_placeholders(verify_sql, seeds, sql_literals=True)
    if missing:
        return None, "", []
    try:
        row = run_sql_first_row(
            cfg, query, on_log=on_log, catalog=job.db or None, verify=True
        )
    except TestRejected as e:
        if verify_env_error(e):
            return str(e), "env", []
        return f"已有行读不出种子列: {e}", "case", []
    captured, blank = seeds_from_columns(row)
    seeds.update(captured)
    return None, "", blank


def verify_case(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    *,
    fingerprint: str = "",
    on_log: Callable[[str], None] | None = None,
    executor: Any | None = None,
    siblings: list[CaseJob] | None = None,
) -> VerifyResult:
    """Prove one case's prerequisites: run setup, run `verify.sql`, then cleanup.

    Never raises for a data gap (that is the verdict); only programming errors
    propagate. The artifact is always written so the gate and the design loop
    have a durable record.
    """
    result = VerifyResult(case=job.id, fingerprint=fingerprint)

    def finish() -> VerifyResult:
        if result.status == "failed" and not result.lint.get("skip_design"):
            enrich_verify_hint(cfg, job, result, root=root, jira=jira)
        try:
            _write_result(paths.qa_dir(root, jira), result)
        except OSError:
            pass
        return result

    if not needs_verify(job):
        result.status = "skipped"
        result.reason = "无数据断言（豁免）"
        return finish()

    if not job.verify:
        result.status = "failed"
        result.error = (
            "missing verify.sql: 用例声明了 setup/cleanup 或 DB 预期，"
            "但 frontmatter 没有 data.verify（纯 UI 用例请显式写 `SELECT 1`）"
        )
        return finish()

    try:
        script = case_script_path(job, job.verify, "verify")
        text = script.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TestRejected) as e:
        result.status = "failed"
        result.error = f"verify.sql 不可读：{e}"
        return finish()
    result.verify_sql = text.strip()

    try:
        assert_readonly_sql(text)
    except TestRejected as e:
        result.status = "failed"
        result.error = str(e)
        return finish()

    result.lint = lint_verify(job, text)
    noted: list[str] = []
    for _ in range(2):
        if result.lint.get("ok"):
            break
        added = note_lint_names(job, result.lint)
        if not added:
            break
        noted.extend(added)
        result.lint = lint_verify(job, text)
    if noted:
        result.lint = {**result.lint, "noted": noted}
    if not result.lint.get("ok"):
        # Name gaps are a host note, not a design round. Anything left here
        # could not be noted (no case file); still do not spend a design pass.
        result.status = "failed"
        result.error = str(result.lint.get("detail") or "verify-lint failed")
        result.lint["skip_design"] = True
        return finish()

    query = text
    from dev_yard.qa_seeds import (
        apply_seed_placeholders,
        has_seed_placeholders,
        parse_qa_seeds,
        seed_action_text,
        seeds_from_deps,
        write_case_seeds,
    )

    qa = paths.qa_dir(root, jira)
    siblings = siblings if siblings is not None else [job]
    seeds = seeds_from_deps(qa, job, siblings)
    # A declared setup is not run until verify.sql has been tried against rows
    # already in the database. A query that already matches must drop setup.
    # `:seed` keys only a setup would print cannot be probed; those still run
    # setup, then the query.
    probe_sql = text
    missing_probe: list[str] = []
    if has_seed_placeholders(text):
        probe_sql, missing_probe = apply_seed_placeholders(
            text, seeds, sql_literals=True
        )
    if job.setup and not missing_probe:
        # A previous setup may have committed and then raised. Clear that seed
        # before treating a hit as a row that was already in the database.
        _cleanup(root, jira, cfg, job, result, on_log, executor)
        if result.status == "failed":
            return finish()
        try:
            existing_rows = run_sql_count(
                cfg, probe_sql, on_log=on_log, catalog=job.db or None, verify=True
            )
        except TestRejected as e:
            if verify_env_error(e):
                result.status = "blocked"
                result.blocked_class = "env"
            else:
                result.status = "failed"
            result.error = f"verify query failed: {e}"
            return finish()
        except JobCancelled:
            raise
        if existing_rows >= 1:
            result.status = "failed"
            result.rows = existing_rows
            result.error = (
                "已有行满足 verify.sql，但用例仍声明了 setup。"
                "删掉 data.setup 和 data.cleanup，把前置改成这些已有行。"
                "不要再造数。"
            )
            return finish()
    if job.setup:
        try:
            raw_stdout = run_case_script(
                root, jira, cfg, job, "setup", on_log=on_log, executor=executor
            )
            result.setup_stdout = _truncate(raw_stdout)
        except TestRejected as e:
            result.status = "failed"
            result.setup_ok = False
            result.error = f"setup failed: {e}"
            _cleanup(root, jira, cfg, job, result, on_log, executor)
            return finish()
        except JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — a broken executor must not abort design
            result.status = "failed"
            result.setup_ok = False
            result.error = f"setup error: {e}"
            _cleanup(root, jira, cfg, job, result, on_log, executor)
            return finish()
        own = parse_qa_seeds(raw_stdout or "")
        try:
            write_case_seeds(qa, job.id, own)
        except OSError:
            pass
        seeds.update(own)
    if has_seed_placeholders(text):
        query, missing = apply_seed_placeholders(text, seeds, sql_literals=True)
        if missing:
            result.status = "failed"
            result.error = (
                "verify.sql 引用了 :seed."
                + ", :seed.".join(missing)
                + "，但 setup stdout 没有对应的 QA_SEED 行"
            )
            _cleanup(root, jira, cfg, job, result, on_log, executor)
            return finish()
    body = ""
    if job.path:
        try:
            body = Path(job.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = job.body or ""
    else:
        body = job.body or ""
    # Setup owns its seeds, so a missing `<seed.>` is already a defect.
    # A case with no setup fills those placeholders from the verify row below.
    if job.setup:
        _, missing_steps = apply_seed_placeholders(seed_action_text(body), seeds)
        if missing_steps:
            result.status = "failed"
            result.error = "页面步骤缺 QA_SEED " + ", ".join(missing_steps)
            _cleanup(root, jira, cfg, job, result, on_log, executor)
            return finish()

    # A restricted read-only role (catalog.verify_url / db.verify_url) is used
    # for the verify query so design does not need the write DSN.
    try:
        result.rows = run_sql_count(
            cfg, query, on_log=on_log, catalog=job.db or None, verify=True
        )
    except TestRejected as e:
        if verify_env_error(e):
            # Infrastructure, not a case gap: never feed this back to design.
            result.status = "blocked"
            result.blocked_class = "env"
        else:
            result.status = "failed"
        result.error = f"verify query failed: {e}"
        _cleanup(root, jira, cfg, job, result, on_log, executor)
        return finish()

    if result.rows < 1:
        result.status = "failed"
        result.error = "0 rows"
        attr = attribute_zero_rows(cfg, query, job.db or None, on_log=on_log)
        if attr:
            result.error = f"0 rows；{attr}"
    else:
        _, missing_before = apply_seed_placeholders(seed_action_text(body), seeds)
        if not job.setup and missing_before:
            try:
                row = run_sql_first_row(
                    cfg, query, on_log=on_log, catalog=job.db or None, verify=True
                )
            except TestRejected as e:
                result.status = "failed"
                result.error = f"已有行读不出种子列: {e}"
                _cleanup(root, jira, cfg, job, result, on_log, executor)
                return finish()
            from dev_yard.qa_seeds import explain_missing_seeds, seeds_from_columns

            captured, blank = seeds_from_columns(row)
            seeds.update(captured)
            _, missing_steps = apply_seed_placeholders(seed_action_text(body), seeds)
            if missing_steps:
                result.status = "failed"
                result.error = (
                    "已有行："
                    + explain_missing_seeds(missing_steps, blank)
                    + "。verify.sql 的 SELECT 别名要和 `<seed.别名>` 一致，不要为此造数"
                )
                _cleanup(root, jira, cfg, job, result, on_log, executor)
                return finish()
            if captured:
                try:
                    write_case_seeds(qa, job.id, captured)
                except OSError:
                    pass
        result.status = "passed"
        result.reason = "数据前置已核实"
        _bind_identity(cfg, job, result, on_log, seeds)

    _cleanup(root, jira, cfg, job, result, on_log, executor)
    return finish()


def _bind_identity(
    cfg: QaConfig,
    job: CaseJob,
    result: VerifyResult,
    on_log: Callable[[str], None] | None,
    seeds: dict[str, dict[str, str]] | None = None,
) -> None:
    """Record the row a mutating case bound, before cleanup releases it."""
    if result.status != "passed" or not job.writes:
        return
    if not job.identity:
        _fail_bind(
            result,
            "data.writes 需要配套的 data.identity（只读 SQL，返回这一行的 id）",
        )
        return
    from dev_yard.qa_seeds import apply_seed_placeholders

    identity, missing = apply_seed_placeholders(
        job.identity, seeds or {}, sql_literals=True
    )
    if missing:
        _fail_bind(result, "data.identity 缺 QA_SEED " + ", ".join(missing))
        return
    try:
        assert_readonly_sql(identity)
        cell = run_sql_value(
            cfg, identity, on_log=on_log, catalog=job.db or None, verify=True
        ).strip()
    except TestRejected as e:
        _fail_bind(result, f"identity query failed: {e}")
        return
    if not cell:
        _fail_bind(result, "data.identity 没有返回行 id")
        return
    result.identity = cell
    result.writes = list(job.writes)


def _fail_bind(result: VerifyResult, error: str) -> None:
    result.status = "failed"
    result.error = error
    result.reason = error
    result.identity = ""
    result.writes = []


def write_collisions(results: dict[str, VerifyResult]) -> dict[str, str]:
    """Cases that write the same column of the same row.

    Read-only cases have no `writes` and may share a row. Different columns of
    one row are not a collision.
    """
    owners: dict[tuple[str, str], str] = {}
    errors: dict[str, str] = {}
    ordered = sorted(results.values(), key=lambda r: r.case)
    for result in ordered:
        if result.status != "passed" or not result.identity:
            continue
        for column in result.writes:
            key = (result.identity, column)
            prev = owners.get(key)
            if prev and prev != result.case:
                msg = (
                    f"seed write collision: {prev} 与 {result.case} "
                    f"写同一行 {result.identity} 的 {column}"
                )
                errors[prev] = msg
                errors[result.case] = msg
            else:
                owners[key] = result.case
    return errors


def _cleanup(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    result: VerifyResult,
    on_log: Callable[[str], None] | None,
    executor: Any | None,
) -> None:
    """Always restore the seed after a verify attempt; a failure is a verdict."""
    if not job.cleanup:
        return
    try:
        run_case_script(
            root, jira, cfg, job, "cleanup", on_log=on_log, executor=executor
        )
    except TestRejected as e:
        # A cleanup failure is a real verdict, but do not downgrade an
        # environment-blocked result to a case defect (M5).
        if result.status != "blocked":
            result.status = "failed"
        result.error = (result.error + f" cleanup failed: {e}").strip()


@contextmanager
def env_lock(
    root: Path,
    env: str,
    what: str = "design verification",
    wait_timeout: float = 0.0,
    on_wait: Callable[[bool], None] | None = None,
):
    """Serialize per-env QA work (verification and runs) across requirements.

    `req test` already holds a per-JIRA lock, but seeds share one test DB, so two
    requirements' verifies *or runs* must not overlap. `what` names the holder in
    the refusal message.

    `wait_timeout > 0` makes the caller queue behind a live holder (M8) instead
    of failing immediately: a second requirement's run waits its turn rather
    than erroring out. A stale lock is still reclaimed at once. When `on_wait` is
    given it is called with `True` on entering a wait and `False` once the lock
    is held, so the board can show "waiting for env".
    """
    lock_dir = root / LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", env or "env")
    path = lock_dir / f"{safe}.verify.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + wait_timeout if wait_timeout > 0 else None
    waiting = False
    while True:
        tmp = lock_dir / f".{safe}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(token, encoding="utf-8")
            try:
                os.link(tmp, path)
            except FileExistsError:
                holder = ""
                try:
                    holder = path.read_text(encoding="utf-8").strip()
                except OSError:
                    holder = ""
                head = holder.split(":", 1)[0] if holder else ""
                pid = int(head) if head.isdigit() else None
                if pid is not None and not _pid_alive(pid):
                    # Atomic reclaim: the winner renames, losers get FileNotFound.
                    stale = path.with_name(path.name + f".stale.{uuid.uuid4().hex}")
                    try:
                        os.rename(path, stale)
                    except OSError:
                        pass
                    else:
                        stale.unlink(missing_ok=True)
                    continue
                if deadline is not None and time.monotonic() < deadline:
                    if not waiting:
                        waiting = True
                        if on_wait is not None:
                            on_wait(True)
                    time.sleep(1.0)
                    continue
                raise TestRejected(
                    f"another {what} is running for env {env}; "
                    f"wait for it (or delete {path} if it is stale)"
                ) from None
        finally:
            tmp.unlink(missing_ok=True)
        break
    if waiting and on_wait is not None:
        on_wait(False)
    try:
        yield
    finally:
        try:
            if path.read_text(encoding="utf-8").strip() == token:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _cases_in_dep_order(cases: list[CaseJob]) -> list[CaseJob]:
    """Ancestors first, stable among unrelated cases. Cycles keep the leftover order."""
    by_id = {c.id: c for c in cases}
    waiting = {c.id: [d for d in c.depends_on if d in by_id] for c in cases}
    ready = [c.id for c in cases if not waiting[c.id]]
    out: list[CaseJob] = []
    seen: set[str] = set()
    while ready:
        cid = ready.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(by_id[cid])
        for other in cases:
            if cid in waiting[other.id]:
                waiting[other.id].remove(cid)
                if not waiting[other.id] and other.id not in seen:
                    ready.append(other.id)
    for case in cases:
        if case.id not in seen:
            out.append(case)
    return out


def verify_cases(
    root: Path,
    jira: str,
    cfg: QaConfig,
    cases: list[CaseJob],
    *,
    fingerprint: str = "",
    on_log: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, VerifyResult]:
    """Verify every case that declares data prerequisites, one at a time.

    Sequential on purpose: seeds share a DB, and the env lock already excludes
    concurrent verifications, so parallel cases would only race each other.
    """
    from dev_yard.script_exec import resolve_executor

    results: dict[str, VerifyResult] = {}
    executor = None
    ordered = _cases_in_dep_order(cases)
    with env_lock(root, cfg.active_env):
        try:
            for job in ordered:
                if cancel_check is not None and cancel_check():
                    break
                if job.setup and not job.setup.lower().endswith(".sql") and executor is None:
                    wt = paths.req_worktree(root, jira, job.repo) if job.repo else None
                    from dev_yard.qa_exec import exec_site_for_job, origin_for_job

                    executor = resolve_executor(
                        cfg.env,
                        base_url=origin_for_job(root, cfg, job) or cfg.env.base_url,
                        worktree=wt,
                        root=root,
                        site=exec_site_for_job(root, cfg, job),
                    )
                results[job.id] = verify_case(
                    root,
                    jira,
                    cfg,
                    job,
                    fingerprint=fingerprint,
                    on_log=on_log,
                    executor=executor,
                    siblings=ordered,
                )
        finally:
            if executor is not None:
                executor.close()
    qa = paths.qa_dir(root, jira)
    for cid, msg in write_collisions(results).items():
        result = results[cid]
        result.status = "failed"
        result.error = msg
        result.reason = msg
        _write_result(qa, result)
    return results


def status_counts(results: dict[str, VerifyResult]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for r in results.values():
        counts[r.status] = counts.get(r.status, 0) + 1
    counts["total"] = len(results)
    return counts


def summary_payload(
    results: dict[str, VerifyResult], fingerprint: str
) -> dict[str, Any]:
    return {
        "updated_at": now_iso(),
        "fingerprint": fingerprint,
        "summary": status_counts(results),
        "cases": {
            cid: {
                "status": r.status,
                "reason": r.reason,
                "error": r.error,
                "rows": r.rows,
                "verify_sql": r.verify_sql,
                "lint": r.lint,
            }
            for cid, r in results.items()
        },
    }


def write_summary(
    root: Path, jira: str, results: dict[str, VerifyResult], fingerprint: str
) -> Path:
    out = paths.qa_dir(root, jira) / VERIFY_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / SUMMARY_FILE
    path.write_text(
        yaml.safe_dump(
            summary_payload(results, fingerprint), sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )
    return path


def read_summary(qa: Path) -> dict[str, Any] | None:
    path = qa / VERIFY_DIR / SUMMARY_FILE
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _failed_ids(cases: dict[str, Any]) -> list[str]:
    return sorted(
        cid
        for cid, item in cases.items()
        if isinstance(item, dict) and item.get("status") == "failed"
    )


def _blocked_ids(cases: dict[str, Any]) -> list[str]:
    return sorted(
        cid
        for cid, item in cases.items()
        if isinstance(item, dict) and item.get("status") == "blocked"
    )


def failed_cases(qa: Path, fingerprint: str) -> list[str]:
    """Cases whose verdict is `failed` for the *current* case set."""
    data = read_summary(qa)
    if not data or str(data.get("fingerprint") or "") != fingerprint:
        return []
    cases = data.get("cases")
    return _failed_ids(cases) if isinstance(cases, dict) else []


def blocked_cases(qa: Path, fingerprint: str) -> list[str]:
    """Cases whose verification was blocked by the environment (M5)."""
    data = read_summary(qa)
    if not data or str(data.get("fingerprint") or "") != fingerprint:
        return []
    cases = data.get("cases")
    return _blocked_ids(cases) if isinstance(cases, dict) else []


def write_blocked(root: Path, jira: str, results: dict[str, VerifyResult]) -> Path | None:
    """Durable list of cases the design loop could not make data-verifiable.

    The spec allows any visible artifact instead of OPEN-QUESTIONS.md (whose
    `Qn:` lines mean business ambiguity, not host findings). Returns None when
    nothing failed, so callers can stay unconditional.
    """
    failed = {cid: r for cid, r in results.items() if r.status == "failed"}
    if not failed:
        return None
    out = paths.qa_dir(root, jira) / VERIFY_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / BLOCKED_FILE
    lines = [
        "# design-blocked（数据核实未通过）",
        "",
        "以下用例在设计期多次核实后仍取不到前置数据，未进入 run。",
        "修好 setup / verify.sql / 用真实数据重写前置后重跑 `dev-yard req test <JIRA>`。",
        "",
    ]
    for cid in sorted(failed):
        r = failed[cid]
        lines.append(f"## {cid}")
        lines.append(f"- verify.sql: `{r.verify_sql or '(缺失)'}`")
        if r.lint.get("detail"):
            lines.append(f"- lint: {r.lint['detail']}")
        if r.error:
            lines.append(f"- 错误: {r.error}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def verify_gate(
    qa: Path,
    fingerprint: str,
    *,
    required: bool = True,
    allow_unverified: bool = False,
) -> tuple[bool, str]:
    """`(can_proceed, reason)` for the data-verification half of the gate.

    With `required`, a missing summary blocks too: "not run" must not be
    indistinguishable from "passed" (waive it with `--no-verify` / `--allow-
    unverified`, which callers turn into `required=False` / `allow_unverified`).
    """
    if not required or allow_unverified:
        return True, ""
    data = read_summary(qa)
    if data is None:
        return False, "尚未做数据核实（qa/design-verify/ 无产物）；跑设计期核实或加 --no-verify"
    if str(data.get("fingerprint") or "") != fingerprint:
        # Cases changed after the last run; the review gate's stale check owns
        # this, not the data verdict.
        return True, ""
    failed = failed_cases(qa, fingerprint)
    blocked = blocked_cases(qa, fingerprint)
    if not failed and not blocked:
        return True, ""
    parts: list[str] = []
    if failed:
        parts.append(f"{len(failed)} 条用例数据核实未通过：{', '.join(failed)}")
    if blocked:
        parts.append(f"{len(blocked)} 条因环境不可用未能核实：{', '.join(blocked)}")
    return False, (
        "；".join(parts)
        + "（见 qa/design-verify/ 与 BLOCKED.md；"
        "修好后重跑设计，或加 --allow-unverified 越权）"
    )


def verify_view(qa: Path, fingerprint: str) -> dict[str, Any]:
    """Compact verify status for the review gate / board."""
    data = read_summary(qa)
    if not data:
        return {"present": False, "stale": False}
    stale = str(data.get("fingerprint") or "") != fingerprint
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else {}
    failed = _failed_ids(cases)
    blocked = _blocked_ids(cases)
    return {
        "present": True,
        "stale": stale,
        "updated_at": str(data.get("updated_at") or ""),
        "summary": data.get("summary") if isinstance(data.get("summary"), dict) else {},
        "failed": failed,
        "blocked": blocked,
        "empty": sorted(
            cid
            for cid, item in cases.items()
            if isinstance(item, dict)
            and isinstance(item.get("lint"), dict)
            and item["lint"].get("empty")
        ),
        "details": [
            _failed_detail(cid, cases.get(cid)) for cid in [*failed, *blocked]
        ],
    }


def _failed_detail(cid: str, item: Any) -> dict[str, Any]:
    """Per-case verify evidence for the review card's expandable rows."""
    case = item if isinstance(item, dict) else {}
    return {
        "case": cid,
        "verify_sql": str(case.get("verify_sql") or ""),
        "rows": case.get("rows"),
        "reason": str(case.get("reason") or ""),
        "error": str(case.get("error") or ""),
        "lint": case.get("lint") if isinstance(case.get("lint"), dict) else {},
    }


_VALIDATION = re.compile(r"(?:验证失败|Validation failed)\s*:\s*(.+)", re.I)
# Ruby prints `in `method': message (ErrorClass)`. The closing mark is a
# single quote, not a second backtick.
_RUBY_EXC = re.compile(
    r"in `([^`']+)'?:?\s*(.+?)\s*\(([A-Za-z:]*(?:Error|Exception))\)\s*$"
)
_NAMED_EXC = re.compile(
    r"\b([A-Za-z_][\w:]*(?:Error|Exception)):\s*(.+)$"
)
# A nil receiver inside a callback. A bare "setup failed:" is a timeout,
# a missing file, or a validation error, and must not take this hint.
_SETUP_CRASH = re.compile(
    r"NoMethodError|undefined method|for nil:NilClass",
    re.I,
)


def _exception_signature(raw: str) -> str:
    """Stable id for one raised exception, ignoring SQL and stack frames."""
    text = (raw or "").replace("\\n", "\n").replace("\\t", " ")
    found: list[str] = []
    for line in text.splitlines():
        s = " ".join(line.strip().split())
        if not s:
            continue
        ruby = _RUBY_EXC.search(s)
        if ruby:
            method, msg, cls = ruby.group(1), ruby.group(2), ruby.group(3)
            msg = msg.replace("`", "").replace("'", "")
            cls = cls.split("::")[-1]
            found.append(f"exception:{cls}:{method}:{msg}"[:220])
            continue
        named = _NAMED_EXC.search(s)
        if named and named.group(1).lower() != "error":
            cls = named.group(1).split("::")[-1]
            msg = named.group(2).replace("`", "").replace("'", "")
            found.append(f"exception:{cls}:{msg}"[:220])
    return found[-1] if found else ""


def _md_fence(text: str) -> str:
    """Fence text so backticks in a Ruby exception are not inline code."""
    body = (text or "").replace("\r\n", "\n").strip()
    fence = "```"
    while fence in body:
        fence += "`"
    return f"\n\n{fence}\n{body}\n{fence}\n"


def _failure_signature(r: VerifyResult) -> str:
    # The raw error, not salient_error: a 验证失败 line often has neither
    # "error" nor "exception", and the filter would drop it.
    raw = (r.error or "").replace("\\n", "\n").replace("\\t", " ")
    validation = _VALIDATION.search(raw)
    if validation:
        msg = validation.group(1).split("\n", 1)[0]
        msg = re.sub(r"\s*\(ActiveRecord::.*", "", " ".join(msg.split()))
        return "validation:" + msg[:160]
    # The same save! crash must not become one signature per verify.sql.
    exc = _exception_signature(raw)
    if exc:
        return exc
    # The drop-setup hint is the same for every case. Cluster on the query.
    if "已有行满足 verify.sql" in raw:
        sql = " ".join((r.verify_sql or "").split())[:120]
        return "sql:" + sql if sql else "err:" + r.case
    err = salient_error(raw, 400)
    m = re.search(r"(setup_[\w.-]+)", err)
    if m:
        return f"setup:{m.group(1)}"
    # A shared setup filename is not a root cause. Use it only when the
    # failure text itself is empty, so unrelated errors in one script stay apart.
    if not err:
        script = re.search(r"([\w./-]*setup[\w.-]*\.(?:rb|sql|py))", raw, re.I)
        if script:
            return "setup:" + script.group(1)
    if r.hint:
        return "hint:" + " ".join(r.hint.split())[:120]
    sql = " ".join((r.verify_sql or "").split())[:120]
    if sql:
        return "sql:" + sql
    return "err:" + (err[:80] or r.case)


def render_feedback(
    results: dict[str, VerifyResult], *, limit: int = _MAX_ATTEMPTS_REASON
) -> str:
    """Structured failure list fed back into the design agent."""
    lines = [
        "数据核实未通过。只改下列用例的 setup / cleanup / verify.sql 和「前置」。",
        "删掉 setup 时可以一并删掉 data.writes 和 data.identity，不要改成别的目标。",
        "不要改其它用例，不要改「步骤」和「预期」，不要放宽预期，"
        "不要用 SELECT 1 掩盖真断言。",
        "0 行且 SQL 含 id=<数字>：禁止再换假主键；写 setup 造数或按业务条件查已有行。",
        "列不存在：用提示里的真实列名；应用常量不是表字段。"
        "属「需求新增·现场未部署」子节的列必须保留断言，并在用例备注标注待部署。",
        "",
    ]
    failed = [
        results[cid]
        for cid in sorted(results)
        if results[cid].status == "failed" and not results[cid].lint.get("skip_design")
    ]
    groups: dict[str, list[VerifyResult]] = {}
    for r in failed:
        groups.setdefault(_failure_signature(r), []).append(r)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if multi:
        lines.append("# 根因聚类")
        for sig, items in multi.items():
            ids = ", ".join(r.case for r in items)
            lines.append(f"这 {len(items)} 条同一根因（{sig}）：{ids}")
        lines.append("")
    if any(sig.startswith(("exception:", "setup:")) for sig in multi):
        lines.append(
            "同一处造数崩溃只改一次：删掉 setup 改查已有行，或只改共用的 setup。"
            "不要按用例分别改 verify.sql。"
        )
        lines.append("")
    for r in failed:
        cid = r.case
        lines.append(f"## {cid}")
        sql = " ".join((r.verify_sql or "").split())
        if len(sql) > 240:
            sql = sql[:240] + "…"
        lines.append(f"- verify.sql: `{sql or '(缺失)'}`")
        if (r.error or "").startswith(("setup failed:", "setup error:")):
            lines.append("- 实际行数: 未查询（造数未完成）")
        else:
            lines.append(f"- 实际行数: {r.rows}")
        if r.error:
            lines.append("- 错误:" + _md_fence(salient_error(r.error, limit)))
        if r.hint:
            lines.append(f"- 怎么改: {r.hint}")
        if r.setup_stdout:
            lines.append(f"- setup stdout: {salient_error(r.setup_stdout, limit)}")
        lines.append("")
    return "\n".join(lines).strip()


def prior_defects(qa: Path, cases: list[CaseJob]) -> dict[str, list[str]]:
    """Gaps a previous round recorded for the same case ids.

    Two sources: a run's `case-defect:` reason from `evidence/`, and the last
    design-time verdict's failures (the previous round's `design-blocked`). The
    same gap repeating across rounds is the failure this design exists to stop
    (spec trigger: case-02), so the history is injected into the prompt.
    """
    wanted = {c.id for c in cases}
    if not wanted:
        return {}
    out: dict[str, list[str]] = {}

    def add(cid: str, reason: str) -> None:
        bucket = out.setdefault(cid, [])
        if reason not in bucket:
            bucket.append(reason)

    evidence = qa / "evidence"
    if evidence.is_dir():
        for result_path in sorted(evidence.glob("*/*/result.yaml")):
            cid = result_path.parent.name
            if cid not in wanted:
                continue
            try:
                data = yaml.safe_load(result_path.read_text(encoding="utf-8")) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            reason = str(data.get("reason") or "").strip()
            if reason.lower().startswith("case-defect:"):
                add(cid, reason)

    summary = read_summary(qa)
    prev = summary.get("cases") if summary else None
    if isinstance(prev, dict):
        for cid, item in prev.items():
            if cid not in wanted or not isinstance(item, dict):
                continue
            if item.get("status") != "failed":
                continue
            detail = str(item.get("error") or item.get("reason") or "").strip()
            add(cid, f"design-blocked: {detail or '数据核实未通过'}")
    return out


def render_prior_defects(history: dict[str, list[str]]) -> str:
    if not history:
        return ""
    lines = [
        "上一轮 run 记录到的同 case 数据缺口（务必在本轮设计期修掉，不要原样复现）：",
        "",
    ]
    for cid in sorted(history):
        lines.append(f"## {cid}")
        lines.extend(f"- {r}" for r in history[cid])
        lines.append("")
    return "\n".join(lines).strip()


def describe(results: dict[str, VerifyResult]) -> str:
    """One-line summary for CLI/web logs."""
    counts = status_counts(results)
    return (
        f"verify passed={counts['passed']} failed={counts['failed']} "
        f"blocked={counts['blocked']} skipped={counts['skipped']} "
        f"total={counts['total']}"
    )
