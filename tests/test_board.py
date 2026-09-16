from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.service import init_yard, repo_add, req_freeze, req_open
from dev_yard.web.board import (
    DOC_FILES,
    available_actions,
    list_requirements,
    parse_requirement_title,
    requirement_detail,
    save_doc,
)


def _yard(tmp_path: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    return yard


def test_list_empty(tmp_path: Path):
    yard = _yard(tmp_path)
    assert list_requirements(yard) == []


def test_list_skips_shared_docs_dir(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    adr = yard / "reqs" / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001.md").write_text("# adr\n")
    (yard / "reqs" / "CONTEXT.md").write_text("# glossary\n")
    items = list_requirements(yard)
    assert [i.jira for i in items] == ["AB-1"]
    assert requirement_detail(yard, "docs") is None
    assert requirement_detail(yard, "DOCS") is None


def test_list_and_detail_after_open(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    items = list_requirements(yard)
    assert len(items) == 1
    assert items[0].jira == "AB-1"
    assert items[0].title is None
    assert items[0].phase == "open"
    assert items[0].next_label == "grill"

    detail = requirement_detail(yard, "AB-1")
    assert detail is not None
    assert detail.title is None
    assert detail.phase == "open"
    assert [d.filename for d in detail.docs] == list(DOC_FILES.values())
    assert all(d.exists for d in detail.docs)
    assert not any(d.filled for d in detail.docs)
    assert detail.tickets == []
    ids = {a.id: a for a in detail.actions}
    assert ids["grill"].enabled
    assert ids["spec"].enabled
    assert not ids["freeze"].enabled
    assert not ids["implement"].enabled
    assert not ids["review"].enabled
    assert not ids["contract"].enabled
    assert not ids["fix-contract"].enabled


def test_tickets_enable_freeze(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-2", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: backend api\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: frontend\n- repo: frontend\n- depends_on: T1\n- parallel: false\n"
    )
    detail = requirement_detail(yard, "AB-2")
    assert [t.id for t in detail.tickets] == ["T1", "T2"]
    assert detail.tickets[0].title == "backend api"
    assert detail.tickets[1].depends_on == ["T1"]
    assert detail.next_label == "freeze"
    ids = {a.id: a for a in available_actions(detail, yard)}
    assert ids["freeze"].enabled
    assert not ids["implement"].enabled


def test_frozen_ready_implement(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-3", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-3")
    detail = requirement_detail(yard, "AB-3")
    assert detail.phase == "frozen"
    assert detail.next_label == "implement"
    assert detail.tickets[0].state == "ready"
    assert detail.tickets[0].can_implement
    assert not detail.tickets[0].can_review
    ids = {a.id: a for a in detail.actions}
    assert ids["implement"].enabled
    assert ids["freeze"].enabled
    assert not ids["review"].enabled
    assert ids["contract"].enabled
    assert not ids["fix-contract"].enabled
    assert detail.worktrees


def test_ready_for_submit_test_marks_testing_current(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-7", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-7")
    data = st.load(yard, "AB-7")
    data["tickets"]["T1"]["state"] = "done"
    data["contract_review"] = "passed"
    st.save(yard, "AB-7", data)
    detail = requirement_detail(yard, "AB-7")
    assert detail.next_label == "submit-test"
    by_id = {s.id: s for s in detail.steps}
    assert by_id["review"].done
    assert not by_id["testing"].done
    assert by_id["testing"].current
    assert not by_id["done"].done
    assert not by_id["done"].current
    ids = {a.id: a for a in detail.actions}
    assert ids["submit-test"].enabled
    assert ids["fill-test-report"].enabled
    assert ids["freeze"].enabled


def test_freeze_disabled_after_submit_test(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-77", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-77")
    data = st.load(yard, "AB-77")
    data["tickets"]["T1"]["state"] = "done"
    data["contract_review"] = "passed"
    data["phase"] = "testing"
    st.save(yard, "AB-77", data)
    ids = {a.id: a for a in requirement_detail(yard, "AB-77").actions}
    assert not ids["freeze"].enabled
    assert "CLI --force" in ids["freeze"].reason


def test_legacy_done_without_test_report_is_not_complete(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-8", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-8")
    data = st.load(yard, "AB-8")
    data["tickets"]["T1"]["state"] = "done"
    data["phase"] = "done"
    data["contract_review"] = "passed"
    st.save(yard, "AB-8", data)
    detail = requirement_detail(yard, "AB-8")
    assert detail.next_label == "submit-test"
    assert {s.id: s.current for s in detail.steps}["testing"]
    ids = {a.id: a for a in detail.actions}
    assert ids["submit-test"].enabled


def test_fix_contract_enabled_with_summary(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-6", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "AB-6")
    data = st.load(yard, "AB-6")
    data["contract_summary"] = "gap"
    data["contract_review"] = "failed"
    st.save(yard, "AB-6", data)
    ids = {a.id: a for a in requirement_detail(yard, "AB-6").actions}
    assert ids["fix-contract"].enabled


def test_missing_requirement_is_none(tmp_path: Path):
    yard = _yard(tmp_path)
    assert requirement_detail(yard, "NO-1") is None


def test_save_doc_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    req_open(yard, "AB-4", source="none")
    save_doc(yard, "AB-4", "grill", "# Grill — AB-4\n\nQ: scope?\nA: this ticket only.\n")
    detail = requirement_detail(yard, "AB-4")
    grill = next(d for d in detail.docs if d.slug == "grill")
    assert grill.filled
    assert "this ticket only" in grill.text
    assert detail.next_label == "spec"


def test_pending_grill_round_keeps_next_as_grill(tmp_path: Path, monkeypatch):
    import json

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-5", source="none")
    (d / "GRILL.md").write_text("# Grill — AB-5\n\n## Round 1 — answers\n\n- **Q1**：选 A\n")
    (d / ".grill-round.json").write_text(
        json.dumps(
            {
                "done": False,
                "round": 2,
                "questions": [{"id": "Q1", "title": "范围", "options": [{"id": "A", "label": "只这张票"}]}],
            }
        )
    )
    detail = requirement_detail(yard, "AB-5")
    grill = next(d for d in detail.docs if d.slug == "grill")
    assert grill.filled
    assert detail.next_label == "grill"
    by_id = {s.id: s for s in detail.steps}
    assert not by_id["grill"].done
    assert by_id["grill"].current


def test_parse_title_from_paragraph_after_h1():
    text = "# PG-1\n\n促单任务支持指定给所属人\n\n## Jira\n"
    assert parse_requirement_title(text, "PG-1") == "促单任务支持指定给所属人"


def test_parse_title_prefers_summary_over_lede():
    text = (
        "# PG-1\n\n长说明（不是票标题）\n\n## Jira\n\n"
        "| 项目 | 内容 |\n| --- | --- |\n"
        "| Summary | 列表增加跟进人 |\n"
    )
    assert parse_requirement_title(text, "PG-1") == "列表增加跟进人"


def test_parse_title_from_summary_table_when_no_lede():
    text = (
        "# PG-1\n\n## Jira\n\n"
        "| 项目 | 内容 |\n| --- | --- |\n"
        "| Key | PG-1 |\n"
        "| Summary | 列表增加跟进人 |\n"
    )
    assert parse_requirement_title(text, "PG-1") == "列表增加跟进人"


def test_parse_title_ignores_skeleton_key_repeat():
    assert parse_requirement_title("# AB-1\n\nAB-1\n\n", "AB-1") is None


def test_list_and_detail_expose_requirement_title(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-9", source="none")
    (d / "REQUIREMENT.md").write_text(
        "# AB-9\n\n给所属人指派促单任务\n\n## Jira\n", encoding="utf-8"
    )
    items = list_requirements(yard)
    assert items[0].title == "给所属人指派促单任务"
    detail = requirement_detail(yard, "AB-9")
    assert detail is not None
    assert detail.title == "给所属人指派促单任务"


# ---- plugin stages on the board ----


def _plugin(yard: Path, name: str = "deploy", requires_phase=None) -> None:
    pdir = yard / "plugins" / name
    pdir.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "title": "部署", "tools": ["read"]}
    if requires_phase:
        meta["requires_phase"] = requires_phase
    import yaml

    (pdir / "plugin.yaml").write_text(
        yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
    )
    (pdir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (yard / "yard.yaml").write_text(
        f"plugins: [plugins/{name}]\n", encoding="utf-8"
    )


def test_plugin_action_appended(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    _plugin(yard, "deploy", requires_phase="frozen")
    req_open(yard, "AB-40", source="none")
    detail = requirement_detail(yard, "AB-40")
    assert detail.phase == "open"
    actions = available_actions(detail, yard)
    deploy = next(a for a in actions if a.id == "deploy")
    assert deploy.label == "部署"
    assert not deploy.enabled
    assert "frozen" in deploy.reason
    from dev_yard.web.board import PIPELINE

    assert {s.id for s in detail.steps} == set(PIPELINE)
    assert "deploy" not in {s.id for s in detail.steps}


def test_plugin_action_enabled_when_phase_matches(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    _plugin(yard, "deploy", requires_phase="frozen")
    d, _ = req_open(yard, "AB-41", source="none")
    import yaml

    (d / "STATUS.yaml").write_text(
        "phase: frozen\ntickets: {}\n", encoding="utf-8"
    )
    detail = requirement_detail(yard, "AB-41")
    actions = available_actions(detail, yard)
    deploy = next(a for a in actions if a.id == "deploy")
    assert deploy.enabled
    assert deploy.reason == ""


def test_plugin_without_phase_gate_always_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    _plugin(yard, "scan")
    req_open(yard, "AB-42", source="none")
    detail = requirement_detail(yard, "AB-42")
    actions = available_actions(detail, yard)
    scan = next(a for a in actions if a.id == "scan")
    assert scan.enabled


@pytest.mark.parametrize("name,label", [("grill", "对齐"), ("spec", "写规约"), ("tickets", "拆票"), ("review", "审查")])
def test_overriding_builtin_does_not_duplicate_action(tmp_path: Path, monkeypatch, name, label):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    _plugin(yard, name)
    req_open(yard, "AB-44", source="none")
    detail = requirement_detail(yard, "AB-44")
    actions = available_actions(detail, yard)
    matched = [a for a in actions if a.id == name]
    assert len(matched) == 1
    assert matched[0].label == label


def test_stage_runs_surfaced(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-43", source="none")
    import yaml

    data = yaml.safe_load((d / "STATUS.yaml").read_text(encoding="utf-8"))
    data["stage_runs"] = {"deploy": {"at": "2026-09-13T00:00:00Z", "ok": True, "summary": "s"}}
    (d / "STATUS.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    detail = requirement_detail(yard, "AB-43")
    assert detail.stage_runs["deploy"]["ok"] is True
