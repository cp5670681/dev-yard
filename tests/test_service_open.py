from pathlib import Path

from dev_yard.service import init_yard, req_open


def test_init_and_open(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    init_yard(tmp_path)
    assert (tmp_path / "repos.yaml").exists()
    d, warning = req_open(tmp_path, "ABC-1")
    assert warning
    assert (d / "REQUIREMENT.md").exists()
    assert (d / "GRILL.md").exists()
    assert (d / "SPEC.md").exists()
    assert (d / "TICKETS.md").exists()
