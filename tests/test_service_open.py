from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.service import init_yard, req_open


def test_init_and_open(tmp_path: Path, monkeypatch):
    for k in (
        "JIRA_BASE_URL",
        "JIRA_URL",
        "JIRA_USERNAME",
        "JIRA_USER",
        "JIRA_EMAIL",
        "JIRA_PASSWORD",
        "JIRA_TOKEN",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)
    init_yard(tmp_path)
    assert (tmp_path / "repos.yaml").exists()
    d, warning = req_open(tmp_path, "ABC-1", source="none")
    assert warning
    assert (d / "REQUIREMENT.md").exists()
    assert (d / "GRILL.md").exists()
    assert (d / "SPEC.md").exists()
    assert (d / "TICKETS.md").exists()
    assert "## T1" not in (d / "TICKETS.md").read_text()


def test_dry_run_does_not_write(tmp_path: Path):
    init_yard(tmp_path)
    d, warning = req_open(tmp_path, "ABC-2", source="claude", dry_run=True)
    assert "claude" in warning
    assert "(stdin prompt)" in warning
    assert not d.exists()
    assert not (tmp_path / "reqs" / "ABC-2" / "STATUS.yaml").exists()


def test_http_dry_run_does_not_write(tmp_path: Path):
    init_yard(tmp_path)
    d, warning = req_open(tmp_path, "ABC-3", source="http", dry_run=True)
    assert "no request" in warning
    assert not d.exists()


def test_reopen_refuses_later_phase(tmp_path: Path, monkeypatch):
    for k in ("JIRA_BASE_URL", "JIRA_URL"):
        monkeypatch.delenv(k, raising=False)
    init_yard(tmp_path)
    d, _ = req_open(tmp_path, "ABC-4", source="none")
    data = st.load(tmp_path, "ABC-4")
    data["phase"] = "frozen"
    st.save(tmp_path, "ABC-4", data)
    with pytest.raises(ValueError, match="phase=frozen"):
        req_open(tmp_path, "ABC-4", source="none")
    req_open(tmp_path, "ABC-4", source="none", force=True)
    assert st.load(tmp_path, "ABC-4")["phase"] == "open"


def test_claude_missing_does_not_delete_assets(tmp_path: Path, monkeypatch):
    init_yard(tmp_path)
    d = tmp_path / "reqs" / "ABC-5"
    assets = d / "assets"
    assets.mkdir(parents=True)
    (assets / "shot.png").write_text("img")
    monkeypatch.setenv("YARD_CLAUDE", "/definitely/missing-claude")
    with pytest.raises(FileNotFoundError, match="claude not found"):
        req_open(tmp_path, "ABC-5", source="claude")
    assert (assets / "shot.png").exists()
