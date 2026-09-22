from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dev_yard import paths
from dev_yard.qa_accounts import Candidate
from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.app import create_app


def _client(yard: Path) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True))


def _req(tmp_path: Path, git_src: Path, monkeypatch) -> Path:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    req_open(yard, "QA-A1", source="none")
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: admin\n"
        "      accounts:\n        admin: { username: w.deng, password: pw-admin }\n"
        "    db:\n      url: postgres://localhost/app\n",
        encoding="utf-8",
    )
    qa = yard / "reqs" / "QA-A1" / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    paths.qa_accounts_discover_sql(yard, "QA-A1").write_text(
        "SELECT username, 'has_perm' FROM employees\n", encoding="utf-8"
    )
    return yard


def test_accounts_overview_falls_back_to_global(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    r = _client(yard).get("/api/requirements/QA-A1/accounts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "global"
    assert body["discover_sql"] is True
    assert body["accounts"][0]["name"] == "admin"
    assert body["accounts"][0]["has_password"] is True
    assert "pw-admin" not in r.text


def test_accounts_discover_and_auto_write(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    monkeypatch.setattr(
        "dev_yard.qa_accounts.discover",
        lambda dsn, sql, timeout=30: [
            Candidate("has01", "has_perm"),
            Candidate("no01", "no_perm"),
        ],
    )
    client = _client(yard)
    disc = client.post("/api/requirements/QA-A1/accounts/discover")
    assert disc.status_code == 200, disc.text
    assert [c["key"] for c in disc.json()["candidates"]] == ["has_perm", "no_perm"]

    auto = client.post("/api/requirements/QA-A1/accounts/auto")
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert set(body["added"]) == {"has_perm", "no_perm"}
    assert body["source"] == "requirement"
    names = {a["name"]: a["username"] for a in body["accounts"]}
    assert names == {"admin": "w.deng", "has_perm": "has01", "no_perm": "no01"}
    assert paths.req_accounts_yaml(yard, "QA-A1").is_file()
    # global qa.yaml is untouched
    assert "has_perm" not in (yard / "qa.yaml").read_text(encoding="utf-8")


def test_accounts_auto_without_discovery_sql_is_404(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    paths.qa_accounts_discover_sql(yard, "QA-A1").unlink()
    r = _client(yard).post("/api/requirements/QA-A1/accounts/auto")
    assert r.status_code == 404


def test_accounts_refresh_works_without_discovery_sql(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    monkeypatch.setattr(
        "dev_yard.qa_accounts.discover",
        lambda dsn, sql, timeout=30: [Candidate("has01", "has_perm")],
    )
    client = _client(yard)
    assert client.post("/api/requirements/QA-A1/accounts/auto").status_code == 200
    paths.qa_accounts_discover_sql(yard, "QA-A1").unlink()
    state = yard / ".yard-qa" / "requirements" / "QA-A1" / "auth-local-has_perm.json"
    state.write_text("{}", encoding="utf-8")

    r = client.post("/api/requirements/QA-A1/accounts/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["dropped"] == ["has_perm"]
    assert not state.exists()
