from __future__ import annotations

from pathlib import Path

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
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """dev-yard: multi-repo requirement worktrees."""


def root_opt() -> Path:
    try:
        root = paths.find_root()
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    load_env(root)
    return root


def _die(exc: BaseException) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1)


def _echo_blocked_hint(summary: dict, jira: str = "") -> None:
    """Show the blocked breakdown and the actionable follow-up."""
    from dev_yard.qa_schedule import format_blocked_kind

    kind = summary.get("blocked_kind") if isinstance(summary, dict) else None
    shown = format_blocked_kind(kind)
    if not shown:
        return
    typer.echo(f"  blocked 分类：{shown}")
    target = (
        f"dev-yard req test {jira} --redesign" if jira
        else "dev-yard req test <JIRA> --redesign"
    )
    if kind.get("case-defect"):
        typer.echo(
            f"  {kind['case-defect']} 条为用例种子缺口（case-defect），"
            f"补种子后重跑：{target}"
        )
    if kind.get("other"):
        typer.echo(
            f"  {kind['other']} 条原因未归类，请人工查看 qa/evidence 下的 result.yaml"
        )


def _echo_verify_hint(view: dict | None) -> None:
    """Show the design-time data-verification verdict, if one is on disk."""
    if not isinstance(view, dict) or not view.get("present"):
        return
    summary = view.get("summary") or {}
    total = summary.get("total")
    state = "过期" if view.get("stale") else "最新"
    typer.echo(
        f"  数据核实（{state}）：passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)}"
        + (f" total={total}" if total is not None else "")
    )
    failed = view.get("failed") or []
    if failed:
        typer.echo(f"  未通过：{', '.join(failed)}（见 qa/design-verify/）")
    exempt = view.get("empty") or []
    if exempt:
        typer.echo(f"  空转豁免（SELECT 1）：{', '.join(exempt)}")


def _echo_uncovered_hint(result: dict) -> None:
    """Advisory: change points no case claims (see meta.yaml `changes`)."""
    uncovered = result.get("uncovered_changes") or []
    if uncovered:
        typer.echo(f"  未覆盖改动点：{', '.join(uncovered)}（无用例 covers）")


def _echo_account_hint(root: Path, jira: str) -> None:
    """Point at discovery only while accounts are still missing."""
    if not paths.qa_accounts_discover_sql(root, jira).is_file():
        return
    from dev_yard.qa_config import load_qa_config

    try:
        accounts = load_qa_config(root, None, jira).env.accounts
    except Exception:  # noqa: BLE001 — advisory hint must never break the command
        return
    if len(accounts) > 1:
        return
    typer.echo(f"  账号发现：dev-yard req accounts {jira} --auto")


def _discover_usernames(dsn: str, sql: str) -> list[str]:
    """Run a read-only query and return the first column as candidate usernames."""
    from dev_yard.qa_accounts import discover

    return [cand.username for cand in discover(dsn, sql)]


def _resolve_discovery_sql(root: Path, jira: str, sql: str) -> str | None:
    """The discovery SQL to run: --sql, else qa/accounts-discover.sql.

    Returns None after printing an error when neither source is available.
    """
    if sql.strip():
        return sql
    sql_path = paths.qa_accounts_discover_sql(root, jira)
    if not sql_path.is_file():
        _die(
            ValueError(
                f"缺少 {sql_path}（涉及权限时由 qa-design 产出只读查询）；"
                "或改用 --sql 直接给查询"
            )
        )
        return None
    try:
        return sql_path.read_text(encoding="utf-8")
    except OSError as e:
        _die(e)
        return None


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
    path: str | None = None,
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    test_branch: str | None = typer.Option(
        None, "--test-branch", help="共享测试分支（提测时 merge 冻结分支到这里）"
    ),
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
            test_branch=test_branch,
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
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
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
    key: str | None = typer.Option(None, "--key", "-k", help="Custom requirement key/ID (overrides auto-extraction)"),
    text: str | None = typer.Option(None, "--text", "-t", help="Raw requirement text to write directly"),
    file: Path | None = typer.Option(None, "--file", "-f", help="Local Markdown or text file to import"),
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


@req_app.command("reset-phase")
def req_reset_phase(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Rewind a requirement to phase=open and tear down its worktrees/branches.

    Docs and assets under reqs/<jira>/ are kept.
    """
    root = root_opt()
    if not yes:
        typer.confirm(
            f"Reset {jira} to phase=open and remove its worktrees/branches?",
            abort=True,
        )
    try:
        data = service.req_reset_phase(root, jira)
    except (ValueError, FileNotFoundError, GitError) as e:
        _die(e)
    typer.echo(f"{jira} phase={data.get('phase')}")


@req_app.command("reset-grill")
def req_reset_grill(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Drop the pending alignment round and reset GRILL.md.

    The next `dev-yard grill` regenerates the frontier from scratch instead of
    replaying a stale `.grill-round.json`. Phase/tickets/contract are kept.
    """
    root = root_opt()
    if not yes:
        typer.confirm(
            f"Reset alignment for {jira} (drop pending round + clear GRILL.md)?",
            abort=True,
        )
    try:
        service.req_reset_grill(root, jira)
    except (FileNotFoundError, OSError) as e:
        _die(e)
    typer.echo(f"{jira} 对齐已重置")


@req_app.command("change")
def req_change_cmd(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)"),
    note: str = typer.Option(..., "--note", "-n", help="变更说明（一句话）"),
    repo: str = typer.Option(
        ..., "--repo", "-r", help="受影响的仓 alias（必须是本需求已冻结的仓）"
    ),
    grill: bool = typer.Option(False, "--grill", help="先跑一轮 grill 澄清"),
    run: bool = typer.Option(False, "--run", help="建票后立即实现这张轻量票"),
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Lightweight requirement-doc change: append change note, update SPEC, add one ticket."""
    root = root_opt()
    try:
        out = service.req_change(
            root,
            jira,
            note,
            repo=repo,
            grill=grill,
            run=run,
            print_mode=print_mode,
            actor="cli",
            on_progress=lambda line: typer.echo(line, err=True),
        )
    except (ValueError, FileNotFoundError, RuntimeError, GitError) as e:
        _die(e)
        return
    typer.echo(f"change {out['change_id']}: +{out['ticket']} ({out['repo']})")
    if out.get("contract_touched"):
        typer.echo("warning: SPEC 契约段被改动，建议重跑契约审查", err=True)
    if not out.get("ran"):
        typer.echo(f"next: dev-yard implement {jira} {out['ticket']}")


@req_app.command("changes")
def req_changes(jira: str = typer.Argument(..., help="Requirement key (e.g. PROJ-101)")) -> None:
    """Print this requirement's lightweight-change log (read-only)."""
    from dev_yard import status as st

    root = root_opt()
    try:
        paths.req_dir(root, jira)
    except ValueError as e:
        _die(e)
        return
    entries = st.changes(st.load(root, jira))
    if not entries:
        typer.echo("(no changes)")
        return
    for c in entries:
        bits = []
        if c.get("repo"):
            bits.append(f"repo={c['repo']}")
        if c.get("ticket"):
            bits.append(f"ticket={c['ticket']}")
        if c.get("contract_touched"):
            bits.append("contract_touched")
        suffix = "  " + " ".join(bits) if bits else ""
        typer.echo(f"{c.get('id')} {c.get('at')} ({c.get('actor')}) {c.get('note')}{suffix}")


@req_app.command("attach")
def req_attach(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PG-12937)"),
    files: list[Path] = typer.Argument(..., help="Local files to attach (e.g. HTML prototype)"),
    name: list[str] | None = typer.Option(
        None, "--name", "-n", help="Override stored name; repeat once per file"
    ),
) -> None:
    """Attach local files (prototypes, docs) under reqs/<JIRA>/uploads/.

    uploads/ is never wiped by `req open`, so attachments survive re-extraction.
    REQUIREMENT.md gets a managed 补充附件 section pointing at them.
    """
    root = root_opt()
    try:
        added = service.req_attach(root, jira, list(files), names=name)
    except (ValueError, FileNotFoundError) as e:
        _die(e)
        return
    for stored in added:
        typer.echo(f"attached {jira}/uploads/{stored}")


@req_app.command("detach")
def req_detach(
    jira: str = typer.Argument(..., help="Requirement key (e.g. PG-12937)"),
    names: list[str] = typer.Argument(..., help="Stored attachment names to remove"),
) -> None:
    """Remove attachments from reqs/<JIRA>/uploads/ and refresh REQUIREMENT.md."""
    root = root_opt()
    try:
        remaining = service.req_detach(root, jira, list(names))
    except (ValueError, FileNotFoundError) as e:
        _die(e)
        return
    typer.echo(f"{jira} uploads: {', '.join(remaining) if remaining else '(none)'}")


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
    repos: list[str] | None = typer.Argument(
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
    repos: list[str] | None = typer.Argument(None, help="Optional repo aliases to push (default: all)"),
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
def req_submit_test(
    jira: str,
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote name"),
    no_resolve: bool = typer.Option(
        False, "--no-resolve", help="Close AI merge-conflict resolution (default: on)"
    ),
    force_all: bool = typer.Option(
        False,
        "--all",
        "--force-resubmit",
        help="Ignore incremental/unchanged checks; integrate every eligible repo",
    ),
) -> None:
    """Merge each repo's freeze branch into its test branch and push, then mark testing."""
    from dev_yard import test_integrate
    from dev_yard.test_report import ReportRejected, submit_test

    root = root_opt()
    ai_resolve = False if no_resolve else None
    try:
        data = submit_test(
            root,
            jira,
            remote=remote,
            ai_resolve=ai_resolve,
            force_all=force_all,
            on_progress=lambda line: typer.echo(line, err=True),
        )
    except (ValueError, FileNotFoundError, ReportRejected) as e:
        _die(e)
    for line in test_integrate.integration_report(data):
        typer.echo(line)
    typer.echo(f"{jira} phase={data.get('phase')} test={data.get('test', {}).get('status')}")


@req_app.command("accept-test")
def req_accept_test(
    jira: str,
    verdict: str = typer.Option(..., "--verdict", help="passed | failed | blocked"),
    body_file: Path | None = typer.Option(None, "--body-file", help="Optional notes (Markdown)"),
    findings_file: Path | None = typer.Option(
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
        "", "--sql", help="只读查询，返回 username[|account_key] 列（发现候选账号）"
    ),
    discover: bool = typer.Option(
        False,
        "--discover",
        help="用 qa/accounts-discover.sql（qa-design 产出）跑只读查询列候选账号",
    ),
    reset: bool = typer.Option(
        False, "--reset", help="清空本环境已配的需求账号，从零开始收集"
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="非交互：发现候选后按 account_key 一键写入本需求账号"
        "（复用全局默认账号密码，不动全局 qa.yaml）",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="清掉本需求账号的缓存登录态强制重登；与 --auto 同用则发现后一并刷新",
    ),
) -> None:
    """配置本需求要用的账号，写入 .yard-qa/requirements/<JIRA>/accounts.yaml。

    全局 qa.yaml 只留默认账号；需求要用多账号时在这里补，账号随需求变。
    默认在已配账号基础上追加，--reset 才清空重来。
    --auto 发现即写入（权限会漂移，重跑即刷新）；--refresh 清缓存登录态。
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

    if discover and sql:
        _die(ValueError("--discover and --sql are mutually exclusive"))
        return

    if refresh and not auto:
        from dev_yard import qa_accounts

        dropped = qa_accounts.invalidate_all(root, jira, cfg.active_env, cfg)
        if dropped:
            typer.echo(f"已清缓存登录态：{', '.join(dropped)}")
        else:
            typer.echo("无本需求账号或无可清缓存。")
        return

    if auto:
        from dev_yard import qa_accounts

        query = _resolve_discovery_sql(root, jira, sql)
        if query is None:
            return
        try:
            candidates = qa_accounts.discover(cfg.env.db_url, query)
        except (ValueError, OSError) as e:
            _die(e)
            return
        if not candidates:
            _die(TestRejected("发现 0 个候选账号；检查 accounts-discover.sql 与被测权限点"))
            return
        typer.echo("发现候选账号（account_key: username）：")
        for cand in candidates:
            typer.echo(f"  - {cand.key or 'auto'}: {cand.username}")
        try:
            result = qa_accounts.autofill(
                root, jira, cfg.active_env, cfg, candidates, force_relogin=refresh
            )
        except (ValueError, TestRejected) as e:
            _die(e)
            return
        typer.echo(f"写入 {result.path}（default: {result.default}）")
        typer.echo(
            "  账号：" + "、".join(f"{n}({u})" for n, u in result.accounts.items())
        )
        if result.added:
            typer.echo(f"  新增：{', '.join(result.added)}")
        if result.changed:
            typer.echo(f"  改绑：{', '.join(result.changed)}（缓存登录态已清）")
        if refresh:
            typer.echo("  已清本需求账号缓存登录态，下次运行会重新登录。")
        typer.echo(
            "  用例引用：frontmatter 写 account: <account_key>（不要动全局账号）"
        )
        return

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
    if discover or sql:
        query = _resolve_discovery_sql(root, jira, sql)
        if query is None:
            return
        try:
            candidates = _discover_usernames(cfg.env.db_url, query)
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


@qa_app.command("report")
def qa_report_cmd(
    jira: str,
    run: str = typer.Option("", "--run", help="run id; default latest"),
) -> None:
    """Render a markdown test report from the run's result.yaml/progress.yaml."""
    from dev_yard.qa_config import TestRejected
    from dev_yard.qa_doc import render_qa_report

    root = root_opt()
    try:
        path, summary = render_qa_report(root, jira, run.strip() or None)
    except (ValueError, FileNotFoundError, TestRejected) as e:
        _die(e)
        return
    typer.echo(f"{jira} 报告: {path}")
    typer.echo(
        f"  total={summary.get('total', 0)} passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} blocked={summary.get('blocked', 0)} "
        f"skipped={summary.get('skipped', 0)}"
    )
    _echo_blocked_hint(summary, jira)


@qa_app.command("status")
def qa_status_cmd(jira: str) -> None:
    """Show the QA state machine: current phase, triage and next step."""
    from dev_yard.qa_state import status_payload

    root = root_opt()
    payload = status_payload(root, jira)
    review = payload.get("review") or {}
    triage = payload.get("triage") or {}
    typer.echo(f"{jira} phase={payload.get('phase')}")
    typer.echo(f"  review={review.get('status') or '?'} approved={bool(review.get('approved'))}")
    pending = triage.get("pending") or []
    recycled = triage.get("auto_recycled") or []
    if pending:
        typer.echo(f"  待判定失败（下 bug 或 --redesign）: {', '.join(pending)}")
    if recycled:
        typer.echo(f"  自动回流用例缺陷: {', '.join(recycled)}")
    pools = payload.get("pools") or {}
    bad = [k for k, v in pools.items() if (v or {}).get("state") == "quarantined"]
    if bad:
        typer.echo(f"  隔离模型池: {', '.join(bad)}")
    if payload.get("last_run_id"):
        typer.echo(f"  last_run={payload['last_run_id']}")
    typer.echo(f"  下一步: {payload.get('next') or ''}")


@qa_app.command("logs")
def qa_logs_cmd(
    jira: str,
    request_id: str = typer.Option("", "--request-id", help="响应头 x-request-id"),
    grep: str = typer.Option("", "--grep", help="没有 request-id 时按关键字过滤"),
    tail: int = typer.Option(2000, "--tail", help="tail 行数上限"),
    env: str = typer.Option("", "--env", help="qa.yaml envs.<name>; default active_env"),
) -> None:
    """Read-only deployed-env log lookup for 5xx diagnosis (jms-k8s envs)."""
    from dev_yard.qa_config import TestRejected
    from dev_yard.script_exec import ExecUnreachable, fetch_logs

    root = root_opt()
    try:
        use, text = fetch_logs(
            root,
            env_name=env.strip() or None,
            jira=jira,
            request_id=request_id,
            grep=grep,
            tail=tail,
            on_log=lambda line: typer.echo(str(line).rstrip()),
        )
    except (ValueError, FileNotFoundError, TestRejected, ExecUnreachable, GitError) as e:
        _die(e)
        return
    if not text.strip():
        typer.echo(f"({use}) 无匹配日志")
        return
    typer.echo(text.rstrip())


@req_app.command("triage")
def req_triage_cmd(
    jira: str,
    product: bool = typer.Option(
        True,
        "--product/--all",
        help="只对宿主判定为 product 的失败建票；--all 连未分类一起建",
    ),
) -> None:
    """批量给待判定失败用例下 bug（M6b）。"""
    root = root_opt()
    try:
        out = service.triage_qa_cases(root, jira, product_only=product)
    except (ValueError, FileNotFoundError) as e:
        _die(e)
        return
    filed = out.get("filed") or {}
    skipped = out.get("skipped") or []
    if not filed and not skipped:
        typer.echo(f"{jira} 没有待判定失败用例")
        return
    for cid, tid in filed.items():
        typer.echo(f"  {cid} -> {tid}")
    if skipped:
        typer.echo(f"  跳过（非 product / 无结果）：{', '.join(skipped)}")
    typer.echo(f"{jira} 已建 {len(filed)} 张，跳过 {len(skipped)} 条")


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
    approve: bool = typer.Option(
        False,
        "--approve",
        help="人工审核通过当前用例（只标记，不执行；执行用 --run-only 或直接 req test）",
    ),
    feedback: str = typer.Option(
        "", "--feedback", help="审核意见；配合 --redesign 让 qa-design 重做用例"
    ),
    feedback_file: str = typer.Option(
        "", "--feedback-file", help="从文件读取审核意见（与 --feedback 互斥）"
    ),
    no_ingest: bool = typer.Option(False, "--no-ingest"),
    resume: bool = typer.Option(
        False, "--resume", help="Continue the latest incomplete run"
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore an incomplete run and start a new one"
    ),
    full: bool = typer.Option(
        False, "--full", help="强制全量重跑（新 run），不做 M10 增量重跑"
    ),
    rerun_case: list[str] = typer.Option(
        [],
        "--rerun-case",
        help="Re-run only these case ids (repeatable); amends the run they belong to",
    ),
    no_wait: bool = typer.Option(
        False,
        "--no-wait",
        help="同 env 已有 run 时立即拒绝，不排队等待（run.env_wait_timeout）",
    ),
    no_verify: bool = typer.Option(
        False, "--no-verify", help="跳过设计期数据核实（verify.sql）"
    ),
    verify_only: bool = typer.Option(
        False, "--verify-only", help="只跑设计期数据核实并落盘，不设计、不执行"
    ),
    allow_unverified: bool = typer.Option(
        False,
        "--allow-unverified",
        help="显式越权：数据核实未通过也允许 --approve/执行（未通过用例会被跳过）",
    ),
    unsafe_skip_review: bool = typer.Option(
        False,
        "--unsafe-skip-review",
        help="显式越权：允许 --run-only 在用例未审核时执行",
    ),
) -> None:
    """Design and run UI cases after submit-test. Ingests into the test slot."""
    from dev_yard.qa import req_test
    from dev_yard.qa_config import TestRejected
    from dev_yard.test_report import ReportRejected

    root = root_opt()
    if resume and (fresh or full):
        _die(ValueError("--resume and --fresh/--full are mutually exclusive"))
        return
    if verify_only and no_verify:
        _die(ValueError("--verify-only and --no-verify are mutually exclusive"))
        return
    if rerun_case and (
        resume
        or fresh
        or full
        or design_only
        or run_only
        or redesign
        or approve
        or feedback
        or feedback_file
        or verify_only
    ):
        _die(
            ValueError(
                "--rerun-case cannot be combined with "
                "--resume/--fresh/--design-only/--run-only/--redesign/--approve"
                "/--feedback/--verify-only"
            )
        )
        return
    if feedback and feedback_file:
        _die(ValueError("--feedback and --feedback-file are mutually exclusive"))
        return
    text = feedback
    if feedback_file:
        try:
            text = Path(feedback_file).read_text(encoding="utf-8")
        except OSError as e:
            _die(e)
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
            approve=approve,
            feedback=text or None,
            ingest=not no_ingest,
            resume=True if resume else False if (fresh or full) else None,
            rerun_cases=rerun_case or None,
            verify=False if no_verify else None,
            verify_only=verify_only,
            allow_unverified=allow_unverified,
            unsafe_skip_review=unsafe_skip_review,
            no_wait=no_wait,
            on_log=lambda line: typer.echo(line.rstrip() if isinstance(line, str) else line),
        )
    except (ValueError, FileNotFoundError, TestRejected, ReportRejected, GitError) as e:
        _die(e)
    if result.get("awaiting_review"):
        oq = result.get("open_questions") or {}
        q = result.get("questions") or 0
        if q:
            qline = f"\n  OPEN-QUESTIONS: {q}（见 qa/OPEN-QUESTIONS.md）"
        elif oq.get("error"):
            qline = "\n  OPEN-QUESTIONS: 读取失败（qa/OPEN-QUESTIONS.md 存在但不可读）"
        elif oq.get("exists"):
            qline = "\n  OPEN-QUESTIONS: 0（已产出空文件，无待澄清）"
        else:
            qline = "\n  OPEN-QUESTIONS: 未产出（qa/OPEN-QUESTIONS.md 缺失）"
        typer.echo(
            f"{jira} 用例待审核 cases={result.get('cases')}（{result.get('reason') or ''}）{qline}"
        )
        _echo_verify_hint(result.get("verify"))
        _echo_uncovered_hint(result)
        _echo_account_hint(root, jira)
        typer.echo(
            f"  通过（只标记）：dev-yard req test {jira} --approve\n"
            f"  通过后执行：dev-yard req test {jira} --run-only\n"
            f"  打回重做：dev-yard req test {jira} --redesign --feedback-file <path>"
        )
        return
    if result.get("approved"):
        review = result.get("review") or {}
        typer.echo(
            f"{jira} 用例已审核通过 cases={result.get('cases')} "
            f"review={review.get('status') or '?'}"
        )
        typer.echo(f"  执行：dev-yard req test {jira} --run-only")
        return
    if result.get("verify_only"):
        typer.echo(f"{jira} verify-only cases={result.get('cases')}")
        _echo_verify_hint(result.get("verify"))
        return
    if result.get("design_only"):
        review = result.get("review") or {}
        oq = result.get("open_questions") or {}
        q = result.get("questions") or 0
        if q:
            qline = f" OPEN-QUESTIONS={q}"
        elif oq.get("error"):
            qline = " OPEN-QUESTIONS=(读取失败)"
        elif oq.get("exists"):
            qline = " OPEN-QUESTIONS=0(空文件)"
        else:
            qline = " OPEN-QUESTIONS=(缺失)"
        typer.echo(
            f"{jira} design-only cases={result.get('cases')} "
            f"review={review.get('status') or '?'}{qline}"
        )
        _echo_verify_hint(result.get("verify"))
        _echo_uncovered_hint(result)
        _echo_account_hint(root, jira)
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
    _echo_blocked_hint(summary, jira)
    if result.get("run_id"):
        typer.echo(f"  报告：dev-yard qa report {jira}")


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
    ticket_ids: list[str] | None = typer.Argument(None),
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
    ticket_ids: list[str] | None = typer.Argument(None),
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
    ticket_id: str | None = typer.Argument(None, help="Ticket ID (e.g. T1). Omit when overriding contract review."),
    contract: bool = typer.Option(False, "--contract", help="Override contract review instead of a ticket"),
    verdict: str = typer.Option(..., "--verdict", "-v", help="Verdict: 'passed' or 'failed'"),
    summary: str | None = typer.Option(None, "--summary", "-m", help="Review comments / feedback"),
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
    repos: list[str] | None = typer.Argument(
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
    repos: list[str] | None = typer.Argument(None, help="Optional repo aliases to push (default: all)"),
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
def status(jira: str | None = typer.Argument(None)) -> None:
    root = root_opt()
    typer.echo(service.status_text(root, jira))


@app.command(name="tdd")
def tdd_cmd(
    mode: str | None = typer.Argument(
        None, help="'on' | 'off' | 'status'. Omit to view current status."
    ),
) -> None:
    """Get or set workspace TDD development and review mode (default: on)."""
    from dev_yard.config import load_dev_settings, save_dev_settings

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
