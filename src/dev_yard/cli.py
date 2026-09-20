from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer
from dev_yard import __version__, paths, service
from dev_yard.config import load_repos
from dev_yard.env import load_env
from dev_yard.gitops import GitError


app = typer.Typer(help="dev-yard: multi-repo requirement worktrees")
repo_app = typer.Typer(help="Register source repos")
req_app = typer.Typer(help="Requirements")
ticket_app = typer.Typer(help="Tickets / child worktrees")
qa_app = typer.Typer(help="QA environment helpers")
app.add_typer(repo_app, name="repo")
app.add_typer(req_app, name="req")
app.add_typer(ticket_app, name="ticket")
app.add_typer(qa_app, name="qa")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dev-yard {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """dev-yard: multi-repo requirement worktrees."""
    pass


def root_opt() -> Path:
    try:
        root = paths.find_root()
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    load_env(root)
    return root


def _die(exc: BaseException) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1)


_READONLY_SQL = frozenset({"select", "show", "desc", "describe", "explain", "with", "table"})
# Heuristic, not a real SQL parser: blocks obvious writes/exfiltration. The DB
# account's own privileges remain the real guard.
_WRITE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge"
    r"|call|do|copy|vacuum|analyze|refresh|into|outfile|load_file|dblink"
    r"|pg_read_file|lo_import|lo_export)\b",
    re.I,
)


def _discover_usernames(dsn: str, sql: str) -> list[str]:
    """Run a read-only query and return the first column as candidate usernames."""
    import shutil
    import subprocess

    if not dsn:
        raise ValueError("qa.yaml envs.<env>.db.url is not configured; cannot run --sql")
    binary = shutil.which("usql")
    if not binary:
        raise ValueError("usql not found; install it to discover accounts")
    text = sql.strip()
    if not text:
        raise ValueError("--sql must not be empty")
    if ";" in text.rstrip(";"):
        raise ValueError("--sql must be a single statement (no `;`)")
    head = text.split(None, 1)[0].lower()
    if head not in _READONLY_SQL:
        raise ValueError("--sql must be read-only (select/show/desc/explain/table)")
    if _WRITE_SQL.search(text):
        raise ValueError("--sql must be read-only (no write keywords)")
    try:
        r = subprocess.run(
            [binary, dsn, "-t", "-A", "-c", text],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError("discovery query timed out") from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip() or str(r.returncode)
        raise ValueError(f"discovery query failed: {err}")
    seen: list[str] = []
    for line in r.stdout.splitlines():
        value = line.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Workspace root"),
) -> None:
    """Create repos.yaml, gitignore entries, reqs/."""
    service.init_yard(directory.resolve())
    typer.echo(f"initialized {directory.resolve()}")


@repo_app.command("add")
def repo_add(
    alias: str,
    url: str,
    default_base: str = "main",
    role: str = "svc",
    path: Optional[str] = None,
    provider: Optional[str] = typer.Option(None, "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    root = root_opt()
    try:
        repo = service.repo_add(
            root,
            alias,
            url,
            default_base,
            role,
            path,
            on_progress=lambda line: typer.echo(line, err=True),
            provider=provider,
            model=model,
        )
    except (ValueError, GitError) as e:
        _die(e)
    typer.echo(f"added {repo.alias} -> {repo.source_path(root)}")


@repo_app.command("list")
def repo_list() -> None:
    root = root_opt()
    repos = load_repos(root)
    if not repos:
        typer.echo("(no repos)")
        return
    for a, r in repos.items():
        pi = f"\t{r.provider}/{r.model}" if r.provider and r.model else ""
        typer.echo(f"{a}\t{r.role}\t{r.default_base}\t{r.url}{pi}")


@repo_app.command("set-model")
def repo_set_model(
    alias: str,
    provider: Optional[str] = typer.Option(None, "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """Set implement provider/model for a repo. Omit both to clear."""
    root = root_opt()
    try:
        repo = service.repo_set_pi(root, alias, provider, model)
    except ValueError as e:
        _die(e)
    if repo.provider and repo.model:
        typer.echo(f"{repo.alias} implement {repo.provider}/{repo.model}")
    else:
        typer.echo(f"{repo.alias} implement (inherit)")


@req_app.command("open")
def req_open(
    target: str = typer.Argument(..., help="Requirement key (e.g. PG-13068, GH-42), URL, or description"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="Custom requirement key/ID (overrides auto-extraction)"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Raw requirement text to write directly"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Local Markdown or text file to import"),
    none: bool = typer.Option(False, "--none", help="Create empty skeleton only (skip remote fetching)"),
    http: bool = typer.Option(False, "--http", help="Crawl Jira/Confluence over HTTP (downloads extra history)"),
    dry_run: bool = False,
    force: bool = typer.Option(False, "--force", help="Re-open even if phase is past open"),
) -> None:
    root = root_opt()
    req_key = (key or "").strip() or service.extract_req_key(target)
    if not req_key:
        _die(ValueError("Could not determine requirement key. Please specify --key."))

    source = "pi"
    payload = None
    if none:
        source = "none"
    elif text is not None:
        source = "text"
        payload = text
    elif file is not None:
        source = "file"
        payload = str(file)
    elif http:
        source = "http"

    try:
        d, warning = service.req_open(
            root,
            req_key,
            source=source,
            target=target,
            payload=payload,
            dry_run=dry_run,
            force=force,
        )
    except (ValueError, FileNotFoundError, RuntimeError, GitError) as e:
        _die(e)
    typer.echo(str(d))
    if warning:
        typer.echo(warning, err=True)


@req_app.command("delete")
def req_delete(jira: str) -> None:
    """Remove this requirement's docs and worktrees. Does not touch Jira or shared glossary/ADR."""
    root = root_opt()
    try:
        service.req_delete(root, jira)
    except (ValueError, FileNotFoundError, GitError) as e:
        _die(e)
    typer.echo(f"deleted {jira}")


@req_app.command("freeze")
def req_freeze(
    jira: str,
    force: bool = typer.Option(
        False, "--force", help="Allow re-freeze after submit-test / done"
    ),
) -> None:
    root = root_opt()
    try:
        created = service.req_freeze(root, jira, force=force)
    except (ValueError, GitError, KeyError) as e:
        _die(e)
    for p in created:
        typer.echo(str(p))


@req_app.command("sync")
def req_sync(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    repos: Optional[list[str]] = typer.Argument(
        None, help="Optional repo aliases (default: all registered)"
    ),
    strategy: str = typer.Option(
        "ff-only",
        "--strategy",
        "-s",
        help="How to update freeze worktrees: ff-only | merge | rebase",
    ),
) -> None:
    """Fetch remotes and update clones / freeze worktrees onto default_base."""
    root = root_opt()
    try:
        results = service.req_sync(
            root,
            jira,
            repos=repos,
            strategy=strategy,
            on_progress=lambda line: typer.echo(line, err=True),
        )
    except (ValueError, FileNotFoundError, GitError) as e:
        _die(e)
    for r in results:
        extra = f" ({r['branch']})" if r.get("branch") else ""
        typer.echo(f"{r['status']} {r['repo']}{extra}")


@req_app.command("push")
def req_push(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    repos: Optional[list[str]] = typer.Argument(None, help="Optional repo aliases to push (default: all)"),
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote name (default: origin)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force push (git push --force)"),
) -> None:
    """Push frozen requirement worktree branches to remote."""
    root = root_opt()
    try:
        results = service.req_push(
            root,
            jira,
            repos=repos,
            remote=remote,
            force=force,
            on_progress=lambda line: typer.echo(line, err=True),
        )
    except (ValueError, FileNotFoundError, GitError) as e:
        _die(e)
    for r in results:
        typer.echo(f"pushed {r['repo']} ({r['branch']}) -> {r['remote']}")



@req_app.command("submit-test")
def req_submit_test(jira: str) -> None:
    """Mark the requirement as submitted for third-party testing."""
    from dev_yard.test_report import ReportRejected, submit_test

    root = root_opt()
    try:
        data = submit_test(root, jira)
    except (ValueError, FileNotFoundError, ReportRejected) as e:
        _die(e)
    typer.echo(f"{jira} phase={data.get('phase')} test={data.get('test', {}).get('status')}")


@req_app.command("accept-test")
def req_accept_test(
    jira: str,
    verdict: str = typer.Option(..., "--verdict", help="passed | failed | blocked"),
    body_file: Optional[Path] = typer.Option(None, "--body-file", help="Optional notes (Markdown)"),
    findings_file: Optional[Path] = typer.Option(
        None, "--findings-file", help="JSON list of bugs (required when failed)"
    ),
    summary: str = typer.Option("", "--summary"),
    source: str = typer.Option("cli", "--source"),
) -> None:
    """Submit QA bugs (or pass this round). Spawns B tickets; does not keep TEST-REPORT.md."""
    from dev_yard.test_report import ReportRejected, accept_test_report, parse_inbound

    root = root_opt()
    try:
        text = body_file.read_text(encoding="utf-8") if body_file else ""
        findings = []
        if findings_file:
            import json

            findings = json.loads(findings_file.read_text(encoding="utf-8"))
        report = parse_inbound(
            {
                "verdict": verdict,
                "body": text,
                "summary": summary,
                "source": source,
                "findings": findings,
            },
            default_source="cli",
        )
        data = accept_test_report(root, jira, report)
    except (ValueError, FileNotFoundError, ReportRejected, OSError) as e:
        _die(e)
    test = data.get("test") or {}
    typer.echo(f"{jira} phase={data.get('phase')} verdict={test.get('latest_verdict')}")


@req_app.command("accounts")
def req_accounts(
    jira: str,
    env: str = typer.Option("", "--env", help="qa.yaml envs.<name>; default active_env"),
    sql: str = typer.Option(
        "", "--sql", help="只读查询，返回用用户名的列（用于发现候选账号）"
    ),
    reset: bool = typer.Option(
        False, "--reset", help="清空本环境已配的需求账号，从零开始收集"
    ),
) -> None:
    """配置本需求要用的账号，写入 .yard-qa/requirements/<JIRA>/accounts.yaml。

    全局 qa.yaml 只留默认账号；需求要用多账号时在这里补，账号随需求变。
    默认在已配账号基础上追加，--reset 才清空重来。
    """
    from dev_yard.qa_config import (
        QaAccount,
        TestRejected,
        load_qa_config,
        load_req_accounts,
        save_req_accounts,
    )

    root = root_opt()
    try:
        cfg = load_qa_config(root, env.strip() or None)
    except (ValueError, FileNotFoundError, TestRejected) as e:
        _die(e)
        return
    typer.echo(f"env: {cfg.active_env}  base_url: {cfg.env.base_url}")
    gacct = cfg.env.accounts.get(cfg.env.auth_default)
    if gacct and gacct.username:
        typer.echo(f"全局默认账号: {cfg.env.auth_default} ({gacct.username})")

    accounts: dict[str, QaAccount] = {}
    default = cfg.env.auth_default or "default"
    if not reset:
        try:
            existing = load_req_accounts(root, jira, cfg.active_env)
        except TestRejected as e:
            _die(e)
            return
        if existing:
            default, accounts = existing
            typer.echo(
                "已有需求账号: " + ", ".join(f"{n}({a.username})" for n, a in accounts.items())
            )
            typer.echo("直接回车可结束；输入新账号名继续追加。")

    candidates: list[str] = []
    if sql:
        try:
            candidates = _discover_usernames(cfg.env.db_url, sql)
        except (ValueError, OSError) as e:
            _die(e)
            return
        typer.echo("候选用户名：")
        for user in candidates:
            typer.echo(f"  - {user}")

    while True:
        name = typer.prompt("账号名（如 admin/buyer）").strip()
        if not name:
            break
        if name in accounts:
            typer.echo(f"账号「{name}」已存在，跳过。", err=True)
            continue
        username = typer.prompt(
            f"  {name} username", default=candidates[0] if candidates else ""
        )
        if gacct and gacct.password and typer.confirm(
            "  与全局默认账号同密码?", default=False
        ):
            # Do not prompt with the secret as a default: click would echo it.
            password = gacct.password
        else:
            password = typer.prompt(f"  {name} password", hide_input=True)
        accounts[name] = QaAccount(
            name=name,
            username=username.strip(),
            password=password,
            state_file=(
                f".yard-qa/requirements/{jira}/auth-{cfg.active_env}-{name}.json"
            ),
        )
        if not typer.confirm("  再加一个账号?", default=False):
            break

    if not accounts:
        typer.echo("未配置任何账号。")
        return
    if default not in accounts:
        default = typer.prompt("本需求默认账号名", default=next(iter(accounts)))
    try:
        path = save_req_accounts(root, jira, cfg.active_env, default, accounts)
    except (ValueError, TestRejected) as e:
        _die(e)
        return
    typer.echo(
        f"写入 {path}（账号: {', '.join(accounts)}，default: {default}）"
    )


@qa_app.command("check-env")
def qa_check_env(
    env: str = typer.Option("", "--env", help="qa.yaml envs.<name>; default active_env"),
    jira: str = typer.Option("", "--jira", help="Requirement key; used to locate freeze worktree"),
) -> None:
    """Parse exec recipe, ping, run a hello script, print the roundtrip."""
    from dev_yard import paths as p
    from dev_yard.qa_config import TestRejected
    from dev_yard.script_exec import ExecUnreachable, check_env

    root = root_opt()
    worktree = None
    key = jira.strip()
    if key:
        req = p.req_dir(root, key)
        wt_root = req / "worktrees"
        if wt_root.is_dir():
            for child in sorted(wt_root.iterdir()):
                if child.is_dir():
                    worktree = child
                    break
    try:
        result = check_env(
            root,
            env_name=env.strip() or None,
            jira=key or None,
            worktree=worktree,
            on_log=lambda line: typer.echo(line.rstrip() if isinstance(line, str) else line),
        )
    except (ValueError, FileNotFoundError, TestRejected, ExecUnreachable, GitError) as e:
        _die(e)
        return
    for step in result.get("steps") or []:
        typer.echo(f"{step.get('status', '?')} {step.get('step')}: {step.get('detail') or ''}".rstrip())
    typer.echo(f"ok env={result.get('env')} use={result.get('use')} site={result.get('site')}")


@req_app.command("test")
def req_test_cmd(
    jira: str,
    env: str = typer.Option(
        "", "--env", help="qa.yaml envs.<name> to run; default active_env"
    ),
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot for design"),
    design_only: bool = typer.Option(False, "--design-only"),
    run_only: bool = typer.Option(False, "--run-only"),
    redesign: bool = typer.Option(False, "--redesign"),
    no_ingest: bool = typer.Option(False, "--no-ingest"),
    resume: bool = typer.Option(
        False, "--resume", help="Continue the latest incomplete run"
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore an incomplete run and start a new one"
    ),
) -> None:
    """Design and run UI cases after submit-test. Ingests into the test slot."""
    from dev_yard.qa import req_test
    from dev_yard.qa_config import TestRejected
    from dev_yard.test_report import ReportRejected

    root = root_opt()
    if resume and fresh:
        _die(ValueError("--resume and --fresh are mutually exclusive"))
        return
    try:
        result = req_test(
            root,
            jira,
            env=env.strip() or None,
            print_mode=print_mode,
            design_only=design_only,
            run_only=run_only,
            redesign=redesign,
            ingest=not no_ingest,
            resume=True if resume else False if fresh else None,
            on_log=lambda line: typer.echo(line.rstrip() if isinstance(line, str) else line),
        )
    except (ValueError, FileNotFoundError, TestRejected, ReportRejected, GitError) as e:
        _die(e)
    if result.get("design_only"):
        typer.echo(f"{jira} design-only cases={result.get('cases')}")
        return
    summary = result.get("summary") or {}
    extra = ""
    if result.get("ingest_skipped"):
        extra = f" ingest=skipped({result['ingest_skipped']})"
    elif result.get("ingested"):
        extra = " ingest=ok"
    typer.echo(
        f"{jira} run={result.get('run_id')} "
        f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} "
        f"blocked={summary.get('blocked', 0)} skipped={summary.get('skipped', 0)}"
        f"{extra}"
    )


@ticket_app.command("start")
def ticket_start(jira: str, ticket_id: str) -> None:
    root = root_opt()
    try:
        p = service.ticket_start(root, jira, ticket_id)
    except (ValueError, GitError, KeyError, FileNotFoundError) as e:
        _die(e)
    typer.echo(str(p))


@ticket_app.command("done")
def ticket_done(jira: str, ticket_id: str) -> None:
    root = root_opt()
    try:
        service.ticket_done(root, jira, ticket_id)
    except (ValueError, GitError, KeyError) as e:
        _die(e)
    typer.echo(f"{ticket_id} merged and child worktree removed")


def _run_registered(name: str, jira: str, dry_run: bool, print_mode: bool) -> None:
    from dev_yard.stages import load_registry

    root = root_opt()
    spec = load_registry(root).get(name)
    if spec is None:
        typer.echo(f"unknown stage {name}; run: dev-yard stages", err=True)
        raise typer.Exit(1)
    try:
        result = service.run_stage(root, spec, jira, dry_run=dry_run, print_mode=print_mode)
    except (FileNotFoundError, GitError, ValueError) as e:
        _die(e)
    if dry_run or print_mode or "restored" in (result.summary or ""):
        typer.echo(result.summary)
    if not result.ok:
        raise typer.Exit(result.exit_code or 1)


@app.command()
def grill(
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Start pi with grill-with-docs for this requirement."""
    _run_registered("grill", jira, dry_run, print_mode)


@app.command()
def spec(
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Start pi with to-spec for this requirement."""
    _run_registered("spec", jira, dry_run, print_mode)


@app.command()
def tickets(
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Start pi with to-tickets for this requirement."""
    _run_registered("tickets", jira, dry_run, print_mode)


_RUN_VIA_DEDICATED = {
    "open": "dev-yard req open",
    "implement": "dev-yard implement",
    "review": "dev-yard review",
    "contract": "dev-yard review --contract",
    "qa-design": "dev-yard req test",
    "qa-run": "dev-yard req test",
    "test": "dev-yard req test",
}


@app.command()
def run(
    stage: str = typer.Argument(
        ...,
        help="Plugin stage, or grill/spec/tickets; open/implement/review/contract use dedicated commands",
    ),
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Run a registry stage (plugin or grill/spec/tickets). Ticket loops use dedicated commands."""
    hint = _RUN_VIA_DEDICATED.get(stage)
    if hint is not None:
        typer.echo(
            f"`dev-yard run {stage}` is not the ticket/open loop; use {hint}",
            err=True,
        )
        raise typer.Exit(2)
    _run_registered(stage, jira, dry_run, print_mode)


@app.command(name="stages")
def stages_cmd() -> None:
    """List all registered stages (builtin + plugins)."""
    from dev_yard.stages import load_registry, plugin_root

    root = root_opt()
    for spec in sorted(load_registry(root).values(), key=lambda s: s.order):
        if spec.builtin or spec.skill_dir is None:
            tag = "builtin"
        else:
            pdir = plugin_root(spec) or spec.skill_dir
            try:
                tag = f"plugin {pdir.relative_to(root)}"
            except ValueError:
                tag = f"plugin {pdir}"
        parts = [spec.name, f"[{tag}]", f"order={spec.order}"]
        if spec.title:
            parts.append(spec.title)
        if spec.description:
            parts.append(" ".join(spec.description.split()))
        typer.echo("\t".join(parts))


@app.command()
def implement(
    jira: str,
    ticket_ids: Optional[list[str]] = typer.Argument(None),
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
    from_contract: bool = typer.Option(
        False,
        "--from-contract",
        help="Spawn/implement contract bug tickets (independent B tickets, DAG-aware)",
    ),
    from_test: bool = typer.Option(
        False,
        "--from-test",
        help="Implement ready test bug tickets (B tickets from 提 bug)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Implement explicit ticket ids even if pending/done",
    ),
) -> None:
    root = root_opt()
    try:
        ran = service.implement(
            root,
            jira,
            ticket_ids,
            dry_run=dry_run,
            print_mode=print_mode,
            from_contract=from_contract,
            from_test=from_test,
            force=force,
        )
    except (ValueError, GitError, KeyError, FileNotFoundError) as e:
        _die(e)
    typer.echo("ran: " + (", ".join(ran) if ran else "(none)"))


@app.command()
def review(
    jira: str,
    ticket_ids: Optional[list[str]] = typer.Argument(None),
    contract: bool = False,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    root = root_opt()
    try:
        ran = service.review(
            root, jira, ticket_ids, contract=contract, dry_run=dry_run, print_mode=print_mode
        )
    except (ValueError, GitError, KeyError, FileNotFoundError) as e:
        _die(e)
    typer.echo("reviewed: " + (", ".join(ran) if ran else "(none)"))


@app.command(name="review-override")
def review_override(
    jira: str,
    ticket_id: Optional[str] = typer.Argument(None, help="Ticket ID (e.g. T1). Omit when overriding contract review."),
    contract: bool = typer.Option(False, "--contract", help="Override contract review instead of a ticket"),
    verdict: str = typer.Option(..., "--verdict", "-v", help="Verdict: 'passed' or 'failed'"),
    summary: Optional[str] = typer.Option(None, "--summary", "-m", help="Review comments / feedback"),
) -> None:
    """Manually override a ticket's or contract review verdict and feedback."""
    root = root_opt()
    if contract or not ticket_id:
        if not contract and not ticket_id:
            typer.echo("Error: Provide a ticket_id or specify --contract", err=True)
            raise typer.Exit(1)
        try:
            updated = service.contract_review_override(
                root, jira, verdict=verdict, summary=summary
            )
            typer.echo(f"Updated contract review: {updated.get('contract_review')}")
        except (ValueError, GitError, KeyError, FileNotFoundError) as e:
            _die(e)
        return

    try:
        updated = service.ticket_review_override(
            root, jira, ticket_id, verdict=verdict, summary=summary
        )
        typer.echo(f"Updated {ticket_id}: state={updated.get('state')}")
    except (ValueError, GitError, KeyError, FileNotFoundError) as e:
        _die(e)


@app.command()
def sync(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    repos: Optional[list[str]] = typer.Argument(
        None, help="Optional repo aliases (default: all registered)"
    ),
    strategy: str = typer.Option(
        "ff-only",
        "--strategy",
        "-s",
        help="How to update freeze worktrees: ff-only | merge | rebase",
    ),
) -> None:
    """Fetch remotes and update clones / freeze worktrees onto default_base."""
    req_sync(jira, repos=repos, strategy=strategy)


@app.command()
def push(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    repos: Optional[list[str]] = typer.Argument(None, help="Optional repo aliases to push (default: all)"),
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote name (default: origin)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force push (git push --force)"),
) -> None:
    """Push frozen requirement worktree branches to remote."""
    root = root_opt()
    try:
        results = service.req_push(
            root,
            jira,
            repos=repos,
            remote=remote,
            force=force,
            on_progress=lambda line: typer.echo(line, err=True),
        )
    except (ValueError, FileNotFoundError, GitError) as e:
        _die(e)
    for r in results:
        typer.echo(f"pushed {r['repo']} ({r['branch']}) -> {r['remote']}")


@app.command()
def status(jira: Optional[str] = typer.Argument(None)) -> None:
    root = root_opt()
    typer.echo(service.status_text(root, jira))


@app.command(name="tdd")
def tdd_cmd(
    mode: Optional[str] = typer.Argument(
        None, help="'on' | 'off' | 'status'. Omit to view current status."
    ),
) -> None:
    """Get or set workspace TDD development and review mode (default: on)."""
    from dev_yard.config import DevSettings, load_dev_settings, save_dev_settings

    root = root_opt()
    settings = load_dev_settings(root)
    if mode is None or mode.strip().lower() == "status":
        status_str = "on" if settings.tdd else "off"
        typer.echo(f"tdd: {status_str}")
        return
    m = mode.strip().lower()
    if m == "on":
        val = True
    elif m == "off":
        val = False
    else:
        _die(ValueError(f"invalid mode {mode!r}; expected 'on' or 'off'"))
        return
    settings.tdd = val
    save_dev_settings(root, settings)
    typer.echo(f"tdd set to {'on' if val else 'off'}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8765, help="Bind port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI in a browser"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Allow binding off loopback (no auth; pi --approve is exposed)",
    ),
) -> None:
    """Local web console: board, docs, and print-mode stages."""
    from dev_yard.web.app import serve

    root = root_opt()
    if allow_remote:
        typer.echo(
            "warning: --allow-remote binds off loopback with no auth; pi jobs are exposed",
            err=True,
        )
    try:
        serve(root, host=host, port=port, open_browser=open_browser, allow_remote=allow_remote)
    except ValueError as e:
        _die(e)
