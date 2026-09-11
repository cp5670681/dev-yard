from __future__ import annotations

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
app.add_typer(repo_app, name="repo")
app.add_typer(req_app, name="req")
app.add_typer(ticket_app, name="ticket")


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
def req_freeze(jira: str) -> None:
    root = root_opt()
    try:
        created = service.req_freeze(root, jira)
    except (ValueError, GitError, KeyError) as e:
        _die(e)
    for p in created:
        typer.echo(str(p))


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
    body_file: Path = typer.Option(..., "--body-file", help="Markdown report file"),
    summary: str = typer.Option("", "--summary"),
    source: str = typer.Option("cli", "--source"),
) -> None:
    """Ingest a test report while phase is testing."""
    from dev_yard.test_report import ReportRejected, accept_test_report, parse_inbound

    root = root_opt()
    try:
        text = body_file.read_text()
        report = parse_inbound(
            {"verdict": verdict, "body": text, "summary": summary, "source": source},
            default_source="cli",
        )
        data = accept_test_report(root, jira, report)
    except (ValueError, FileNotFoundError, ReportRejected, OSError) as e:
        _die(e)
    test = data.get("test") or {}
    typer.echo(f"{jira} phase={data.get('phase')} verdict={test.get('latest_verdict')}")


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


def _launch(name: str, jira: str, dry_run: bool, print_mode: bool) -> None:
    root = root_opt()
    try:
        result = service.launch_skill(root, name, jira, dry_run=dry_run, print_mode=print_mode)
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
    _launch("grill", jira, dry_run, print_mode)


@app.command()
def spec(
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Start pi with to-spec for this requirement."""
    _launch("spec", jira, dry_run, print_mode)


@app.command()
def tickets(
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Start pi with to-tickets for this requirement."""
    _launch("tickets", jira, dry_run, print_mode)


@app.command()
def implement(
    jira: str,
    ticket_ids: Optional[list[str]] = typer.Argument(None),
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
    from_contract: bool = typer.Option(
        False,
        "--from-contract",
        help="Re-implement using STATUS contract_summary (default: last ticket per repo)",
    ),
    from_test: bool = typer.Option(
        False,
        "--from-test",
        help="Re-implement using the latest failed TEST-REPORT.md",
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
def status(jira: Optional[str] = typer.Argument(None)) -> None:
    root = root_opt()
    typer.echo(service.status_text(root, jira))


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
