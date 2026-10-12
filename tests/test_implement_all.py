"""One-shot implement: review each ticket, fix from the verdict, then the next."""

import re
import threading
from pathlib import Path

import pytest

from dev_yard import status as st
from dev_yard.reqboard import requirement_detail
from dev_yard.runners import JobCancelled, Runner, RunResult
from dev_yard.service import (
    implement,
    implement_until_done,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
)
from dev_yard.web.jobs import JobRunner


class Script(Runner):
    """Implement writes a file. Review follows a per-ticket verdict script."""

    def __init__(self, reviews: dict[str, list[str]]) -> None:
        self.reviews = {tid: list(plan) for tid, plan in reviews.items()}
        self.events: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self._last: dict[str, str] = {}

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.prompts.append(prompt)
        match = re.search(r"Ticket:\s*(\w+)", prompt)
        tid = match.group(1) if match else "?"
        if "Stay in this worktree" in prompt:
            self.events.append(("implement", tid))
            (Path(cwd) / f"{tid}.txt").write_text(f"{tid}\n", encoding="utf-8")
            return RunResult(True, f"impl {tid}")
        self.events.append(("review", tid))
        if tid not in self.reviews:
            verdict = "passed"
        else:
            plan = self.reviews[tid]
            if plan:
                verdict = plan.pop(0)
                self._last[tid] = verdict
            else:
                verdict = self._last.get(tid, "failed")
        if verdict == "inconclusive":
            return RunResult(False, "no verdict", verdict=None)
        if verdict == "failed":
            return RunResult(
                False,
                "gap: missing field",
                verdict="failed",
                findings=[
                    {
                        "id": "F1",
                        "title": "缺字段",
                        "repo": "backend",
                        "detail": "补上标题",
                        "gap": "missing",
                    }
                ],
            )
        return RunResult(True, "ok", verdict="passed")


def _yard(tmp_path: Path, git_src: Path, tickets: str, key: str = "AB-90") -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(tickets, encoding="utf-8")
    req_freeze(yard, key)
    return yard


def _two(dep: bool) -> str:
    second = "T1" if dep else ""
    return (
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        f"## T2: 第二张\n- repo: backend\n- depends_on: {second}\n- parallel: false\n"
    )


def test_until_done_reviews_and_continues_in_dependency_order(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src, _two(dep=True))
    script = Script({"T1": ["failed", "passed"], "T2": ["passed"]})
    result = implement_until_done(
        yard, "AB-90", impl_runner=script, review_runner=script
    )
    assert result["done"] == ["T1", "T2"]
    assert result["parked"] == []
    assert script.events == [
        ("implement", "T1"),
        ("review", "T1"),
        ("implement", "T1"),
        ("review", "T1"),
        ("implement", "T2"),
        ("review", "T2"),
    ]
    assert "gap: missing field" in script.prompts[2]
    data = st.load(yard, "AB-90")
    assert data["tickets"]["T1"]["state"] == "done"
    assert data["tickets"]["T2"]["state"] == "done"


def test_until_done_parks_a_stuck_ticket_and_continues_independents(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src, _two(dep=False), key="AB-91")
    script = Script({"T1": ["failed"], "T2": ["passed"]})
    with pytest.raises(RuntimeError, match="T1"):
        implement_until_done(yard, "AB-91", impl_runner=script, review_runner=script)
    data = st.load(yard, "AB-91")
    assert data["tickets"]["T1"]["state"] == "blocked"
    assert data["tickets"]["T1"]["last_verdict"] == "failed"
    assert data["tickets"]["T2"]["state"] == "done"
    assert [event for event in script.events if event[0] == "implement" and event[1] == "T1"]
    assert script.events.count(("implement", "T1")) == 4
    assert ("implement", "T2") in script.events


def test_until_done_names_a_stuck_ticket_instead_of_the_step_ceiling(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(
        tmp_path,
        git_src,
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n",
        key="AB-91B",
    )
    script = Script({"T1": ["failed"]})
    with pytest.raises(RuntimeError, match="审查未通过") as raised:
        implement_until_done(yard, "AB-91B", impl_runner=script, review_runner=script)
    assert "步数超过上限" not in str(raised.value)
    assert st.load(yard, "AB-91B")["tickets"]["T1"]["state"] == "blocked"
    assert script.events.count(("implement", "T1")) == 4


def test_until_done_parks_an_implement_error_and_continues(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(tmp_path, git_src, _two(dep=False), key="AB-91C")

    class Boom(Script):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            if "Stay in this worktree" in prompt and "Ticket: T1" in prompt:
                raise ValueError("worktree exploded")
            return super().start(prompt, cwd, extra_read_paths, repo)

    script = Boom({"T2": ["passed"]})
    with pytest.raises(RuntimeError, match="worktree exploded"):
        implement_until_done(yard, "AB-91C", impl_runner=script, review_runner=script)
    data = st.load(yard, "AB-91C")
    assert data["tickets"]["T1"]["state"] == "ready"
    assert data["tickets"]["T2"]["state"] == "done"


def test_until_done_parks_inconclusive_without_rewriting(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(
        tmp_path,
        git_src,
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n",
        key="AB-92",
    )
    script = Script({"T1": ["inconclusive", "inconclusive"]})
    with pytest.raises(RuntimeError, match="没有结论"):
        implement_until_done(yard, "AB-92", impl_runner=script, review_runner=script)
    assert script.events == [
        ("implement", "T1"),
        ("review", "T1"),
        ("review", "T1"),
    ]
    assert st.load(yard, "AB-92")["tickets"]["T1"]["state"] == "inconclusive"


def test_until_done_merges_an_approved_ticket_without_rereview(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(
        tmp_path,
        git_src,
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n",
        key="AB-93",
    )
    writer = Script({})
    implement(yard, "AB-93", ["T1"], runner=writer)
    data = st.load(yard, "AB-93")
    data["tickets"]["T1"]["state"] = "approved"
    data["tickets"]["T1"]["last_verdict"] = "passed"
    st.save(yard, "AB-93", data)

    class Boom(Runner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            raise AssertionError(prompt[:80])

    result = implement_until_done(yard, "AB-93", impl_runner=Boom(), review_runner=Boom())
    assert result["done"] == ["T1"]
    assert st.load(yard, "AB-93")["tickets"]["T1"]["state"] == "done"


def test_until_done_stops_when_cancelled(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(
        tmp_path,
        git_src,
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n",
        key="AB-94",
    )
    flag = {"n": 0}

    class Once(Script):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            flag["n"] += 1
            return super().start(prompt, cwd, extra_read_paths, repo)

    with pytest.raises(JobCancelled):
        implement_until_done(
            yard,
            "AB-94",
            impl_runner=Once({}),
            review_runner=Once({}),
            cancel_check=lambda: flag["n"] >= 1,
        )
    assert st.load(yard, "AB-94")["tickets"]["T1"]["state"] == "implemented"


def test_implement_all_action_follows_unfinished_tickets(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = _yard(
        tmp_path,
        git_src,
        "## T1: 第一张\n- repo: backend\n- depends_on:\n- parallel: false\n",
        key="AB-95",
    )
    detail = requirement_detail(yard, "AB-95")
    action = {a.id: a for a in detail.actions}["implement-all"]
    assert action.enabled
    assert action.label == "一次性实现"
    assert action.stage == "implement"
    data = st.load(yard, "AB-95")
    data["tickets"]["T1"]["state"] = "done"
    st.save(yard, "AB-95", data)
    done = {a.id: a for a in requirement_detail(yard, "AB-95").actions}["implement-all"]
    assert not done.enabled
    assert done.reason == "票都已完成"


def test_web_implement_all_job_logs_and_conflicts(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    seen: dict[str, object] = {}

    def fake(root, jira, **kwargs):
        seen["jira"] = jira
        seen["cancel"] = kwargs["cancel_check"]
        kwargs["on_log"]("T1 开始实现（第 1 次）")
        return {"done": ["T1"], "parked": [], "pending": []}

    monkeypatch.setattr("dev_yard.service.implement_until_done", fake)
    runner = JobRunner(yard, sync=True)
    job = runner.submit("implement-all", "AB-96")
    assert job.state == "ok"
    assert "全部票已完成" not in job.log
    assert "T1 开始实现" in job.log
    assert "done: T1" in job.log
    assert seen["jira"] == "AB-96"
    assert callable(seen["cancel"])


def test_web_implement_all_occupies_the_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()

    def execute(root, job):
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    job = runner.submit("implement-all", "AB-97")
    try:
        with pytest.raises(ValueError, match="already"):
            runner.submit("implement", "AB-97", ticket_ids=["T1"])
    finally:
        gate.set()
        job.done.wait(timeout=5)
