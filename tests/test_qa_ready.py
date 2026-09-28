from pathlib import Path

import pytest

from dev_yard.qa_config import TestRejected, load_qa_config
from dev_yard.qa_ready import assert_ready, assess_ready
from dev_yard.qa_schedule import CaseJob


def _yard(tmp_path: Path, accounts: str = "") -> Path:
    root = tmp_path / "yard"
    root.mkdir()
    (root / "reqs" / "QA-1").mkdir(parents=True)
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "workers:\n"
        "  - {id: a, provider: rcc, model: m, concurrency: 1, priority: 1}\n"
        "envs:\n"
        "  local:\n"
        "    base_url: http://127.0.0.1:9\n"
        "    db: {url: postgres://localhost/app}\n"
        f"{accounts}",
        encoding="utf-8",
    )
    return root


def _ok_env(*_a, **_k):
    return {
        "ok": True,
        "env": "local",
        "use": "local",
        "site": "local",
        "steps": [
            {"step": "ping", "status": "ok", "detail": ""},
            {"step": "hello", "status": "ok", "detail": "qa-check-env-ok"},
            {"step": "db", "status": "ok", "detail": "usql select 1"},
        ],
    }


def test_ready_fails_without_account(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr("dev_yard.script_exec.check_env", _ok_env)
    root = _yard(tmp_path)
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg)
    assert report["ok"] is False
    assert any(s["step"] == "accounts" and s["status"] == "fail" for s in report["steps"])
    with pytest.raises(TestRejected, match="测试前置未通过"):
        assert_ready(root, "QA-1", cfg)


def test_ready_fails_when_database_warns(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr(
        "dev_yard.script_exec.check_env",
        lambda *_a, **_k: {
            "ok": True,
            "steps": [{"step": "db", "status": "warn", "detail": "db.url 从本机不通"}],
        },
    )
    root = _yard(
        tmp_path,
        "    auth:\n      default: default\n      accounts:\n"
        "        default: {username: ada, password: secret}\n",
    )
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg)
    assert report["ok"] is False
    db = next(s for s in report["steps"] if s["step"] == "db")
    assert db["status"] == "fail"


def test_ready_passes_with_credentials(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr("dev_yard.script_exec.check_env", _ok_env)
    root = _yard(
        tmp_path,
        "    auth:\n      default: default\n      accounts:\n"
        "        default: {username: ada, password: secret}\n",
    )
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg, [CaseJob(id="case-01", title="t", repo="backend")])
    assert report["ok"] is True


def _case(root: Path, account: str) -> None:
    mod = root / "reqs" / "QA-1" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    (mod / "case-01.md").write_text(
        "---\n"
        "id: case-01\n"
        "title: t\n"
        "repo: backend\n"
        f"account: {account}\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )


def test_ready_discovers_case_account_without_default(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr("dev_yard.script_exec.check_env", _ok_env)
    root = _yard(
        tmp_path,
        "    auth:\n      default: default\n      accounts:\n"
        "        buyer: {username: ada, password: secret}\n",
    )
    _case(root, "buyer")
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg)
    assert report["ok"] is True
    accounts = next(s for s in report["steps"] if s["step"] == "accounts")
    assert accounts["detail"] == "buyer"


def test_ready_fails_on_named_case_account(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", True)
    monkeypatch.setattr("dev_yard.script_exec.check_env", _ok_env)
    root = _yard(
        tmp_path,
        "    auth:\n      default: default\n      accounts:\n"
        "        default: {username: ada, password: secret}\n",
    )
    _case(root, "buyer")
    cfg = load_qa_config(root, None, "QA-1")
    report = assess_ready(root, "QA-1", cfg)
    assert report["ok"] is False
    accounts = next(s for s in report["steps"] if s["step"] == "accounts")
    assert "buyer" in accounts["detail"]
    assert "default" not in accounts["detail"]


def test_assert_ready_skipped_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("dev_yard.qa_ready.ENABLED", False)
    root = _yard(tmp_path)
    cfg = load_qa_config(root, None, "QA-1")
    assert assert_ready(root, "QA-1", cfg) is None
