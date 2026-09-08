from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from dev_yard import paths, service
from dev_yard.config import load_repos


app = typer.Typer(help="dev-yard: multi-repo requirement worktrees")
repo_app = typer.Typer(help="Register source repos")
req_app = typer.Typer(help="Requirements")
ticket_app = typer.Typer(help="Tickets / child worktrees")
app.add_typer(repo_app, name="repo")
app.add_typer(req_app, name="req")
app.add_typer(ticket_app, name="ticket")


def root_opt() -> Path:
    try:
        return paths.find_root()
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
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
    repo = service.repo_add(root, alias, url, default_base, role, path)
    typer.echo(f"added {alias} -> {repo.source_path(root)}")


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
def req_open(jira: str) -> None:
    root = root_opt()
    d, warning = service.req_open(root, jira)
    typer.echo(str(d))
    if warning:
        typer.echo(warning, err=True)


@req_app.command("freeze")
def req_freeze(jira: str) -> None:
    root = root_opt()
    try:
        created = service.req_freeze(root, jira)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    for p in created:
        typer.echo(str(p))


@ticket_app.command("start")
def ticket_start(jira: str, ticket_id: str) -> None:
    root = root_opt()
    p = service.ticket_start(root, jira, ticket_id)
    typer.echo(str(p))


@ticket_app.command("done")
def ticket_done(jira: str, ticket_id: str) -> None:
    root = root_opt()
    service.ticket_done(root, jira, ticket_id)
    typer.echo(f"{ticket_id} merged and child worktree removed")


def _launch(name: str, jira: str, dry_run: bool, print_mode: bool) -> None:
    root = root_opt()
    try:
        result = service.launch_skill(root, name, jira, dry_run=dry_run, print_mode=print_mode)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    if dry_run or print_mode:
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
) -> None:
    root = root_opt()
    ran = service.implement(root, jira, ticket_ids, dry_run=dry_run, print_mode=print_mode)
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
    ran = service.review(
        root, jira, ticket_ids, contract=contract, dry_run=dry_run, print_mode=print_mode
    )
    typer.echo("reviewed: " + (", ".join(ran) if ran else "(none)"))


@app.command()
def status(jira: Optional[str] = typer.Argument(None)) -> None:
    root = root_opt()
    typer.echo(service.status_text(root, jira))
