from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dev_yard import paths
from dev_yard.cli import app
from dev_yard.qa_accounts import (
    Candidate,
    accounts_overview,
    autofill,
    discovery_key_gaps,
    first_per_key,
    invalidate_all,
    note_discovery_gaps,
    parse_candidates,
    query_for_usql,
)
from dev_yard.qa_config import TestRejected, load_qa_config

runner = CliRunner()

QA_YAML = """\
active_env: local
envs:
  local:
    base_url: http://127.0.0.1:8080
    auth:
      default: admin
      accounts:
        admin: { username: w.deng, password: pw-admin }
    db:
      url: postgres://localhost/app
"""


def _workspace(tmp_path: Path, qa: str = QA_YAML) -> Path:
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\npi:\n  provider: rcc\n  model: grok-4\n", encoding="utf-8"
    )
    (tmp_path / "reqs" / "J-1").mkdir(parents=True)
    (tmp_path / "qa.yaml").write_text(qa, encoding="utf-8")
    return tmp_path


def test_discovery_key_gaps_when_query_misses_a_case_key(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    case_dir = root / "reqs" / "J-1" / "qa" / "cases" / "mod"
    case_dir.mkdir(parents=True)
    (case_dir / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: has_perm\n---\n\nbody\n",
        encoding="utf-8",
    )
    sql_path = paths.qa_accounts_discover_sql(root, "J-1")
    sql_path.write_text("SELECT username, 'other' AS account_key FROM users\n", encoding="utf-8")
    monkeypatch.setattr(
        "dev_yard.qa_accounts.discover",
        lambda *args, **kwargs: [Candidate("someone", "other")],
    )
    cfg = load_qa_config(root, "local", "J-1")
    missing, reason = discovery_key_gaps(root, "J-1", cfg)
    assert missing == ["has_perm"]
    assert reason == ""


def test_discovery_key_gaps_skips_when_no_query_file(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    case_dir = root / "reqs" / "J-1" / "qa" / "cases" / "mod"
    case_dir.mkdir(parents=True)
    (case_dir / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: has_perm\n---\n\nbody\n",
        encoding="utf-8",
    )
    def _boom(*_args, **_kwargs):
        raise AssertionError("should not query")

    monkeypatch.setattr("dev_yard.qa_accounts.discover", _boom)
    cfg = load_qa_config(root, "local", "J-1")
    assert discovery_key_gaps(root, "J-1", cfg) == ([], "")


def test_note_discovery_gaps_once(tmp_path: Path):
    qa = tmp_path / "qa"
    note_discovery_gaps(qa, ["has_perm"], "0 rows")
    note_discovery_gaps(qa, ["has_perm"], "0 rows")
    text = (qa / "OPEN-QUESTIONS.md").read_text(encoding="utf-8")
    assert text.count("has_perm") == 1
    assert "不要编 JOIN" in text


def test_parse_candidates_drops_usql_exec_status():
    assert parse_candidates("EXEC 2\n") == []
    assert parse_candidates("exec 2\nalice|has_perm\n") == [
        Candidate("alice", "has_perm"),
    ]


def test_query_for_usql_wraps_parenthesized_statement():
    sql = """\
-- header
(
  SELECT username, 'has_perm' AS account_key FROM users LIMIT 1
);
"""
    wrapped = query_for_usql(sql)
    assert wrapped.startswith("SELECT * FROM (")
    assert wrapped.rstrip().endswith(") qa_discover")
    assert "AS qa_discover" not in wrapped
    assert "SELECT username" in wrapped
    plain = query_for_usql("SELECT username, 'has_perm' AS account_key FROM users;")
    assert plain.startswith("SELECT username")
    assert "qa_discover" not in plain


def test_autofill_refuses_when_case_accounts_are_missing(tmp_path: Path):
    root = _workspace(tmp_path)
    case_dir = root / "reqs" / "J-1" / "qa" / "cases" / "mod"
    case_dir.mkdir(parents=True)
    (case_dir / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: has_perm\n---\n\nbody\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(root, "local")
    with pytest.raises(TestRejected, match="has_perm"):
        autofill(root, "J-1", "local", cfg, [Candidate("EXEC 2", "")])
    assert not (root / ".yard-qa" / "requirements" / "J-1" / "accounts.yaml").exists()


def test_autofill_accepts_keys_already_configured(tmp_path: Path):
    root = _workspace(tmp_path)
    case_dir = root / "reqs" / "J-1" / "qa" / "cases" / "mod"
    case_dir.mkdir(parents=True)
    (case_dir / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: admin\n---\n\nbody\n",
        encoding="utf-8",
    )
    (case_dir / "case-02.md").write_text(
        "---\nid: case-02\ntitle: t2\nrepo: backend\naccount: has_perm\n---\n\nbody\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(root, "local")
    result = autofill(root, "J-1", "local", cfg, [Candidate("has01", "has_perm")])
    assert "has_perm" in result.added
    assert result.accounts["admin"] == "w.deng"
    assert result.accounts["has_perm"] == "has01"


def test_parse_candidates_two_columns_and_dedup():
    out = "alice|has_perm\nbob|no_perm\nalice|has_perm\ncarol\n"
    assert parse_candidates(out) == [
        Candidate("alice", "has_perm"),
        Candidate("bob", "no_perm"),
        Candidate("carol", ""),
    ]


def test_first_per_key_takes_first_and_falls_back_to_auto():
    cands = [
        Candidate("a1", "has_perm"),
        Candidate("a2", "has_perm"),
        Candidate("b1", "no_perm"),
        Candidate("c1", ""),
    ]
    picked = first_per_key(cands)
    assert [(c.key, c.username) for c in picked] == [
        ("has_perm", "a1"),
        ("no_perm", "b1"),
        ("auto", "c1"),
    ]
    assert [c.username for c in first_per_key(cands, {"no_perm"})] == ["b1"]


def test_first_per_key_avoids_reusing_the_excluded_username():
    cands = [Candidate("w.deng", "no_perm"), Candidate("other", "no_perm")]
    picked = first_per_key(cands, exclude={"w.deng"})
    assert [c.username for c in picked] == ["other"]
    # if the excluded user is the only candidate, keep it rather than drop the key
    only = first_per_key([Candidate("w.deng", "no_perm")], exclude={"w.deng"})
    assert [c.username for c in only] == ["w.deng"]


def test_autofill_writes_requirement_file_and_reuses_default_password(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local")
    result = autofill(
        root,
        "J-1",
        "local",
        cfg,
        [Candidate("has01", "has_perm"), Candidate("no01", "no_perm")],
    )
    assert set(result.added) == {"has_perm", "no_perm"}
    assert result.default == "admin"
    # global qa.yaml is untouched
    assert "has_perm" not in (root / "qa.yaml").read_text(encoding="utf-8")
    cfg2 = load_qa_config(root, "local", "J-1")
    assert cfg2.env.accounts["has_perm"].username == "has01"
    assert cfg2.env.accounts["has_perm"].password == "pw-admin"
    assert cfg2.env.accounts["no_perm"].username == "no01"
    # the default account rides along so the req file is self-contained
    assert cfg2.env.accounts["admin"].username == "w.deng"
    assert set(load_qa_config(root, "local", "J-2").env.accounts) == {"admin"}


def test_autofill_repoints_changed_candidate_and_drops_cached_login(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local")
    autofill(root, "J-1", "local", cfg, [Candidate("has01", "has_perm")])

    state = root / ".yard-qa" / "requirements" / "J-1" / "auth-local-has_perm.json"
    replay = state.with_suffix(".replay.sh")
    state.write_text("{}", encoding="utf-8")
    replay.write_text("echo x\n", encoding="utf-8")

    result = autofill(root, "J-1", "local", cfg, [Candidate("has02", "has_perm")])
    assert result.changed == ["has_perm"]
    assert result.added == []
    assert not state.exists()
    assert not replay.exists()
    assert load_qa_config(root, "local", "J-1").env.accounts["has_perm"].username == "has02"


def test_autofill_force_relogin_drops_cache_even_when_username_same(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local")
    autofill(root, "J-1", "local", cfg, [Candidate("has01", "has_perm")])
    states = {}
    for name in ("admin", "has_perm"):
        state = root / ".yard-qa" / "requirements" / "J-1" / f"auth-local-{name}.json"
        state.write_text("{}", encoding="utf-8")
        states[name] = state

    result = autofill(
        root,
        "J-1",
        "local",
        cfg,
        [Candidate("has01", "has_perm")],
        force_relogin=True,
    )
    assert result.changed == []
    # refresh clears every account's cache, including the carried-over default
    assert not states["has_perm"].exists()
    assert not states["admin"].exists()


def test_autofill_requires_default_password(tmp_path: Path):
    root = _workspace(
        tmp_path,
        """\
active_env: local
envs:
  local:
    base_url: http://127.0.0.1:8080
    auth:
      default: admin
      accounts:
        admin: { username: w.deng }
""",
    )
    cfg = load_qa_config(root, "local")
    with pytest.raises(TestRejected, match="no password"):
        autofill(root, "J-1", "local", cfg, [Candidate("has01", "has_perm")])


def test_invalidate_all_clears_every_requirement_account(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local")
    autofill(root, "J-1", "local", cfg, [Candidate("has01", "has_perm")])
    for name in ("admin", "has_perm"):
        state = root / ".yard-qa" / "requirements" / "J-1" / f"auth-local-{name}.json"
        state.write_text("{}", encoding="utf-8")
    dropped = invalidate_all(root, "J-1", "local", cfg)
    assert set(dropped) == {"admin", "has_perm"}


def test_accounts_overview_masks_passwords(tmp_path: Path):
    root = _workspace(tmp_path)
    cfg = load_qa_config(root, "local")
    overview = accounts_overview(root, "J-1", cfg)
    assert overview["source"] == "global"
    assert overview["accounts"][0]["has_password"] is True
    assert "password" not in overview["accounts"][0]
    assert "pw-admin" not in str(overview)


def test_cli_req_accounts_auto_is_non_interactive(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "dev_yard.qa_accounts.discover",
        lambda dsn, sql, timeout=30: [Candidate("has01", "has_perm"), Candidate("no01", "no_perm")],
    )
    out = runner.invoke(app, ["req", "accounts", "J-1", "--sql", "select username from users", "--auto"])
    assert out.exit_code == 0, out.output
    cfg = load_qa_config(root, "local", "J-1")
    assert cfg.env.accounts["has_perm"].username == "has01"
    assert cfg.env.accounts["no_perm"].username == "no01"
    assert cfg.env.accounts["has_perm"].password == "pw-admin"
    assert paths.req_accounts_yaml(root, "J-1").is_file()


def test_cli_req_accounts_refresh_only_clears_cache(tmp_path: Path, monkeypatch):
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "dev_yard.qa_accounts.discover",
        lambda dsn, sql, timeout=30: [Candidate("has01", "has_perm")],
    )
    runner.invoke(app, ["req", "accounts", "J-1", "--sql", "select username from users", "--auto"])
    state = root / ".yard-qa" / "requirements" / "J-1" / "auth-local-has_perm.json"
    state.write_text("{}", encoding="utf-8")
    out = runner.invoke(app, ["req", "accounts", "J-1", "--refresh"])
    assert out.exit_code == 0, out.output
    assert not state.exists()
