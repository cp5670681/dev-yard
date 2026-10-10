"""One execution: the run lock, resume, verdicts, and the worker prompt."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dev_yard import gitops, paths, qa_run
from dev_yard import status as st
from dev_yard.config import load_repos
from dev_yard.qa_cases import _involved_aliases
from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_design import _context_block, _duties
from dev_yard.qa_exec import (
    diagnose_pi_exit,
    ensure_auth,
    exec_site_for_job,
    normalize_case_result,
    origin_for_job,
    replay_path,
    state_path,
)
from dev_yard.qa_schedule import (
    BLOCKED_KINDS,
    TERMINAL,
    CaseJob,
    PoolSlot,
    blocked_kind,
    normalize_status,
    progress_line,
    progress_payload,
    resumable,
)
from dev_yard.runners import pi_argv, run_pi_print_tracked
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import load_registry

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, Any]], None]
SpawnFn = Callable[[subprocess.Popen[str]], None]
ReapFn = Callable[[subprocess.Popen[str]], None]

_PNG = re.compile(r"\.png$", re.I)


def _check_case_repos(root: Path, cases: list[CaseJob]) -> None:
    """Every runnable case must name a real repos.yaml alias.

    Caught before the run so a repo-less case cannot abort ingest afterwards
    (a failed finding needs a repo) or attribute its result to everything.
    """
    repos = set(load_repos(root))
    for job in cases:
        if not job.repo:
            raise TestRejected(f"case {job.id} has no repo; add `repo: <alias>` to its frontmatter")
        if repos and job.repo not in repos:
            raise TestRejected(f"case {job.id} repo {job.repo!r} is not a repos.yaml alias")


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
    missing = [a for a in aliases if not paths.req_worktree(root, jira, a).is_dir()]
    if missing:
        raise TestRejected(f"{jira} missing freeze worktrees: {', '.join(missing)}")


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
        return gitops.run(["git", "status", "--porcelain", "-uall"], cwd=worktree)
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
    if uses_default and cfg.env.accounts and cfg.env.auth_default not in cfg.env.accounts:
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


def _case_auth_env(root: Path, cfg: QaConfig, job: CaseJob) -> dict[str, str]:
    """Credentials for a case, handed to the worker via env, never the prompt."""
    name = _case_account(cfg, job)
    acct = cfg.env.accounts.get(name) if name else None
    if acct is None:
        return {}
    site = exec_site_for_job(root, cfg, job) or ""
    origin = origin_for_job(root, cfg, job)
    state = state_path(root, cfg, acct, site=site)
    replay = state.with_suffix(".replay.sh")
    try:
        state_str = str(state.relative_to(root))
    except ValueError:
        state_str = str(state)
    try:
        replay_str = str(replay.relative_to(root))
    except ValueError:
        replay_str = str(replay)
    return {
        "YARD_QA_USERNAME": acct.username or "",
        "YARD_QA_PASSWORD": acct.password or "",
        "YARD_QA_STATE_FILE": state_str,
        "YARD_QA_AUTH_REPLAY": replay_str,
        "YARD_QA_BASE_URL": origin or cfg.env.base_url,
        "YARD_QA_EXEC_SITE": site,
        "BASE_URL": origin or cfg.env.base_url,
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
    cases: list[CaseJob] | None = None,
) -> dict[str, str]:
    """Ensure sessions exist; returns {account: error} for the ones that failed."""
    sites: list[str] = []
    for job in cases or []:
        site = exec_site_for_job(root, cfg, job) or ""
        if site not in sites:
            sites.append(site)
    return ensure_auth(root, cfg, names, on_log, sites=sites or None)


def _auth_failure_for(
    root: Path, cfg: QaConfig, job: CaseJob, failures: dict[str, str]
) -> str | None:
    """Match ensure_auth keys: `{account}/{site}` or bare account."""
    acct = _case_account(cfg, job)
    if not acct:
        return None
    site = exec_site_for_job(root, cfg, job) or ""
    if site:
        keyed = failures.get(f"{acct}/{site}")
        if keyed is not None:
            return keyed
    return failures.get(acct)


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


def _run_prompt(
    root: Path,
    jira: str,
    cfg: QaConfig,
    job: CaseJob,
    run_id: str,
    *,
    case_text: str | None = None,
) -> str:
    spec = load_registry(root)["qa-run"]
    qa = paths.qa_dir(root, jira)
    evidence = qa / "evidence" / run_id / job.id
    headed = "true" if cfg.headed else "false"
    account = _case_account(cfg, job)
    acct = cfg.env.accounts.get(account)
    account_line = f"Account: {account or '(none)'}"
    if acct:
        site = exec_site_for_job(root, cfg, job) or ""
        origin = origin_for_job(root, cfg, job) or cfg.env.base_url
        state = state_path(root, cfg, acct, site=site)
        try:
            state_str = str(state.relative_to(root))
        except ValueError:
            state_str = str(state)
        replay_str = str(Path(state_str).with_suffix(".replay.sh"))
        account_line += (
            f"\nCase origin: {origin} (site {site or '(default)'})\n"
            f"Account Details (credentials are in the environment, not the prompt):\n"
            f"  username: $YARD_QA_USERNAME\n"
            f"  password: $YARD_QA_PASSWORD (never echo this)\n"
            f"  base_url: $YARD_QA_BASE_URL\n"
            f"  state_file: {state_str}\n"
            f"  auth_replay: {replay_str}\n"
            f"Login & Session Protocol:\n"
            f"  1. If `{state_str}` exists, run `playwright-cli -s=qap-{job.id} state-load {state_str}`.\n"
            f"  2. Open $YARD_QA_BASE_URL (this case's site). If unauthenticated / on login page:\n"
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
        + (
            case_text
            if case_text is not None
            else (Path(job.path).read_text(encoding="utf-8") if job.path else job.body)
        )
    )
    return session_prompt_for(spec, root, jira, extra=extra)


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
    cleaned = normalize_case_result(data, job, require_assertions=strict)
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
                "failure": data.get("failure") if isinstance(data.get("failure"), dict) else None,
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


def _run_matches(run_dir: Path, case_ids: set[str] | None, env: str | None) -> bool:
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


def _row_resumable(row: dict[str, Any]) -> bool:
    """Whether a `progress.yaml` case row has no final verdict (resume re-runs it)."""
    return resumable(
        str(row.get("state") or ""),
        str(row.get("blocked_class") or ""),
        str(row.get("reason") or ""),
    )


def _run_incomplete(run_dir: Path, case_ids: set[str]) -> bool:
    # A concluded run has no work left, whatever the case rows say.
    if qa_run.status(run_dir) not in qa_run.RESUMABLE:
        return False
    doc = _progress_doc(run_dir)
    if doc is not None and doc.get("cases"):
        # Progress is authoritative once the run wrote it: only a case with no
        # final verdict — or a cancelled one — means there is work left.
        for item in doc.get("cases") or []:
            if isinstance(item, dict) and _row_resumable(item):
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
            1 for c in (data.get("cases") or []) if isinstance(c, dict) and _row_resumable(c)
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


def find_run_for_rerun(qa: Path, target_ids: set[str]) -> tuple[str, Path, str] | None:
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


def latest_retryable(qa: Path, cases: list[CaseJob], env: str) -> list[str]:
    """Ids to re-run in place: the latest run's failed/blocked cases + dependents.

    Only a *finished* run that matches `env` and the current case set counts;
    `case-defect` ids are excluded (they belong to the design recycle path).
    A case whose `depends_on` chain reaches a retried case is included too, so a
    fixed upstream is re-verified downstream.
    """
    got = _latest_retryable_run(qa, {c.id for c in cases}, env)
    if got is None or not got[1]:
        return []
    return _with_dependents(cases, got[1])


def _with_dependents(cases: list[CaseJob], base: list[str]) -> list[str]:
    """Grow a retry set with every case whose `depends_on` chain reaches it."""
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


def affected_covers(root: Path, jira: str, cases: list[CaseJob], run_dir: Path) -> set[str]:
    """Case ids whose `covers` change points were touched since the run baseline.

    M10: when a B-ticket fix lands, a case covering a changed file must re-run
    even if it passed last time. `meta.yaml` maps each change id (`D*`) to repo
    files (`changes[].ref`); the run's `repo-baseline/<alias>.head` gives the
    per-repo commit the last run started from, so `git diff` yields exactly what
    moved since. Best-effort: any unreadable baseline/repo is skipped.
    """
    meta = paths.qa_dir(root, jira) / "meta.yaml"
    if not meta.is_file():
        return set()
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return set()
    changes = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(changes, list):
        return set()
    changed: dict[str, set[str]] = {}

    def repo_changes(alias: str) -> set[str]:
        if alias in changed:
            return changed[alias]
        files: set[str] = set()
        head_path = run_dir / "repo-baseline" / f"{alias}.head"
        wt = paths.req_worktree(root, jira, alias)
        if head_path.is_file() and wt.is_dir():
            try:
                base = head_path.read_text(encoding="utf-8").strip()
            except OSError:
                base = ""
            if base:
                files = gitops.changed_files(wt, base)
        changed[alias] = files
        return files

    hit: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        cid = str(change.get("id") or "").strip()
        alias = str(change.get("repo") or "").strip()
        refs = set()
        for raw in str(change.get("ref") or "").split(","):
            path = raw.strip()
            while path.startswith("./"):
                path = path[2:]
            if path:
                refs.add(path)
        if not cid or not alias or not refs:
            continue
        if refs & repo_changes(alias):
            hit.add(cid)
    if not hit:
        return set()
    return {job.id for job in cases if job.covers and hit & {str(c).strip() for c in job.covers}}


def _latest_retryable_run(qa: Path, case_ids: set[str], env: str) -> tuple[Path, list[str]] | None:
    """Newest finished run matching env+case set, with its failed/blocked ids."""
    evidence = qa / "evidence"
    if not evidence.is_dir():
        return None
    for run_dir in sorted((p for p in evidence.iterdir() if p.is_dir()), reverse=True):
        # Only a concluded run has a real verdict to amend; a paused/running one
        # is the resume path's job, not incremental rerun's.
        if qa_run.status(run_dir) != qa_run.CONCLUDED:
            continue
        doc = _progress_doc(run_dir)
        if doc is None:
            continue
        run_env = str(doc.get("env") or "")
        if env and run_env and run_env != env:
            continue
        ids = {
            str(c.get("id")) for c in doc.get("cases") or [] if isinstance(c, dict) and c.get("id")
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
        return run_dir, out
    return None


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
    if state in {"pending", "ready", "running", "held"}:
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
        new_state, new_reason = reconcile_case_verdict(state, reason, file_status, file_reason)
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
        if state == "held":
            # Parked by a single-case run. Copy it onto the job so refresh_ready
            # does not treat the default `pending` as "go run this".
            job.state = "held"
            job.reason = str(prev.get("reason") or "")
            continue
        if prev_by_id and state not in TERMINAL:
            # pending/ready/running stay at the job default (`pending`) so a
            # resume dispatches them again. `held` is handled above.
            continue
        if _row_resumable(prev):
            # A cancelled row is not a verdict: re-run it. (pending/ready/running
            # already continued above; this covers blocked(cancelled).)
            job.state = "ready"
            job.reason = ""
            job.blocked_class = ""
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
        if job.id in prev_by_id and (state != status or str(prev.get("reason") or "") != reason):
            prev["state"] = status
            prev["reason"] = reason
            changed_doc = True
        job.state = status
        job.reason = reason
        job.blocked_class = str(got.get("blocked_class") or prev.get("blocked_class") or "")
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


def _hold_unselected(cases: list[CaseJob], selected: set[str]) -> None:
    """Park every case the user did not ask to run.

    A selected case whose dependency is not also selected cannot run yet:
    nothing in this fresh round has passed.
    """
    by_id = {c.id: c for c in cases}
    for cid in sorted(selected):
        for dep in by_id[cid].depends_on:
            if dep not in selected:
                raise TestRejected(f"{cid} 还在等 {dep} 通过")
    for job in cases:
        if job.id not in selected:
            job.state = "held"


def _reject_selective(cases: list[CaseJob], selected: set[str], run_dir: Path) -> None:
    """Refuse a single-case click that cannot make progress.

    Passed, failed, and blocked cases keep the existing re-run path. A skipped
    case, or a parked case whose dependency has not passed, is rejected so the
    click does not silently no-op.
    """
    doc = _progress_doc(run_dir) or {}
    states: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for item in doc.get("cases") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cid = str(item["id"])
        states[cid] = str(item.get("state") or "")
        reasons[cid] = str(item.get("reason") or "")
    by_id = {c.id: c for c in cases}
    for cid in sorted(selected):
        state = states.get(cid, "")
        if state == "skipped":
            raise TestRejected(reasons.get(cid) or f"{cid} 已跳过")
        if state not in {"held", "pending"}:
            continue
        for dep in by_id[cid].depends_on:
            if dep in selected:
                continue
            dep_state = states.get(dep, "")
            if dep_state == "passed":
                continue
            if dep_state == "skipped":
                raise TestRejected(reasons.get(dep) or f"{cid} 已跳过：{dep} 未通过")
            raise TestRejected(f"{cid} 还在等 {dep} 通过")


def _release_held_cases(run_dir: Path) -> list[str]:
    """Turn parked cases back into `pending` so a full run schedules them."""
    doc = _progress_doc(run_dir)
    if doc is None:
        return []
    released: list[str] = []
    for item in doc.get("cases") or []:
        if isinstance(item, dict) and str(item.get("state") or "") == "held":
            item["state"] = "pending"
            item["reason"] = ""
            item["ended_at"] = None
            released.append(str(item.get("id") or ""))
    if not released:
        return []
    (run_dir / "progress.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return [cid for cid in released if cid]


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
