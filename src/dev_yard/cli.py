from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from dev_yard import paths, service
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
        typer.echo(f"{a}\t{r.role}\t{r.default_base}\t{r.url}")


@req_app.command("open")
def req_open(
    jira: str,
    http: bool = typer.Option(False, "--http", help="Crawl Jira/Confluence over HTTP (downloads extra history)"),
    dry_run: bool = False,
    force: bool = typer.Option(False, "--force", help="Re-open even if phase is past open"),
) -> None:
    root = root_opt()
    source = "http" if http else "pi"
    try:
        d, warning = service.req_open(root, jira, source=source, dry_run=dry_run, force=force)
    except (ValueError, FileNotFoundError, RuntimeError, GitError) as e:
        _die(e)
    typer.echo(str(d))
    if warning:
        typer.echo(warning, err=True)


@req_app.command("freeze")
def req_freeze(jira: str) -> None:
    root = root_opt()
    try:
        created = service.req_freeze(root, jira)
    except (ValueError, GitError, KeyError) as e:
        _die(e)
    for p in created:
        typer.echo(str(p))


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
