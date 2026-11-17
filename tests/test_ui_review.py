from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dev_yard import gitops, paths
from dev_yard import status as st
from dev_yard.bug_tickets import spawn_fix_tickets
from dev_yard.config import load_repos
from dev_yard.reqboard import available_actions, requirement_detail
from dev_yard.runners import Runner, RunResult
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review
from dev_yard.stages import load_registry
from dev_yard.tickets import load_tickets


class _FakeRunner(Runner):
    def __init__(self, verdict: str = "passed", summary: str = "all good", findings: list | None = None):
        super().__init__()
        self.verdict = verdict
        self.summary = summary
        self.findings = findings or []
        self.calls: list[dict[str, Any]] = []

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path] | None = None, repo: str | None = None) -> RunResult:
        self.calls.append({
            "prompt": prompt,
            "cwd": cwd,
            "extra_read_paths": extra_read_paths or [],
            "repo": repo,
        })
        return RunResult(ok=True, verdict=self.verdict, summary=self.summary, findings=self.findings)


def _setup_yard_with_repo(tmp_path: Path, git_src: Path, jira: str = "PROJ-101") -> tuple[Path, Path]:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "frontend", str(git_src), "main", "fe", str(git_src))
    req_dir, _ = req_open(yard, jira, source="none")
    (req_dir / "TICKETS.md").write_text(
        "## T1: 列表与表单开发\n"
        "- repo: frontend\n"
        "- depends_on:\n"
        "- parallel: false\n"
        "- covers: FR-001\n"
    )
    req_freeze(yard, jira)
    return yard, req_dir


def test_ui_review_stage_spec_registered(tmp_path: Path):
    reg = load_registry(tmp_path)
    assert "ui-review" in reg
    spec = reg["ui-review"]
    assert spec.skill == "ui-review"
    assert "submit_review" in spec.tools
    assert "read" in spec.tools


def test_ui_review_passed(tmp_path: Path, git_src: Path):
    yard, req_dir = _setup_yard_with_repo(tmp_path, git_src, "PROJ-101")
    data = st.load(yard, "PROJ-101")
    data["tickets"]["T1"]["state"] = "done"
    st.save(yard, "PROJ-101", data)

    runner = _FakeRunner(verdict="passed", summary="UI完全符合原型图要求")
    ran = review(yard, "PROJ-101", None, ui=True, runner=runner)
    assert ran == ["__ui__"]

    updated = st.load(yard, "PROJ-101")
    assert updated["ui_review"] == "passed"
    assert updated["ui_summary"] == "UI完全符合原型图要求"
    assert updated.get("ui_findings") == []


def test_ui_review_failed_spawns_ui_fix_tickets(tmp_path: Path, git_src: Path):
    yard, req_dir = _setup_yard_with_repo(tmp_path, git_src, "PROJ-102")
    data = st.load(yard, "PROJ-102")
    data["tickets"]["T1"]["state"] = "done"
    st.save(yard, "PROJ-102", data)

    findings = [
        {
            "id": "UI-001",
            "title": "缺少导出按钮和状态筛选框",
            "repo": "frontend",
            "detail": "原型图右上角有「导出」按钮，表格上方有状态下拉筛选",
            "files": ["src/views/list.vue"],
        }
    ]
    runner = _FakeRunner(verdict="failed", summary="缺少部分原型组件", findings=findings)
    ran = review(yard, "PROJ-102", None, ui=True, runner=runner)
    assert ran == ["__ui__"]

    updated = st.load(yard, "PROJ-102")
    assert updated["ui_review"] == "failed"
    assert len(updated["ui_findings"]) == 1
    assert updated["ui_findings"][0]["id"] == "UI-001"

    # Verify that a fix ticket was spawned into TICKETS.md
    tickets = load_tickets(req_dir)
    ui_tickets = [t for t in tickets if t.source == "ui"]
    assert len(ui_tickets) == 1
    b_ticket = ui_tickets[0]
    assert b_ticket.id.startswith("B")
    assert b_ticket.finding == "UI-001"
    assert b_ticket.repo == "frontend"

    # Detail check in reqboard
    detail = requirement_detail(yard, "PROJ-102")
    assert detail.ui_review == "failed"
    assert detail.next_label == "fix-ui"

    # Now implement --from-ui
    impl_runner = _FakeRunner(summary="已修复 UI 缺陷")
    impl_ran = implement(yard, "PROJ-102", [b_ticket.id], from_ui=True, runner=impl_runner)
    assert b_ticket.id in impl_ran
    assert any("UI-001" in call["prompt"] or "缺少导出按钮" in call["prompt"] for call in impl_runner.calls)


def test_cli_ui_review_and_implement_flags(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from dev_yard.cli import app

    cli_runner = CliRunner()
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)

    res = cli_runner.invoke(app, ["review", "--help"])
    assert res.exit_code == 0
    assert "--ui" in res.stdout

    res2 = cli_runner.invoke(app, ["implement", "--help"])
    assert res2.exit_code == 0
    assert "--from-ui" in res2.stdout

    res3 = cli_runner.invoke(app, ["ui-review", "--help"])
    assert res3.exit_code == 0


def test_ui_review_override(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.service import ui_review_override

    yard, req_dir = _setup_yard_with_repo(tmp_path, git_src, "PROJ-103")
    res = ui_review_override(yard, "PROJ-103", verdict="passed", summary="人工确认还原良好")
    assert res["ui_review"] == "passed"
    assert res["ui_summary"] == "人工确认还原良好"

    updated = st.load(yard, "PROJ-103")
    assert updated["ui_review"] == "passed"

    # Override with failed to ensure fix tickets spawn
    res_fail = ui_review_override(yard, "PROJ-103", verdict="failed", summary="人工复查漏了搜索框")
    assert res_fail["ui_review"] == "failed"
    tickets = load_tickets(req_dir)
    assert any(t.source == "ui" for t in tickets)

    # CLI review-override --ui
    from typer.testing import CliRunner
    from dev_yard.cli import app
    monkeypatch.chdir(yard)
    cli_runner = CliRunner()
    cli_res = cli_runner.invoke(app, ["review-override", "PROJ-103", "--ui", "-v", "passed", "-m", "CLI确认通过"])
    assert cli_res.exit_code == 0
    assert "Updated ui review: passed" in cli_res.stdout


def test_cli_dedicated_rejection_for_ui_review(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from dev_yard.cli import app

    cli_runner = CliRunner()
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)

    res = cli_runner.invoke(app, ["run", "ui-review", "PROJ-104"])
    assert res.exit_code == 2
    assert "use dev-yard review --ui" in res.output


def test_ui_ticket_delete_and_claim_run(tmp_path: Path, git_src: Path):
    from dev_yard.service import claim_run, ticket_delete, ui_review_override
    from dev_yard.doc_analyze import _EXEMPT_SOURCES

    assert "ui" in _EXEMPT_SOURCES

    yard, req_dir = _setup_yard_with_repo(tmp_path, git_src, "PROJ-105")
    ui_review_override(yard, "PROJ-105", verdict="failed", summary="需要修复按钮位置")
    tickets = load_tickets(req_dir)
    ui_tickets = [t for t in tickets if t.source == "ui"]
    assert len(ui_tickets) == 1
    tid = ui_tickets[0].id

    # Test claim_run for fix-ui
    claimed, previous = claim_run(yard, "PROJ-105", "fix-ui", [tid])
    assert claimed == [tid]

    # Revert state back to ready to test ticket_delete
    data = st.load(yard, "PROJ-105")
    data["tickets"][tid]["state"] = "ready"
    st.save(yard, "PROJ-105", data)

    # Test ticket_delete on ui ticket
    del_res = ticket_delete(yard, "PROJ-105", tid)
    assert del_res["ticket_id"] == tid
    remaining = load_tickets(req_dir)
    assert not any(t.id == tid for t in remaining)


def test_ui_web_job_dispatch(tmp_path: Path, git_src: Path):
    from fastapi.testclient import TestClient
    from dev_yard.web.app import create_app
    from dev_yard.service import ui_review_override

    yard, req_dir = _setup_yard_with_repo(tmp_path, git_src, "PROJ-106")
    client = TestClient(create_app(yard, sync_jobs=False))

    # Dispatch ui-review via API
    res = client.post("/api/requirements/PROJ-106/actions/ui-review", json={})
    assert res.status_code == 200
    assert any(j["action"] == "ui-review" for j in res.json().get("jobs", []))

    # Test fix-ui on a separate requirement with failed UI review
    _setup_yard_with_repo(tmp_path, git_src, "PROJ-107")
    ui_review_override(yard, "PROJ-107", verdict="failed", summary="缺样式")

    # Dispatch fix-ui via API
    res_fix = client.post("/api/requirements/PROJ-107/actions/fix-ui", json={})
    assert res_fix.status_code == 200
    assert any(j["action"] == "fix-ui" for j in res_fix.json().get("jobs", []))
    assert any(j["action"] == "fix-ui" for j in res_fix.json().get("jobs", []))
    assert any(j["action"] == "fix-ui" for j in res_fix.json().get("jobs", []))


