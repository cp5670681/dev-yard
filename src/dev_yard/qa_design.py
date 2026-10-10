"""Design context, prompts, lint, and the verify-repair loop."""

from __future__ import annotations

import re
import time
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
    split_frontmatter,
    uncovered_changes,
)
from dev_yard.qa_config import QaConfig, TestRejected, redact_url
from dev_yard.qa_exec import case_script_path
from dev_yard.qa_review import cases_fingerprint, reject_cases
from dev_yard.qa_schedule import CaseJob, now_iso
from dev_yard.qa_verify import (
    VerifyResult,
    dump_live_schema,
    failed_cases,
    needs_verify,
    render_feedback,
    verify_cases,
    worktree_schema_files,
)
from dev_yard.runners import JobCancelled, Runner
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
    others = [n for n in cfg.env_names if n != cfg.active_env]
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
        "Page steps write `<seed.name>`; verify.sql writes `:seed.<name>`.",
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
        "",
        "## Database columns",
        "",
        "verify.sql 和 setup 的表名、列名必须来自本节（宿主已按 catalog 查过 "
        "information_schema）或下列 schema 文件。属「需求新增·现场未部署」子节的列"
        "**必须**写进断言，不得删除。非默认库在用例 frontmatter 写 "
        "`data.db: <catalog>`。不要猜列名，不要把代码常量"
        "（NEED_RENOVATION_TYPE 等）写成表字段，不要编造主键 id=1001。",
    ]
    schema_files = worktree_schema_files(root, jira, aliases)
    if schema_files:
        lines.extend(schema_files)
    live = dump_live_schema(cfg, names=_catalogs_for_requirement(root, jira, cfg))
    if live:
        lines.extend(live)
    else:
        lines.append(
            "- live dump unavailable; read the schema files above "
            "(db/schema.rb / structure.sql / prisma)."
        )
    from dev_yard.qa_deploy import frozen_models, undeployed_columns

    undeployed = undeployed_columns(root, jira, cfg, aliases)
    lines += ["", "### 需求新增·现场未部署（断言不得删除）", ""]
    if undeployed:
        for alias, col, mig in undeployed:
            extra = f" （{mig}）" if mig else ""
            lines.append(f"- {alias}: `{col}`{extra}")
    else:
        lines.append("（无；现场已含需求分支新增列）")
    lines += ["", "## 现场脚本契约", ""]
    spec = getattr(env, "exec_cfg", None)
    sites = list(getattr(spec, "sites", ()) or ())
    if sites:
        for s in sites:
            rails = s.rails or "（未声明）"
            bound = [
                a
                for a in aliases
                if (repos.get(a) and (repos[a].exec or "").strip() == s.name) or a == s.name
            ]
            models: set[str] = set()
            for alias in bound:
                models |= frozen_models(root, jira, alias)
            freeze = ",".join(sorted(models)) if models else "（无）"
            lines.append(
                f"- {s.name}: runner={s.runner or '(none)'} rails={rails} freeze_models={freeze}"
            )
    else:
        runner = getattr(spec, "runner", None) or env.script_runner or "(sql only)"
        lines.append(f"- (single): runner={runner}")
    if others:
        lines.append(f"Other envs exist ({', '.join(others)}) but are out of scope for this run.")
    if redact_url(env.base_url) != env.base_url:
        lines.append(
            "base_url userinfo is masked here; read the full URL from "
            f"`qa.yaml` envs.{cfg.active_env}.base_url if login needs it."
        )
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
    from dev_yard.qa_facts import write_facts_yaml

    try:
        write_facts_yaml(root, jira, cfg, aliases)
    except Exception:  # noqa: BLE001 — facts are advisory; never block design
        pass
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


def _catalogs_for_requirement(root: Path, jira: str, cfg: QaConfig) -> list[str]:
    """Catalogs this requirement should dump: declared on involved repos, else all."""
    repos = load_repos(root)
    aliases = _involved_aliases(root, jira)
    names: list[str] = []
    seen: set[str] = set()
    fallback = False
    for alias in aliases:
        repo = repos.get(alias)
        declared = list(repo.databases) if repo and repo.databases else []
        if declared:
            for name in declared:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        else:
            fallback = True
    if fallback or not names:
        for cat in cfg.env.db_catalogs:
            if cat.name not in seen:
                seen.add(cat.name)
                names.append(cat.name)
    if not names and cfg.env.db_default:
        names = [cfg.env.db_default]
    return names


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
        if needs_verify(job) and not job.verify:
            problems.append(
                f"{job.id}: 声明了 setup/cleanup 或 DB 预期，但缺少 data.verify"
                "（纯 UI 用例请显式写 `SELECT 1`）"
            )
        problems.extend(_lint_script_facts(root, jira, cfg, job, repos_map))
        problems.extend(_lint_verify_sql_syntax(cfg, job))
    for cid in uncovered_changes(paths.qa_dir(root, jira), cases):
        problems.append(f"改动点 {cid} 未被任何用例 covers")
    return problems


_EQ_STRING = re.compile(r"(?:(?P<table>\w+)\.)?(?P<col>\w+)\s*=\s*'(?P<lit>[^']*)'")

_WHERE_AR = re.compile(r"\.where\s*\(")

_DELETE_ALL = re.compile(r"\b([A-Z]\w*)\.delete_all\b")

_CREATE_QA_TABLE = re.compile(r"CREATE\s+TABLE\s+_qa_", re.I)

_QA_EXEC_SEEDS = re.compile(r"_qa_exec_seeds", re.I)


def _rails_major(ver: str) -> int | None:
    m = re.match(r"(\d+)", (ver or "").strip())
    return int(m.group(1)) if m else None


def _case_script_blob(job: CaseJob) -> str:
    chunks = [job.body or ""]
    for kind, name in (("setup", job.setup), ("cleanup", job.cleanup), ("verify", job.verify)):
        if not name or not job.path:
            continue
        try:
            path = case_script_path(job, name, kind)
        except TestRejected:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def _lint_script_facts(
    root: Path, jira: str, cfg: QaConfig, job: CaseJob, repos_map: dict
) -> list[str]:
    """Static B-class rules; skip when the fact is missing so we do not false-hit."""
    from dev_yard.qa_deploy import frozen_models
    from dev_yard.qa_verify import dump_live_column_types, is_numeric_type

    blob = _case_script_blob(job)
    if not blob.strip():
        return []
    out: list[str] = []
    repo = repos_map.get(job.repo)
    site_name = (getattr(repo, "exec", "") or "").strip() if repo else ""
    spec = getattr(cfg.env, "exec_cfg", None)
    site = spec.named_site(site_name) if spec and site_name else None
    rails = (site.rails if site else "") or ""
    major = _rails_major(rails)
    if major is not None and major < 3 and _WHERE_AR.search(blob):
        out.append(
            f"{job.id}: {site_name or 'exec'} 站是 AR {rails}，无 Model.where，"
            "用 find(:all, :conditions => …)"
        )
    if _CREATE_QA_TABLE.search(blob) or _QA_EXEC_SEEDS.search(blob):
        out.append(
            f"{job.id}: 不要在业务库建/查 _qa_exec_seeds；"
            "setup stdout 打 QA_SEED key=… id=…，verify.sql 用 :seed.<key>"
        )
    facts = {}
    try:
        from dev_yard.qa_facts import lint_facts_blob, load_facts

        facts = load_facts(paths.qa_dir(root, jira))
    except Exception:  # noqa: BLE001
        facts = {}
    wt = (facts.get("worktrees") or {}).get(job.repo or "") if facts else {}
    constants = list((wt or {}).get("constants") or [])
    frozen_from_facts = set((wt or {}).get("frozen_models") or [])
    frozen: set[str] = set()
    if job.repo:
        frozen = frozen_from_facts or frozen_models(root, jira, job.repo)
        if frozen:
            for hit in _DELETE_ALL.finditer(blob):
                model = hit.group(1)
                if model in frozen:
                    out.append(f"{job.id}: {model} 带 freeze_model_concern，不能 delete_all")
    if constants or frozen:
        from dev_yard.qa_facts import lint_facts_blob

        for msg in lint_facts_blob(blob, constants, set(frozen)):
            out.append(f"{job.id}: {msg}")
    from_tables = re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(\w+)", blob, re.I)
    tables = sorted(
        {m.group("table") for m in _EQ_STRING.finditer(blob) if m.group("table")}
        | {t.lower() for t in from_tables}
    )
    types = dump_live_column_types(cfg, tables, job.db or None) if tables else {}
    if types:
        for m in _EQ_STRING.finditer(blob):
            lit = m.group("lit")
            if re.fullmatch(r"[+-]?\d+(\.\d+)?", lit or ""):
                continue
            table = (m.group("table") or "").lower()
            col = (m.group("col") or "").lower()
            hits: list[tuple[str, str]] = []
            if table and table in types:
                typ = types[table].get(col, "")
                if typ and is_numeric_type(typ):
                    hits.append((table, typ))
            elif not table:
                for tname, cols in types.items():
                    typ = cols.get(col, "")
                    if typ and is_numeric_type(typ):
                        hits.append((tname, typ))
            for tname, typ in hits:
                out.append(f"{job.id}: {tname}.{col} 是 {typ.upper()}，不能写 {col} = '{lit}'")
    return out


def _lint_verify_sql_syntax(cfg: QaConfig, job: CaseJob) -> list[str]:
    """Run verify.sql once at lint time: 0 rows is ok; syntax/column errors are not."""
    if not job.verify or not job.path:
        return []
    try:
        path = case_script_path(job, job.verify, "verify")
        sql = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TestRejected):
        return []
    from dev_yard.qa_exec import assert_readonly_sql, run_sql_count
    from dev_yard.qa_seeds import has_seed_placeholders
    from dev_yard.qa_verify import verify_env_error

    if has_seed_placeholders(sql):
        return []
    try:
        assert_readonly_sql(sql)
    except TestRejected:
        return []
    try:
        run_sql_count(cfg, sql, catalog=job.db or None, verify=True)
    except TestRejected as e:
        if verify_env_error(e):
            return []
        return [f"{job.id}: verify.sql 语法/列错误（lint 期）：{e}"]
    except Exception:  # noqa: BLE001 — missing usql/DSN must not fail lint
        return []
    return []


def _duties(kind: str, jira: str) -> str:
    # Hard constraints only; the detailed playbook lives in the SKILL.md next to
    # this text. Keep the two in sync (a test asserts the key phrases).
    qa = f"reqs/{jira}/qa/"
    if kind == "design":
        return (
            "You are designing UI test cases for this freeze worktree.\n"
            f"Write only under {qa} (meta.yaml, cases/, OPEN-QUESTIONS.md, probes/). "
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
            "`req accounts --auto`).\n"
            "Data prerequisites must be executable: declare `data.verify` "
            "(single read-only SELECT; >=1 row means pass) for every case that "
            "declares setup/cleanup or a DB expectation; the host runs it and "
            "feeds failures back to you. A pure-UI case writes `SELECT 1` and is "
            "flagged as an exemption.\n"
            "Before writing cases, read qa/facts.yaml and qa/probe-results.md "
            "(also inlined in this prompt). Honor dialect, Rails version, and "
            "frozen models. A constant that exists may still be the wrong kind of "
            "id — confirm with a probe SELECT against the real table. "
            "Setup self-checks assert named seed rows, never a live yearly total "
            "of 1 or 2 (the test DB is dirty). "
            "Need a live value or dialect check: write a single read-only SELECT "
            "to qa/probes/<name>.sql (optional first line `-- probe: <catalog>`); "
            "do not run usql yourself (DSN in qa.yaml, host runs probes). "
            "Column names come from context.md Database columns (host dumped "
            "information_schema per named catalog) or worktree schema.rb. "
            "Columns listed under 需求新增·现场未部署 must appear in assertions "
            "and must not be deleted. Honor ## 现场脚本契约 (Rails version, "
            "frozen models) when writing setup/cleanup. "
            "When a case talks to a non-default database, set `data.db` to that "
            "catalog name. Page URLs use that case's repo exec-site origin "
            "(context.md origins), not the env default host for another site. "
            "One case, one origin; cross-app flows use depends_on. Never invent columns or "
            "treat app constants as table fields. Do not invent primary keys in "
            "verify.sql without a setup that inserts those rows. "
            "Prefer a row that already exists. The first draft has no setup and no "
            "`:seed` key that only a setup would print. verify.sql selects the row "
            "by the business condition the case needs (LIMIT 1, or ROWNUM = 1 on "
            "Oracle) and aliases every column the page types or asserts. Steps use "
            "`<seed.alias>` for those columns; the host stores the first row and "
            "substitutes before the browser. Do not invent a marker string that "
            "forces an INSERT. The host runs verify.sql before any setup. If that "
            "query already returns a row, delete setup. Add setup only after the "
            "host reports 0 rows for that predicate. A save crash (NoMethodError, "
            "or nil inside before_save) means delete setup and select an existing "
            "row; do not invent the foreign key the callback dereferenced. When "
            "setup is allowed, read every belongs_to without `optional: true` and "
            "every presence validation on that model, set them all in one script, "
            "and write a matching cleanup. Do not learn required fields one "
            "exception at a time. "
            "Generated ids: setup prints `QA_SEED key=<name> id=<n>`; "
            "host writes qa/design-verify/seeds.yaml; verify.sql uses `:seed.<name>`; "
            "page steps use `<seed.name>` and the host substitutes before the browser. "
            "A setup path may be relative inside qa/cases/ (for example `../setup.rb`) "
            "so several cases share one factory. "
            "A case with no page sets `driver: host`: the host runs setup, verify.sql, "
            "and cleanup and does not open a browser. "
            "Do not CREATE TABLE _qa_* in the business catalog.\n"
            "A case with no setup is proved by verify.sql. When setup exists, it "
            "must exit(1) when its own seed assertion fails — not just print.\n"
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
        "URLs come from this case's site origin (context.md origins / "
        "$YARD_QA_BASE_URL) + Routes or frontend route code. "
        "Do not guess hosts or reuse another site's origin. "
        "Hash routers need `#/` in the path.\n"
        "Authentication: load state_file if available. If unauthenticated or redirected to login, "
        "inspect the page dynamically with snapshot, fill credentials, submit, and save state.\n"
        "Host already ran data.setup if the case has one; do not re-run it. "
        "Host will run cleanup after you finish.\n"
        "Generated ids in the case text are already numbers. "
        "`<seed.name>` was replaced before this prompt; open those concrete ids.\n"
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
    verify_feedback: str | None = None,
    history: str | None = None,
    repair_only: bool = False,
    contract_fix: bool = False,
) -> str:
    spec = load_registry(root)["qa-design"]
    if contract_fix and verify_feedback and verify_feedback.strip():
        extra = (
            "契约修补。这不是重新设计。\n"
            "只改清单点名的问题：给已有用例补上 covers，或补上 data.verify / repo / identity。\n"
            "不要改其它用例的步骤和预期，不要 diff，不要读 dev-yard 的 src/。\n"
            "改动点没有 covers 时，加到已经在断言该行为的用例上；没有这样的用例再补一条。\n"
            "缺账号保持未覆盖，不要为账号重写用例。\n\n" + verify_feedback.strip()
        )
        return session_prompt_for(spec, root, jira, extra=extra)
    if repair_only and verify_feedback and verify_feedback.strip():
        extra = (
            "数据核实修补。这不是重新设计。\n"
            "只改失败清单里的用例文件（setup、cleanup、verify.sql、该用例的前置）。\n"
            "不要改其它用例，不要 diff，不要读 dev-yard 的 src/。\n"
            "错误里的异常行是结论；忽略 Rails 启动警告。\n"
            "优先用库里已有的行：删掉 setup，verify.sql 按业务条件选出一行，"
            "SELECT 的列别名就是步骤里的 `<seed.别名>`。\n"
            "宿主会先按 verify.sql 查已有行，即使声明了 setup 也先查。"
            "查得到就删掉 data.setup 和 data.cleanup，前置改成这些已有行。\n"
            "保存崩溃（NoMethodError、回调里对 nil 调方法）同样删掉 setup，不要补那个外键。\n"
            "删掉 setup 时一并删掉 data.writes 和 data.identity，不要改成别的目标。\n"
            "可以改 data.setup、data.cleanup 和「前置」。不要改「步骤」和「预期」。\n"
            "根因聚类标成同一个异常或同一个 setup 脚本时，只改那一处共用造数，"
            "不要按用例分别改 verify.sql。其它失败按各条 verify.sql 分别改。\n"
            "只有这种状态不可能已存在时才造数。造数被「验证失败」拦住时，"
            "读该模型全部未写 optional: true 的 belongs_to 和 presence 校验，一次填齐，"
            "并写对称 cleanup。不要按这一次报错补一个字段再重跑。\n"
            "列名以 context.md Database columns 为准（含「需求新增·现场未部署」子节）；"
            "该子节的列不得删，保留断言并在用例备注标注待部署。\n"
            "缺账号保持未覆盖，不要为账号重写用例。\n\n" + verify_feedback.strip()
        )
        return session_prompt_for(spec, root, jira, extra=extra)
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
    extra += qa_design_refs.prompt_block(paths.qa_dir(root, jira))
    return session_prompt_for(spec, root, jira, extra=extra)


def _context_block(root: Path, jira: str, cfg: QaConfig) -> str:
    qa = paths.qa_dir(root, jira)
    ctx = qa / "context.md"
    text = ctx.read_text(encoding="utf-8") if ctx.is_file() else ""
    from dev_yard.qa_facts import facts_prompt_block
    from dev_yard.qa_probe import probe_prompt_block

    return f"qa/context.md:\n{text}".strip() + facts_prompt_block(qa) + probe_prompt_block(qa)


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
    prev_fail_ids: frozenset[str] | None = None
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
        failures = [
            r for r in results.values() if r.status == "failed" and not r.lint.get("skip_design")
        ]
        blocked = [r for r in results.values() if r.status == "blocked"]
        if not failures and not blocked:
            break
        if failures:
            fail_ids = frozenset(r.case for r in failures)
            if prev_fail_ids is not None and fail_ids == prev_fail_ids:
                reject_cases(qa, render_feedback(results))
                if on_log is not None:
                    on_log(
                        f"数据核实失败集合未变（{len(failures)} 条），零收益即停，不再回灌 design\n"
                    )
                break
            prev_fail_ids = fail_ids
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
                    f"数据核实 {len(failures)} 条未通过，只修补这些用例"
                    f"（第 {case_round}/{case_attempts - 1} 次）\n"
                )
            prompt = _design_prompt(
                root,
                jira,
                cfg,
                verify_feedback=render_feedback(results),
                repair_only=True,
            )
            body_snap = _snapshot_case_bodies(qa)
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
            if not _repair_keeps_assertions(qa, body_snap):
                _restore_case_bodies(qa, body_snap)
                _finish_design(qa)
                reject_cases(qa, render_feedback(results))
                if on_log is not None:
                    on_log(
                        "数据核实修补改了步骤或预期，已回滚，交人工；"
                        "只允许改 setup / cleanup / verify.sql，以及前置和 data.setup\n"
                    )
                break
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


def _raise_if_cancelled(cancel_check: CancelCheck | None, what: str) -> None:
    if cancel_check is not None and cancel_check():
        raise JobCancelled(f"{what} cancelled")


_REPAIRABLE_SECTION = "前置"


def _assertion_sections(body: str) -> list[tuple[str, str]]:
    """Locked sections in order. A repeated heading stays, so it cannot hide an edit."""
    parts = re.split(r"(?m)^(##\s+.+)$", body)
    locked: list[tuple[str, str]] = []
    if parts[0].strip():
        locked.append(("__preamble__", parts[0]))
    index = 1
    while index < len(parts):
        heading = parts[index].strip()
        content = parts[index + 1] if index + 1 < len(parts) else ""
        title = re.sub(r"^##\s+", "", heading).strip()
        if title != _REPAIRABLE_SECTION:
            locked.append((title, content))
        index += 2
    return locked


def _data_map(meta: dict[str, Any]) -> dict[str, Any]:
    data = meta.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _meta_without_scripts(meta: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    kept = {key: value for key, value in data.items() if key not in {"setup", "cleanup"}}
    if kept:
        out["data"] = kept
    else:
        out.pop("data", None)
    return out


def repair_keeps_assertions(before: str, after: str) -> bool:
    """True when a repair only drops setup/cleanup or rewrites 前置.

    Dropping setup may also drop data.writes and data.identity. Those keys
    may not be pointed at a different row.
    """
    try:
        meta_before, body_before = split_frontmatter(before)
        meta_after, body_after = split_frontmatter(after)
    except yaml.YAMLError:
        return False
    before_data = _data_map(meta_before)
    after_data = _data_map(meta_after)
    dropped_scripts = bool(before_data.get("setup") or before_data.get("cleanup")) and not (
        after_data.get("setup") or after_data.get("cleanup")
    )
    if dropped_scripts:
        for key in ("writes", "identity"):
            if key not in after_data:
                before_data.pop(key, None)
            elif before_data.get(key) != after_data.get(key):
                return False
    if _meta_without_scripts(meta_before, before_data) != _meta_without_scripts(
        meta_after, after_data
    ):
        return False
    return _assertion_sections(body_before) == _assertion_sections(body_after)


def _repair_keeps_assertions(qa: Path, snap: dict[str, bytes]) -> bool:
    from dev_yard.qa_review import _case_assets

    current = {str(path): path.read_bytes() for path in _case_assets(qa, "bodies")}
    if set(current) != set(snap):
        return False
    for path, old in snap.items():
        new = current[path]
        if old == new:
            continue
        try:
            before = old.decode("utf-8")
            after = new.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not repair_keeps_assertions(before, after):
            return False
    return True


def _snapshot_case_bodies(qa: Path) -> dict[str, bytes]:
    from dev_yard.qa_review import _case_assets

    return {str(path): path.read_bytes() for path in _case_assets(qa, "bodies")}


def _restore_case_bodies(qa: Path, snap: dict[str, bytes]) -> None:
    from dev_yard.qa_review import _case_assets

    for path, data in snap.items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for path in _case_assets(qa, "bodies"):
        if str(path) not in snap:
            path.unlink(missing_ok=True)


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
                "design-blocked: 数据核实未通过（--allow-unverified 越权放行，仅跳过本用例）"
            )
            job.ended_at = stamp
