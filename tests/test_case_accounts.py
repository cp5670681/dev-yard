from __future__ import annotations

from pathlib import Path

import pytest

from dev_yard.qa import (
    _case_account,
    _check_case_accounts,
    _preload_auth,
    _used_accounts,
    discover_cases,
)
from dev_yard.qa_config import (
    QaAccount,
    QaBrowser,
    QaConfig,
    QaEnv,
    QaWorker,
    TestRejected,
)


def _cfg(
    accounts: dict[str, QaAccount] | None = None,
    default: str = "admin",
    concurrency: int = 1,
) -> QaConfig:
    return QaConfig(
        active_env="local",
        env=QaEnv(
            name="local",
            base_url="http://127.0.0.1:1",
            auth_default=default,
            accounts=accounts or {},
        ),
        browser=QaBrowser(),
        workers=(
            QaWorker(id="a", provider="rcc", model="g", concurrency=concurrency, priority=1),
        ),
    )


def _case(cid: str, account: str = "") -> object:
    from dev_yard.qa_schedule import CaseJob

    return CaseJob(id=cid, title=cid, repo="backend", account=account)


def test_discover_cases_reads_account_frontmatter(tmp_path: Path):
    case = tmp_path / "qa" / "cases" / "mod" / "case-01.md"
    case.parent.mkdir(parents=True)
    case.write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: buyer\n---\n\nbody\n",
        encoding="utf-8",
    )
    jobs = discover_cases(tmp_path / "qa")
    assert jobs[0].account == "buyer"


def test_discover_cases_account_defaults_to_empty(tmp_path: Path):
    case = tmp_path / "qa" / "cases" / "mod" / "case-01.md"
    case.parent.mkdir(parents=True)
    case.write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n", encoding="utf-8"
    )
    assert discover_cases(tmp_path / "qa")[0].account == ""


def test_case_account_falls_back_to_default():
    cfg = _cfg({"admin": QaAccount("admin")}, default="admin")
    assert _case_account(cfg, _case("c1")) == "admin"
    assert _case_account(cfg, _case("c1", "buyer")) == "buyer"


def test_check_case_accounts_rejects_unconfigured():
    cfg = _cfg({"admin": QaAccount("admin")}, default="admin")
    with pytest.raises(TestRejected, match="not configured"):
        _check_case_accounts(cfg, [_case("c1", "buyer")], "J-1")
    # default/no-account cases are fine
    _check_case_accounts(cfg, [_case("c1"), _case("c2", "admin")], "J-1")


def test_check_case_accounts_rejects_default_outside_accounts():
    cfg = _cfg({"admin": QaAccount("admin")}, default="ghost")
    with pytest.raises(TestRejected, match="auth.default"):
        _check_case_accounts(cfg, [_case("c1")], "J-1")


def test_check_case_accounts_allows_missing_default_when_all_cases_explicit():
    cfg = _cfg({"admin": QaAccount("admin")}, default="ghost")
    _check_case_accounts(cfg, [_case("c1", "admin")], "J-1")


def test_check_case_accounts_skips_validation_without_accounts():
    cfg = _cfg({}, default="default")
    _check_case_accounts(cfg, [_case("c1")], "J-1")


def test_used_accounts_lists_each_once():
    cfg = _cfg({"admin": QaAccount("admin"), "buyer": QaAccount("buyer")})
    names = _used_accounts(
        cfg, [_case("c1", "buyer"), _case("c2"), _case("c3", "buyer")]
    )
    assert names == ["buyer", "admin"]


def test_run_lock_release_keeps_another_holders_lock(tmp_path: Path):
    """A finished run must not unlink a lock a successor already holds."""
    from dev_yard.qa import _run_lock

    lock = tmp_path / ".yard-qa" / "locks" / "J-1.run.lock"
    with _run_lock(tmp_path, "J-1"):
        with pytest.raises(TestRejected):
            with _run_lock(tmp_path, "J-1"):
                pass
    assert not lock.exists()
    # if the lock was replaced while we held it, our release must not unlink it
    with _run_lock(tmp_path, "J-1"):
        lock.write_text("4242:someoneelse", encoding="utf-8")
    assert lock.exists()


def test_preload_skips_missing_state_with_creds_when_sequential(tmp_path: Path):
    cfg = _cfg(
        {
            "buyer": QaAccount(
                "buyer",
                username="buyer01",
                password="pw",
                state_file=".yard-qa/missing.json",
            )
        },
        default="buyer",
    )
    _preload_auth(tmp_path, cfg, ["buyer"])


def test_preload_rejects_missing_state_with_creds_when_concurrent(tmp_path: Path):
    cfg = _cfg(
        {
            "buyer": QaAccount(
                "buyer",
                username="buyer01",
                password="pw",
                state_file=".yard-qa/missing.json",
            )
        },
        default="buyer",
        concurrency=2,
    )
    with pytest.raises(TestRejected, match="concurrency"):
        _preload_auth(tmp_path, cfg, ["buyer"])


def test_preload_rejects_missing_state_without_creds(tmp_path: Path):
    cfg = _cfg(
        {"buyer": QaAccount("buyer", state_file=".yard-qa/missing.json")},
        default="buyer",
    )
    with pytest.raises(TestRejected, match="missing auth state_file"):
        _preload_auth(tmp_path, cfg, ["buyer"])


def test_preload_loads_every_used_account(tmp_path: Path, monkeypatch):
    from dev_yard import qa as qa_mod

    cfg = _cfg(
        {
            "admin": QaAccount("admin", state_file=".yard-qa/a.json"),
            "buyer": QaAccount("buyer", state_file=".yard-qa/b.json"),
        }
    )
    for name in ("a.json", "b.json"):
        p = tmp_path / ".yard-qa" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(qa_mod.shutil, "which", lambda _: "/bin/true")
    monkeypatch.setattr(
        qa_mod.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _R()
    )
    _preload_auth(tmp_path, cfg, ["admin", "buyer"])
    assert len(calls) == 2
    assert "a.json" in " ".join(calls[0])
    assert "b.json" in " ".join(calls[1])
