from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dev_yard import cli, paths
from dev_yard.cli import app
from dev_yard.qa_config import (
    QaAccount,
    TestRejected,
    load_qa_config,
    save_req_accounts,
)

runner = CliRunner()

QA_YAML = """\
active_env: local
envs:
  local:
    base_url: http://127.0.0.1:8080
    auth:
      default: admin
      accounts:
        admin: { username: admin, password: pw-admin }
    db:
      url: postgres://localhost/app
  test:
    base_url: https://test.example.com
"""


def _workspace(tmp_path: Path, qa: str = QA_YAML) -> Path:
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "reqs").mkdir()
    (tmp_path / "qa.yaml").write_text(qa, encoding="utf-8")
    return tmp_path


def _acct(name: str, username: str = "", password: str = "") -> QaAccount:
    return QaAccount(name=name, username=username or name, password=password)


def test_load_uses_global_default_without_a_requirement_file(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local", "J-1")
    assert cfg.env.auth_default == "admin"
    assert cfg.env.accounts["admin"].password == "pw-admin"


def test_requirement_accounts_override_global(tmp_path: Path):
    root = _workspace(tmp_path)
    save_req_accounts(
        root,
        "J-1",
        "local",
        "buyer",
        {"admin": _acct("admin", "admin", "pw-admin"), "buyer": _acct("buyer", "buyer01", "pw")},
    )
    cfg = load_qa_config(root, "local", "J-1")
    assert cfg.env.auth_default == "buyer"
    assert set(cfg.env.accounts) == {"admin", "buyer"}
    assert cfg.env.accounts["buyer"].username == "buyer01"
    # base_url/db stay global
    assert cfg.env.base_url == "http://127.0.0.1:8080"
    assert cfg.env.db_url == "postgres://localhost/app"
    # a different requirement still sees the global default
    other = load_qa_config(root, "local", "J-2")
    assert other.env.auth_default == "admin"
    assert set(other.env.accounts) == {"admin"}


def test_requirement_file_without_the_env_falls_back(tmp_path: Path):
    root = _workspace(tmp_path)
    save_req_accounts(root, "J-1", "test", "admin", {"admin": _acct("admin")})
    cfg = load_qa_config(root, "local", "J-1")
    assert cfg.env.auth_default == "admin"
    assert set(cfg.env.accounts) == {"admin"}


def test_save_req_accounts_preserves_other_envs(tmp_path: Path):
    root = _workspace(tmp_path)
    save_req_accounts(root, "J-1", "local", "admin", {"admin": _acct("admin")})
    save_req_accounts(root, "J-1", "test", "buyer", {"buyer": _acct("buyer")})
    data = yaml.safe_load(paths.req_accounts_yaml(root, "J-1").read_text(encoding="utf-8"))
    assert set(data["envs"]) == {"local", "test"}
    assert data["envs"]["local"]["default"] == "admin"
    assert data["envs"]["test"]["default"] == "buyer"


def test_save_req_accounts_rejects_empty(tmp_path: Path):
    root = _workspace(tmp_path)
    with pytest.raises(TestRejected):
        save_req_accounts(root, "J-1", "local", "admin", {})


def test_save_req_accounts_rejects_default_outside_accounts(tmp_path: Path):
    root = _workspace(tmp_path)
    with pytest.raises(TestRejected, match="not one of the accounts"):
        save_req_accounts(root, "J-1", "local", "ghost", {"admin": _acct("admin")})


def test_save_req_accounts_rejects_corrupt_file(tmp_path: Path):
    root = _workspace(tmp_path)
    path = paths.req_accounts_yaml(root, "J-1")
    path.parent.mkdir(parents=True)
    path.write_text("envs: [unclosed\n", encoding="utf-8")
    with pytest.raises(TestRejected):
        save_req_accounts(root, "J-1", "local", "admin", {"admin": _acct("admin")})
    assert path.read_text(encoding="utf-8") == "envs: [unclosed\n"


def test_corrupt_accounts_error_is_redacted(tmp_path: Path, monkeypatch):
    from dev_yard import qa_config

    root = _workspace(tmp_path)
    path = paths.req_accounts_yaml(root, "J-1")
    path.parent.mkdir(parents=True)
    path.write_text("envs: {}\n", encoding="utf-8")

    def boom(_text):
        raise yaml.YAMLError("while parsing: password: SUPERSECRET99")

    monkeypatch.setattr(qa_config.yaml, "safe_load", boom)
    with pytest.raises(TestRejected) as err:
        qa_config.load_req_accounts(root, "J-1", "local")
    assert "SUPERSECRET99" not in str(err.value)
    with pytest.raises(TestRejected) as err2:
        save_req_accounts(root, "J-1", "local", "admin", {"admin": _acct("admin")})
    assert "SUPERSECRET99" not in str(err2.value)


def test_save_req_accounts_refuses_unignored_git_path(tmp_path: Path):
    root = _workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    with pytest.raises(TestRejected, match="gitignored"):
        save_req_accounts(root, "J-1", "local", "admin", {"admin": _acct("admin")})


def test_save_req_accounts_allows_ignored_git_path(tmp_path: Path):
    root = _workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text(".yard-qa/\n", encoding="utf-8")
    path = save_req_accounts(root, "J-1", "local", "admin", {"admin": _acct("admin")})
    assert path.is_file()


def test_cli_req_accounts_writes_requirement_file(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    # name, username, "same password?" (no), password, "another?" (no), default (enter)
    inp = "buyer\nbuyer01\nn\npw-buyer\nn\n\n"
    out = runner.invoke(app, ["req", "accounts", "J-1"], input=inp)
    assert out.exit_code == 0, out.output
    cfg = load_qa_config(root, "local", "J-1")
    assert cfg.env.auth_default == "buyer"
    assert cfg.env.accounts["buyer"].username == "buyer01"
    assert cfg.env.accounts["buyer"].password == "pw-buyer"


def test_cli_req_accounts_reuses_default_password_without_echoing_it(
    tmp_path: Path, monkeypatch
):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    # name, username, "same password?" (yes), "another?" (no), default (enter)
    inp = "buyer\nbuyer01\ny\nn\n\n"
    out = runner.invoke(app, ["req", "accounts", "J-1"], input=inp)
    assert out.exit_code == 0, out.output
    assert "pw-admin" not in out.output
    assert load_qa_config(root, "local", "J-1").env.accounts["buyer"].password == "pw-admin"


def test_cli_req_accounts_appends_on_rerun(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    runner.invoke(app, ["req", "accounts", "J-1"], input="buyer\nbuyer01\nn\npw\nn\n\n")
    # re-run seeds the existing buyer account, then appends admin
    inp = "admin\nadmin\nn\npw-admin\nn\n"
    out = runner.invoke(app, ["req", "accounts", "J-1"], input=inp)
    assert out.exit_code == 0, out.output
    cfg = load_qa_config(root, "local", "J-1")
    assert set(cfg.env.accounts) == {"buyer", "admin"}


def test_cli_req_accounts_reset_starts_empty(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    runner.invoke(app, ["req", "accounts", "J-1"], input="buyer\nbuyer01\nn\npw\nn\n\n")
    out = runner.invoke(
        app, ["req", "accounts", "J-1", "--reset"], input="admin\nadmin\nn\npw\nn\n\n"
    )
    assert out.exit_code == 0, out.output
    cfg = load_qa_config(root, "local", "J-1")
    assert set(cfg.env.accounts) == {"admin"}


def test_cli_req_accounts_uses_discovered_candidate(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "_discover_usernames", lambda dsn, sql: ["found01"])
    # name, username (accept candidate), "same password?" (no), password, "another?" (no), default
    inp = "buyer\n\nn\npw\nn\n\n"
    out = runner.invoke(
        app, ["req", "accounts", "J-1", "--sql", "select username from users"], input=inp
    )
    assert out.exit_code == 0, out.output
    assert "found01" in out.stdout
    cfg = load_qa_config(root, "local", "J-1")
    assert next(iter(cfg.env.accounts.values())).username == "found01"


@pytest.mark.parametrize(
    "sql",
    [
        "delete from users",
        "select 1; drop table users",
        "with x as (delete from users returning id) select id from x",
        "explain analyze delete from users",
        "pragma journal_mode = wal",
        "select 1 into outfile '/tmp/x'",
        "select load_file('/etc/passwd')",
    ],
)
def test_discover_usernames_rejects_writes(sql: str):
    with pytest.raises(ValueError):
        cli._discover_usernames("postgres://x", sql)


def test_discover_usernames_requires_dsn():
    with pytest.raises(ValueError, match="db.url"):
        cli._discover_usernames("", "select 1")
