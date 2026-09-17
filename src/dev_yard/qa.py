from __future__ import annotations

import codecs
import hashlib
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from dev_yard import gitops, paths, status as st
from dev_yard.config import load_repos
from dev_yard.qa_config import QaConfig, TestRejected, load_qa_config, redact_url
from dev_yard.qa_report import map_qa_result
from dev_yard.qa_schedule import (
    CaseJob,
    PoolSlot,
    normalize_status,
    progress_line,
    progress_payload,
    run_schedule,
)
from dev_yard.runners import RunResult, Runner, get_runner, pi_argv, run_pi_print
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import load_registry
from dev_yard.test_report import ReportRejected, accept_test_report
from dev_yard.tickets import load_tickets

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]
_CASE_NAME = re.compile(r"^case-.+\.md$")
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PORCELAIN_LINE = re.compile(r"^\s*([A-Z?!]{1,2})\s+(.+)$")
_PNG = re.compile(r"\.png$", re.I)


def write_context_md(root: Path, jira: str, cfg: QaConfig) -> Path:
    req = paths.req_dir(root, jira)
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    repos = load_repos(root)
    aliases = _involved_aliases(root, jira)
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
        lines.append(
            f"- {alias}: {wt.resolve()}  "
            f"(branch req/{jira}, base {base}, role {role})"
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
        "",
        "This run uses only the env above; do not switch env or guess another host.",
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
        "the default. Load that account's state_file before the case steps "
        "(the host does not log in for you).",
        "Passwords are not listed here. Read them from the requirement accounts "
        f"file `.yard-qa/requirements/{jira}/accounts.yaml` (preferred when this "
        f"requirement has one), else `qa.yaml envs.{cfg.active_env}.auth.accounts.<name>`.",
        "If a state_file is missing or not logged in, open `base_url`, log in with "
        "that account's username/password, then `state-save` to state_file "
        "(only when this run is sequential).",
        "Never copy a password into result.yaml, evidence, or this context.md; "
        "redact DSNs and passwords as `***` in commands and evidence.",
        "Do not `state-save` while more than one case is in flight.",
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
        deps = meta.get("depends_on") or []
        if isinstance(deps, str):
            deps = [x.strip() for x in deps.replace(",", " ").split() if x.strip()]
        elif not isinstance(deps, list):
            deps = []
        covers = meta.get("covers") or []
        if isinstance(covers, str):
            covers = [x.strip() for x in covers.replace(",", " ").split() if x.strip()]
        elif not isinstance(covers, list):
            covers = []
        module = path.parent.name
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


def _gate(root: Path, jira: str) -> None:
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
    if not st.all_done(data):
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
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = ""
            try:
                holder = path.read_text(encoding="utf-8").strip()
            except OSError:
                holder = ""
            pid = int(holder.split(":", 1)[0]) if holder else 0
            if not _pid_alive(pid) and attempt == 0:
                # Atomic reclaim: the winner renames, losers get FileNotFound.
                tmp = path.with_name(path.name + f".stale.{uuid.uuid4().hex}")
                try:
                    os.rename(path, tmp)
                except OSError:
                    pass
                else:
                    tmp.unlink(missing_ok=True)
                continue
            raise TestRejected(
                f"another run for {jira} is in progress; wait for it to finish "
                f"(or delete {path} if it is stale)"
            ) from None
        os.write(fd, token.encode())
        os.close(fd)
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
        entry = _porcelain_entry(ln)
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
) -> None:
    env = cfg.env
    if names is None:
        names = [env.auth_default] if env.auth_default else []
    binary = shutil.which("playwright-cli")
    for name in names:
        acct = env.accounts.get(name)
        if acct is None or not acct.state_file:
            continue
        has_creds = bool(acct.username and acct.password)
        state = Path(acct.state_file)
        if not state.is_absolute():
            state = root / state
        if not state.is_file():
            if has_creds and cfg.total_concurrency <= 1:
                # Sequential: the case agent logs in and state-saves.
                continue
            if has_creds:
                raise TestRejected(
                    f"account {name!r} has no saved session at {acct.state_file!r} "
                    "and concurrency > 1; run once sequentially first to log in"
                )
            raise TestRejected(
                f"missing auth state_file {acct.state_file!r} for account {name!r}; "
                "log in once and playwright-cli state-save, or put "
                "username/password in qa.yaml"
            )
        if not binary:
            raise TestRejected("playwright-cli not found; cannot load auth state")
        # Validation-only session: cases use their own `-s=qap-<id>` and load
        # this file themselves, so preloading here just proves it is usable.
        cmd = [
            binary,
            "--browser",
            cfg.browser.channel,
            "-s=qap-preload",
            "state-load",
            str(state),
        ]
        if on_log is not None:
            on_log(f"$ {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
            raise TestRejected(f"auth state-load failed for account {name!r}: {err}")


def _porcelain_entry(line: str) -> tuple[str, str] | None:
    """`XY path` → (status, path). `gitops.run` strips the leading space of the
    first line, so the status columns are matched by regex, not by offset."""
    m = _PORCELAIN_LINE.match(line)
    if not m:
        return None
    status = m.group(1).strip() or "??"
    path = m.group(2)
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip()
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        # git C-quotes non-ASCII / special paths; decode so the file resolves.
        try:
            decoded, _ = codecs.escape_decode(path[1:-1].encode("utf-8"))
            path = decoded.decode("utf-8", "surrogateescape")
        except (ValueError, UnicodeDecodeError):
            path = path[1:-1]
    if not path:
        return None
    return status, path


def _duties(kind: str, jira: str) -> str:
    qa = f"reqs/{jira}/qa/"
    if kind == "design":
        return (
            "You are designing UI test cases for this freeze worktree.\n"
            f"Write only under {qa} (meta.yaml and cases/). "
            "Do not write STATUS.yaml or REQUIREMENT/GRILL/SPEC/TICKETS.md.\n"
            "Read REQUIREMENT.md, SPEC.md, TICKETS.md. Do not call MCP or re-fetch Jira.\n"
            "Diff each worktree with `git diff <default_base>...HEAD`. "
            "Empty diff: stop and say so.\n"
            "Do not git checkout, commit, push, or switch. Do not interview."
        )
    return (
        "You are executing ONE UI test case. Do not repair product code.\n"
        f"Write only under {qa}. Do not change any worktree file.\n"
        "Do not git checkout, commit, push, switch, or deploy.\n"
        "Do not spawn other cases. Do not change case expected values to go green.\n"
        "URLs come from context.md base_url + Routes, else frontend route code. "
        "Do not guess hosts."
    )


def _design_prompt(root: Path, jira: str, cfg: QaConfig) -> str:
    spec = load_registry(root)["qa-design"]
    extra = _duties("design", jira) + "\n\n" + _context_block(root, jira, cfg)
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
        account_line += (
            f"  state_file: {acct.state_file or '(none)'}"
            "  (log in with its username/password from the requirement accounts "
            "file or qa.yaml if needed)"
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
        + f"Write case result to `{evidence / 'result.yaml'}` "
        + f"and screenshots to `{evidence / 'screenshots'}`.\n"
        + "This invocation runs only the case below.\n\n"
        + f"# Case {job.id}\n\n"
        + (Path(job.path).read_text(encoding="utf-8") if job.path else job.body)
    )
    return session_prompt_for(spec, root, jira, extra=extra)


def _context_block(root: Path, jira: str, cfg: QaConfig) -> str:
    ctx = paths.qa_dir(root, jira) / "context.md"
    text = ctx.read_text(encoding="utf-8") if ctx.is_file() else ""
    return f"qa/context.md:\n{text}".strip()


def _write_progress(
    path: Path,
    run_id: str,
    env: str,
    pools: list[PoolSlot],
    cases: list[CaseJob],
    on_progress: ProgressFn | None,
    on_log: LogFn | None,
) -> dict[str, Any]:
    payload = progress_payload(run_id, env, pools, cases)
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


def _read_case_result(path: Path, job: CaseJob, slot: PoolSlot) -> dict[str, Any]:
    if not path.is_file():
        return {
            "status": "blocked",
            "reason": "worker exit: missing case result.yaml",
            "repo": job.repo,
            "model": slot.model,
            "provider": slot.provider,
        }
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
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
    status = normalize_status(data.get("status"))
    assertions = data.get("assertions")
    return {
        "status": status,
        "reason": str(data.get("reason") or ""),
        "repo": str(data.get("repo") or job.repo),
        "title": str(data.get("title") or job.title),
        "covers": data.get("covers") or job.covers,
        "model": str(data.get("model") or slot.model or ""),
        "provider": str(data.get("provider") or slot.provider or ""),
        "failure": data.get("failure") if isinstance(data.get("failure"), dict) else None,
        "assertions": assertions if isinstance(assertions, list) else [],
        "raw": data,
    }


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


def _summarize(cases: list[CaseJob]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for c in cases:
        if c.state in counts:
            counts[c.state] += 1
    counts["total"] = len(cases)
    return counts


def req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    ingest: bool = True,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
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
            ingest=ingest,
            runner=runner,
            case_runner=case_runner,
            on_progress=on_progress,
            on_log=on_log,
        )


def _req_test(
    root: Path,
    jira: str,
    *,
    env: str | None = None,
    print_mode: bool = False,
    design_only: bool = False,
    run_only: bool = False,
    redesign: bool = False,
    ingest: bool = True,
    runner: Runner | None = None,
    case_runner: Callable[[CaseJob, PoolSlot], dict[str, Any]] | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    if design_only and run_only:
        raise TestRejected("--design-only and --run-only are mutually exclusive")
    _gate(root, jira)
    cfg = load_qa_config(root, env, jira)
    qa = paths.qa_dir(root, jira)
    qa.mkdir(parents=True, exist_ok=True)
    write_context_md(root, jira, cfg)
    cases = discover_cases(qa)
    if run_only and not cases:
        raise TestRejected(f"{jira} has no qa/cases; cannot --run-only")
    need_design = (not run_only) and (redesign or not cases)
    if need_design:
        spec = load_registry(root)["qa-design"]
        r = runner or get_runner(
            root, "qa-design", print_mode=print_mode, spec=spec
        )
        prompt = _design_prompt(root, jira, cfg)
        result = r.start(prompt, root, [qa, paths.req_dir(root, jira)])
        if not result.ok:
            raise TestRejected(
                f"qa-design failed: {result.summary or result.exit_code}"
            )
        cases = discover_cases(qa)
    if design_only:
        return {"jira": jira, "design_only": True, "cases": len(cases)}
    if not cases:
        raise TestRejected(f"{jira} qa-design produced no cases")

    evidence = qa / "evidence"
    run_id, run_dir = _claim_run_dir(evidence)
    aliases = _involved_aliases(root, jira)
    tree_before: dict[str, dict[str, str]] = {}
    baseline_dir = run_dir / "repo-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        snap = _tree_state(wt)
        if snap is None:
            raise TestRejected(
                f"cannot read `git status` in {wt}; aborting before the run"
            )
        tree_before[alias] = snap
        text = _porcelain(wt)
        (baseline_dir / f"{alias}.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
    root_png_before = _root_png_names(root)
    _check_case_accounts(cfg, cases, jira)
    # Resolve the default account name onto each case so the scheduler serializes
    # cases that share a login state (an empty `account` still uses the default).
    for job in cases:
        resolved = _case_account(cfg, job)
        if resolved in cfg.env.accounts:
            job.account = resolved
    _preload_auth(root, cfg, _used_accounts(cfg, cases), on_log)

    pools = [PoolSlot.from_worker(w) for w in cfg.workers]
    progress_path = run_dir / "progress.yaml"

    def ping() -> None:
        _write_progress(
            progress_path, run_id, cfg.active_env, pools, cases, on_progress, on_log
        )

    ping()

    def default_case_runner(job: CaseJob, slot: PoolSlot) -> dict[str, Any]:
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
        )
        case_dir = run_dir / job.id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "screenshots").mkdir(exist_ok=True)

        def _echo(line: str) -> None:
            if on_log is not None:
                on_log(line if line.endswith("\n") else line + "\n")

        code, raw = run_pi_print(argv, root, prompt, on_line=_echo)
        result_path = case_dir / "result.yaml"
        if code != 0 and not result_path.is_file():
            return {
                "status": "blocked",
                "reason": f"worker exit: pi exit {code}",
                "repo": job.repo,
                "model": slot.model,
                "provider": slot.provider,
            }
        got = _read_case_result(result_path, job, slot)
        if not got.get("model"):
            got["model"] = slot.model
        if not got.get("provider"):
            got["provider"] = slot.provider
        if raw and on_log is not None and code != 0:
            on_log(raw[-500:])
        return got

    runner_fn = case_runner or default_case_runner
    run_schedule(cases, pools, runner_fn, on_progress=ping)

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
                "assertions": c.assertions,
            }
            for c in cases
        ],
        "summary": summary,
    }
    if extra:
        run_doc["mutation"] = extra
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
            accept_test_report(root, jira, report)
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

    return {
        "jira": jira,
        "run_id": run_id,
        "summary": summary,
        "ingested": ingested,
        "ingest_skipped": ingest_skipped,
        "cases": len(cases),
    }
