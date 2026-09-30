"""Step 1 of a QA run: environment, accounts, and database must all pass.

Design and execution call `assert_ready`. Approving a case set does not, so a
review can be recorded without poking the environment again. `ENABLED` is on
in production; the test suite turns it off so unit tests do not ping or usql.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_yard.qa_config import QaConfig, TestRejected
from dev_yard.qa_exec import state_path
from dev_yard.qa_schedule import CaseJob

ENABLED = True


def _worktree(root: Path, jira: str) -> Path | None:
    wt_root = root / "reqs" / jira / "worktrees"
    if not wt_root.is_dir():
        return None
    for child in sorted(wt_root.iterdir()):
        if child.is_dir():
            return child
    return None


def _usable(root: Path, cfg: QaConfig, name: str) -> str | None:
    """None when the account can log in later. Otherwise a short reason."""
    acct = cfg.env.accounts.get(name)
    if acct is None:
        return f"账号 {name} 未配置"
    if state_path(root, cfg, acct).is_file():
        return None
    if acct.username and acct.password:
        return None
    return f"账号 {name} 没有登录态，也没有用户名和密码"


def _accounts_to_check(cfg: QaConfig, cases: list[CaseJob]) -> list[str]:
    """Same accounts a run would log in with.

    The default is required only when there are no cases yet, or some case
    leaves ``account`` empty. A case that names its own account does not pull
    in an unused default.
    """
    default = cfg.env.auth_default or "default"
    if not cases or any(not case.account for case in cases):
        names = [default]
    else:
        names = []
    for case in cases:
        if case.account and case.account not in names:
            names.append(case.account)
    return names


def _account_step(root: Path, cfg: QaConfig, cases: list[CaseJob]) -> dict[str, str]:
    names = _accounts_to_check(cfg, cases)
    reasons = [why for name in names if (why := _usable(root, cfg, name))]
    if reasons:
        return {"step": "accounts", "status": "fail", "detail": "；".join(reasons)}
    return {
        "step": "accounts",
        "status": "ok",
        "detail": "、".join(names),
    }


def _harden_origin(steps: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for step in steps:
        item = dict(step)
        if item.get("step") == "origin" and item.get("status") != "ok":
            item["status"] = "fail"
            if not item.get("detail"):
                item["detail"] = "site origin 打不开"
        out.append(item)
    return out


def _harden_db(cfg: QaConfig, steps: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    saw_db = False
    for step in steps:
        item = dict(step)
        if item.get("step") == "db":
            saw_db = True
            if item.get("status") != "ok":
                item["status"] = "fail"
                if not item.get("detail"):
                    item["detail"] = "数据库连不上"
        out.append(item)
    catalogs = list(getattr(cfg.env, "db_catalogs", ()) or ())
    if not catalogs and not cfg.env.db_url:
        out.append(
            {
                "step": "db",
                "status": "fail",
                "detail": "未配置 db.url / catalogs；造数和断言都走这些连接",
            }
        )
        return out
    if not saw_db:
        out.append(
            {
                "step": "db",
                "status": "fail",
                "detail": "没有完成数据库检查（需要本机 usql，并对每个 catalog 执行探测查询）",
            }
        )
        return out
    names = [c.name for c in catalogs] if catalogs else [cfg.env.db_default or "default"]
    probed = {
        str(s.get("catalog") or "")
        for s in out
        if s.get("step") == "db"
    }
    if probed == {""} or not catalogs:
        return out
    for name in names:
        if name not in probed:
            out.append(
                {
                    "step": "db",
                    "catalog": name,
                    "status": "fail",
                    "detail": f"{name}: 没有完成数据库检查（需要本机 usql，并对 db.url 执行探测查询）",
                }
            )
    return out


def _deploy_step(root: Path, jira: str, cfg: QaConfig) -> dict[str, str]:
    """Refuse design when the live DB is missing columns this branch added."""
    from dev_yard.qa import _involved_aliases
    from dev_yard.qa_deploy import missing_on_site

    aliases = _involved_aliases(root, jira)
    if not aliases:
        return {
            "step": "deploy",
            "status": "warn",
            "detail": "未能判定需求迁移（无涉仓）",
        }
    fails: list[str] = []
    warns: list[str] = []
    for alias in aliases:
        missing = missing_on_site(root, jira, cfg, alias)
        if missing is None:
            warns.append(f"{alias}: 未能判定需求迁移")
            continue
        files, cols = missing
        if files or cols:
            bits = list(files) + list(cols)
            fails.append(f"{alias}: " + " ".join(bits))
    if fails:
        return {
            "step": "deploy",
            "status": "fail",
            "detail": "现场缺需求分支迁移：" + "；".join(fails),
        }
    if warns:
        return {"step": "deploy", "status": "warn", "detail": "；".join(warns)}
    return {"step": "deploy", "status": "ok", "detail": "需求分支新增列已在现场"}


def assess_ready(
    root: Path,
    jira: str,
    cfg: QaConfig,
    cases: list[CaseJob] | None = None,
) -> dict[str, Any]:
    """Env ping + hello, a hard database check, and account credentials.

    A database warning from `check_env` is a failure here. Account login itself
    still happens at execution when only a username and password are configured.
    """
    from dev_yard import paths
    from dev_yard.qa import discover_cases
    from dev_yard.script_exec import ExecUnreachable, check_env

    if cases is None:
        qa = paths.qa_dir(root, jira)
        cases = discover_cases(qa) if qa.is_dir() else []

    try:
        checked = check_env(
            root,
            env_name=cfg.active_env,
            jira=jira,
            worktree=_worktree(root, jira),
        )
        steps = _harden_origin(_harden_db(cfg, list(checked.get("steps") or [])))
        env_ok = True
    except (TestRejected, ExecUnreachable) as e:
        steps = [{"step": "env", "status": "fail", "detail": str(e)}]
        env_ok = False
    steps.append(_account_step(root, cfg, cases))
    steps.append(_deploy_step(root, jira, cfg))
    ok = env_ok and all(step.get("status") != "fail" for step in steps)
    return {
        "ok": ok,
        "env": cfg.active_env,
        "jira": jira,
        "steps": steps,
    }


def assert_ready(
    root: Path,
    jira: str,
    cfg: QaConfig,
    cases: list[CaseJob] | None = None,
) -> dict[str, Any] | None:
    """Refuse design and execution until environment, accounts, db, and deploy pass."""
    if not ENABLED:
        return None
    report = assess_ready(root, jira, cfg, cases)
    if report["ok"]:
        return report
    failed = [
        f"{step.get('step')}: {step.get('detail') or step.get('status')}"
        for step in report["steps"]
        if step.get("status") == "fail"
    ]
    raise TestRejected(
        "测试前置未通过（环境、账号、数据库都要正常）：" + "；".join(failed)
    )
