from pathlib import Path

from dev_yard.runners import DryRunRunner
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review


def test_implement_dry_run(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-8")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-8")
    ran = implement(yard, "AB-8", None, runner=DryRunRunner())
    assert ran == ["T1"]
    reviewed = review(yard, "AB-8", None, runner=DryRunRunner())
    assert reviewed == ["T1"]
