"""Ticket review routes gaps by covers and does not rewrite TICKETS.md on a pass."""

from pathlib import Path

from dev_yard import status as st
from dev_yard.bug_tickets import split_review_findings
from dev_yard.runners import Runner, RunResult
from dev_yard.service import (
    _implement_prompt_extra,
    implement,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    review,
    ticket_delete,
)
from dev_yard.tickets import Ticket


class _Verdict(Runner):
    def __init__(self, result: RunResult) -> None:
        self.result = result

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        return self.result


def _yard(tmp_path: Path, git_src: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-7", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: 列表\n- repo: backend\n- depends_on:\n- parallel: false\n- covers: FR-001 US1\n",
        encoding="utf-8",
    )
    (d / "SPEC.md").write_text(
        "# Spec\n\n### US1\n\n- US1/AC1: Given x, When y, Then z\n\n- FR-001: 列显示资源标题\n",
        encoding="utf-8",
    )
    req_freeze(yard, "AB-7")
    # FR-002 lands after freeze so the residual gap is real work the ticket
    # does not cover, while freeze itself still sees a fully covered spec.
    spec = d / "SPEC.md"
    spec.write_text(
        spec.read_text(encoding="utf-8") + "\n- FR-002: 筛选仍按地块标题\n",
        encoding="utf-8",
    )
    return yard


def test_split_keeps_legacy_findings_on_the_ticket():
    ticket = Ticket(id="T1", title="t", repo="backend")
    findings = [{"id": "F1", "gap": "missing", "ref": "FR-002", "repo": "backend"}]
    in_ticket, residual, _unreq = split_review_findings(ticket, findings)
    assert [item["id"] for item in in_ticket] == ["F1"]
    assert residual == []


def test_pass_does_not_change_tickets(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src)
    implement(yard, "AB-7", ["T1"], runner=_Verdict(RunResult(True, "impl")))
    before = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    review(
        yard,
        "AB-7",
        ["T1"],
        runner=_Verdict(
            RunResult(
                True,
                "ok",
                verdict="passed",
                findings=[{"id": "F9", "title": "多余", "repo": "backend", "gap": "unrequested", "ref": "FR-001", "detail": "多了一个字段"}],
            )
        ),
    )
    after = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    assert after == before
    assert st.load(yard, "AB-7")["tickets"]["T1"]["state"] == "done"


def test_in_ticket_gap_blocks_without_rewriting_tickets(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src)
    implement(yard, "AB-7", ["T1"], runner=_Verdict(RunResult(True, "impl")))
    before = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    review(
        yard,
        "AB-7",
        ["T1"],
        runner=_Verdict(
            RunResult(
                True,
                "缺了",
                verdict="failed",
                findings=[{"id": "F1", "title": "列", "repo": "backend", "gap": "missing", "ref": "FR-001", "detail": "列还是旧的"}],
            )
        ),
    )
    after = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    assert after == before
    slot = st.load(yard, "AB-7")["tickets"]["T1"]
    assert slot["state"] == "blocked"
    assert slot["review_findings"][0]["ref"] == "FR-001"


def test_residual_gap_appends_a_ticket_and_accepts_this_one(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src)
    implement(yard, "AB-7", ["T1"], runner=_Verdict(RunResult(True, "impl")))
    before = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    review(
        yard,
        "AB-7",
        ["T1"],
        runner=_Verdict(
            RunResult(
                True,
                "另一条没做",
                verdict="failed",
                findings=[{"id": "F2", "title": "筛选", "repo": "backend", "gap": "missing", "ref": "FR-002", "detail": "筛选还在搜项目名"}],
            )
        ),
    )
    text = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    assert text.startswith(before.rstrip())
    assert "## T1:" in text
    assert "- covers: FR-001 US1" in text
    assert "## B1:" in text
    assert "- source: review" in text
    assert "- finding: F2" in text
    assert "FR-002" in text
    assert st.load(yard, "AB-7")["tickets"]["T1"]["state"] == "done"


def test_residual_with_unknown_repo_stays_on_this_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src)
    before = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    implement(yard, "AB-7", ["T1"], runner=_Verdict(RunResult(True, "impl")))
    review(
        yard,
        "AB-7",
        ["T1"],
        runner=_Verdict(
            RunResult(
                True,
                "仓名不对",
                verdict="failed",
                findings=[{"id": "F3", "title": "筛选", "repo": "other", "gap": "missing", "ref": "FR-002", "detail": "没处落"}],
            )
        ),
    )
    text = (yard / "reqs" / "AB-7" / "TICKETS.md").read_text(encoding="utf-8")
    assert text == before
    slot = st.load(yard, "AB-7")["tickets"]["T1"]
    assert slot["state"] == "blocked"
    assert slot["review_findings"][0]["ref"] == "FR-002"


def test_in_ticket_prompt_does_not_ask_to_fix_the_whole_report():
    prompt = _implement_prompt_extra(
        "T1",
        "列表",
        "backend",
        "另外把 FR-002 也改了",
        prior_verdict="failed",
        review_findings=[{"gap": "missing", "ref": "FR-001", "detail": "列还是旧的"}],
    )
    assert "Fix only these" in prompt
    assert "FR-001" in prompt
    assert "for context only" in prompt
    assert "Fix hard violations" not in prompt


def test_review_gap_ticket_can_be_deleted(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-9", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: 列表\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## B1: 筛选\n- repo: backend\n- depends_on:\n- parallel: false\n"
        "- source: review\n- finding: F2\n",
        encoding="utf-8",
    )
    out = ticket_delete(yard, "AB-9", "B1")
    assert out["source"] == "review"
    assert "## B1:" not in (d / "TICKETS.md").read_text(encoding="utf-8")


def test_freeze_refuses_uncovered_ids(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-8", source="none")
    (d / "SPEC.md").write_text("# Spec\n\n- FR-001: 要做\n", encoding="utf-8")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n",
        encoding="utf-8",
    )
    from dev_yard.service import req_freeze

    try:
        req_freeze(yard, "AB-8")
    except ValueError as exc:
        assert "FR-001" in str(exc)
    else:
        raise AssertionError("freeze should reject an uncovered FR")
