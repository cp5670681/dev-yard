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
    status: str = "skipped"  # passed | failed | skipped
    reason: str = ""
    setup_ok: bool = True
    setup_stdout: str = ""
    verify_sql: str = ""
    rows: int = 0
    lint: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    # sha1 of the case set this result belongs to; the gate ignores stale ones.
    fingerprint: str = ""

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
        return out


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
    required = tables or idents
    missing = sorted(t for t in required if t not in body)
    matched = sorted(t for t in required if t in body)
    if not missing:
        return {"ok": True, "empty": False, "matched": matched}
    return {
        "ok": False,
        "empty": False,
        "matched": matched,
        "detail": (
            "verify.sql 引用了 "
            + ", ".join(missing)
            + "，但用例正文从未提及这些表/字段；请把断言对象写进正文，或改用真查该数据的 verify.sql"
        ),
    }


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

    try:
        result.rows = run_sql_count(cfg, text, on_log=on_log)
    except TestRejected as e:
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

    _cleanup(root, jira, cfg, job, result, on_log, executor)
    return finish()


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
        result.status = "failed"
        result.error = (result.error + f" cleanup failed: {e}").strip()


@contextmanager
def env_lock(root: Path, env: str):
    """Serialize design-time verification per env across requirements.

    `req test` already holds a per-JIRA lock; verification additionally writes
    seeds to the shared test DB, so two requirements' verifies must not overlap.
    """
    lock_dir = root / LOCK_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", env or "env")
    path = lock_dir / f"{safe}.verify.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    for attempt in range(2):
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
                if pid is not None and not _pid_alive(pid) and attempt == 0:
                    stale = path.with_name(path.name + f".stale.{uuid.uuid4().hex}")
                    try:
                        os.rename(path, stale)
                    except OSError:
                        pass
                    else:
                        stale.unlink(missing_ok=True)
                    continue
                raise TestRejected(
                    f"another design verification is running for env {env}; "
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
    return results


def status_counts(results: dict[str, VerifyResult]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
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


def failed_cases(qa: Path, fingerprint: str) -> list[str]:
    """Cases whose verdict is `failed` for the *current* case set."""
    data = read_summary(qa)
    if not data or str(data.get("fingerprint") or "") != fingerprint:
        return []
    cases = data.get("cases")
    return _failed_ids(cases) if isinstance(cases, dict) else []


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
    if not failed:
        return True, ""
    return False, (
        f"{len(failed)} 条用例数据核实未通过：{', '.join(failed)}"
        "（见 qa/design-verify/ 与 BLOCKED.md；"
        "修好后重跑设计，或加 --allow-unverified 越权）"
    )


def verify_view(qa: Path, fingerprint: str) -> dict[str, Any]:
    """Compact verify status for the review gate / board."""
    data = read_summary(qa)
    if not data:
        return {"present": False, "stale": False}
    stale = str(data.get("fingerprint") or "") != fingerprint
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else {}
    return {
        "present": True,
        "stale": stale,
        "updated_at": str(data.get("updated_at") or ""),
        "summary": data.get("summary") if isinstance(data.get("summary"), dict) else {},
        "failed": _failed_ids(cases),
        "empty": sorted(
            cid
            for cid, item in cases.items()
            if isinstance(item, dict)
            and isinstance(item.get("lint"), dict)
            and item["lint"].get("empty")
        ),
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
        f"skipped={counts['skipped']} total={counts['total']}"
    )
