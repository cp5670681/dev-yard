"""Host-side QA helpers: login, setup/cleanup, result contract, worktree revert."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dev_yard import gitops, paths
from dev_yard.qa_config import (
    QaAccount,
    QaConfig,
    TestRejected,
    connect_url,
    default_state_file,
    redact_qa_yaml,
)
from dev_yard.qa_schedule import CaseJob, normalize_status

_ASSERT_TYPES = {"ui", "net", "db"}
# Structured values a worker may put in result.yaml `blocked_class`; the host
# trusts this over guessing from the free-form `reason` text.
BLOCKED_CLASSES = frozenset({"case-defect", "env", "undeployed", "auth", "other"})
# Read-only statements a `data.verify` query may start with, and the write
# keywords that disqualify one. Kept here (next to the SQL executor) so the
# design-time verifier and any CLI discovery share one definition.
READONLY_SQL_HEADS = frozenset(
    {"select", "show", "desc", "describe", "explain", "with", "table"}
)
_WRITE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge"
    r"|call|do|copy|vacuum|analyze|refresh|into|outfile|load_file|dblink"
    r"|pg_read_file|lo_import|lo_export)\b",
    re.I,
)


def state_path(
    root: Path, cfg: QaConfig, acct: QaAccount, *, site: str = ""
) -> Path:
    rel = acct.state_file or default_state_file(cfg.active_env, acct.name)
    site = (site or "").strip()
    default = (getattr(getattr(cfg.env, "exec_cfg", None), "exec_default", None) or "").strip()
    if site and site != default:
        path = Path(rel)
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in site) or "site"
        rel = str(path.with_name(f"{path.stem}-{slug}{path.suffix}"))
    path = Path(rel)
    return path if path.is_absolute() else root / path


def auth_replay_path(
    root: Path, cfg: QaConfig, acct: QaAccount, *, site: str = ""
) -> Path:
    state = state_path(root, cfg, acct, site=site)
    return state.with_suffix(".replay.sh")


def _which_playwright() -> str:
    binary = shutil.which("playwright-cli")
    if not binary:
        raise TestRejected("playwright-cli not found; cannot load or save auth state")
    return binary


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    label: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """subprocess.run that turns timeouts/OS errors into TestRejected.

    `label` is used in error messages instead of the argv so a command carrying
    a credential on its command line is never echoed back.
    """
    what = label or cmd[0]
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise TestRejected(f"{what} timed out after {timeout}s") from e
    except OSError as e:
        raise TestRejected(f"cannot run {what}: {e}") from e


def _cli(
    binary: str,
    cfg: QaConfig,
    session: str,
    args: list[str],
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    cmd = [binary, "--browser", cfg.browser.channel, f"-s={session}", *args]
    label = f"playwright-cli {args[0]}" if args else "playwright-cli"
    return _run(cmd, cwd=cwd, timeout=timeout, label=label)


class _AiAuthPending(TestRejected):
    """Signals that authentication state will be explored dynamically by AI worker."""


def ensure_auth(
    root: Path,
    cfg: QaConfig,
    names: list[str] | None = None,
    on_log: Any | None = None,
    *,
    sites: list[str] | None = None,
) -> dict[str, str]:
    """Pre-check auth states in workspace, or run saved replay scripts.

    Returns `{account: error}` ONLY for accounts that cannot authenticate at all
    (e.g. missing state file AND missing username/password credentials).
    Accounts with credentials will be explored and saved dynamically by the AI worker.
    """
    env = cfg.env
    if names is None:
        names = [env.auth_default] if env.auth_default else []
    spec = getattr(env, "exec_cfg", None)
    if sites is None:
        sites = [s.name for s in getattr(spec, "sites", ()) or ()] or [""]
    failures: dict[str, str] = {}
    binary: str | None = None
    for name in names:
        acct = env.accounts.get(name)
        if acct is None:
            continue
        for site in sites:
            try:
                binary = _ensure_one(
                    root, cfg, name, acct, binary, on_log, site=site or ""
                )
            except _AiAuthPending as e:
                if on_log is not None:
                    on_log(f"{e}\n")
            except TestRejected as e:
                key = f"{name}/{site}" if site else name
                failures[key] = str(e)
                if on_log is not None:
                    on_log(f"auth for {key} failed: {e}\n")
    return failures


def _ensure_one(
    root: Path,
    cfg: QaConfig,
    name: str,
    acct: QaAccount,
    binary: str | None,
    on_log: Any | None,
    *,
    site: str = "",
) -> str:
    state = state_path(root, cfg, acct, site=site)
    replay = auth_replay_path(root, cfg, acct, site=site)
    state.parent.mkdir(parents=True, exist_ok=True)
    has_creds = bool(acct.username and acct.password)

    if binary is None:
        binary = _which_playwright()

    # 1. Fast path: load saved state file if present
    if state.is_file():
        if on_log is not None:
            on_log(f"$ playwright-cli state-load {state}")
        r = _cli(binary, cfg, "qap-preload", ["state-load", str(state)], root)
        if r.returncode == 0:
            _cli(binary, cfg, "qap-preload", ["close"], root)
            return binary
        _cli(binary, cfg, "qap-preload", ["close"], root)

    # 2. Replay path: run saved explored login script if present
    if replay.is_file():
        if on_log is not None:
            on_log(f"replaying saved auth script: {replay}")
        if _run_auth_replay(
            binary, cfg, acct, state, replay, root, on_log, site=site
        ):
            return binary

    # 3. AI Exploration path:
    if not has_creds:
        raise TestRejected(
            f"missing auth state_file {state} for account {name!r}; "
            "put username/password in qa.yaml so AI can explore login"
        )

    raise _AiAuthPending(
        f"auth for account {name!r} will be explored by AI worker during execution"
    )


def _run_auth_replay(
    binary: str,
    cfg: QaConfig,
    acct: QaAccount,
    state: Path,
    replay: Path,
    root: Path,
    on_log: Any | None,
    *,
    site: str = "",
) -> bool:
    from dev_yard.exec_cfg import origin_for_site

    session = "qap-preload"
    env = os.environ.copy()
    env["PLAYWRIGHT_SESSION"] = session
    env["BASE_URL"] = origin_for_site(cfg.env, site) or cfg.env.base_url
    env["USERNAME"] = acct.username or ""
    env["PASSWORD"] = acct.password or ""
    if on_log is not None:
        on_log(f"$ bash {replay}")
    try:
        r = subprocess.run(
            ["bash", str(replay)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode == 0:
            saved = _cli(binary, cfg, session, ["state-save", str(state)], root)
            _cli(binary, cfg, session, ["close"], root)
            return saved.returncode == 0 and state.is_file()
    except Exception as e:
        if on_log is not None:
            on_log(f"auth replay error: {e}")
    _cli(binary, cfg, session, ["close"], root)
    return False


def replay_path(job: CaseJob) -> Path | None:
    if not job.path:
        return None
    path = Path(job.path).with_suffix(".replay.sh")
    return path if path.is_file() else None


def cases_root_for(case_file: Path) -> Path:
    """Sandbox for setup/cleanup/verify: the nearest `cases` directory.

    A shared factory lives next to the case dirs (`qa/cases/setup.rb`) and is
    referenced as `../setup.rb`. Paths that climb out of `cases/` are rejected.
    A case file that is not under a `cases` directory is sandboxed to its own dir.
    """
    cur = case_file.parent
    for parent in (cur, *cur.parents):
        if parent.name == "cases":
            return parent
    return cur


def case_script_path(job: CaseJob, name: str, kind: str) -> Path:
    """Resolve a case's setup/cleanup/verify file inside the cases directory.

    Frontmatter is AI-authored. Absolute paths and anything that resolves
    outside `qa/cases/` are rejected. A relative path such as `../setup.rb`
    is allowed when it stays inside that directory, so several cases can share
    one factory.
    """
    if not job.path:
        raise TestRejected(f"{job.id} has {kind} {name!r} but no case path")
    raw = Path(name)
    if raw.is_absolute() or name in {".", ".."}:
        raise TestRejected(
            f"{job.id} {kind} {name!r} must be a relative path inside the cases directory"
        )
    case_file = Path(job.path)
    root = cases_root_for(case_file)
    script = (case_file.parent / raw).resolve()
    try:
        script.relative_to(root.resolve())
    except ValueError as e:
        raise TestRejected(
            f"{job.id} {kind} {name!r} must stay inside the cases directory"
        ) from e
    if not script.is_file():
        raise TestRejected(f"{job.id} {kind} file missing: {script}")
    return script


def host_case_verdict(
    cfg: QaConfig,
    job: CaseJob,
    seeds: dict[str, dict[str, str]],
    *,
    on_log: Any | None = None,
) -> dict[str, Any]:
    """Prove a `driver: host` case with verify.sql. No browser.

    The same contract as design-time verify: one read-only query, at least one
    row passes. Seed placeholders are substituted from this case's setup stdout.
    """
    from dev_yard.qa_seeds import apply_seed_placeholders

    if not job.verify:
        return {
            "status": "failed",
            "reason": "driver host 需要 data.verify",
            "defect_class": "case",
            "repo": job.repo,
            "assertions": [],
        }
    try:
        script = case_script_path(job, job.verify, "verify")
        text = script.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TestRejected) as e:
        return {
            "status": "blocked",
            "blocked_class": "case-defect",
            "reason": f"case-defect: {e}",
            "repo": job.repo,
            "assertions": [],
        }
    if script.suffix.lower() != ".sql":
        return {
            "status": "failed",
            "reason": "driver host 的 data.verify 必须是 .sql",
            "defect_class": "case",
            "repo": job.repo,
            "assertions": [],
        }
    query, missing = apply_seed_placeholders(text, seeds, sql_literals=True)
    if missing:
        return {
            "status": "blocked",
            "blocked_class": "case-defect",
            "reason": "case-defect: verify.sql 缺 QA_SEED " + ", ".join(missing),
            "repo": job.repo,
            "assertions": [],
        }
    try:
        assert_readonly_sql(query)
        rows = run_sql_count(
            cfg, query, on_log=on_log, catalog=job.db or None, verify=True
        )
    except TestRejected as e:
        from dev_yard.qa_verify import verify_env_error

        if verify_env_error(e):
            return {
                "status": "blocked",
                "blocked_class": "env",
                "reason": f"env fault: {e}",
                "repo": job.repo,
                "assertions": [],
            }
        return {
            "status": "failed",
            "reason": f"verify query failed: {e}",
            "defect_class": "case",
            "repo": job.repo,
            "assertions": [],
        }
    status = "passed" if rows >= 1 else "failed"
    return {
        "status": status,
        "reason": "" if status == "passed" else "0 rows",
        "repo": job.repo,
        "assertions": [
            {
                "type": "db",
                "expected": ">=1",
                "actual": str(rows),
                "status": status,
                "sql": query.strip(),
            }
        ],
    }


def ping_case_sites(
    root: Path,
    jira: str,
    cfg: QaConfig,
    cases: list[CaseJob],
) -> tuple[Any, dict[str, dict[str, str]]]:
    """Ping every exec site this run's cases use.

    Returns `(template_executor, faults_by_site)`. The template stays open so
    later setup calls can rebind it per case. Other site executors are closed
    after the ping. A fault is keyed by the site name (empty string = default).
    """
    from dev_yard.exec_cfg import origin_for_site
    from dev_yard.script_exec import ExecUnreachable, resolve_executor

    ordered: list[str] = []
    for job in cases:
        site = exec_site_for_job(root, cfg, job) or ""
        if site not in ordered:
            ordered.append(site)
    if not ordered:
        ordered.append("")
    template = None
    faults: dict[str, dict[str, str]] = {}
    for site in ordered:
        origin = origin_for_site(cfg.env, site or None) or cfg.env.base_url
        wt = None
        for job in cases:
            if (exec_site_for_job(root, cfg, job) or "") != site or not job.repo:
                continue
            cand = paths.req_worktree(root, jira, job.repo)
            if cand.is_dir():
                wt = cand
                break
        try:
            ex = resolve_executor(
                cfg.env,
                base_url=origin,
                worktree=wt,
                root=root,
                site=site or None,
            )
        except (ExecUnreachable, TestRejected) as e:
            faults[site] = {
                "class": getattr(e, "error_class", None) or "unreachable",
                "message": str(e),
            }
            continue
        try:
            ex.ping()
        except (ExecUnreachable, TestRejected) as e:
            faults[site] = {
                "class": getattr(e, "error_class", None) or "unreachable",
                "message": str(e),
            }
            ex.close()
            continue
        # Only a site that answered ping may be copied onto later cases.
        # A failed socket must not become the shared template.
        if template is None:
            template = ex
        else:
            ex.close()
    return template, faults


def case_needs_exec_site(job: CaseJob) -> bool:
    """True when setup or cleanup is a script the exec site must run.

    `.sql` always runs on the host via usql, so a dead runner does not block it.
    """
    for name in (job.setup, job.cleanup):
        if name and Path(name).suffix.lower() != ".sql":
            return True
    return False


def exec_site_for_job(root: Path, cfg: QaConfig, job: CaseJob) -> str | None:
    """Named exec site for this case: repo.exec, else env default site."""
    from dev_yard.config import load_repos

    spec = getattr(cfg.env, "exec_cfg", None)
    default = getattr(spec, "exec_default", "") or ""
    alias = (job.repo or "").strip()
    if alias:
        repo = load_repos(root).get(alias)
        bound = (getattr(repo, "exec", None) or "").strip() if repo else ""
        if bound:
            return bound
    return default or None


def origin_for_job(root: Path, cfg: QaConfig, job: CaseJob) -> str:
    from dev_yard.exec_cfg import origin_for_site

    return origin_for_site(cfg.env, exec_site_for_job(root, cfg, job))


def run_case_script(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    kind: str,
    *,
    on_log: Any | None = None,
    executor: Any | None = None,
) -> str:
    """Run setup or cleanup. Reverts worktree files the script created (local only)."""
    from dev_yard.script_exec import ExecErrorClass, resolve_executor

    name = job.setup if kind == "setup" else job.cleanup
    if not name:
        return ""
    script = case_script_path(job, name, kind)
    wt = paths.req_worktree(root, jira, job.repo) if job.repo else None
    # .sql 造数/清理一律在宿主用 usql 跑：无论 exec.use / db.exec 怎么配，
    # 都不把 SQL 丢进现场（pod 里既无 psql 也无 usql，且远端退出码会被吞）。
    if script.suffix.lower() == ".sql":
        return _run_sql(cfg, script, on_log, catalog=job.db or None)
    started = time.time()
    db_env: dict[str, str] = {}
    needs_db = bool(job.db) or bool(getattr(cfg.env, "db_catalogs", ())) or bool(
        cfg.env.db_url
    )
    if needs_db:
        db_env["DATABASE_URL"] = connect_url(cfg, job.db or None)
        db_env["QA_DB"] = job.db or cfg.env.db_default or "default"
    from dev_yard.exec_cfg import bind_site

    site_name = exec_site_for_job(root, cfg, job)
    if executor is None:
        origin = origin_for_job(root, cfg, job)
        executor = resolve_executor(
            cfg.env,
            base_url=origin or cfg.env.base_url,
            worktree=wt,
            root=root,
            site=site_name,
        )
    else:
        # Never mutate a caller-shared executor: parallel cases in different
        # repos would race on `worktree` / named site.
        executor = copy.copy(executor)
        executor.worktree = wt
        executor.spec = bind_site(executor.spec, site_name)
    local = executor.site == "local"
    before = gitops.porcelain_paths(wt) if local and wt and wt.is_dir() else set()
    try:
        result = executor.run(
            script,
            on_log=on_log,
            env_extra={
                "QA_ENV": cfg.active_env,
                "QA_JIRA": jira,
                "QA_CASE_ID": job.id,
                "QA_SCRIPT_KIND": kind,
                "QA_EXEC_SITE": site_name or "",
                **db_env,
            },
        )
        if result.code != 0:
            err = (
                redact_qa_yaml((result.stderr or result.stdout or "").strip())
                or str(result.code)
            )
            raise TestRejected(
                f"{executor.label} {script.name} failed: {err}",
                error_class=str(result.error_class or ExecErrorClass.SCRIPT),
            )
        return redact_qa_yaml((result.stdout or "").strip())
    finally:
        if local and wt and wt.is_dir():
            _revert_new_paths(wt, before, window=(started, time.time()))


def _run_sql(
    cfg: QaConfig,
    script: Path,
    on_log: Any | None,
    catalog: str | None = None,
) -> str:
    try:
        url = connect_url(cfg, catalog)
    except TestRejected as e:
        raise TestRejected("qa.yaml has no db.url; cannot run .sql setup/cleanup") from e
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot run .sql setup/cleanup")
    cmd = [binary, url, "-f", str(script)]
    if on_log is not None:
        name = catalog or cfg.env.db_default or "db.url"
        on_log(f"$ usql <{name}> -f {script}")
    r = _run(cmd, timeout=300, label=f"usql {script.name}")
    if r.returncode != 0:
        err = redact_qa_yaml((r.stderr or r.stdout or "").strip()) or str(r.returncode)
        raise TestRejected(f"usql {script.name} failed: {err}")
    return redact_qa_yaml((r.stdout or "").strip())


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def _scan_sql(text: str, *, blank_strings: bool) -> str:
    """Walk SQL once, dropping comments and (optionally) blanking strings.

    A regex cannot do this safely: a `/*` born inside a `--` comment would
    swallow real code, and a `--` inside a `/* */` would do the same. A single
    pass with explicit state is the only way to keep the read-only guard honest,
    so a query cannot smuggle a write past it, while a comment-prefixed or
    dollar-quoted read-only query is not wrongly rejected.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":  # line comment
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            out.append(" ")
            continue
        if ch == "/" and nxt == "*":  # block comment, nesting-aware
            i += 2
            depth = 1
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            out.append(" ")
            continue
        if ch == "$":  # dollar-quoted string: $tag$ ... $tag$
            tag = _DOLLAR_TAG.match(text, i)
            end = text.find(tag.group(0), tag.end()) if tag else -1
            if tag and end != -1:
                stop = end + len(tag.group(0))
                out.append("''" if blank_strings else text[i:stop])
                i = stop
                continue
        if ch in {"'", '"'}:  # string literal / quoted identifier
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == quote:
                    if j + 1 < n and text[j + 1] == quote:  # doubled escape
                        j += 2
                        continue
                    break
                j += 1
            stop = min(j + 1, n)
            if blank_strings:
                out.append("''" if quote == "'" else '""')
            else:
                out.append(text[i:stop])
            i = stop
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def assert_readonly_sql(text: str, *, what: str = "verify.sql") -> str:
    """Validate a single read-only statement; return it without a trailing `;`.

    The returned statement has comments stripped and any single trailing `;`
    removed, so callers can safely wrap it (see `run_sql_count`).
    """
    body = (text or "").strip()
    if not body:
        raise TestRejected(f"{what} is empty")
    # Judge the code with comments and string literals blanked, so prose in a
    # `-- case-01 verify: ...` header or a write word in a literal is not SQL.
    code = _scan_sql(body, blank_strings=True).strip()
    if not code:
        raise TestRejected(f"{what} is empty")
    if ";" in code.rstrip(";"):
        raise TestRejected(f"{what} must be a single statement (no `;`)")
    head = code.split(None, 1)[0].lower()
    if head not in READONLY_SQL_HEADS:
        raise TestRejected(
            f"{what} must be read-only (select/show/desc/describe/explain/with/table)"
        )
    if _WRITE_SQL.search(code):
        raise TestRejected(f"{what} must be read-only (no write keywords)")
    clean = _scan_sql(body, blank_strings=False).strip()
    while clean.endswith(";"):
        clean = clean[:-1].rstrip()
    return clean


def run_sql_value(
    cfg: QaConfig,
    sql: str,
    on_log: Any | None = None,
    catalog: str | None = None,
    *,
    verify: bool = False,
) -> str:
    """Run a read-only verify statement and return its first scalar cell.

    Used to re-check a worker's `db` assertion independently: the host runs the
    statement the assertion carries and compares the value to `expected`. Empty
    output is returned as "" so the caller can flag the mismatch.
    """
    body = assert_readonly_sql(sql, what="db assertion sql")
    try:
        url = connect_url(cfg, catalog, verify=verify)
    except TestRejected as e:
        raise TestRejected("qa.yaml has no db.url; cannot re-check db assertion") from e
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot re-check db assertion")
    if on_log is not None:
        label = catalog or cfg.env.db_default or "db.url"
        on_log(f"$ usql <{label}> -t -A -c {body[:200]}")
    r = _run(
        [binary, url, "-t", "-A", "-c", body],
        timeout=120,
        label="usql recheck",
    )
    if r.returncode != 0:
        err = redact_qa_yaml((r.stderr or r.stdout or "").strip()) or str(r.returncode)
        raise TestRejected(f"db assertion sql failed: {err}")
    for line in (r.stdout or "").splitlines():
        cell = line.split("|", 1)[0].strip()
        if cell:
            return cell
    return ""


def recheck_db_assertions(
    cfg: QaConfig,
    job: CaseJob,
    result: dict[str, Any],
    on_log: Any | None = None,
    seeds: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Re-run the `db` assertions that carry a `sql` field; report mismatches.

    A worker self-attests `passed`; this is the host's independent check. Only
    assertions that declare a machine-runnable read-only `sql` can be re-checked
    — ones without it are left to the human and recorded as unverified.
    `:seed.key` / `<seed.key>` left in the worker SQL are filled from setup stdout.
    """
    from dev_yard.qa_seeds import apply_seed_placeholders

    problems: list[dict[str, Any]] = []
    for item in result.get("assertions") or []:
        if not isinstance(item, dict) or str(item.get("type")) != "db":
            continue
        sql = str(item.get("sql") or "").strip()
        if not sql:
            continue
        if seeds:
            sql, missing = apply_seed_placeholders(sql, seeds, sql_literals=True)
            if missing:
                problems.append(
                    {
                        "case": job.id,
                        "expected": item.get("expected"),
                        "actual": "recheck error: missing QA_SEED " + ", ".join(missing),
                        "sql": sql,
                        "kind": "error",
                    }
                )
                continue
            item["sql"] = sql
        expected = item.get("expected")
        try:
            got = run_sql_value(cfg, sql, on_log=on_log, catalog=job.db or None)
        except TestRejected as e:
            # Could not run the check (usql missing, bad SQL): report it, but
            # do not fail the case — only a real value mismatch downgrades.
            problems.append(
                {
                    "case": job.id,
                    "expected": expected,
                    "actual": f"recheck error: {e}",
                    "kind": "error",
                }
            )
            continue
        recorded = item.get("actual")
        kind = _db_recheck_kind(expected, recorded, got)
        if kind == "ok":
            continue
        problems.append(
            {
                "case": job.id,
                "expected": expected,
                "actual": got,
                "recorded": recorded,
                "sql": sql,
                "kind": kind,
            }
        )
    return problems


def diagnose_pi_exit(code: int, output: str) -> tuple[str, str]:
    """Return `(reason, detail)` for a worker that exited without a result.

    `reason` is what the scheduler classifies. It stays a `worker exit` plus a
    fixed hint, so a raw log line like `context canceled` cannot look like a
    user cancel and skip the pool breaker. `detail` is the last output line
    for the run log only.
    """
    text = (output or "").strip()
    low = text.lower()
    hint = ""
    if any(
        s in low
        for s in (
            "unauthorized",
            "invalid api key",
            "authentication failed",
            "auth failed",
        )
    ) or re.search(r"\b401\b", low):
        hint = "疑似鉴权失败，检查该 pool 的 provider 凭据"
    elif any(
        s in low
        for s in ("429", "rate limit", "quota", "insufficient_quota", "billing")
    ):
        hint = "疑似额度或限流，检查该 pool 的配额"
    elif any(
        s in low
        for s in (
            "input_too_large",
            "context length",
            "maximum context",
            "too many tokens",
            "context window",
        )
    ):
        hint = "疑似上下文超限，缩短 prompt 或换更大窗口的模型"
    tail = ""
    for line in reversed(text.splitlines()):
        line = " ".join(line.split())
        if line:
            tail = line[:180]
            break
    reason = f"worker exit: pi exit {code}"
    if hint:
        reason += f"; {hint}"
    return reason, tail


def _is_prose_expected(value: Any) -> bool:
    """Case prose ("col = 0", a full sentence), not a value the cell can equal."""
    if value is None:
        return False
    text = " ".join(str(value).split())
    if not text or "=" in text:
        return bool(text)
    return len(text.split(" ")) > 2


def _db_recheck_kind(expected: Any, recorded: Any, sql_value: str) -> str:
    """`ok`, `mismatch`, or `unverified`.

    A cell-shaped expected must equal the SQL cell. Obvious prose falls back
    to the cell the worker recorded in `actual`. When that recorded value is
    itself prose, the host cannot check it: keep the worker verdict and mark
    the assertion unverified instead of failing the case.
    """
    if _scalar_eq(expected, sql_value):
        return "ok"
    if not _is_prose_expected(expected):
        return "mismatch"
    if _is_prose_expected(recorded):
        return "unverified"
    if _scalar_eq(recorded, sql_value):
        return "ok"
    return "mismatch"


def _db_recheck_ok(expected: Any, recorded: Any, sql_value: str) -> bool:
    """True when the host's SQL cell confirms the assertion."""
    return _db_recheck_kind(expected, recorded, sql_value) == "ok"


def _scalar_eq(expected: Any, got: str) -> bool:
    if expected is None:
        return False
    return " ".join(str(expected).split()).casefold() == " ".join(got.split()).casefold()


def run_sql_lines(
    cfg: QaConfig,
    sql: str,
    on_log: Any | None = None,
    catalog: str | None = None,
    *,
    verify: bool = False,
) -> list[str]:
    """Run a read-only statement and return non-empty stdout lines (usql -t -A)."""
    body = assert_readonly_sql(sql)
    try:
        url = connect_url(cfg, catalog, verify=verify)
    except TestRejected as e:
        raise TestRejected("qa.yaml has no db.url; cannot query schema") from e
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot query schema")
    if on_log is not None:
        label = catalog or cfg.env.db_default or "db.url"
        on_log(f"$ usql <{label}> -t -A -c {body[:200]}")
    r = _run(
        [binary, url, "-t", "-A", "-c", body],
        timeout=120,
        label="usql schema",
    )
    if r.returncode != 0:
        err = redact_qa_yaml((r.stderr or r.stdout or "").strip()) or str(r.returncode)
        raise TestRejected(f"schema query failed: {err}")
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def parse_usql_json_row(stdout: str) -> dict[str, Any]:
    """First object from `usql -J` output. Empty output is an empty row."""
    text = (stdout or "").strip()
    if not text:
        return {}
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start > 0:
        text = text[start:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        line = next((ln for ln in text.splitlines() if ln.strip()[:1] in "[{"), "")
        if not line:
            raise TestRejected(f"verify row was not JSON: {text[:200]!r}") from e
        try:
            data = json.loads(line)
        except json.JSONDecodeError as inner:
            raise TestRejected(f"verify row was not JSON: {text[:200]!r}") from inner
    if isinstance(data, list):
        if not data:
            return {}
        data = data[0]
    if not isinstance(data, dict):
        raise TestRejected(f"verify row was not an object: {text[:200]!r}")
    return data


def run_sql_first_row(
    cfg: QaConfig,
    sql: str,
    on_log: Any | None = None,
    catalog: str | None = None,
    *,
    verify: bool = False,
) -> dict[str, Any]:
    """First row of a read-only verify statement, keyed by column alias.

    Used when a case has no setup and names page values as `<seed.alias>`.
    Oracle gets `ROWNUM = 1`; other catalogs get `LIMIT 1`.
    """
    body = assert_readonly_sql(sql)
    try:
        url = connect_url(cfg, catalog, verify=verify)
    except TestRejected as e:
        raise TestRejected("qa.yaml has no db.url; cannot read an existing row") from e
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot read an existing row")
    scheme = url.split(":", 1)[0].lower()
    if scheme in {"oracle", "godror"}:
        wrapped = f"SELECT * FROM ({body}) qa_verify WHERE ROWNUM = 1"
    else:
        wrapped = f"SELECT * FROM ({body}) qa_verify LIMIT 1"
    if on_log is not None:
        label = catalog or cfg.env.db_default or "db.url"
        on_log(f"$ usql <{label}> -J -c {wrapped[:200]}")
    r = _run(
        [binary, url, "-J", "-q", "-c", wrapped],
        timeout=120,
        label="usql verify row",
    )
    if r.returncode != 0:
        err = redact_qa_yaml((r.stderr or r.stdout or "").strip()) or str(r.returncode)
        raise TestRejected(f"verify row failed: {err}")
    return parse_usql_json_row(r.stdout or "")


def run_sql_count(
    cfg: QaConfig,
    sql: str,
    on_log: Any | None = None,
    catalog: str | None = None,
    *,
    verify: bool = False,
) -> int:
    """Row count of a read-only verify statement, run host-side via usql.

    A `SELECT`/`WITH` is wrapped in `count(*)`, so the count is a real number
    rather than a parse of the client's table rendering; the other read-only
    heads report the number of output lines.
    """
    body = assert_readonly_sql(sql)
    try:
        url = connect_url(cfg, catalog, verify=verify)
    except TestRejected as e:
        raise TestRejected("qa.yaml has no db.url; cannot verify data") from e
    binary = shutil.which("usql")
    if not binary:
        raise TestRejected("usql not found; cannot verify data")
    head = body.split(None, 1)[0].lower()
    # Bare alias, never `AS`: Oracle rejects `FROM (...) AS x` with ORA-00933,
    # while it accepts `FROM (...) x` — which Postgres and MySQL accept too, so
    # one form covers every catalog we talk to.
    query = f"SELECT count(*) FROM ({body}) qa_verify" if head in {"select", "with"} else body
    if on_log is not None:
        label = catalog or cfg.env.db_default or "db.url"
        on_log(f"$ usql <{label}> -t -A -c {query[:200]}")
    r = _run(
        [binary, url, "-t", "-A", "-c", query],
        timeout=120,
        label="usql verify",
    )
    if r.returncode != 0:
        err = redact_qa_yaml((r.stderr or r.stdout or "").strip()) or str(r.returncode)
        raise TestRejected(f"verify query failed: {err}")
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if head in {"select", "with"}:
        if not lines:
            raise TestRejected("verify query returned no count")
        try:
            return int(lines[0])
        except ValueError as e:
            raise TestRejected(
                f"verify query returned a non-numeric count: {lines[0]!r}"
            ) from e
    return len(lines)


def _revert_new_paths(
    worktree: Path, before: set[str], *, window: tuple[float, float] | None = None
) -> None:
    after = gitops.porcelain_paths(worktree)
    for rel in sorted(after - before):
        target = worktree / rel
        if window is not None and not _created_in_window(target, window):
            # Not ours (or touched after the script finished): leave it for the
            # run-level mutation gate instead of deleting another case's work.
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except OSError:
            try:
                gitops.run(["git", "checkout", "--", rel], cwd=worktree)
            except gitops.GitError:
                pass
    # Drop empty tmp dirs the script left behind.
    tmp = worktree / "tmp"
    if tmp.is_dir() and "tmp" not in before:
        try:
            next(tmp.iterdir())
        except StopIteration:
            tmp.rmdir()
        except OSError:
            pass


def _created_in_window(target: Path, window: tuple[float, float]) -> bool:
    start, end = window
    try:
        mtime = target.lstat().st_mtime
    except OSError:
        return False
    return start - 1.0 <= mtime <= end + 1.0


def normalize_case_result(
    data: dict[str, Any], job: CaseJob, *, require_assertions: bool
) -> dict[str, Any] | None:
    """Return a cleaned result, or None if the contract is broken."""
    status = normalize_status(data.get("status"))
    assertions = _normalize_assertions(data.get("assertions"))
    if require_assertions and status in {"passed", "failed"}:
        if assertions is None:
            return None
        if status == "passed" and any(
            str(a.get("status")) == "failed" for a in assertions
        ):
            status = "failed"
    failure = data.get("failure") if isinstance(data.get("failure"), dict) else None
    if status == "failed" and not failure:
        failed = next(
            (a for a in (assertions or []) if str(a.get("status")) == "failed"),
            None,
        )
        if failed:
            failure = {
                "step": 0,
                "step_desc": str(failed.get("expected") or "assertion failed"),
                "evidence": "",
            }
        else:
            return None
    raw_class = str(data.get("blocked_class") or "").strip().lower()
    blocked_class = raw_class if status == "blocked" and raw_class in BLOCKED_CLASSES else ""
    raw_defect = str(data.get("defect_class") or "").strip().lower()
    defect_class = raw_defect if raw_defect in {"product", "case", "unclassified"} else ""
    return {
        "status": status,
        "reason": str(data.get("reason") or ""),
        "blocked_class": blocked_class,
        "defect_class": defect_class,
        "repo": str(data.get("repo") or job.repo),
        "title": str(data.get("title") or job.title),
        "covers": data.get("covers") or job.covers,
        "failure": failure,
        "assertions": assertions or [],
        "raw": data,
    }


def _normalize_assertions(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        typ = str(item.get("type") or "").strip().lower()
        if typ == "network":
            typ = "net"
        if typ not in _ASSERT_TYPES:
            return None
        if "expected" not in item or "actual" not in item:
            return None
        row = {
            "type": typ,
            "expected": item.get("expected"),
            "actual": item.get("actual"),
            "status": normalize_status(item.get("status")),
        }
        if item.get("carrier"):
            row["carrier"] = item.get("carrier")
        if typ == "db" and item.get("sql"):
            # Carried so the host can re-run the assertion itself (see
            # recheck_db_assertions) instead of trusting the worker's `passed`.
            row["sql"] = str(item["sql"])
        out.append(row)
    return out
