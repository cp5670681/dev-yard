from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextlib import nullcontext as _nullcontext
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from dev_yard import attachments, gitops, paths
from dev_yard import qa_state as qa_st
from dev_yard import status as st
from dev_yard.config import load_repos, resolve_freeze_branch
from dev_yard.parse import as_name_list
from dev_yard.qa_config import (
    QaConfig,
    TestRejected,
    default_state_file,
    load_qa_config,
    redact_url,
)
from dev_yard.qa_exec import (
    diagnose_pi_exit,
    ensure_auth,
    normalize_case_result,
    recheck_db_assertions,
    replay_path,
    run_case_script,
)
from dev_yard.qa_report import has_design_blocked_skip, map_qa_result, triage_buckets
from dev_yard.qa_review import (
    approve_cases,
    cases_fingerprint,
    clear_stale,
    mark_machine_fixed,
    reject_cases,
    review_gate,
    review_payload,
)
from dev_yard.qa_schedule import (
    BLOCKED_KINDS,
    TERMINAL,
    CaseJob,
    PoolSlot,
    blocked_kind,
    normalize_status,
    now_iso,
    progress_line,
    progress_payload,
    run_schedule,
)
from dev_yard.qa_verify import (
    VerifyResult,
    describe,
    env_lock,
    failed_cases,
    prior_defects,
    render_feedback,
    render_prior_defects,
    verify_cases,
    verify_gate,
    verify_view,
    write_blocked,
    write_summary,
)
from dev_yard.runners import (
    JobCancelled,
    Runner,
    get_runner,
    pi_argv,
    run_pi_print_tracked,
)
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import load_registry
from dev_yard.test_report import ReportRejected, accept_test_report
from dev_yard.tickets import load_tickets

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]
SpawnFn = Callable[[subprocess.Popen[str]], None]
ReapFn = Callable[[subprocess.Popen[str]], None]
_CASE_NAME = re.compile(r"^case-.+\.md$")
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PNG = re.compile(r"\.png$", re.I)


def write_context_md(root: Path, jira: str, cfg: QaConfig) -> Path:
    req = paths.req_dir(root, jira)
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    repos = load_repos(root)
    aliases = _involved_aliases(root, jira)
    data = st.load(root, jira) if (req / "STATUS.yaml").is_file() else {}
    lines = [
        f"# yard-qa context — {jira}",
        "",
        f"Req dir: `{req}`",
        f"qa dir: `{qa}`",
        "",
        "## Worktrees",
        "",
    ]
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        repo = repos.get(alias)
        base = repo.default_base if repo else "?"
        role = repo.role if repo else "?"
        branch = resolve_freeze_branch(root, jira, data, wt)
        lines.append(
            f"- {alias}: {wt.resolve()}  "
            f"(branch {branch}, base {base}, role {role})"
        )
    env = cfg.env
    routes = _meta_routes(qa)
    db = "configured" if env.db_url else "not configured"
    headed = "true" if cfg.headed else "false"
    others = [n for n in cfg.env_names if n != cfg.active_env]
    lines += [
        "",
        "## Environment",
        "",
        f"- env: {cfg.active_env}",
        f"- available envs: {', '.join(cfg.env_names) or cfg.active_env}",
        f"- base_url: {redact_url(env.base_url)}",
        f"- browser: {cfg.browser.channel} headed={headed}",
        f"- account.default: {env.auth_default or '(none)'}",
        f"- concurrency: {cfg.total_concurrency} "
        "(state-save is only allowed when this is 1)",
        f"- db: {db} (qa.yaml envs.{cfg.active_env}.db.url)",
        f"- script.runner: {env.script_runner or '(sql only)'}",
        f"- exec.use: {getattr(env.exec_cfg, 'use', None) or 'local'}",
        f"- exec.site: {getattr(env.exec_cfg, 'site', None) or 'local'}",
        "",
        "This run uses only the env above; do not switch env or guess another host.",
    ]
    site = getattr(env.exec_cfg, "site", None) or "local"
    if site == "remote":
        lines.append(
            "This env's site is remote: the browser and setup/cleanup scripts hit the "
            "**deployed** environment, not the freeze worktree. DB assertions and seed "
            "models follow the deployed code. Worktrees are for reading code and the "
            "mutation gate only."
        )
        lines.append(
            f"- 5xx 诊断：`dev-yard qa logs {jira} --request-id <x-request-id>`"
            "（宿主只读查日志；不要自己 ssh/kubectl）"
        )
    lines += [
        "",
        "Setup/cleanup scripts (host-run): single file; stdin + QA_ENV/QA_JIRA/"
        "QA_CASE_ID/QA_SCRIPT_KIND; stdout is the only channel back; state lives in "
        "the DB, never on the execution host's disk; do not assume two runs land on "
        "the same replica; do not read ARGV.",
    ]
    if others:
        lines.append(
            f"Other envs exist ({', '.join(others)}) but are out of scope for this run."
        )
    if redact_url(env.base_url) != env.base_url:
        lines.append(
            "base_url userinfo is masked here; read the full URL from "
            f"`qa.yaml` envs.{cfg.active_env}.base_url if login needs it."
        )
    lines += ["", "## Routes", ""]
    if routes:
        for name, route in routes.items():
            lines.append(f"- {name}: {redact_url(env.base_url.rstrip('/') + route)}")
    else:
        lines.append(
            "(none in meta.yaml; read frontend route code and join with base_url)"
        )
    lines += [
        "",
        "## Accounts",
        "",
    ]
    if env.accounts:
        for name, a in env.accounts.items():
            default = " (default)" if name == env.auth_default else ""
            state = a.state_file or "(none)"
            lines.append(
                f"- {name}: username={a.username or '?'} state_file={state}{default}"
            )
    else:
        lines.append("(none configured; cases must not use `account`)")
    lines += [
        "",
        "A case frontmatter `account:` picks one of the above; no `account` uses "
        "the default. Load that account's state_file before the case steps if available. "
        "If not authenticated or redirected to login, inspect the login form dynamically "
        "and complete login with the account's credentials.",
        "Passwords are not listed in evidence files. Never copy a password into result.yaml "
        "or evidence; redact DSNs and passwords as `***`.",
        "",
        "## Notes",
        "",
    ]
    if env.notes:
        lines.extend(f"- {n}" for n in env.notes)
    else:
        lines.append("(none)")
    lines.append("")
    path = qa / "context.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _meta_routes(qa: Path) -> dict[str, str]:
    meta = qa / "meta.yaml"
    if not meta.is_file():
        return {}
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return {}
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, dict):
        return {}
    return {str(k): str(v) for k, v in routes.items()}


def discover_cases(qa: Path) -> list[CaseJob]:
    cases_root = qa / "cases"
    if not cases_root.is_dir():
        return []
    out: list[CaseJob] = []
    seen: dict[str, Path] = {}
    for path in sorted(cases_root.rglob("case-*.md")):
        if not path.is_file() or not _CASE_NAME.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = split_frontmatter(text)
        except yaml.YAMLError as e:
            raise TestRejected(f"unreadable case frontmatter in {path}: {e}") from e
        cid = str(meta.get("id") or path.stem).strip()
        if not _CASE_ID.match(cid):
            raise TestRejected(
                f"invalid case id {cid!r} in {path}; "
                "use letters/digits/._- and start alphanumeric"
            )
        if cid in seen:
            raise TestRejected(
                f"duplicate case id {cid!r}: {seen[cid]} and {path}"
            )
        seen[cid] = path
        deps = as_name_list(meta.get("depends_on"))
        covers = as_name_list(meta.get("covers"))
        module = path.parent.name
        data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
        if "writes" in data and data.get("writes") is not None and not isinstance(
            data.get("writes"), list
        ):
            raise TestRejected(
                f"case {cid} data.writes must be a list of table.column"
            )
        writes_raw = data.get("writes") or []
        writes = [str(w).strip() for w in writes_raw if str(w).strip()]
        out.append(
            CaseJob(
                id=cid,
                title=str(meta.get("title") or cid),
                repo=str(meta.get("repo") or "").strip(),
                depends_on=[str(d).strip() for d in deps if str(d).strip()],
                priority=str(meta.get("priority") or "P1"),
                body=body,
                path=str(path),
                covers=[str(c).strip() for c in covers if str(c).strip()],
                module=module,
                account=str(meta.get("account") or "").strip(),
                setup=str(data.get("setup") or "").strip(),
                cleanup=str(data.get("cleanup") or "").strip(),
                verify=str(data.get("verify") or "").strip(),
                writes=writes,
                identity=str(data.get("identity") or "").strip(),
            )
        )
    return out


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    rest = text[3:].lstrip("\n")
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    raw = rest[:end]
    body = rest[end + 4 :].lstrip("\n")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        return {}, body
    return data, body


def _involved_aliases(root: Path, jira: str) -> list[str]:
    req = paths.req_dir(root, jira)
    aliases: set[str] = set()
    for t in load_tickets(req):
        if t.repo:
            aliases.add(t.repo)
    data = st.load(root, jira)
    for a in data.get("repos") or []:
        if a:
            aliases.add(str(a))
    for slot in (data.get("tickets") or {}).values():
        repo = (slot or {}).get("repo")
        if repo:
            aliases.add(str(repo))
    return sorted(aliases)


def _check_case_repos(root: Path, cases: list[CaseJob]) -> None:
    """Every runnable case must name a real repos.yaml alias.

    Caught before the run so a repo-less case cannot abort ingest afterwards
    (a failed finding needs a repo) or attribute its result to everything.
    """
    repos = set(load_repos(root))
    for job in cases:
        if not job.repo:
            raise TestRejected(
                f"case {job.id} has no repo; add `repo: <alias>` to its frontmatter"
            )
        if repos and job.repo not in repos:
            raise TestRejected(
                f"case {job.id} repo {job.repo!r} is not a repos.yaml alias"
            )


def uncovered_changes(qa: Path, cases: list[CaseJob]) -> list[str]:
    """Change ids (meta.yaml `changes`) no case declares in `covers`."""
    meta = qa / "meta.yaml"
    if not meta.is_file():
        return []
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    changes = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(changes, list):
        return []
    ids = [
        str(c.get("id")).strip()
        for c in changes
        if isinstance(c, dict) and str(c.get("id") or "").strip()
    ]
    if not ids:
        return []
    covered = {c for job in cases for c in job.covers}
    return [cid for cid in ids if cid not in covered]


_PERMISSION_HINT = re.compile(r"权限|角色|授权|可见范围|permission|authoriz", re.I)
_PERMISSION_SCAN_MAX = 2 * 1024 * 1024


def _read_capped(path: Path, cap: int) -> str:
    """Read at most `cap` chars, never raising (advisory scan must not break)."""
    try:
        with path.open("r", encoding="utf-8", errors="strict") as fh:
            return fh.read(cap)
    except (OSError, UnicodeDecodeError):
        return ""


def _permission_gap_warning(root: Path, jira: str, cfg: QaConfig) -> str | None:
    """P1 nudge: permission-flavoured change but only the default account.

    Advisory only — never blocks and never raises. Point at the discovery path
    so the human can turn the nudge into accounts in one command.
    """
    if len(cfg.env.accounts) > 1:
        return None
    req = paths.req_dir(root, jira)
    text = ""
    for name in ("REQUIREMENT.md", "SPEC.md", "GRILL.md", "TICKETS.md"):
        p = req / name
        if p.is_file():
            text += _read_capped(p, _PERMISSION_SCAN_MAX - len(text))
    repos = load_repos(root)
    for alias in _involved_aliases(root, jira):
        if len(text) >= _PERMISSION_SCAN_MAX:
            break
        wt = paths.req_worktree(root, jira, alias)
        repo = repos.get(alias)
        if not wt.is_dir() or repo is None:
            continue
        try:
            base = gitops.freeze_base(wt, repo.default_base)
            changed = gitops.changed_files(wt, base)
        except Exception:  # noqa: BLE001 — a nudge must never break design
            continue
        for rel in sorted(changed):
            if len(text) >= _PERMISSION_SCAN_MAX:
                break
            f = wt / rel
            if not f.is_file():
                continue
            text += _read_capped(f, _PERMISSION_SCAN_MAX - len(text))
    if not _PERMISSION_HINT.search(text):
        return None
    return (
        f"{jira} 改动疑似涉及权限控制，但只配了默认账号；"
        f"跑 `dev-yard req accounts {jira} --auto` 一键发现并填入本需求账号"
        "（仅提示，不阻塞）。"
    )


def _gate(root: Path, jira: str, *, waive_open_tickets: bool = False) -> None:
    req = paths.req_dir(root, jira)
    if not req.is_dir():
        raise TestRejected(f"missing {req}; run: dev-yard req open {jira}")
    data = st.load(root, jira)
    if data.get("phase") != "testing":
        raise TestRejected(
            f"{jira} phase={data.get('phase')}; run `dev-yard req submit-test` first"
        )
    if data.get("contract_review") != "passed":
        raise TestRejected(
            f"{jira} contract_review is {data.get('contract_review')!r}; "
            "must be passed before req test"
        )
    if st.test_passed(data):
        raise TestRejected(f"{jira} already has a passed test report")
    # A single-case rerun and a case redesign are diagnostic. Open product
    # tickets must not lock them; a fresh full run still waits for those tickets.
    if not waive_open_tickets and not st.all_done(data):
        raise TestRejected(
            f"{jira} still has open tickets; finish the test bug tickets before re-testing"
        )
    aliases = _involved_aliases(root, jira)
    missing = [
        a for a in aliases if not paths.req_worktree(root, jira, a).is_dir()
    ]
    if missing:
        raise TestRejected(
            f"{jira} missing freeze worktrees: {', '.join(missing)}"
        )


def _claim_run_dir(evidence: Path) -> tuple[str, Path]:
    """Atomically claim a fresh run dir so two runs never share one."""
    evidence.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    for suffix in ["", *(f"-{n}" for n in range(2, 100))]:
        rid = base + suffix
        d = evidence / rid
        try:
            d.mkdir()
            return rid, d
        except FileExistsError:
            continue
    raise TestRejected(f"cannot allocate a run dir under {evidence}")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # exists but owned by another user: treat as live
    except OSError:
        return False
    return True


@contextmanager
def _run_lock(root: Path, jira: str):
    """Refuse a second concurrent `req test` for the same requirement.

    A stale lock (owner process gone) is reclaimed by atomically renaming it
    away first, so two reclaimers cannot delete each other's fresh lock. The
    lock file holds a unique token; release only removes our own lock.
    """
    import uuid

    lock_dir = root / ".yard-qa" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / f"{jira}.run.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    for attempt in range(2):
        # Hardlink a fully-written temp file into place: the lock is atomically
        # created *with* its token, so a concurrent reader can never observe an
        # empty holder and mistake a live lock for a stale one.
        tmp = lock_dir / f".{jira}.{uuid.uuid4().hex}.tmp"
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
                # Unknown/empty holder means we cannot prove it is stale: treat
                # it as live rather than stealing another run's lock.
                if pid is not None and not _pid_alive(pid) and attempt == 0:
                    # Atomic reclaim: the winner renames, losers get FileNotFound.
                    stale = path.with_name(path.name + f".stale.{uuid.uuid4().hex}")
                    try:
                        os.rename(path, stale)
                    except OSError:
                        pass
                    else:
                        stale.unlink(missing_ok=True)
                    continue
                raise TestRejected(
                    f"another run for {jira} is in progress; wait for it to finish "
                    f"(or delete {path} if it is stale)"
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


def _porcelain(worktree: Path) -> str:
    try:
        return gitops.run(
            ["git", "status", "--porcelain", "-uall"], cwd=worktree
        )
    except gitops.GitError as e:
        return f"(git status failed: {e})"


def _porcelain_lines(text: str) -> set[str]:
    return {ln for ln in text.splitlines() if ln.strip() and not ln.startswith("(")}


def _head_sha(worktree: Path) -> str | None:
    try:
        return gitops.run(["git", "rev-parse", "HEAD"], cwd=worktree).strip()
    except gitops.GitError:
        return None


def _tree_state(worktree: Path) -> dict[str, str] | None:
    """Map changed path → `status|content-hash`, or None if git status failed.

    Hashing catches edits to files that were already dirty at baseline, which a
    plain porcelain set-difference cannot see.
    """
    text = _porcelain(worktree)
    if text.startswith("("):
        return None
    state: dict[str, str] = {}
    for ln in _porcelain_lines(text):
        entry = gitops.parse_porcelain_line(ln)
        if entry is None:
            continue
        status, path = entry
        digest = ""
        try:
            target = worktree / path
            if target.is_file():
                digest = hashlib.sha1(target.read_bytes()).hexdigest()
        except OSError:
            digest = "?"
        state[path] = f"{status}|{digest}"
    return state


def _root_png_names(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_file() and _PNG.search(p.name)}


def _case_account(cfg: QaConfig, job: CaseJob) -> str:
    return job.account or cfg.env.auth_default


def _check_case_accounts(cfg: QaConfig, cases: list[CaseJob], jira: str) -> None:
    """A case that names an account must name one this run has configured."""
    uses_default = any(not job.account for job in cases)
    if (
        uses_default
        and cfg.env.accounts
        and cfg.env.auth_default not in cfg.env.accounts
    ):
        raise TestRejected(
            f"auth.default {cfg.env.auth_default!r} is not one of the configured "
            f"accounts ({', '.join(cfg.env.accounts)}); fix qa.yaml or "
            f"`dev-yard req accounts {jira}`"
        )
    for job in cases:
        if job.account and job.account not in cfg.env.accounts:
            raise TestRejected(
                f"case {job.id} uses account {job.account!r}, which is not configured "
                f"for env {cfg.active_env}; run `dev-yard req accounts {jira}`"
            )


def _case_auth_env(cfg: QaConfig, job: CaseJob) -> dict[str, str]:
    """Credentials for a case, handed to the worker via env, never the prompt."""
    name = _case_account(cfg, job)
    acct = cfg.env.accounts.get(name) if name else None
    if acct is None:
        return {}
    state_str = acct.state_file or str(
        default_state_file(cfg.active_env, acct.name)
    )
    return {
        "YARD_QA_USERNAME": acct.username or "",
        "YARD_QA_PASSWORD": acct.password or "",
        "YARD_QA_STATE_FILE": state_str,
        "YARD_QA_AUTH_REPLAY": str(Path(state_str).with_suffix(".replay.sh")),
    }


def _used_accounts(cfg: QaConfig, cases: list[CaseJob]) -> list[str]:
    names: list[str] = []
    for job in cases:
        name = _case_account(cfg, job)
        if name and name in cfg.env.accounts and name not in names:
            names.append(name)
    return names


def _preload_auth(
    root: Path,
    cfg: QaConfig,
    names: list[str] | None = None,
    on_log: LogFn | None = None,
) -> dict[str, str]:
    """Ensure sessions exist; returns {account: error} for the ones that failed."""
    return ensure_auth(root, cfg, names, on_log)


def lint_cases(
    root: Path, jira: str, cfg: QaConfig, cases: list[CaseJob]
) -> list[str]:
    """Static contract checks a case must pass before review/approve (M7).

    Catches what used to only blow up mid-run: a missing/invalid repo, an
    unconfigured account, `data.writes` without `data.identity`, and a change
    point no case covers. Returns human-readable problems (empty = clean).
    """
    problems: list[str] = []
    repos = set(load_repos(root))
    for job in cases:
        if not job.repo:
            problems.append(f"{job.id}: 缺少 repo")
        elif repos and job.repo not in repos:
            problems.append(f"{job.id}: repo {job.repo!r} 不是 repos.yaml 别名")
        if job.account and job.account not in cfg.env.accounts:
            problems.append(f"{job.id}: account {job.account!r} 未配置")
        if job.writes and not job.identity:
            problems.append(f"{job.id}: data.writes 需要配套 data.identity")
    for cid in uncovered_changes(paths.qa_dir(root, jira), cases):
        problems.append(f"改动点 {cid} 未被任何用例 covers")
    return problems


def _real_pool_probe(
    root: Path, slot: PoolSlot, *, on_spawn: SpawnFn | None = None, on_reap: ReapFn | None = None
) -> tuple[bool, str]:
    """A trivial pi call that proves a pool's provider/model can run (M3)."""
    spec = load_registry(root)["qa-run"]
    argv = pi_argv(
        root=root,
        bundle="qa-run",
        prompt=None,
        print_mode=True,
        spec=spec,
        provider=slot.provider,
        model=slot.model,
    )
    code, raw = run_pi_print_tracked(
        argv,
        root,
        "Reply with the single word: ok",
        timeout=60,
        on_spawn=on_spawn,
        on_reap=on_reap,
    )
    if code == 0:
        return True, ""
    reason, _detail = diagnose_pi_exit(code, raw)
    return False, reason


def _duties(kind: str, jira: str) -> str:
    # Hard constraints only; the detailed playbook lives in the SKILL.md next to
    # this text. Keep the two in sync (a test asserts the key phrases).
    qa = f"reqs/{jira}/qa/"
    if kind == "design":
        return (
            "You are designing UI test cases for this freeze worktree.\n"
            f"Write only under {qa} (meta.yaml, cases/, OPEN-QUESTIONS.md). "
            "Do not write STATUS.yaml or REQUIREMENT/GRILL/SPEC/TICKETS.md.\n"
            "Read REQUIREMENT.md, SPEC.md, TICKETS.md. Do not call MCP or re-fetch Jira.\n"
            "Diff each worktree with `git diff <default_base>...HEAD`. "
            "Empty diff: stop and say so.\n"
            "Do not git checkout, commit, push, or switch.\n"
            "Do not interview interactively; record uncertainties in "
            "qa/OPEN-QUESTIONS.md instead.\n"
            "Honor context.md Notes. Cover permission branches with real accounts, "
            "or write 'not covered (missing account X)' explicitly.\n"
            "When permissions are involved, discover accounts from the backend "
            "permission code and write the read-only discovery query to "
            "qa/accounts-discover.sql (username|account_key; host runs it via "
            "`req accounts --auto`).\n"
            "Data prerequisites must be executable: declare `data.verify` "
            "(single read-only SELECT; >=1 row means pass) for every case that "
            "declares setup/cleanup or a DB expectation; the host runs it and "
            "feeds failures back to you. A pure-UI case writes `SELECT 1` and is "
            "flagged as an exemption.\n"
            "seed must hard self-prove: every entity/field/link a case asserts must "
            "be created by setup (or verified read-only), and setup must exit(1) "
            "when its own assertion fails — not just print.\n"
            "When setup mutates a shared row, declare `data.writes` "
            "(list of `table.column`) and `data.identity` (one read-only SELECT "
            "returning that row's id). Two cases must not write the same column "
            "of the same row. Read-only cases omit `writes`; sharing a row to read "
            "it is fine.\n"
            "Enumerate ALL required fields from the target form's validators "
            "(:rules/required/custom), not only the one an error message names."
        )
    return (
        "You are executing ONE UI test case. Do not repair product code.\n"
        f"Write only under {qa}. Do not change any worktree file.\n"
        "Do not git checkout, commit, push, switch, or deploy.\n"
        "Do not spawn other cases. Do not change case expected values to go green.\n"
        "URLs come from context.md base_url + Routes, else frontend route code. "
        "Do not guess hosts. Hash routers need `#/` in the path.\n"
        "Authentication: load state_file if available. If unauthenticated or redirected to login, "
        "inspect the page dynamically with snapshot, fill credentials, submit, and save state.\n"
        "Host already ran data.setup if the case has one; do not re-run it. "
        "Host will run cleanup after you finish.\n"
        "Blocked reasons must be classified: data gap -> `case-defect:`; a 5xx must be "
        "checked via `dev-yard qa logs <JIRA> --request-id <id>` (host read-only log "
        "lookup; do not ssh/kubectl yourself) before deciding env vs product; "
        "cancellation -> `cancelled:`.\n"
        "A failed result.yaml must set `defect_class`: `product` when the "
        "implementation contradicts the requirement; `case` when the assertion, "
        "seed, or expected wording is wrong. Omit it when unsure (locale-dependent "
        "copy, HTTP 201 vs 200, anything that might be a spec question). Omitted "
        "means unclassified and does not open a product ticket.\n"
        "Assertions in result.yaml must use type (ui|net|db) plus expected and actual."
    )


_OPEN_QUESTION = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+)?(?:\*\*|__)?Q\d+\b", re.I)


def open_questions_payload(qa: Path) -> dict[str, Any]:
    """Read-only view of qa/OPEN-QUESTIONS.md for CLI/web surfacing.

    `exists` distinguishes "the agent thought about it and wrote an empty file"
    (the contract) from "the agent never wrote one" — `count` alone cannot.
    """
    path = qa / "OPEN-QUESTIONS.md"
    if not path.is_file():
        return {"count": 0, "body": "", "exists": False}
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # The file is there but unreadable: say so instead of pretending the
        # agent forgot to write it.
        return {"count": 0, "body": "", "exists": True, "error": "unreadable"}
    count = sum(1 for line in body.splitlines() if _OPEN_QUESTION.match(line))
    return {"count": count, "body": body.strip(), "exists": True}


def _count_open_questions(qa: Path) -> int:
    return int(open_questions_payload(qa)["count"])


_DESIGN_MARKER = "design.yaml"
_DESIGN_PENDING = ".design.pending"


def design_pending(qa: Path) -> bool:
    """True when a previous design run started but never finished (M4).

    The host drops this sentinel before invoking qa-design and removes it only
    once the run returns ok, so a crashed/partial design is never mistaken for
    a completed one on the next invocation.
    """
    return (qa / _DESIGN_PENDING).is_file()


def read_design_marker(qa: Path) -> dict[str, Any] | None:
    """The last completed design generation, or None."""
    path = qa / _DESIGN_MARKER
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _mark_design_pending(qa: Path) -> None:
    qa.mkdir(parents=True, exist_ok=True)
    (qa / _DESIGN_PENDING).write_text(now_iso(), encoding="utf-8")


def _finish_design(qa: Path) -> dict[str, Any]:
    """Record a completed design generation and clear the pending sentinel."""
    ids = [c.id for c in discover_cases(qa)]
    marker = {
        "generation": now_iso(),
        "cases": ids,
        "count": len(ids),
        "fingerprint": cases_fingerprint(qa),
    }
    (qa / _DESIGN_MARKER).write_text(
        yaml.safe_dump(marker, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (qa / _DESIGN_PENDING).unlink(missing_ok=True)
    return marker


def _design_prompt(
    root: Path,
    jira: str,
    cfg: QaConfig,
    feedback: str | None = None,
    verify_feedback: str | None = None,
    history: str | None = None,
) -> str:
    spec = load_registry(root)["qa-design"]
    extra = _duties("design", jira) + "\n\n" + _context_block(root, jira, cfg)
    if history and history.strip():
        extra += "\n\n# 历史数据缺口（上一轮 run 记录，本轮必须修掉）\n\n" + history.strip()
    if feedback and feedback.strip():
        extra += (
            "\n\n# 人工审核意见（必须据此修订用例）\n\n"
            + feedback.strip()
            + "\n\n在保留仍然成立的用例的前提下，按上述意见修改 qa/cases/ 下的用例："
            "被指出缺失的覆盖补上，被指出错误或多余的改写或删除。"
            "不要为迎合意见而放宽预期；实现与需求不符时仍按需求口径写并标注「需求偏差」。"
        )
    if verify_feedback and verify_feedback.strip():
        extra += (
            "\n\n# 宿主数据核实失败清单（必须据此修订用例）\n\n"
            + verify_feedback.strip()
            + "\n\n在保留仍然成立的用例的前提下，按上述失败修正 qa/cases/ 下的用例："
            "改 setup 让前置真的就位、把断言对象写进 verify.sql、或按真实数据重写前置。"
            "不要为通过核实而放宽预期、删断言，也不要写空转的 SELECT 1 掩盖真断言。"
        )
    return session_prompt_for(spec, root, jira, extra=extra)


def _run_prompt(root: Path, jira: str, cfg: QaConfig, job: CaseJob, run_id: str) -> str:
    spec = load_registry(root)["qa-run"]
    qa = paths.qa_dir(root, jira)
    evidence = qa / "evidence" / run_id / job.id
    headed = "true" if cfg.headed else "false"
    account = _case_account(cfg, job)
    acct = cfg.env.accounts.get(account)
    account_line = f"Account: {account or '(none)'}"
    if acct:
        state_str = acct.state_file or str(default_state_file(cfg.active_env, acct.name, jira))
        replay_str = str(Path(state_str).with_suffix(".replay.sh"))
        account_line += (
            f"\nAccount Details (credentials are in the environment, not the prompt):\n"
            f"  username: $YARD_QA_USERNAME\n"
            f"  password: $YARD_QA_PASSWORD (never echo this)\n"
            f"  state_file: {state_str}\n"
            f"  auth_replay: {replay_str}\n"
            f"Login & Session Protocol:\n"
            f"  1. If `{state_str}` exists, run `playwright-cli -s=qap-{job.id} state-load {state_str}`.\n"
            f"  2. Navigate to target URL. If unauthenticated / on login page:\n"
            f"     - If `{replay_str}` exists, replay or reference its login commands.\n"
            f"     - Otherwise, explore login form with snapshot (inspect actual inputs/buttons dynamically).\n"
            f"     - Fill username/password from the env vars above, submit, and verify entry into system.\n"
            f"     - Save session: `playwright-cli -s=qap-{job.id} state-save {state_str}`\n"
            f"     - Save explored login commands to `{replay_str}` for future runs to reuse.\n"
            f"  3. Never write credentials to result.yaml, .replay.sh, or evidence; redact as `***`."
        )
    replay = replay_path(job)
    replay_line = (
        f"Replay script (run these UI commands; on locator miss re-explore that step "
        f"and patch the file): `{replay}`\n"
        if replay
        else "No replay script yet. Explore with snapshot, then write semantic "
        f"locator commands to `{Path(job.path).with_suffix('.replay.sh')}` "
        "if the case path is known.\n"
        if job.path
        else ""
    )
    extra = (
        _duties("run", jira)
        + "\n\n"
        + _context_block(root, jira, cfg)
        + "\n\n"
        + f"Run id: {run_id}\n"
        + f"Playwright session: `-s=qap-{job.id}`\n"
        + f"headed: {headed}\n"
        + account_line
        + "\n"
        + replay_line
        + "HTTP 2xx is not success — read response bodies. "
        "Toast assertions: snapshot immediately, else assert the request was not sent.\n"
        "Screenshots: absolute paths under the screenshots dir below.\n"
        f"Write case result to `{evidence / 'result.yaml'}` "
        + f"and screenshots to `{evidence / 'screenshots'}`.\n"
        + "result.yaml assertions: each item needs type (ui|net|db), expected, actual, status.\n"
        + "On status failed, set defect_class to product or case. Omit it when the "
        "failure might be the case wording or the environment rather than the product.\n"
        + "result.yaml must be valid YAML: quote every free-text scalar (reason/expected/"
        "actual/step_desc). An unquoted value bearing a colon+space — such as a "
        "'case-defect:' prefix — makes the whole file unparseable, and the host then marks "
        "this case blocked (unreadable case result.yaml).\n"
        + "This invocation runs only the case below.\n\n"
        + f"# Case {job.id}\n\n"
        + (Path(job.path).read_text(encoding="utf-8") if job.path else job.body)
    )
    return session_prompt_for(spec, root, jira, extra=extra)


def _context_block(root: Path, jira: str, cfg: QaConfig) -> str:
    ctx = paths.qa_dir(root, jira) / "context.md"
    text = ctx.read_text(encoding="utf-8") if ctx.is_file() else ""
    return f"qa/context.md:\n{text}".strip()


def _verify_loop(
    root: Path,
    jira: str,
    cfg: QaConfig,
    qa: Path,
    cases: list[CaseJob],
    *,
    history_text: str,
    design_runner: Callable[[], Runner],
    on_log: LogFn | None,
    cancel_check: CancelCheck | None,
) -> dict[str, VerifyResult]:
    """Verify, feed case gaps back to design, and retry environment blocks (M5)."""
    case_attempts = max(1, cfg.design_verify_attempts)
    env_retries = max(0, cfg.design_verify_retry_attempts)
    results: dict[str, VerifyResult] = {}
    case_round = 0
    env_round = 0
    while True:
        _raise_if_cancelled(cancel_check, "qa-verify")
        fingerprint = cases_fingerprint(qa)
        results = verify_cases(
            root,
            jira,
            cfg,
            cases,
            fingerprint=fingerprint,
            on_log=on_log,
            cancel_check=cancel_check,
        )
        failures = [r for r in results.values() if r.status == "failed"]
        blocked = [r for r in results.values() if r.status == "blocked"]
        if not failures and not blocked:
            break
        if failures:
            case_round += 1
            if case_round >= case_attempts:
                # Out of retries: this verdict is what the human has to act on,
                # so it must land in the review state.
                reject_cases(qa, render_feedback(results))
                if on_log is not None:
                    on_log(
                        f"数据核实仍有 {len(failures)} 条未通过，已达上限 "
                        f"{case_attempts}；标 design-blocked，交人工\n"
                    )
                break
            if on_log is not None:
                on_log(
                    f"数据核实 {len(failures)} 条未通过，回灌 design 重做"
                    f"（第 {case_round}/{case_attempts - 1} 次）\n"
                )
            prompt = _design_prompt(
                root,
                jira,
                cfg,
                verify_feedback=render_feedback(results),
                history=history_text or None,
            )
            _mark_design_pending(qa)
            result = design_runner().start(
                prompt,
                root,
                attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
            )
            _raise_if_cancelled(cancel_check, "qa-design")
            if not result.ok:
                raise TestRejected(
                    f"qa-design 数据核实回流失败: {result.summary or result.exit_code}"
                )
            cases = discover_cases(qa)
            _finish_design(qa)
            # Record the host's findings as the review state's feedback, so a
            # human sees what the loop changed even when it ends green.
            reject_cases(qa, render_feedback(results))
            continue
        # Only environment-blocked cases remain: retry with backoff (M5), do not
        # burn design rounds on infrastructure.
        if env_round >= env_retries:
            if on_log is not None:
                on_log(
                    f"数据核实 {len(blocked)} 条因环境不可用未完成（非用例缺陷）；"
                    f"已重试 {env_round} 次，修好环境后重跑\n"
                )
            break
        env_round += 1
        if on_log is not None:
            on_log(
                f"数据核实 {len(blocked)} 条因环境不可用，退避重试"
                f"（第 {env_round}/{env_retries} 次）\n"
            )
        if cfg.design_verify_retry_backoff > 0:
            time.sleep(cfg.design_verify_retry_backoff)
    return results


def _mark_design_blocked(cases: list[CaseJob], qa: Path, fingerprint: str) -> None:
    """Skip cases whose data could not be verified, even when waived through."""
    failed = set(failed_cases(qa, fingerprint))
    if not failed:
        return
    stamp = now_iso()
    for job in cases:
        if job.id in failed and job.state in {"pending", "ready"}:
            job.state = "skipped"
            job.reason = (
                "design-blocked: 数据核实未通过（--allow-unverified 越权放行，"
                "仅跳过本用例）"
            )
            job.ended_at = stamp


def _write_progress(
    path: Path,
    run_id: str,
    env: str,
    pools: list[PoolSlot],
    cases: list[CaseJob],
    on_progress: ProgressFn | None,
    on_log: LogFn | None,
    env_fault: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = progress_payload(run_id, env, pools, cases, env_fault=env_fault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    if on_progress is not None:
        on_progress(payload)
    if on_log is not None:
        on_log(progress_line(payload))
    return payload


_YAML_SCALAR = re.compile(r"^(\s*(?:- )?)([A-Za-z_][\w-]*):\s+(.+)$")


def _repair_yaml_scalars(text: str) -> str:
    """Quote free-text scalars that carry a bare `: ` (M7).

    A worker writing `reason: case-defect: 缺 x` makes the whole file
    unparseable. Re-quoting just those values salvages the result instead of
    failing the case as `unreadable`.
    """
    out: list[str] = []
    for line in text.splitlines():
        match = _YAML_SCALAR.match(line)
        if match is None:
            out.append(line)
            continue
        prefix, key, value = match.groups()
        val = value.strip()
        if not val or val[:1] in {'"', "'", "|", ">", "[", "{"} or ": " not in val:
            out.append(line)
            continue
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'{prefix}{key}: "{escaped}"')
    return "\n".join(out)


def load_yaml_tolerant(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a worker result file, or None (M7)."""
    for candidate in (text, _repair_yaml_scalars(text)):
        try:
            data = yaml.safe_load(candidate)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            return data
    lines = _repair_yaml_scalars(text).splitlines()
    while lines:
        try:
            data = yaml.safe_load("\n".join(lines))
        except yaml.YAMLError:
            lines.pop()
            continue
        return data if isinstance(data, dict) else None
    return None


def _read_case_result(
    path: Path, job: CaseJob, slot: PoolSlot, *, strict: bool = True
) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "blocked",
            "reason": "worker exit: missing case result.yaml",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {
            "status": "blocked",
            "reason": "worker exit: unreadable case result.yaml",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    data = load_yaml_tolerant(text)
    if data is None:
        return {
            "status": "blocked",
            "reason": "worker exit: unreadable case result.yaml",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    if not isinstance(data, dict):
        return {
            "status": "blocked",
            "reason": "worker exit: case result.yaml is not a mapping",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    cleaned = normalize_case_result(
        data, job, require_assertions=strict
    )
    if cleaned is None:
        if not strict:
            return {
                "status": normalize_status(data.get("status")),
                "reason": str(data.get("reason") or ""),
                "blocked_class": str(data.get("blocked_class") or ""),
                "repo": str(data.get("repo") or job.repo),
                "title": str(data.get("title") or job.title),
                "covers": data.get("covers") or job.covers,
                "model": str(data.get("model") or slot.model or ""),
                "provider": str(data.get("provider") or slot.provider or ""),
                "failure": data.get("failure")
                if isinstance(data.get("failure"), dict)
                else None,
                "assertions": data.get("assertions")
                if isinstance(data.get("assertions"), list)
                else [],
                "raw": data,
            }
        return {
            "status": "blocked",
            "reason": "malformed result.yaml: assertions need type/expected/actual",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    cleaned["model"] = str(data.get("model") or slot.model or "")
    cleaned["provider"] = str(data.get("provider") or slot.provider or "")
    return cleaned


def _write_skipped_result(path: Path, job: CaseJob) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "case": job.id,
        "title": job.title,
        "repo": job.repo,
        "covers": job.covers,
        "status": job.state,
        "reason": job.reason,
        "model": job.model or "",
        "provider": job.provider or "",
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _summarize(cases: list[CaseJob]) -> dict[str, Any]:
    counts: dict[str, Any] = {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for c in cases:
        if c.state in counts:
            counts[c.state] += 1
    counts["total"] = len(cases)
    # Blocked is not one thing: a case-defect must go back to design, a cancelled
    # run is collateral, env is external. Split it so the metrics stay honest.
    # A cancelled run bails before this summary is written (see _raise_if_cancelled
    # below the schedule); its `cancelled` cases surface from progress.yaml via
    # `dev-yard qa report` instead.
    breakdown = dict.fromkeys(BLOCKED_KINDS, 0)
    for c in cases:
        if c.state == "blocked":
            breakdown[blocked_kind(c.reason, c.blocked_class)] += 1
    counts["blocked_kind"] = breakdown
    return counts


def _progress_doc(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "progress.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def find_incomplete_run(
    qa: Path,
    case_ids: set[str] | None = None,
    env: str | None = None,
) -> tuple[str, Path] | None:
    """Latest evidence dir that still has work left, or None.

    A run only counts when it belongs to `env` and was produced for the same
    case set, so switching env or redesigning cases never resumes a stale run.
    """
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return None
    dirs = sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True)
    if not dirs:
        return None
    run_dir = dirs[0]
    if not _run_matches(run_dir, case_ids, env):
        return None
    if _run_incomplete(run_dir, case_ids or set()):
        return run_dir.name, run_dir
    return None


def _run_matches(
    run_dir: Path, case_ids: set[str] | None, env: str | None
) -> bool:
    data = _progress_doc(run_dir)
    if data is None:
        # No progress file: the old run wrote only case results, so env and the
        # case set cannot be verified. Fall back to the result-based check.
        return True
    run_env = str(data.get("env") or "")
    if env and run_env and run_env != env:
        return False
    if case_ids is not None:
        ids = {
            str(c.get("id"))
            for c in (data.get("cases") or [])
            if isinstance(c, dict) and c.get("id")
        }
        if ids and ids != set(case_ids):
            return False
    return True


def _run_incomplete(run_dir: Path, case_ids: set[str]) -> bool:
    doc = _progress_doc(run_dir)
    if doc is not None and doc.get("cases"):
        # Progress is authoritative once the run wrote it: only a case it still
        # calls active, or a missing run summary, means there is work left.
        for item in doc.get("cases") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("state") or "") in {"pending", "ready", "running"}:
                return True
        return not (run_dir / "result.yaml").is_file()
    if not (run_dir / "result.yaml").is_file():
        return True
    ids = case_ids or {
        p.name
        for p in run_dir.iterdir()
        if p.is_dir() and p.name not in {"repo-baseline", "_root_png"}
    }
    for cid in ids:
        path = run_dir / cid / "result.yaml"
        if not path.is_file():
            return True
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return True
        if not isinstance(data, dict):
            return True
        if normalize_status(data.get("status")) not in TERMINAL:
            return True
    return False


def _pending_in(run_dir: Path) -> int:
    data = _progress_doc(run_dir)
    if data is not None:
        return sum(
            1
            for c in (data.get("cases") or [])
            if isinstance(c, dict)
            and str(c.get("state") or "") in {"pending", "ready", "running"}
        )
    pending = 0
    for p in sorted(run_dir.iterdir()):
        if not p.is_dir() or p.name in {"repo-baseline", "_root_png"}:
            continue
        rp = p / "result.yaml"
        if not rp.is_file():
            pending += 1
            continue
        try:
            raw = yaml.safe_load(rp.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            pending += 1
            continue
        if not isinstance(raw, dict):
            pending += 1
            continue
        if normalize_status(raw.get("status")) not in TERMINAL:
            pending += 1
    return pending


def incomplete_run_payload(
    qa: Path,
    case_ids: set[str] | None = None,
    env: str | None = None,
) -> dict[str, Any] | None:
    """`{run_id, pending}` for the run a resume would pick, or None."""
    found = find_incomplete_run(qa, case_ids, env)
    if found is None:
        return None
    run_id, run_dir = found
    return {"run_id": run_id, "pending": _pending_in(run_dir)}


def find_run_for_rerun(
    qa: Path, target_ids: set[str]
) -> tuple[str, Path, str] | None:
    """Newest run that contains every id in `target_ids`: `(run_id, dir, env)`.

    Unlike `find_incomplete_run` this ignores env and completeness: re-running a
    case is valid even for a finished run, and the run's own env is the one the
    case was tested against, so it becomes the default for the re-run.
    """
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return None
    wanted = {t for t in target_ids if t}
    if not wanted:
        return None
    for run_dir in sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True):
        doc = _progress_doc(run_dir)
        present = {
            str(c.get("id"))
            for c in (doc or {}).get("cases") or []
            if isinstance(c, dict) and c.get("id")
        }
        if not present:
            present = {
                p.name
                for p in run_dir.iterdir()
                if p.is_dir() and p.name not in {"repo-baseline", "_root_png"}
            }
        if wanted <= present:
            env = str((doc or {}).get("env") or "")
            return run_dir.name, run_dir, env
    return None


def latest_retryable(
    qa: Path, cases: list[CaseJob], env: str
) -> list[str]:
    """Ids to re-run in place: the latest run's failed/blocked cases + dependents.

    Only a *finished* run that matches `env` and the current case set counts;
    `case-defect` ids are excluded (they belong to the design recycle path).
    A case whose `depends_on` chain reaches a retried case is included too, so a
    fixed upstream is re-verified downstream.
    """
    base = _latest_retryable_ids(qa, {c.id for c in cases}, env)
    if not base:
        return []
    out = set(base)
    progressed = True
    while progressed:
        progressed = False
        for job in cases:
            if job.id in out:
                continue
            if any(dep in out for dep in job.depends_on):
                out.add(job.id)
                progressed = True
    return sorted(out)


def _latest_retryable_ids(qa: Path, case_ids: set[str], env: str) -> list[str]:
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return []
    for run_dir in sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True):
        doc = _progress_doc(run_dir)
        if doc is None:
            continue
        run_env = str(doc.get("env") or "")
        if env and run_env and run_env != env:
            continue
        ids = {
            str(c.get("id"))
            for c in doc.get("cases") or []
            if isinstance(c, dict) and c.get("id")
        }
        if ids and case_ids and ids != set(case_ids):
            continue
        out: list[str] = []
        for item in doc.get("cases") or []:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "")
            if state not in {"failed", "blocked"}:
                continue
            reason = str(item.get("reason") or "")
            blocked_class = str(item.get("blocked_class") or "")
            if blocked_kind(reason, blocked_class) == "case-defect":
                continue
            out.append(str(item.get("id")))
        return out
    return []


def reconcile_case_verdict(
    progress_state: str,
    progress_reason: str,
    file_status: str,
    file_reason: str,
) -> tuple[str, str]:
    """One status for a case that has both a progress row and a result file.

    Queued or running rows stay as progress. A finished disagreement adopts
    the file only when the progress reason is a copy of the file reason — the
    resume merge that pasted pass text onto a stale failed row. A later
    verdict (cancel, auth, worker exit, host recheck) keeps the progress row.
    """
    state = str(progress_state or "")
    reason = str(progress_reason or "")
    fstatus = str(file_status or "")
    freason = str(file_reason or "")
    if state in {"pending", "ready", "running"}:
        return state, reason
    if (
        state in TERMINAL
        and fstatus in TERMINAL
        and state != fstatus
        and reason
        and reason == freason
    ):
        return fstatus, freason
    if state in TERMINAL:
        return state, reason or freason
    if fstatus in TERMINAL:
        return fstatus, freason or reason
    return state or fstatus or "pending", reason or freason


def _file_verdict(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", ""
    try:
        data = load_yaml_tolerant(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("status") or ""), str(data.get("reason") or "")


def reconcile_progress_doc(run_dir: Path, doc: dict[str, Any]) -> bool:
    """Rewrite terminal progress rows that only disagree by a copied file reason."""
    changed = False
    for item in doc.get("cases") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        state = str(item.get("state") or "")
        if state not in TERMINAL:
            continue
        reason = str(item.get("reason") or "")
        file_status, file_reason = _file_verdict(run_dir / str(item["id"]) / "result.yaml")
        new_state, new_reason = reconcile_case_verdict(
            state, reason, file_status, file_reason
        )
        if new_state != state or new_reason != reason:
            item["state"] = new_state
            item["reason"] = new_reason
            changed = True
    return changed


def persist_case_verdict(path: Path, got: dict[str, Any]) -> None:
    """Write the host verdict back into the case result file.

    The scheduler records status in progress.yaml from memory. Leaving the
    worker file untouched makes the board show the file while the run is idle
    and the progress row while any other case is active.
    """
    if not path.is_file():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return
    if not isinstance(data, dict):
        return
    status = str(got.get("status") or "")
    if not status:
        return
    reason = str(got.get("reason") or "")
    failed_sql = {
        str(item.get("sql") or "").strip()
        for item in got.get("assertions") or []
        if isinstance(item, dict)
        and str(item.get("status") or "") == "failed"
        and str(item.get("sql") or "").strip()
    }
    unverified_sql = {
        str(item.get("sql") or "").strip()
        for item in got.get("assertions") or []
        if isinstance(item, dict)
        and item.get("host_recheck") == "unverified"
        and str(item.get("sql") or "").strip()
    }
    changed = str(data.get("status") or "") != status or str(data.get("reason") or "") != reason
    assertions = data.get("assertions")
    if isinstance(assertions, list):
        for item in assertions:
            if not isinstance(item, dict):
                continue
            sql = str(item.get("sql") or "").strip()
            if sql in failed_sql and str(item.get("status") or "") != "failed":
                item["status"] = "failed"
                changed = True
            if sql in unverified_sql and item.get("host_recheck") != "unverified":
                item["host_recheck"] = "unverified"
                changed = True
    if not changed:
        return
    data["status"] = status
    data["reason"] = reason
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _apply_resume(cases: list[CaseJob], run_dir: Path) -> int:
    """Mark finished cases so the scheduler will not dispatch them. Returns skip count.

    `progress.yaml` is the authority: a case it still calls pending/ready/running
    is re-run even if the interrupted attempt left a `result.yaml` behind, so a
    stale file never masquerades as this run's outcome.
    """
    doc = _progress_doc(run_dir)
    prev_by_id: dict[str, dict[str, Any]] = {}
    if doc is not None:
        prev_by_id = {
            str(c.get("id")): c
            for c in doc.get("cases") or []
            if isinstance(c, dict) and c.get("id")
        }
    dummy = PoolSlot(id="resume", provider=None, model=None, concurrency=1, priority=1)
    skipped = 0
    changed_doc = False
    for job in cases:
        prev = prev_by_id.get(job.id) or {}
        state = str(prev.get("state") or "")
        # A run interrupted mid-retry must not get a fresh retry budget.
        if isinstance(prev.get("attempts"), int):
            job.attempts = prev["attempts"]
        if prev_by_id and state not in TERMINAL:
            continue
        path = run_dir / job.id / "result.yaml"
        got = _read_case_result(path, job, dummy, strict=False) if path.is_file() else {}
        status, reason = reconcile_case_verdict(
            state,
            str(prev.get("reason") or ""),
            str(got.get("status") or ""),
            str(got.get("reason") or ""),
        )
        if status not in TERMINAL:
            continue
        if job.id in prev_by_id and (
            state != status or str(prev.get("reason") or "") != reason
        ):
            prev["state"] = status
            prev["reason"] = reason
            changed_doc = True
        job.state = status
        job.reason = reason
        job.blocked_class = str(
            got.get("blocked_class") or prev.get("blocked_class") or ""
        )
        job.model = str(got.get("model") or prev.get("model") or "")
        job.provider = str(got.get("provider") or prev.get("provider") or "")
        job.failure = got.get("failure") if isinstance(got.get("failure"), dict) else None
        if isinstance(got.get("assertions"), list):
            job.assertions = got["assertions"]
        job.pool = prev.get("pool") or job.pool
        job.started_at = prev.get("started_at") or job.started_at
        job.ended_at = prev.get("ended_at") or job.ended_at
        skipped += 1
    if doc is not None and changed_doc:
        (run_dir / "progress.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return skipped


def reset_cases_in_run(
    run_dir: Path, case_ids: set[str], known_ids: set[str] | None = None
) -> list[str]:
    """Reset cases in a run to `ready` and drop their case-level artifacts.

    Returns the ids actually reset. A case whose state is already non-terminal
    is left alone (it is queued/running, not a stale outcome), so re-resetting a
    run never disturbs work already in flight.
    """
    doc = _progress_doc(run_dir)
    if doc is None:
        return []
    known = known_ids if known_ids is not None else set()
    if known:
        unknown = sorted(c for c in case_ids if c not in known)
        if unknown:
            raise TestRejected(f"unknown case(s): {', '.join(unknown)}")
    reset: list[str] = []
    for item in doc.get("cases") or []:
        if not isinstance(item, dict) or item.get("id") not in case_ids:
            continue
        cid = str(item["id"])
        # Only a finished case is a stale outcome worth resetting; a pending/
        # ready/running one is already queued, so leave it be.
        if str(item.get("state") or "") not in TERMINAL:
            continue
        reset.append(cid)
        item["state"] = "ready"
        item["pool"] = None
        item["model"] = None
        item["started_at"] = None
        item["ended_at"] = None
        item["reason"] = ""
        item["attempts"] = 0
    if not reset:
        return []
    # Drop the case's own result + screenshots so the old outcome cannot be read
    # as this attempt's; the root result.yaml is regenerated when the run ends.
    for cid in reset:
        case_dir = run_dir / cid
        (case_dir / "result.yaml").unlink(missing_ok=True)
        shots = case_dir / "screenshots"
        if shots.is_dir():
            shutil.rmtree(shots, ignore_errors=True)
    # The run's aggregate summary still counts the reset case as its old outcome
    # until the re-run finishes, so drop it to keep the page honest ("执行中").
    (run_dir / "result.yaml").unlink(missing_ok=True)
    reconcile_progress_doc(run_dir, doc)
    (run_dir / "progress.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return reset


def req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    approve: bool = False,
    feedback: str | None = None,
    ingest: bool = True,
    resume: bool | None = None,
    rerun_cases: list[str] | None = None,
    verify: bool | None = None,
    verify_only: bool = False,
    allow_unverified: bool = False,
    unsafe_skip_review: bool = False,
    no_wait: bool = False,
    pool_probe: Callable[[PoolSlot], Any] | None = None,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel_check: CancelCheck | None = None,
    on_spawn: SpawnFn | None = None,
    on_reap: ReapFn | None = None,
) -> dict[str, Any]:
    """One run per requirement at a time; the lock guards evidence and skills."""
    with _run_lock(root, jira):
        return _req_test(
            root,
            jira,
            env=env,
            print_mode=print_mode,
            design_only=design_only,
            run_only=run_only,
            redesign=redesign,
            approve=approve,
            feedback=feedback,
            ingest=ingest,
            resume=resume,
            rerun_cases=rerun_cases,
            verify=verify,
            verify_only=verify_only,
            allow_unverified=allow_unverified,
            unsafe_skip_review=unsafe_skip_review,
            no_wait=no_wait,
            pool_probe=pool_probe,
            runner=runner,
            case_runner=case_runner,
            on_progress=on_progress,
            on_log=on_log,
            cancel_check=cancel_check,
            on_spawn=on_spawn,
            on_reap=on_reap,
        )


def _raise_if_cancelled(cancel_check: CancelCheck | None, what: str) -> None:
    if cancel_check is not None and cancel_check():
        raise JobCancelled(f"{what} cancelled")


def _req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    approve: bool = False,
    feedback: str | None = None,
    ingest: bool = True,
    resume: bool | None = None,
    rerun_cases: list[str] | None = None,
    verify: bool | None = None,
    verify_only: bool = False,
    allow_unverified: bool = False,
    unsafe_skip_review: bool = False,
    no_wait: bool = False,
    pool_probe: Callable[[PoolSlot], Any] | None = None,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel_check: CancelCheck | None = None,
    on_spawn: SpawnFn | None = None,
    on_reap: ReapFn | None = None,
) -> dict[str, Any]:
    if design_only and run_only:
        raise TestRejected("--design-only and --run-only are mutually exclusive")
    if approve and (design_only or run_only):
        raise TestRejected(
            "--approve cannot be combined with --design-only or --run-only"
        )
    if verify_only and (
        design_only or run_only or redesign or approve or feedback or rerun_cases
    ):
        raise TestRejected(
            "--verify-only cannot be combined with "
            "--design-only/--run-only/--redesign/--approve/--feedback/--rerun-case"
        )
    rerun_ids = {str(c).strip() for c in (rerun_cases or []) if str(c).strip()}
    if rerun_ids and (design_only or run_only or redesign or approve or feedback):
        raise TestRejected(
            "rerun_cases cannot be combined with design/run-only/redesign/approve/feedback"
        )
    _gate(root, jira, waive_open_tickets=bool(rerun_ids or redesign))
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    cases = discover_cases(qa)
    had_cases = bool(cases)
    # Resolve the run to amend before loading config so the env can default to
    # the one that run actually used.
    rerun_run: tuple[str, Path, str] | None = None
    if rerun_ids:
        known = {c.id for c in cases}
        unknown = sorted(rerun_ids - known)
        if unknown:
            raise TestRejected(
                f"unknown case(s): {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known)) or '(none)'}"
            )
        rerun_run = find_run_for_rerun(qa, rerun_ids)
        if rerun_run is None:
            raise TestRejected(
                f"no run contains {', '.join(sorted(rerun_ids))}; run `执行用例` first"
            )
        if not env and rerun_run[2]:
            env = rerun_run[2]
    cfg = load_qa_config(root, env, jira)
    try:
        qa_yaml_mtime = paths.qa_yaml(root).stat().st_mtime
    except OSError:
        qa_yaml_mtime = None
    write_context_md(root, jira, cfg)
    if run_only and not cases:
        raise TestRejected(f"{jira} has no qa/cases; cannot --run-only")
    feedback_text = feedback.strip() if feedback else ""
    review_bypassed = False
    if approve and (redesign or feedback_text):
        raise TestRejected(
            "--approve cannot be combined with --redesign or --feedback"
        )
    if approve and not had_cases:
        # Approval must be a human act on cases that were already shown; a
        # design run in the same invocation would self-approve unseen cases.
        raise TestRejected(
            f"{jira} has no cases to approve; run `dev-yard req test {jira}` "
            "first, review the cases, then --approve"
        )
    # M6b: a run that ended in case-defects leaves them recorded in state.yaml.
    # On the next design invocation, recycle them automatically — fix the seed,
    # not the tested point — unless the human asked for something else.
    auto_recycled_ids = qa_st.triage(root, jira)["auto_recycled"]
    auto_recycled_now = False
    if (
        auto_recycled_ids
        and not run_only
        and not verify_only
        and not redesign
        and not approve
        and not rerun_ids
        and not feedback_text
    ):
        redesign = True
        auto_recycled_now = True
        feedback_text = (
            "上一轮 run 判定以下用例为用例/种子缺陷（case-defect），"
            "请只修 setup / verify.sql / 依赖声明，保持用例正文与预期不变："
            + "、".join(auto_recycled_ids)
        )
        if on_log is not None:
            on_log(f"自动回流 case-defect：{', '.join(auto_recycled_ids)}\n")
    bodies_before = cases_fingerprint(qa, scope="bodies")
    need_design = (not run_only) and not verify_only and (
        redesign or not cases or design_pending(qa)
    )
    if feedback_text:
        if run_only:
            raise TestRejected("--feedback cannot be used with --run-only")
        if not need_design:
            raise TestRejected("--feedback requires --redesign")
    verify_enabled = verify is not False
    if verify_only and not cases:
        raise TestRejected(f"{jira} has no qa/cases; cannot --verify-only")
    history_text = ""
    if cases:
        try:
            history_text = render_prior_defects(prior_defects(qa, cases))
        except Exception:  # noqa: BLE001 — history is advisory, never block design
            history_text = ""
    design_runner: Runner | None = runner
    if need_design and on_log is not None:
        try:
            warn = _permission_gap_warning(root, jira, cfg)
        except Exception:  # noqa: BLE001 — advisory only, never block design
            warn = None
        if warn:
            on_log(warn + "\n")

    def _ensure_design_runner() -> Runner:
        nonlocal design_runner
        if design_runner is None:
            design_runner = get_runner(
                root,
                "qa-design",
                print_mode=print_mode,
                spec=load_registry(root)["qa-design"],
                provider=cfg.design_provider,
                model=cfg.design_model,
                on_spawn=on_spawn,
                on_reap=on_reap,
            )
        return design_runner

    if need_design:
        _raise_if_cancelled(cancel_check, "qa-design")
        prompt = _design_prompt(
            root,
            jira,
            cfg,
            feedback=feedback_text or None,
            history=history_text or None,
        )
        _mark_design_pending(qa)
        result = _ensure_design_runner().start(
            prompt,
            root,
            attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
        )
        _raise_if_cancelled(cancel_check, "qa-design")
        if not result.ok:
            raise TestRejected(
                f"qa-design failed: {result.summary or result.exit_code}"
            )
        cases = discover_cases(qa)
        _finish_design(qa)
        if feedback_text:
            if auto_recycled_now and (
                cases_fingerprint(qa, scope="bodies") == bodies_before
            ):
                # Only seeds/verify moved: the tested point is unchanged, so the
                # previous approval carries over (M6).
                mark_machine_fixed(qa, feedback_text)
            else:
                reject_cases(qa, feedback_text)
        elif redesign:
            # A redesign supersedes a doc-change stale flag; the changed case
            # fingerprint still forces a fresh review.
            clear_stale(qa)

    if auto_recycled_now:
        # Consume the case-defect list so a later plain `req test` does not keep
        # forcing a redesign; the next run records a fresh triage anyway.
        prev_triage = qa_st.triage(root, jira)
        qa_st.record_triage(root, jira, prev_triage["pending"], [])

    # M7: static contract lint with one bounded design fix pass, before verify.
    # A seed/contract mistake should not cost a human review or a run.
    if (
        cases
        and not run_only
        and not rerun_ids
        and not verify_only
        and not allow_unverified
    ):
        problems = lint_cases(root, jira, cfg, cases)
        if problems:
            if on_log is not None:
                on_log("用例契约检查未通过，回灌 design 修正："
                       + "；".join(problems) + "\n")
            _raise_if_cancelled(cancel_check, "qa-design")
            fix_prompt = _design_prompt(
                root,
                jira,
                cfg,
                verify_feedback=(
                    "宿主静态契约检查未通过，请修正后重写用例：\n"
                    + "\n".join(f"- {p}" for p in problems)
                ),
            )
            _mark_design_pending(qa)
            result = _ensure_design_runner().start(
                fix_prompt,
                root,
                attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
            )
            _raise_if_cancelled(cancel_check, "qa-design")
            if not result.ok:
                raise TestRejected(
                    f"qa-design 契约修正失败: {result.summary or result.exit_code}"
                )
            cases = discover_cases(qa)
            _finish_design(qa)
            # The changed case set invalidates any prior approval; the review
            # gate holds it for a fresh look (no need to force `rejected`).

    # M1/M3: prove each case's declared data prerequisites host-side. Failures
    # loop back into design (bounded); the final verdict is written so the gate
    # and the human can see exactly what was checked.
    verify_results: dict[str, VerifyResult] = {}
    verify_fingerprint = cases_fingerprint(qa) if cases else ""
    pre_verify_fingerprint = verify_fingerprint
    if verify_enabled and cases and not run_only and not rerun_ids:
        if verify_only:
            verify_results = verify_cases(
                root,
                jira,
                cfg,
                cases,
                fingerprint=verify_fingerprint,
                on_log=on_log,
                cancel_check=cancel_check,
            )
        else:
            verify_results = _verify_loop(
                root,
                jira,
                cfg,
                qa,
                cases,
                history_text=history_text,
                design_runner=_ensure_design_runner,
                on_log=on_log,
                cancel_check=cancel_check,
            )
            cases = discover_cases(qa)
            verify_fingerprint = cases_fingerprint(qa)
        write_summary(root, jira, verify_results, verify_fingerprint)
        if on_log is not None:
            on_log(describe(verify_results) + "\n")
        blocked_path = write_blocked(root, jira, verify_results)
        if blocked_path is not None and on_log is not None:
            on_log(f"数据核实未通过，design-blocked 清单：{blocked_path}\n")
    verify_info = (
        verify_view(qa, verify_fingerprint) if cases else {"present": False, "stale": False}
    )
    # Advisory coverage: a change point no case claims. Surfaced at review time
    # so a human can ask for the missing case before approving.
    uncovered = uncovered_changes(qa, cases)
    if uncovered and on_log is not None:
        on_log(f"提示：改动点未被任何用例 covers：{', '.join(uncovered)}\n")
    # M7: static contract checks, before a human is asked to approve.
    lint_problems = lint_cases(root, jira, cfg, cases) if cases else []
    if lint_problems and on_log is not None:
        on_log("用例契约检查：" + "；".join(lint_problems) + "\n")

    if design_only:
        return {
            "jira": jira,
            "design_only": True,
            "cases": len(cases),
            "questions": _count_open_questions(qa),
            "open_questions": open_questions_payload(qa),
            "review": review_payload(qa),
            "verify": verify_info,
            "uncovered_changes": uncovered,
            "lint": lint_problems,
        }
    if verify_only:
        return {
            "jira": jira,
            "verify_only": True,
            "cases": len(cases),
            "verify": verify_info,
        }
    if not cases:
        raise TestRejected(f"{jira} qa-design produced no cases")
    require_verify = bool(cfg.design_verify_required and verify_enabled)
    if approve:
        if verify_fingerprint != pre_verify_fingerprint:
            # The verify loop redesigned cases in this invocation; approving them
            # here would bless cases the person has not seen.
            return {
                "jira": jira,
                "awaiting_review": True,
                "cases": len(cases),
                "questions": _count_open_questions(qa),
                "open_questions": open_questions_payload(qa),
                "reason": "数据核实失败已自动重做用例，请复核后重新 --approve",
                "review": review_payload(qa),
                "verify": verify_info,
                "uncovered_changes": uncovered,
                "lint": lint_problems,
            }
        if lint_problems and not allow_unverified:
            raise TestRejected(
                "用例契约检查未通过：" + "；".join(lint_problems)
                + "；修好后重跑设计，或加 --allow-unverified 越权"
            )
        ok, why = verify_gate(
            qa,
            cases_fingerprint(qa),
            required=require_verify,
            allow_unverified=allow_unverified,
        )
        if not ok:
            raise TestRejected(
                f"--approve 被拒绝：{why}；修好后重跑设计，或加 --allow-unverified 越权"
            )
        approve_cases(qa)
        # Approval is a marker, never an execution: running the cases is a
        # separate `--run-only` / `qa-run` step so a review cannot silently
        # trigger a run (and so accounts can be configured in between).
        return {
            "jira": jira,
            "approved": True,
            "cases": len(cases),
            "review": review_payload(qa),
            "verify": verify_info,
        }
    elif not rerun_ids:
        can_run, hold_reason = review_gate(
            qa,
            require_verify=require_verify,
            allow_unverified=allow_unverified,
        )
        if not can_run and run_only:
            # `--run-only` is the one path that can run unapproved cases. Make
            # the bypass an explicit, auditable opt-in instead of a silent one.
            if not unsafe_skip_review:
                raise TestRejected(
                    f"{hold_reason}；--run-only 会绕过人工审核，"
                    "确认后加 --unsafe-skip-review 重跑"
                )
            review_bypassed = True
            if on_log is not None:
                on_log(f"警告：--run-only 绕过用例审核（{hold_reason}）\n")
        elif not can_run:
            return {
                "jira": jira,
                "awaiting_review": True,
                "cases": len(cases),
                "questions": _count_open_questions(qa),
                "open_questions": open_questions_payload(qa),
                "reason": hold_reason,
                "review": review_payload(qa),
                "verify": verify_info,
                "uncovered_changes": uncovered,
                "lint": lint_problems,
            }
    if allow_unverified:
        _mark_design_blocked(cases, qa, cases_fingerprint(qa))

    _check_case_repos(root, cases)
    evidence = qa / "evidence"
    case_ids = {c.id for c in cases}
    incomplete = find_incomplete_run(qa, case_ids, cfg.active_env)
    if resume is True and incomplete is None and rerun_run is None:
        raise TestRejected(
            f"--resume: no incomplete run for {jira} in env {cfg.active_env}; "
            "use --fresh to start a new run"
        )
    # M10: a finished run whose cases failed/blocked is amended in place by
    # default, so fixing B tickets only re-runs what actually broke.
    if (
        rerun_run is None
        and incomplete is None
        and resume is None
        and not redesign
        and not run_only
        and cfg.run_incremental
    ):
        retryable = latest_retryable(qa, cases, cfg.active_env)
        if retryable:
            rerun_run = find_run_for_rerun(qa, set(retryable))
            if rerun_run is not None:
                rerun_ids = set(retryable)
                if on_log is not None:
                    on_log(
                        f"增量重跑 {len(retryable)} 条（上一轮 failed/blocked）："
                        f"{', '.join(sorted(retryable))}\n"
                    )
    if rerun_run is not None:
        run_id, run_dir, _ = rerun_run
        reset = reset_cases_in_run(run_dir, rerun_ids, case_ids)
        if not reset:
            raise TestRejected(
                f"nothing to re-run in run {run_id} for "
                f"{', '.join(sorted(rerun_ids))}; already queued or running"
            )
        if on_log is not None:
            on_log(
                f"reset {', '.join(sorted(reset))} for re-run in run {run_id}"
            )
        _apply_resume(cases, run_dir)
        resuming = True
    elif incomplete is not None and (resume is True or (resume is None and not redesign)):
        run_id, run_dir = incomplete
        if on_log is not None:
            on_log(f"resuming run {run_id}")
        _apply_resume(cases, run_dir)
        resuming = True
    else:
        run_id, run_dir = _claim_run_dir(evidence)
        resuming = False
    aliases = _involved_aliases(root, jira)
    tree_before: dict[str, dict[str, str]] = {}
    heads_before: dict[str, str] = {}
    baseline_dir = run_dir / "repo-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        snap = _tree_state(wt)
        if snap is None:
            raise TestRejected(
                f"cannot read `git status` in {wt}; aborting before the run"
            )
        baseline_json = baseline_dir / f"{alias}.json"
        baseline_txt = baseline_dir / f"{alias}.txt"
        # On resume, compare against the run's *original* baseline so a mutation
        # left behind by the interrupted attempt is still caught. Only a fresh
        # run records a new baseline.
        if resuming and baseline_json.is_file():
            try:
                loaded = json.loads(baseline_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                tree_before[alias] = {
                    str(k): str(v) for k, v in loaded.items()
                }
            else:
                tree_before[alias] = snap
        else:
            tree_before[alias] = snap
            baseline_json.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if not baseline_txt.is_file():
            text = _porcelain(wt)
            baseline_txt.write_text(
                text + ("\n" if text else ""), encoding="utf-8"
            )
        # A commit leaves `git status` clean, so the porcelain diff alone cannot
        # see it; pin HEAD as a second baseline.
        head_path = baseline_dir / f"{alias}.head"
        if resuming and head_path.is_file():
            heads_before[alias] = head_path.read_text(encoding="utf-8").strip()
        else:
            sha = _head_sha(wt) or ""
            heads_before[alias] = sha
            head_path.write_text(sha, encoding="utf-8")
    root_png_before = _root_png_names(root)
    _check_case_accounts(cfg, cases, jira)
    # Resolve the default account so context/login use a real name. Concurrent
    # cases may share it unless qa.yaml serialize_accounts is true.
    for job in cases:
        resolved = _case_account(cfg, job)
        if resolved in cfg.env.accounts:
            job.account = resolved
    auth_failures = _preload_auth(root, cfg, _used_accounts(cfg, cases), on_log)
    if auth_failures:
        stamp = now_iso()
        for job in cases:
            acct = _case_account(cfg, job)
            if acct in auth_failures:
                job.state = "blocked"
                job.reason = f"auth failed: {auth_failures[acct]}"
                job.blocked_class = "auth"
                job.ended_at = stamp

    pools = [PoolSlot.from_worker(w) for w in cfg.workers]
    # M3: optionally probe each pool and drop the ones that cannot run.
    if cfg.run_pool_preflight or pool_probe is not None:
        probe = pool_probe or (
            lambda p: _real_pool_probe(root, p, on_spawn=on_spawn, on_reap=on_reap)
        )
        kept: list[PoolSlot] = []
        for p in pools:
            try:
                res = probe(p)
            except Exception as e:  # noqa: BLE001 — a probe failure is just a drop
                res = (False, str(e))
            ok, reason = res if isinstance(res, tuple) else (bool(res), "")
            if ok:
                kept.append(p)
            else:
                qa_st.record_pools(root, jira, {p.id: "quarantined"})
                if on_log is not None:
                    on_log(f"模型池预检失败，隔离 {p.id}：{reason}\n")
        if not kept:
            raise TestRejected("所有模型池预检失败；修好 provider/额度后重跑")
        pools = kept
    qa_st.record_pools(root, jira, {p.id: "healthy" for p in pools})
    if on_log is not None:
        on_log("本轮使用启动时的 worker 配置\n")
    progress_path = run_dir / "progress.yaml"
    script_lock = Lock()
    env_fault: dict[str, Any] | None = None
    fuse_class: str | None = None
    fuse_streak = 0
    from dev_yard.script_exec import (
        FUSE_CLASSES,
        ExecUnreachable,
        resolve_executor,
    )

    first_wt = None
    for job in cases:
        if job.repo:
            cand = paths.req_worktree(root, jira, job.repo)
            if cand.is_dir():
                first_wt = cand
                break
    executor = resolve_executor(
        cfg.env, base_url=cfg.env.base_url, worktree=first_wt, root=root
    )
    if executor.cross_site_warning and on_log is not None:
        on_log(executor.cross_site_warning + "\n")
    serial = not bool(getattr(cfg.env.exec_cfg, "parallel", False))
    hold_setup = serial or executor.use == "jms-k8s"

    def ping() -> None:
        _write_progress(
            progress_path,
            run_id,
            cfg.active_env,
            pools,
            cases,
            on_progress,
            on_log,
            env_fault=env_fault,
        )

    try:
        executor.ping()
    except (ExecUnreachable, TestRejected) as e:
        env_fault = {
            "class": getattr(e, "error_class", None) or "unreachable",
            "message": str(e),
        }
        stamp = now_iso()
        for job in cases:
            if job.setup and job.state not in TERMINAL:
                job.state = "blocked"
                job.reason = f"env fault: {e}"
                job.blocked_class = "env"
                job.ended_at = stamp
        if on_log is not None:
            on_log(f"env fault: {e}\n")

    ping()

    def _mark_env_fault(message: str, cls: str) -> None:
        nonlocal env_fault
        env_fault = {"class": cls, "message": message}
        stamp = now_iso()
        for c in cases:
            if c.setup and c.state not in TERMINAL:
                c.state = "blocked"
                c.reason = f"env fault: {message}"
                c.blocked_class = "env"
                c.ended_at = stamp

    def default_case_runner(job: CaseJob, slot: PoolSlot) -> dict[str, Any]:
        nonlocal fuse_streak, fuse_class
        spec = load_registry(root)["qa-run"]
        prompt = _run_prompt(root, jira, cfg, job, run_id)
        argv = pi_argv(
            root=root,
            bundle="qa-run",
            prompt=None,
            print_mode=True,
            spec=spec,
            provider=slot.provider,
            model=slot.model,
            attach=attachments.list_images(root, jira),
        )
        case_dir = run_dir / job.id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "screenshots").mkdir(exist_ok=True)
        result_path = case_dir / "result.yaml"

        def _echo(line: str) -> None:
            if on_log is not None:
                on_log(line if line.endswith("\n") else line + "\n")

        setup_failed: str | None = None
        if job.setup:
            ctx = script_lock if hold_setup else _nullcontext()
            with ctx:
                if env_fault is not None:
                    setup_failed = f"env fault: {env_fault.get('message')}"
                else:
                    try:
                        out = run_case_script(
                            root,
                            jira,
                            cfg,
                            job,
                            "setup",
                            on_log=on_log,
                            executor=executor,
                        )
                        if out and on_log is not None:
                            on_log(out[-500:])
                        fuse_streak = 0
                        fuse_class = None
                    except TestRejected as e:
                        setup_failed = f"setup failed: {e}"
                        cls = str(getattr(e, "error_class", None) or "")
                        if cls == "auth" and executor.use == "jms-k8s":
                            _mark_env_fault(str(e), cls)
                        elif cls in FUSE_CLASSES:
                            if cls == fuse_class:
                                fuse_streak += 1
                            else:
                                fuse_class = cls
                                fuse_streak = 1
                            if fuse_streak >= 2:
                                _mark_env_fault(str(e), cls)

        if setup_failed is not None:
            got = {
                "status": "blocked",
                "reason": setup_failed,
                "blocked_class": "env",
                "repo": job.repo,
                "model": slot.model,
                "provider": slot.provider,
            }
        else:
            # A resumed case dir may hold the interrupted attempt's result;
            # never let a stale file stand in for this run's outcome.
            result_path.unlink(missing_ok=True)
            _raise_if_cancelled(cancel_check, f"qa-run {job.id}")
            auth_env = _case_auth_env(cfg, job)
            code, raw = run_pi_print_tracked(
                argv,
                root,
                prompt,
                on_line=_echo,
                on_spawn=on_spawn,
                on_reap=on_reap,
                **({"env": auth_env} if auth_env else {}),
            )
            _raise_if_cancelled(cancel_check, f"qa-run {job.id}")
            if code != 0 and not result_path.is_file():
                reason, detail = diagnose_pi_exit(code, raw)
                if detail and on_log is not None:
                    on_log(f"pi exit {code} ({job.id}): {detail}\n")
                got = {
                    "status": "blocked",
                    "reason": reason,
                    "blocked_class": "env",
                    "repo": job.repo,
                    "model": slot.model,
                    "provider": slot.provider,
                }
            else:
                got = _read_case_result(result_path, job, slot)
                if not got.get("model"):
                    got["model"] = slot.model
                if not got.get("provider"):
                    got["provider"] = slot.provider
                if normalize_status(got.get("status")) == "passed":
                    problems = recheck_db_assertions(cfg, job, got, on_log=on_log)
                    mismatches = [p for p in problems if p.get("kind") == "mismatch"]
                    unverified = [p for p in problems if p.get("kind") == "unverified"]
                    if mismatches:
                        detail = "; ".join(
                            f"expected={p.get('expected')!r} actual={p.get('actual')!r}"
                            for p in mismatches
                        )
                        got["status"] = "failed"
                        got["reason"] = f"host-recheck-mismatch: {detail}"
                        if not got.get("defect_class"):
                            got["defect_class"] = "unclassified"
                        bad_sql = {
                            str(p.get("sql") or "").strip()
                            for p in mismatches
                            if str(p.get("sql") or "").strip()
                        }
                        for item in got.get("assertions") or []:
                            if (
                                isinstance(item, dict)
                                and str(item.get("sql") or "").strip() in bad_sql
                            ):
                                item["status"] = "failed"
                    if unverified:
                        soft_sql = {
                            str(p.get("sql") or "").strip()
                            for p in unverified
                            if str(p.get("sql") or "").strip()
                        }
                        for item in got.get("assertions") or []:
                            if (
                                isinstance(item, dict)
                                and str(item.get("sql") or "").strip() in soft_sql
                            ):
                                item["host_recheck"] = "unverified"
                        if on_log is not None:
                            on_log(
                                f"db 断言未复核（{job.id}）：expected 与 worker actual "
                                "都是句子，保留 worker 结论\n"
                            )
                    for p in problems:
                        if p.get("kind") == "error" and on_log is not None:
                            on_log(
                                f"db 断言复核未执行（{job.id}）：{p.get('actual')}\n"
                            )
                if raw and on_log is not None and code != 0:
                    on_log(raw[-500:])
        if job.cleanup:
            try:
                ctx = script_lock if serial else _nullcontext()
                with ctx:
                    run_case_script(
                        root,
                        jira,
                        cfg,
                        job,
                        "cleanup",
                        on_log=on_log,
                        executor=executor,
                    )
            except TestRejected as e:
                got = dict(got)
                if normalize_status(got.get("status")) == "passed":
                    # cleanup failure means polluting data we cannot trust.
                    got["status"] = "blocked"
                got["reason"] = (
                    (got.get("reason") or "") + f" cleanup failed: {e}"
                ).strip()
        if result_path.is_file():
            persist_case_verdict(result_path, got)
        return got

    runner_fn = case_runner or default_case_runner
    try:
        # Seeds share one test DB across requirements, so a run must not overlap
        # another requirement's run/verification in the same env.
        with env_lock(
            root,
            cfg.active_env,
            what="qa run",
            wait_timeout=0 if no_wait else cfg.run_env_wait_timeout,
        ):
            run_schedule(
                cases,
                pools,
                runner_fn,
                on_progress=ping,
                serialize_accounts=cfg.serialize_accounts,
                cancel_check=cancel_check,
                retry_attempts=cfg.run_retry_attempts,
                retry_backoff=cfg.run_retry_backoff,
                on_pool_trip=lambda pid, reason: qa_st.record_pools(
                    root, jira, {pid: "quarantined"}
                ),
            )
    finally:
        executor.close()

    # Cancelled mid-run: bail before writing result.yaml / ingesting, so the
    # partial run is left resumable instead of recorded as a real outcome.
    _raise_if_cancelled(cancel_check, "qa-run")

    for job in cases:
        if job.state in {"skipped", "blocked"} and job.ended_at:
            if not job.model:
                # A case that never got scheduled would have run on the pool
                # `pick_pool` prefers, so record that instead of an empty model.
                preferred = min(pools, key=lambda p: (p.priority, p.id))
                job.model = preferred.model
                job.provider = preferred.provider
            dest = run_dir / job.id / "result.yaml"
            if not dest.is_file():
                _write_skipped_result(dest, job)

    extra: list[str] = []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        after_state = _tree_state(wt)
        before_state = tree_before.get(alias, {})
        if after_state is None:
            # `git status` broke after the run; treat as unsafe rather than
            # silently skipping the mutation check.
            extra.append(f"{alias}: git status unreadable after run")
            continue
        head_after = _head_sha(wt)
        before_head = heads_before.get(alias) or ""
        if before_head and head_after and head_after != before_head:
            extra.append(
                f"{alias}: HEAD moved {before_head[:8]}..{head_after[:8]}"
            )
        for path, sig in after_state.items():
            if before_state.get(path) != sig:
                extra.append(path)
    new_root_png = _root_png_names(root) - root_png_before
    extra = sorted(dict.fromkeys(extra))

    summary = _summarize(cases)
    run_doc: dict[str, Any] = {
        "run_id": run_id,
        "env": cfg.active_env,
        "workers": [
            {
                "id": w.id,
                "model": w.model,
                "concurrency": w.concurrency,
                "priority": w.priority,
            }
            for w in cfg.workers
        ],
        "cases": [
            {
                "case": c.id,
                "status": c.state,
                "repo": c.repo,
                "model": c.model,
                "reason": c.reason,
                "blocked_class": c.blocked_class,
                "defect_class": c.defect_class,
                "assertions": c.assertions,
            }
            for c in cases
        ],
        "summary": summary,
    }
    if qa_yaml_mtime is not None:
        try:
            changed_mtime = paths.qa_yaml(root).stat().st_mtime != qa_yaml_mtime
        except OSError:
            changed_mtime = False
        if changed_mtime:
            run_doc["config_snapshot"] = "startup"
            if on_log is not None:
                on_log("qa.yaml 在本轮运行中被修改，本轮仍使用启动时的 pool 配置\n")
    if env_fault:
        run_doc["env_fault"] = env_fault
    if extra:
        run_doc["mutation"] = extra
    if review_bypassed:
        run_doc["review_bypassed"] = True
    (run_dir / "result.yaml").write_text(
        yaml.safe_dump(run_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    ping()
    if extra:
        # Recorded above before raising, so the run is not lost.
        raise TestRejected(
            f"worker mutated worktree: {', '.join(extra[:5])}"
            + (" ..." if len(extra) > 5 else "")
        )

    case_payloads: list[dict[str, Any]] = []
    for c in cases:
        item = {
            "case": c.id,
            "title": c.title,
            "repo": c.repo,
            "covers": c.covers,
            "model": c.model,
            "provider": c.provider,
            "status": c.state,
            "reason": c.reason,
        }
        if c.defect_class:
            item["defect_class"] = c.defect_class
        if c.failure:
            item["failure"] = c.failure
        if c.assertions:
            item["assertions"] = c.assertions
        raw_path = run_dir / c.id / "result.yaml"
        if raw_path.is_file():
            try:
                raw = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                raw = {}
            if isinstance(raw, dict):
                if not item.get("repo"):
                    item["repo"] = str(raw.get("repo") or "")
                if raw.get("failure") and not item.get("failure"):
                    item["failure"] = raw.get("failure")
                if raw.get("title"):
                    item["title"] = raw.get("title")
                if raw.get("defect_class") and not item.get("defect_class"):
                    item["defect_class"] = str(raw.get("defect_class"))
                if isinstance(raw.get("assertions"), list):
                    item["assertions"] = raw["assertions"]
        case_payloads.append(item)

    ingested = False
    ingest_skipped = None
    if ingest:
        try:
            report = map_qa_result(run_doc, case_payloads)
        except ReportRejected as e:
            raise TestRejected(str(e)) from e
        if report is None:
            if summary.get("blocked"):
                ingest_skipped = "blocked"
            elif has_design_blocked_skip(case_payloads):
                ingest_skipped = "design-blocked"
            elif summary.get("passed"):
                ingest_skipped = "malformed"
            else:
                ingest_skipped = "no passed cases"
        else:
            repos = load_repos(root)
            for f in report.findings:
                if f.repo not in repos:
                    raise TestRejected(
                        f"finding {f.id} repo {f.repo!r} is not a repos.yaml alias"
                    )
            accept_test_report(root, jira, report, spawn=False)
            ingested = True
    # Persist the ingest outcome so a blocked/skipped run is not a silent no-op.
    run_doc["ingested"] = ingested
    if ingest_skipped:
        run_doc["ingest_skipped"] = ingest_skipped
    (run_dir / "result.yaml").write_text(
        yaml.safe_dump(run_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    if new_root_png:
        shots = run_dir / "_root_png"
        shots.mkdir(exist_ok=True)
        for name in sorted(new_root_png):
            src = root / name
            dest = shots / name
            if src.is_file() and not dest.exists():
                try:
                    src.replace(dest)
                except OSError:
                    pass

    # M6b/M1: classify the non-passing cases and record the machine state so the
    # next invocation (or a restarted web server) knows what to do without a
    # human having to re-derive it from the report.
    triage = triage_buckets(case_payloads)
    if triage["pending"]:
        phase = "awaiting_triage"
    elif triage["auto_recycled"]:
        phase = "recycled"
    elif summary.get("blocked"):
        phase = "running"
    else:
        phase = "closed"
    qa_st.record(
        root,
        jira,
        phase=phase,
        last_run_id=run_id,
        last_verdict=(
            "failed"
            if (summary.get("failed") or summary.get("blocked"))
            else "passed"
            if summary.get("passed")
            else ""
        ),
    )
    qa_st.record_triage(root, jira, triage["pending"], triage["auto_recycled"])

    return {
        "jira": jira,
        "run_id": run_id,
        "env": cfg.active_env,
        "summary": summary,
        "ingested": ingested,
        "ingest_skipped": ingest_skipped,
        "cases": len(cases),
        "triage": triage,
    }
