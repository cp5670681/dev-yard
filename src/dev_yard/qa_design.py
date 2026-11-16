"""Design context, prompts, and lint."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from dev_yard import attachments, gitops, paths, qa_design_refs
from dev_yard import status as st
from dev_yard.config import load_repos, resolve_freeze_branch
from dev_yard.qa_cases import (
    _involved_aliases,
    _meta_routes,
    discover_cases,
    uncovered_changes,
)
from dev_yard.qa_config import QaConfig, redact_url
from dev_yard.qa_review import cases_fingerprint
from dev_yard.qa_schedule import CaseJob, now_iso
from dev_yard.runners import JobCancelled, Runner, RunResult
from dev_yard.skillbind import session_prompt_for
from dev_yard.stages import load_registry

LogFn = Callable[[str], None]
CancelCheck = Callable[[], bool]


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
        segments = [f"branch {branch}", f"base {base}", f"role {role}"]
        if repo and repo.note:
            segments.append(f"note {repo.note}")
        if repo and repo.databases:
            segments.append("databases " + ",".join(repo.databases))
        if repo and repo.exec:
            segments.append("exec " + repo.exec)
        # The exact ref to diff against. The worktree's own local `base` may be
        # stale (fetch updates origin/<base> only), and an imported requirement
        # may fork from an older/other base; both are answered by freeze_base.
        if (wt / ".git").exists():
            saved = data.get("base_shas")
            saved = saved.get(alias) if isinstance(saved, dict) else None
            diff_base = gitops.freeze_base(wt, base, saved)
            if diff_base:
                segments.append(f"diff_base {diff_base}")
        lines.append(f"- {alias}: {wt.resolve()}  (" + ", ".join(segments) + ")")
    env = cfg.env
    routes = _meta_routes(qa)
    headed = "true" if cfg.headed else "false"
    lines += [
        "",
        "## Environment",
        "",
        f"- env: {cfg.active_env}",
        f"- available envs: {', '.join(cfg.env_names) or cfg.active_env}",
        f"- base_url: {redact_url(env.base_url)}（缺省 origin；用例用仓 exec site 的 origin）",
        f"- origins: {_origins_line(env)}",
        f"- browser: {cfg.browser.channel} headed={headed}",
        f"- account.default: {env.auth_default or '(none)'}",
        f"- concurrency: {cfg.total_concurrency} (state-save is only allowed when this is 1)",
        f"- db: {_db_context_line(env)}",
        f"- script.runner: {env.script_runner or '(sql only)'}",
        f"- exec.use: {getattr(env.exec_cfg, 'use', None) or 'local'}",
        f"- exec.site: {getattr(env.exec_cfg, 'site', None) or 'local'}",
        f"- exec.default: {getattr(env.exec_cfg, 'exec_default', None) or '(unnamed)'}",
        f"- exec.sites: {_exec_sites_line(env)}",
        "",
        "This run uses the env above. Each case opens **that case's site origin** "
        "(repo → repos.yaml exec → exec.sites.base_url); do not paste another "
        "site's path onto the env default host.",
        "One case, one origin. A flow that crosses origins is several cases with "
        "`depends_on`. `driver: host` does not open a browser: the host runs "
        "setup, verify.sql, and cleanup on that case's catalog and exec site. "
        "Setup paths stay inside `qa/cases/` and may be shared (`../setup.rb`). "
        "Page steps carry the concrete ids you found with `dev-yard qa sql`.",
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
        "## 宿主通道（agent 直接用，DSN/配方不进 prompt）",
        "",
        f"- 查数据 / 跑 DB 断言：`dev-yard qa sql {jira} -c \"SELECT ...\"`"
        "（或 `-f x.sql`；`--json` 取第一行；`--db <catalog>` 选库；单条只读）",
        f"- 造数 / 清理 / 应用内自查：`dev-yard qa exec {jira} <script> --case <case-id>"
        " --kind setup|cleanup`",
        f"- 5xx 查日志：`dev-yard qa logs {jira} --request-id <x-request-id>`",
        "",
        "数据就绪由执行方自己负责：先查库里有没有能用的行，没有才造数，造完再查一遍。"
        "宿主不再在设计期预证数据，也不会因为自检失败回灌或挡住执行。",
        "",
        "Setup/cleanup scripts: single file; stdin + QA_ENV/QA_JIRA/"
        "QA_CASE_ID/QA_SCRIPT_KIND; stdout is the only channel back; state lives in "
        "the DB, never on the execution host's disk; do not assume two runs land on "
        "the same replica; do not read ARGV.",
        "",
        "## Database",
        "",
        "列名/表名自己查：`dev-yard qa sql <JIRA> -c \"SELECT * FROM information_schema."
        "columns WHERE table_name='<表>'\"`，或读 worktree 里的 `db/schema.rb` / "
        "`db/structure.sql` / `prisma/schema.prisma`。不要猜列名，不要把代码常量"
        "（NEED_RENOVATION_TYPE 等）写成表字段，不要编造主键 id=1001。"
        "非默认库在用例 frontmatter 写 `data.db: <catalog>`。",
        "",
    ]
    lines += ["", "## Routes", ""]
    if routes:
        for name, route in routes.items():
            lines.append(
                f"- {name}: {route}  （拼到该用例 site 的 origin 上，不要用缺省 host 硬套）"
            )
    else:
        lines.append(
            "(none in meta.yaml; read frontend route code and join with the case site origin)"
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
            lines.append(f"- {name}: username={a.username or '?'} state_file={state}{default}")
    else:
        lines.append("(none configured; cases must not use `account`)")
    lines += [
        "",
        "A case frontmatter `account:` picks one of the above; no `account` uses "
        "the default. Login state is per account × origin (non-default sites get "
        "`-{site}` on the state_file stem). Load the file for **this case's site**. "
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


def _exec_sites_line(env: Any) -> str:
    sites = list(getattr(getattr(env, "exec_cfg", None), "sites", ()) or ())
    if not sites:
        runner = getattr(getattr(env, "exec_cfg", None), "runner", "") or env.script_runner
        return f"(single) runner={runner or '(sql only)'}"
    parts = [
        f"{s.name}{'*' if s.name == env.exec_cfg.exec_default else ''}={s.runner}" for s in sites
    ]
    return ", ".join(parts) + "（仓 repos.yaml exec 点名；* = 默认）"


def _origins_line(env: Any) -> str:
    from dev_yard.exec_cfg import origin_for_site

    spec = getattr(env, "exec_cfg", None)
    sites = list(getattr(spec, "sites", ()) or ())
    if not sites:
        return redact_url(env.base_url)
    parts = []
    for s in sites:
        mark = "*" if s.name == spec.exec_default else ""
        url = origin_for_site(env, s.name)
        parts.append(f"{s.name}{mark}={redact_url(url)}")
    return ", ".join(parts) + "（* = 默认）"


def _db_context_line(env: Any) -> str:
    catalogs = list(getattr(env, "db_catalogs", ()) or ())
    if not catalogs:
        return "not configured (qa.yaml db.catalogs / db.url)"
    parts = [f"{c.name}{'*' if c.name == env.db_default else ''}" for c in catalogs]
    return f"{', '.join(parts)} (default {env.db_default}; case `data.db` selects one; * = default)"


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
    saved_bases = st.load(root, jira).get("base_shas")
    saved_bases = saved_bases if isinstance(saved_bases, dict) else {}
    for alias in _involved_aliases(root, jira):
        if len(text) >= _PERMISSION_SCAN_MAX:
            break
        wt = paths.req_worktree(root, jira, alias)
        repo = repos.get(alias)
        if not wt.is_dir() or repo is None:
            continue
        try:
            base = gitops.freeze_base(wt, repo.default_base, saved_bases.get(alias))
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


def lint_cases(root: Path, jira: str, cfg: QaConfig, cases: list[CaseJob]) -> list[str]:
    """Static contract checks a case must pass before review/approve (M7).

    Catches what used to only blow up mid-run: a missing/invalid repo, an
    unconfigured account, `data.writes` without `data.identity`, a case that
    declares data prerequisites without a `data.verify` (M7), and a change
    point no case covers. Returns human-readable problems (empty = clean).
    """
    problems: list[str] = []
    repos_map = load_repos(root)
    repos = set(repos_map)
    for job in cases:
        if not job.repo:
            problems.append(f"{job.id}: 缺少 repo")
        elif repos and job.repo not in repos:
            problems.append(f"{job.id}: repo {job.repo!r} 不是 repos.yaml 别名")
        if job.account and job.account not in cfg.env.accounts:
            problems.append(f"{job.id}: account {job.account!r} 未配置")
        if job.writes and not job.identity:
            problems.append(f"{job.id}: data.writes 需要配套 data.identity")
        if job.db:
            cat = cfg.env.catalog(job.db)
            if cat is None:
                names = ", ".join(c.name for c in cfg.env.db_catalogs) or "(none)"
                problems.append(f"{job.id}: data.db {job.db!r} 不是 qa.yaml catalogs（{names}）")
            else:
                repo = repos_map.get(job.repo)
                allowed = list(repo.databases) if repo and repo.databases else []
                if allowed and job.db not in allowed:
                    problems.append(
                        f"{job.id}: data.db {job.db!r} 不在仓 {job.repo} 声明的 "
                        f"databases（{', '.join(allowed)}）"
                    )
        repo = repos_map.get(job.repo)
        site = (getattr(repo, "exec", "") or "").strip() if repo else ""
        site_names = [
            s.name for s in getattr(getattr(cfg.env, "exec_cfg", None), "sites", ()) or ()
        ]
        if site and site_names and site not in site_names:
            problems.append(
                f"{job.id}: 仓 {job.repo} exec {site!r} 不是 qa.yaml exec.sites"
                f"（{', '.join(site_names)}）"
            )
    for cid in uncovered_changes(paths.qa_dir(root, jira), cases):
        problems.append(f"改动点 {cid} 未被任何用例 covers")
    return problems


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
            "Diff each worktree with `git diff <diff_base>...HEAD` "
            "(diff_base from context.md; fall back to <default_base>). "
            "Empty diff: stop and say so.\n"
            "Do not git checkout, commit, push, or switch.\n"
            "Do not interview interactively; record uncertainties in "
            "qa/OPEN-QUESTIONS.md instead.\n"
            "Honor context.md Notes. Cover permission branches with real accounts, "
            "or write 'not covered (missing account X)' explicitly.\n"
            "When permissions are involved, discover accounts from the backend "
            "permission code and write the read-only discovery query to "
            "qa/accounts-discover.sql (username|account_key; host runs it via "
            "`req accounts --auto`). "
            "In Rails projects with right_job_title_roles, permissions link to Right::JobTitle "
            "via employees.position (employee position id = roleable_id). "
            "Crucially, candidate accounts must also possess the target module's base viewing/editing role "
            "(e.g. project_looker for project details, project_news_user/publisher for resource/supplemental news) "
            "so the page does not 403/404 on load; never pick an unrelated sales/front-desk role for internal editorial modules. "
            "Frontend route params in case steps (e.g. /#/.../:id) must match the real entity ID required by the router "
            "(e.g. check router/view code whether :id is project_news_id vs additional_news_id) to avoid 10003/404 redirects.\n"
            "The host runs that query at the end of design. An account_key with "
            "no row is a defect of the query: rewrite it from the permission "
            "code. If the grant is not in a database the host can query, do not "
            "invent a join; record it in OPEN-QUESTIONS and leave the case "
            "not covered.\n"
            "Data is self-served at run time: you may query the database with "
            "`dev-yard qa sql <JIRA> -c \"SELECT ...\"` (single read-only statement) "
            "and run a data script with `dev-yard qa exec <JIRA> <script> --case <id> "
            "--kind setup`. Use them to find real values instead of guessing columns, "
            "constants, or primary keys.\n"
            "Prefer a row that already exists. Write the row's business condition into "
            "`data.verify` (single read-only SELECT, >=1 row = pass) so a reviewer can "
            "see what the case relies on; `driver: host` cases are judged by exactly "
            "that query. Page steps may name concrete ids you found; do not invent a "
            "marker string to justify an INSERT. Add `data.setup` only when the state "
            "cannot already exist, read every belongs_to without `optional: true` and "
            "every presence validation on that model, set them all in one script, and "
            "write a matching cleanup. A setup path may be relative inside qa/cases/ "
            "(for example `../setup.rb`) so several cases share one factory.\n"
            "Never confuse a database surrogate primary key (auto-increment `id`) with "
            "a business code (such as `cutoff_id` YYYYMS, order number, task code). "
            "Do not alias `c.id AS <business_code>` and do not compare `id < 202681`.\n"
            "When a feature is scoped to a role, department, or team (闪讯/flash, audit, "
            "sales), the row you pick must satisfy that scope: include the role/team/"
            "permission filter, never an unrestricted `WHERE e.active = 1` and never a "
            "hardcoded default login. The tested account must also hold the page's base "
            "access role, or the page 403s on load.\n"
            "When a case asserts optional child entities (notes, attachments, history "
            "lists), the chosen row must actually have them; assert on a row whose "
            "children exist.\n"
            "UI radio options, tabs, and buttons MUST be verified against component "
            "templates; never invent conceptual labels like '合计'/'审核'.\n"
            "When setup mutates a shared row, declare `data.writes` (list of "
            "`table.column`) and `data.identity` (one read-only SELECT returning that "
            "row's id). Two cases must not write the same column of the same row. "
            "Read-only cases omit `writes`; sharing a row to read it is fine.\n"
            "A case with no page sets `driver: host`: the host runs setup, the "
            "`data.verify` query, and cleanup, and does not open a browser.\n"
            "Do not CREATE TABLE _qa_* in the business catalog.\n"
            "Enumerate ALL required fields from the target form's validators "
            "(:rules/required/custom), not only the one an error message names."
        )
    return (
        "You are executing ONE UI test case. Do not repair product code.\n"
        f"Write only under {qa}. Do not change any worktree file.\n"
        "Do not git checkout, commit, push, switch, or deploy.\n"
        "Do not spawn other cases. Do not change case expected values to go green.\n"
        "URLs come from this case's site origin (context.md origins / "
        "$YARD_QA_BASE_URL) + Routes or frontend route code. "
        "Do not guess hosts or reuse another site's origin. "
        "Hash routers need `#/` in the path.\n"
        "Authentication: load state_file if available. If unauthenticated or redirected to login, "
        "inspect the page dynamically with snapshot, fill credentials, submit, and save state.\n"
        "Host already ran data.setup if the case has one; do not re-run it. "
        "Host will run cleanup after you finish.\n"
        "You may query the database yourself with "
        "`dev-yard qa sql <JIRA> -c \"SELECT ...\"` (single read-only statement) and "
        "run a data script with `dev-yard qa exec <JIRA> <script> --case <id> "
        "--kind setup|cleanup`. If the data a step needs is missing, query first, "
        "then create it, then query again — do not guess ids or report a data gap "
        "you could have filled.\n"
        "Cases with `driver: host` are not sent to you.\n"
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


def _is_account_gap(problem: str) -> bool:
    """An unconfigured `account:` is a coverage gap, not a design defect."""
    return ": account " in problem and "未配置" in problem


def _design_prompt(
    root: Path,
    jira: str,
    cfg: QaConfig,
    feedback: str | None = None,
    discover_fix: bool = False,
) -> str:
    spec = load_registry(root)["qa-design"]
    if discover_fix and feedback and feedback.strip():
        extra = (
            "发现查询修补。这不是重新设计。\n"
            "只改 qa/accounts-discover.sql。\n"
            "可以读 worktree 里判定权限的代码，确认授权存在哪张表或哪个外部系统。\n"
            "查询必须是单条只读语句，第一列 username，第二列 account_key，"
            "下面每个 key 都要有一行。"
            "若权限表关联职位（如 right_job_title_roles 且 roleable_type 为 Right::JobTitle），"
            "注意 employees 表中职位字段通常为 position，JOIN employees.position::text ~ '^[0-9]+' AND employees.position::numeric::bigint = jtr.roleable_id。"
            "特别注意：测试账号除目标权限外，必须同时具备被测页面的基础业务访问权限（例如看项目详情需具备基础查看角色如 project_looker，看补充资讯/资源需具备对应的资源使用者/发布者角色），"
            "避免挑选纯销售等无模块访问权限的账号导致页面直接 403/404。\n"
            "授权不在宿主能查的库里时，不要编 JOIN。"
            "把缺口写进 qa/OPEN-QUESTIONS.md，对应用例保持未覆盖，不要改步骤和预期。\n"
            "不要 diff，不要读 dev-yard 的 src/。\n\n" + feedback.strip()
        )
        return session_prompt_for(spec, root, jira, extra=extra)
    extra = _duties("design", jira) + "\n\n" + _context_block(root, jira, cfg)
    if feedback and feedback.strip():
        extra += (
            "\n\n# 人工审核意见（必须据此修订用例）\n\n"
            + feedback.strip()
            + "\n\n在保留仍然成立的用例的前提下，按上述意见修改 qa/cases/ 下的用例："
            "被指出缺失的覆盖补上，被指出错误或多余的改写或删除。"
            "不要为迎合意见而放宽预期；实现与需求不符时仍按需求口径写并标注「需求偏差」。"
        )
    extra += qa_design_refs.prompt_block(paths.qa_dir(root, jira))
    return session_prompt_for(spec, root, jira, extra=extra)


def _context_block(root: Path, jira: str, cfg: QaConfig) -> str:
    qa = paths.qa_dir(root, jira)
    ctx = qa / "context.md"
    text = ctx.read_text(encoding="utf-8") if ctx.is_file() else ""
    return f"qa/context.md:\n{text}".strip()


REPAIR_TOOLS = ("read", "edit", "write")


def _start_repair(
    runner: Runner,
    prompt: str,
    root: Path,
    jira: str,
    *,
    on_log: LogFn | None,
) -> RunResult:
    """One repair pi. Tools are read/edit/write when the runner allows it."""
    qa = paths.qa_dir(root, jira)
    previous_tools = getattr(runner, "tool_override", None)
    previous_hook = getattr(runner, "on_tool", None)
    previous_note = getattr(runner, "on_note", None)
    if hasattr(runner, "tool_override"):
        runner.tool_override = REPAIR_TOOLS

    def _hook(brief: str) -> None:
        if on_log is not None and brief:
            on_log(f"修补 {brief}\n")

    def _note(text: str) -> None:
        if on_log is not None and text:
            on_log(text if text.endswith("\n") else text + "\n")

    if hasattr(runner, "on_tool"):
        runner.on_tool = _hook
    if hasattr(runner, "on_note"):
        runner.on_note = _note
    try:
        return runner.start(
            prompt,
            root,
            attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]),
        )
    finally:
        if hasattr(runner, "tool_override"):
            runner.tool_override = previous_tools
        if hasattr(runner, "on_tool"):
            runner.on_tool = previous_hook
        if hasattr(runner, "on_note"):
            runner.on_note = previous_note


def _raise_if_cancelled(cancel_check: CancelCheck | None, what: str) -> None:
    if cancel_check is not None and cancel_check():
        raise JobCancelled(f"{what} cancelled")


_REPAIRABLE_SECTION = "前置"
