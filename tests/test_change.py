from pathlib import Path

import pytest

from dev_yard import qa_review, service, stages
from dev_yard import status as st
from dev_yard.runners import RunResult
from dev_yard.service import init_yard, repo_add, req_change, req_freeze, req_open
from dev_yard.tickets import load_tickets


def _yard(tmp_path: Path, git_src: Path, monkeypatch) -> Path:
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    return yard


def _frozen(tmp_path: Path, git_src: Path, monkeypatch, jira: str = "AB-1") -> Path:
    yard = _yard(tmp_path, git_src, monkeypatch)
    d, _ = req_open(yard, jira, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    (d / "SPEC.md").write_text("# Spec\n\n## Implementation Decisions\n\n- none\n")
    req_freeze(yard, jira)
    return yard


def _fake_run_stage(monkeypatch, *, ok: bool = True, on_spec=None):
    calls: list[dict] = []

    def fake(root, stage, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
        spec = stage if isinstance(stage, stages.StageSpec) else stages.load_registry(root)[stage]
        calls.append({"name": spec.name, "prompt_extra": prompt_extra, "runner": runner})
        if spec.name == "spec" and ok and on_spec is not None:
            on_spec(Path(root) / "reqs" / jira / "SPEC.md")
        return RunResult(ok=ok, summary="ok" if ok else "boom")

    monkeypatch.setattr(service, "run_stage", fake)
    return calls


def test_change_requires_frozen(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _yard(tmp_path, git_src, monkeypatch)
    req_open(yard, "AB-1", source="none")
    with pytest.raises(ValueError, match="frozen/testing"):
        req_change(yard, "AB-1", "加一条规则", repo="backend")


def test_change_rejects_unfrozen_repo(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _yard(tmp_path, git_src, monkeypatch)
    d, _ = req_open(yard, "AB-1", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-1")
    repo_add(yard, "other", str(git_src), "main", "be", None)
    with pytest.raises(ValueError, match="没有冻结 worktree"):
        req_change(yard, "AB-1", "x", repo="other")


def test_change_rejects_done(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    data = st.load(yard, "AB-1")
    data["phase"] = "done"
    st.save(yard, "AB-1", data)
    with pytest.raises(ValueError, match="方案级"):
        req_change(yard, "AB-1", "x", repo="backend")


def test_change_appends_note_and_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    original = (d / "REQUIREMENT.md").read_text()
    _fake_run_stage(
        monkeypatch,
        on_spec=lambda p: p.write_text(
            "# Spec\n\n## Implementation Decisions\n\n- old\n\n## Other\n"
        ),
    )

    out = req_change(yard, "AB-1", "补一条规则：外聘可同时挂靠", repo="backend")

    assert out["change_id"] == "c1"
    assert out["ticket"] == "T2"
    req_text = (d / "REQUIREMENT.md").read_text()
    assert original.strip() in req_text
    assert "## 变更记录" in req_text
    assert "补一条规则：外聘可同时挂靠" in req_text
    tickets = (d / "TICKETS.md").read_text()
    assert "## T2:" in tickets
    assert "- source: light" in tickets
    assert "- change: c1" in tickets
    assert "## T1:" in tickets  # existing ticket untouched
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T2"]["state"] == "ready"
    assert data["tickets"]["T2"]["repo"] == "backend"
    assert data["changes"][0]["id"] == "c1"
    assert data["changes"][0]["ticket"] == "T2"
    assert data["changes"][0]["contract_touched"] is True
    assert data["changes"][0]["stale"]["qa"] is False


def test_change_note_is_sanitized(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    _fake_run_stage(monkeypatch)
    req_change(yard, "AB-1", "第一行\n## T9: 伪造票\n- 注入", repo="backend")
    tickets = (d / "TICKETS.md").read_text()
    assert not any(line.strip().startswith("## T9") for line in tickets.splitlines())
    from dev_yard.tickets import load_tickets

    assert {t.id for t in load_tickets(d)} == {"T1", "T2"}


def test_change_is_idempotent_for_same_note(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    _fake_run_stage(monkeypatch)

    first = req_change(yard, "AB-1", "同一条变更", repo="backend")
    second = req_change(yard, "AB-1", "同一条变更", repo="backend")

    assert first["change_id"] == second["change_id"] == "c1"
    assert first["ticket"] == second["ticket"] == "T2"
    data = st.load(yard, "AB-1")
    assert len(data["changes"]) == 1
    assert data["changes"][0]["ticket"] == "T2"
    tickets = (d / "TICKETS.md").read_text()
    assert tickets.count("## T2:") == 1
    assert (d / "REQUIREMENT.md").read_text().count("### c1 ·") == 1
    assert {t.id for t in load_tickets(d)} == {"T1", "T2"}


def test_change_second_different_note_gets_next_ids(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    req_change(yard, "AB-1", "变更一", repo="backend")
    out = req_change(yard, "AB-1", "变更二", repo="backend")
    assert out["change_id"] == "c2"
    assert out["ticket"] == "T3"
    assert len(st.load(yard, "AB-1")["changes"]) == 2


def test_change_avoids_repo_less_ticket_id(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T5: 无仓票\n- depends_on:\n"
    )
    _fake_run_stage(monkeypatch)
    out = req_change(yard, "AB-1", "x", repo="backend")
    assert out["ticket"] == "T6"


def test_change_contract_section_skeleton(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    (d / "SPEC.md").write_text("# Spec\n\n## Contracts (APIs / events / fields)\n\n- POST /x\n")
    _fake_run_stage(
        monkeypatch,
        on_spec=lambda p: p.write_text(
            "# Spec\n\n## Contracts (APIs / events / fields)\n\n- POST /x\n- POST /y\n"
        ),
    )
    out = req_change(yard, "AB-1", "加接口", repo="backend")
    assert out["contract_touched"] is True


def test_change_spec_failure_creates_no_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    before = (d / "TICKETS.md").read_text()
    _fake_run_stage(monkeypatch, ok=False)
    with pytest.raises(RuntimeError, match="spec failed"):
        req_change(yard, "AB-1", "x", repo="backend")
    assert (d / "TICKETS.md").read_text() == before
    data = st.load(yard, "AB-1")
    assert not data.get("changes")
    # change note is kept for a retry
    assert "## 变更记录" in (d / "REQUIREMENT.md").read_text()


def test_change_marks_qa_stale(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    qa = yard / "reqs" / "AB-1" / "qa"
    case = qa / "cases" / "grp" / "case-01.md"
    case.parent.mkdir(parents=True)
    case.write_text("---\nid: case-01\ntitle: t\n---\n\n## 预期\n- UI: 外聘\n")
    qa_review.approve_cases(qa)
    assert qa_review.review_payload(qa)["approved"] is True

    _fake_run_stage(monkeypatch)
    out = req_change(yard, "AB-1", "改文案", repo="backend")

    assert out["qa_stale"] is True
    payload = qa_review.review_payload(qa)
    assert payload["approved"] is False
    assert payload["stale_reason"] == "轻量变更 c1"
    can_run, reason = qa_review.review_gate(qa)
    assert can_run is False
    assert "复核" in reason
    # re-approving clears it
    qa_review.approve_cases(qa)
    assert qa_review.review_payload(qa)["approved"] is True


def test_clear_stale_keeps_approval_when_cases_unchanged(tmp_path: Path):
    qa = tmp_path / "qa"
    case = qa / "cases" / "case-01.md"
    case.parent.mkdir(parents=True)
    case.write_text("---\nid: case-01\ntitle: t\n---\n")
    qa_review.approve_cases(qa)
    qa_review.mark_stale(qa, "轻量变更 c1")
    assert qa_review.review_payload(qa)["approved"] is False
    qa_review.clear_stale(qa)
    payload = qa_review.review_payload(qa)
    assert payload["stale_reason"] == ""
    assert payload["approved"] is True


def test_clear_stale_still_requires_review_after_case_edit(tmp_path: Path):
    qa = tmp_path / "qa"
    case = qa / "cases" / "case-01.md"
    case.parent.mkdir(parents=True)
    case.write_text("---\nid: case-01\ntitle: t\n---\n")
    qa_review.approve_cases(qa)
    qa_review.mark_stale(qa, "轻量变更 c1")
    case.write_text("---\nid: case-01\ntitle: changed\n---\n")
    qa_review.clear_stale(qa)
    payload = qa_review.review_payload(qa)
    assert payload["stale_reason"] == ""
    assert payload["approved"] is False
    assert payload["stale"] is True


def test_change_grill_injects_note(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    calls = _fake_run_stage(monkeypatch)
    req_change(yard, "AB-1", "需要澄清的变更", repo="backend", grill=True)
    names = [c["name"] for c in calls]
    assert names == ["grill", "spec"]
    assert calls[0]["prompt_extra"] == "需要澄清的变更"


def test_change_run_implements_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.runners import Runner

    class FakeRunner(Runner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            return RunResult(ok=True, summary="done")

    yard = _frozen(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    out = req_change(
        yard,
        "AB-1",
        "实现它",
        repo="backend",
        run=True,
        runner_factory=lambda name: FakeRunner(),
    )
    assert out["ran"] == ["T2"]
    assert st.load(yard, "AB-1")["tickets"]["T2"]["state"] == "implemented"


def test_spec_and_grill_protect_requirement():
    assert "REQUIREMENT.md" in stages.BUILTIN_STAGES["spec"].protects
    assert "GRILL.md" in stages.BUILTIN_STAGES["spec"].protects
    assert "REQUIREMENT.md" in stages.BUILTIN_STAGES["grill"].protects


def test_board_change_gating(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.reqboard import requirement_detail

    yard = _frozen(tmp_path, git_src, monkeypatch)
    detail = requirement_detail(yard, "AB-1")
    change = next(a for a in detail.actions if a.id == "change")
    assert change.enabled is True
    assert detail.changes == []

    data = st.load(yard, "AB-1")
    data["phase"] = "done"
    st.save(yard, "AB-1", data)
    detail = requirement_detail(yard, "AB-1")
    change = next(a for a in detail.actions if a.id == "change")
    assert change.enabled is False
    assert "方案级" in change.reason


def test_next_ticket_id_and_change_field():
    from dev_yard.tickets import Ticket, next_ticket_id, parse_tickets

    parsed = parse_tickets(
        "## T1: a\n- repo: r\n## T3: b\n- repo: r\n## B7: bug\n- repo: r\n"
    )
    assert next_ticket_id(parsed) == "T4"
    assert next_ticket_id([Ticket(id="B9", title="t", repo="r")]) == "T1"
    parsed = parse_tickets("## T2: a\n- repo: r\n- change: c5\n")
    assert parsed[0].change == "c5"


def test_web_job_dispatches_change(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.web.jobs import JobRunner, default_execute

    yard = _frozen(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "change",
        "AB-1",
        extra={"note": "web 变更", "repo": "backend"},
    )
    assert job.state == "ok", job.log
    assert "change c1: +T2" in job.log
    assert st.load(yard, "AB-1")["changes"][0]["note"] == "web 变更"


def test_cli_req_changes(tmp_path: Path, git_src: Path, monkeypatch):
    from typer.testing import CliRunner

    from dev_yard.cli import app

    yard = _frozen(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    req_change(yard, "AB-1", "cli 变更", repo="backend", actor="cli")
    monkeypatch.chdir(yard)
    result = CliRunner().invoke(app, ["req", "changes", "AB-1"])
    assert result.exit_code == 0
    assert "cli 变更" in result.stdout
    assert "ticket=T2" in result.stdout


def _frozen_two(tmp_path: Path, git_src: Path, monkeypatch) -> Path:
    import conftest

    yard = _yard(tmp_path, git_src, monkeypatch)
    front = conftest.make_git_repo(tmp_path / "srcfe")
    repo_add(yard, "frontend", str(front), "main", "fe", str(front))
    d, _ = req_open(yard, "AB-1", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: frontend\n- depends_on:\n- parallel: false\n"
    )
    (d / "SPEC.md").write_text("# Spec\n\n## Implementation Decisions\n\n- none\n")
    req_freeze(yard, "AB-1")
    return yard


def test_change_two_repos_one_spec(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    calls = _fake_run_stage(monkeypatch)

    out = req_change(
        yard, "AB-1", "两边一起改", repos=["backend", "frontend"]
    )

    assert out["change_id"] == "c1"
    assert out["tickets"] == ["T3", "T4"]
    assert out["ticket"] == "T3"
    assert out["repos"] == ["backend", "frontend"]
    assert [c["name"] for c in calls] == ["spec"]
    assert "backend" in calls[0]["prompt_extra"]
    assert "frontend" in calls[0]["prompt_extra"]
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T3"]["state"] == "ready"
    assert data["tickets"]["T3"]["repo"] == "backend"
    assert data["tickets"]["T4"]["state"] == "ready"
    assert data["tickets"]["T4"]["repo"] == "frontend"
    assert data["changes"][0]["tickets"] == ["T3", "T4"]
    assert data["changes"][0]["ticket"] == "T3"
    assert data["changes"][0]["repo"] == "backend"
    req_text = (d / "REQUIREMENT.md").read_text()
    assert "- 仓：backend, frontend" in req_text
    assert "- 票：T3, T4" in req_text
    tickets = load_tickets(d)
    assert [t.id for t in tickets if t.change == "c1"] == ["T3", "T4"]


def test_change_same_note_rejects_different_repos(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    _fake_run_stage(monkeypatch)
    req_change(yard, "AB-1", "两边一起改", repos=["backend"])
    with pytest.raises(ValueError, match="同一说明已记为 c1"):
        req_change(yard, "AB-1", "两边一起改", repos=["backend", "frontend"])
    assert {t.id for t in load_tickets(d)} == {"T1", "T2", "T3"}


def test_change_same_repos_skips_second_spec(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    calls = _fake_run_stage(monkeypatch)
    first = req_change(yard, "AB-1", "两边一起改", repos=["backend", "frontend"])
    second = req_change(yard, "AB-1", "两边一起改", repos=["backend", "frontend"])
    assert first["tickets"] == second["tickets"] == ["T3", "T4"]
    assert [c["name"] for c in calls] == ["spec"]
    assert len(st.load(yard, "AB-1")["changes"]) == 1
    with pytest.raises(ValueError, match="同一说明已记为 c1"):
        req_change(yard, "AB-1", "两边一起改", repos=["frontend", "backend"])


def test_change_rejects_alias_that_cannot_round_trip(
    tmp_path: Path, git_src: Path, monkeypatch
):
    yard = _frozen(tmp_path, git_src, monkeypatch)
    with pytest.raises(ValueError, match="不能包含空格或逗号"):
        req_change(yard, "AB-1", "x", repo="my api")


def test_change_ordered_flip_rejected(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    calls = _fake_run_stage(monkeypatch)
    req_change(
        yard, "AB-1", "先后端再前端", repos=["backend", "frontend"], ordered=True
    )
    with pytest.raises(ValueError, match="要改依赖方式"):
        req_change(
            yard, "AB-1", "先后端再前端", repos=["backend", "frontend"], ordered=False
        )
    assert [c["name"] for c in calls] == ["spec"]
    assert "- 顺序：是" in (yard / "reqs" / "AB-1" / "REQUIREMENT.md").read_text()


def test_change_partial_tickets_skip_spec(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.tickets import append_light_ticket

    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    req = yard / "reqs" / "AB-1"
    note = (req / "REQUIREMENT.md").read_text().rstrip() + (
        "\n\n## 变更记录\n\n"
        "### c1 · 2026-01-01 00:00 (cli)\n"
        "补后一张\n\n"
        "- 仓：backend, frontend\n"
        "- 顺序：是\n"
    )
    (req / "REQUIREMENT.md").write_text(note)
    append_light_ticket(
        req,
        ticket_id="T3",
        title="轻量变更：补后一张",
        repo="backend",
        note="补后一张",
        change_id="c1",
    )
    calls = _fake_run_stage(monkeypatch)
    out = req_change(
        yard, "AB-1", "补后一张", repos=["backend", "frontend"], ordered=True
    )
    assert calls == []
    assert out["tickets"] == ["T3", "T4"]
    assert out["contract_touched"] is True
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T4"]["state"] == "pending"
    assert data["tickets"]["T4"]["depends_on"] == ["T3"]
    assert data["changes"][0]["contract_touched"] is True


def test_change_ordered_run_leaves_the_rest_pending(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.runners import Runner

    class FakeRunner(Runner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            return RunResult(ok=True, summary="done")

    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    out = req_change(
        yard,
        "AB-1",
        "先后端再前端",
        repos=["backend", "frontend"],
        ordered=True,
        run=True,
        runner_factory=lambda name: FakeRunner(),
    )
    assert out["ran"] == ["T3"]
    assert out["tickets"] == ["T3", "T4"]
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T3"]["state"] == "implemented"
    assert data["tickets"]["T4"]["state"] == "pending"


def test_change_ordered_dependency(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    d = yard / "reqs" / "AB-1"
    calls = _fake_run_stage(monkeypatch)
    req_change(
        yard,
        "AB-1",
        "先后端再前端",
        repos=["backend", "frontend"],
        ordered=True,
    )
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T3"]["state"] == "ready"
    assert data["tickets"]["T4"]["state"] == "pending"
    assert data["tickets"]["T4"]["depends_on"] == ["T3"]
    assert data["changes"][0]["ordered"] is True
    assert "Ticket order: backend -> frontend" in calls[0]["prompt_extra"]
    text = (d / "TICKETS.md").read_text()
    assert "- depends_on: T3" in text


def test_change_two_repos_run(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.runners import Runner

    class FakeRunner(Runner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            return RunResult(ok=True, summary="done")

    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    out = req_change(
        yard,
        "AB-1",
        "一起实现",
        repos=["backend", "frontend"],
        run=True,
        runner_factory=lambda name: FakeRunner(),
    )
    assert out["ran"] == ["T3", "T4"]
    data = st.load(yard, "AB-1")
    assert data["tickets"]["T3"]["state"] == "implemented"
    assert data["tickets"]["T4"]["state"] == "implemented"


def test_web_job_change_many_repos(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.web.jobs import JobRunner, default_execute

    yard = _frozen_two(tmp_path, git_src, monkeypatch)
    _fake_run_stage(monkeypatch)
    job = JobRunner(yard, execute=default_execute, sync=True).submit(
        "change",
        "AB-1",
        extra={"note": "web 两边", "repos": ["backend", "frontend"]},
    )
    assert job.state == "ok", job.log
    assert "change c1: +T3, +T4" in job.log


def test_doc_fingerprint(tmp_path: Path):
    req = tmp_path / "reqs" / "AB-1"
    req.mkdir(parents=True)
    (req / "REQUIREMENT.md").write_text("hello")
    fp = service.doc_fingerprint(req)
    assert fp["requirement"].startswith("sha256:")
    assert fp["spec"] is None
