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
from dataclasses import dataclass, field, replace
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


def _truncate(text: str, cap: int = _STDOUT_CAP) -> str:
    text = text or ""
    return text if len(text) <= cap else text[:cap] + f"\n...({len(text) - cap} more)"


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
    # Every table must be named in the case body (a generic column name cannot
    # carry a query on its own), and at least one column must appear too — so
    # `SELECT id FROM unrelated_table` cannot pass by matching an identifier.
    missing_tables = sorted(t for t in tables if t not in body)
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


def verify_case(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    *,
    fingerprint: str = "",
    on_log: Callable[[str], None] | None = None,
    executor: Any | None = None,
) -> VerifyResult:
    """Prove one case's prerequisites: run setup, run `verify.sql`, then cleanup.

    Never raises for a data gap (that is the verdict); only programming errors
    propagate. The artifact is always written so the gate and the design loop
    have a durable record.
    """
    result = VerifyResult(case=job.id, fingerprint=fingerprint)

    def finish() -> VerifyResult:
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
    if not result.lint.get("ok"):
        result.status = "failed"
        result.error = str(result.lint.get("detail") or "verify-lint failed")
        return finish()

    if job.setup:
        try:
            result.setup_stdout = _truncate(
                run_case_script(
                    root, jira, cfg, job, "setup", on_log=on_log, executor=executor
                )
            )
        except TestRejected as e:
            result.status = "failed"
            result.setup_ok = False
            result.error = f"setup failed: {e}"
            return finish()
        except JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — a broken executor must not abort design
            result.status = "failed"
            result.setup_ok = False
            result.error = f"setup error: {e}"
            return finish()

    # A restricted read-only role (db.verify_url) can be used for the verify
    # query without granting the design agent write access to the shared DB.
    verify_cfg = cfg
    if getattr(cfg.env, "verify_db_url", ""):
        verify_cfg = replace(cfg, env=replace(cfg.env, db_url=cfg.env.verify_db_url))
    try:
        result.rows = run_sql_count(verify_cfg, text, on_log=on_log)
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
    else:
        result.status = "passed"
        result.reason = "数据前置已核实"
        _bind_identity(verify_cfg, job, result, on_log)

    _cleanup(root, jira, cfg, job, result, on_log, executor)
    return finish()


def _bind_identity(
    cfg: QaConfig,
    job: CaseJob,
    result: VerifyResult,
    on_log: Callable[[str], None] | None,
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
    try:
        assert_readonly_sql(job.identity)
        cell = run_sql_value(cfg, job.identity, on_log=on_log).strip()
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
):
    """Serialize per-env QA work (verification and runs) across requirements.

    `req test` already holds a per-JIRA lock, but seeds share one test DB, so two
    requirements' verifies *or runs* must not overlap. `what` names the holder in
    the refusal message.

    `wait_timeout > 0` makes the caller queue behind a live holder (M8) instead
    of failing immediately: a second requirement's run waits its turn rather
    than erroring out. A stale lock is still reclaimed at once.
    """
    lock_dir = root / LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", env or "env")
    path = lock_dir / f"{safe}.verify.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + wait_timeout if wait_timeout > 0 else None
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
                    time.sleep(1.0)
                    continue
                raise TestRejected(
                    f"another {what} is running for env {env}; "
                    f"wait for it (or delete {path} if it is stale)"
                ) from None
        finally:
            tmp.unlink(missing_ok=True)
        break
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
    with env_lock(root, cfg.active_env):
        try:
            for job in cases:
                if cancel_check is not None and cancel_check():
                    break
                if job.setup and not job.setup.lower().endswith(".sql") and executor is None:
                    wt = paths.req_worktree(root, jira, job.repo) if job.repo else None
                    executor = resolve_executor(
                        cfg.env, base_url=cfg.env.base_url, worktree=wt, root=root
                    )
                results[job.id] = verify_case(
                    root,
                    jira,
                    cfg,
                    job,
                    fingerprint=fingerprint,
                    on_log=on_log,
                    executor=executor,
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


def render_feedback(
    results: dict[str, VerifyResult], *, limit: int = _MAX_ATTEMPTS_REASON
) -> str:
    """Structured failure list fed back into the design agent."""
    lines = [
        "宿主在设计期执行了数据核实（verify.sql），以下用例未通过。"
        "请修正 setup / verify.sql / 用例前置，使它们真能取到数据；"
        "不要放宽规则，也不要写空转的 verify.sql。",
        "",
    ]
    for cid in sorted(results):
        r = results[cid]
        if r.status != "failed":
            continue
        lines.append(f"## {cid}")
        lines.append(f"- verify.sql: `{r.verify_sql or '(缺失)'}`")
        lines.append(f"- 实际行数: {r.rows}")
        if r.lint.get("detail"):
            lines.append(f"- lint: {r.lint['detail']}")
        if r.error:
            lines.append(f"- 错误: {_truncate(r.error, limit)}")
        if r.setup_stdout:
            lines.append(f"- setup stdout: {_truncate(r.setup_stdout, limit)}")
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
