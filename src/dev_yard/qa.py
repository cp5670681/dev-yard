from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from dev_yard import gitops, paths, status as st
from dev_yard.config import load_repos
from dev_yard.qa_config import QaConfig, TestRejected, load_qa_config
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
    acct = env.accounts.get(env.auth_default)
    state_file = acct.state_file if acct else ""
    db = "configured" if env.db_url else "not configured"
    headed = "true" if cfg.headed else "false"
    others = [n for n in cfg.env_names if n != cfg.active_env]
    lines += [
        "",
        "## Environment",
        "",
        f"- env: {cfg.active_env}",
        f"- available envs: {', '.join(cfg.env_names) or cfg.active_env}",
        f"- base_url: {env.base_url}",
        f"- browser: {cfg.browser.channel} headed={headed}",
        f"- account: {env.auth_default or '(none)'} (see qa.yaml)",
        f"- state_file: {state_file or '(none)'}",
        f"- db: {db} (qa.yaml envs.{cfg.active_env}.db.url)",
        f"- script.runner: {env.script_runner or '(sql only)'}",
        "",
        "This run uses only the env above; do not switch env or guess another host.",
    ]
    if others:
        lines.append(
            f"Other envs exist ({', '.join(others)}) but are out of scope for this run."
        )
    lines += [
        "",
        "Login accounts and the DB DSN live in `qa.yaml` (gitignored) under "
        f"`envs.{cfg.active_env}`; read them from that file.",
        "If state_file is missing or not logged in, open `base_url`, log in with "
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


def discover_cases(qa: Path) -> list[CaseJob]:
    cases_root = qa / "cases"
    if not cases_root.is_dir():
        return []
    out: list[CaseJob] = []
    for path in sorted(cases_root.rglob("case-*.md")):
        if not path.is_file() or not _CASE_NAME.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = split_frontmatter(text)
        except yaml.YAMLError as e:
            raise TestRejected(f"unreadable case frontmatter in {path}: {e}") from e
        cid = str(meta.get("id") or path.stem).strip()
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
    aliases = _involved_aliases(root, jira)
    missing = [
        a for a in aliases if not paths.req_worktree(root, jira, a).is_dir()
    ]
    if missing:
        raise TestRejected(
            f"{jira} missing freeze worktrees: {', '.join(missing)}"
        )


def _new_run_id(evidence: Path) -> str:
    base = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    if not (evidence / base).exists():
        return base
    n = 2
    while (evidence / f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"


def _porcelain(worktree: Path) -> str:
    try:
        return gitops.run(["git", "status", "--porcelain"], cwd=worktree)
    except gitops.GitError as e:
        return f"(git status failed: {e})"


def _porcelain_lines(text: str) -> set[str]:
    return {ln for ln in text.splitlines() if ln.strip() and not ln.startswith("(")}


def _root_png_names(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_file() and _PNG.search(p.name)}


def _preload_auth(root: Path, cfg: QaConfig, on_log: LogFn | None = None) -> None:
    env = cfg.env
    acct = env.accounts.get(env.auth_default)
    if acct is None:
        return
    has_creds = bool(acct.username and acct.password)
    if not acct.state_file:
        # Creds-only account: the run agent logs in from qa.yaml itself.
        return
    state = Path(acct.state_file)
    if not state.is_absolute():
        state = root / state
    if not state.is_file():
        if has_creds:
            # No saved session yet, but qa.yaml has the password: let the run
            # agent log in and state-save rather than aborting the whole run.
            return
        raise TestRejected(
            f"missing auth state_file {acct.state_file!r}; "
            "log in once and playwright-cli state-save, or put "
            "username/password in qa.yaml"
        )
    binary = shutil.which("playwright-cli")
    if not binary:
        raise TestRejected("playwright-cli not found; cannot load auth state")
    cmd = [
        binary,
        "--browser",
        cfg.browser.channel,
        "-s=qap-auth",
        "state-load",
        str(state),
    ]
    if on_log is not None:
        on_log(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
        raise TestRejected(f"auth state-load failed: {err}")


def _mutation_paths(new_lines: set[str]) -> list[str]:
    out: list[str] = []
    for ln in new_lines:
        path = ln[3:] if len(ln) > 3 else ln
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip()
        if path:
            out.append(path)
    return out


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
        "URLs come from context.md base_url plus meta.yaml routes / frontend routes. "
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
    extra = (
        _duties("run", jira)
        + "\n\n"
        + _context_block(root, jira, cfg)
        + "\n\n"
        + f"Run id: {run_id}\n"
        + f"Playwright session: `-s=qap-{job.id}`\n"
        + f"headed: {headed}\n"
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
    return {
        "status": status,
        "reason": str(data.get("reason") or ""),
        "repo": str(data.get("repo") or job.repo),
        "title": str(data.get("title") or job.title),
        "covers": data.get("covers") or job.covers,
        "model": str(data.get("model") or slot.model or ""),
        "provider": str(data.get("provider") or slot.provider or ""),
        "failure": data.get("failure") if isinstance(data.get("failure"), dict) else None,
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
    if design_only and run_only:
        raise TestRejected("--design-only and --run-only are mutually exclusive")
    _gate(root, jira)
    cfg = load_qa_config(root, env)
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
    run_id = _new_run_id(evidence)
    run_dir = evidence / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    aliases = _involved_aliases(root, jira)
    baselines: dict[str, set[str]] = {}
    baseline_dir = run_dir / "repo-baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        text = _porcelain(wt)
        (baseline_dir / f"{alias}.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
        baselines[alias] = _porcelain_lines(text)
    root_png_before = _root_png_names(root)
    _preload_auth(root, cfg, on_log)

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
            dest = run_dir / job.id / "result.yaml"
            if not dest.is_file():
                _write_skipped_result(dest, job)

    extra: list[str] = []
    for alias in aliases:
        wt = paths.req_worktree(root, jira, alias)
        after = _porcelain_lines(_porcelain(wt))
        new = after - baselines.get(alias, set())
        extra.extend(_mutation_paths(new))
    if extra:
        raise TestRejected("worker mutated worktree")
    new_root_png = _root_png_names(root) - root_png_before

    summary = _summarize(cases)
    run_doc = {
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
            }
            for c in cases
        ],
        "summary": summary,
    }
    (run_dir / "result.yaml").write_text(
        yaml.safe_dump(run_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    ping()

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
        case_payloads.append(item)

    ingested = False
    ingest_skipped = None
    if ingest:
        try:
            report = map_qa_result(run_doc, case_payloads)
        except ReportRejected as e:
            raise TestRejected(str(e)) from e
        if report is None:
            ingest_skipped = "blocked" if summary.get("blocked") else "malformed"
        else:
            repos = load_repos(root)
            for f in report.findings:
                if f.repo not in repos:
                    raise TestRejected(
                        f"finding {f.id} repo {f.repo!r} is not a repos.yaml alias"
                    )
            accept_test_report(root, jira, report)
            ingested = True

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
