from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dev_yard import status as st
from dev_yard.cli import app
from dev_yard.qa import req_test
from dev_yard.qa_config import TestRejected, load_qa_config
from dev_yard.qa_report import map_qa_result
from dev_yard.qa_schedule import CaseJob, PoolSlot, normalize_status, run_schedule
from dev_yard.runners import DryRunRunner, Runner, RunResult
from dev_yard.service import implement, init_yard, repo_add, req_freeze, req_open, review
from dev_yard.test_report import ReportRejected, submit_test
from dev_yard.web.board import PIPELINE, requirement_detail

cli = CliRunner()


def _write_qa_yaml(root: Path, extra: str = "") -> None:
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        + extra,
        encoding="utf-8",
    )


def _testing_req(tmp_path: Path, git_src: Path, key: str) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, key, source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n",
        encoding="utf-8",
    )
    (d / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    req_freeze(yard, key)
    implement(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, runner=DryRunRunner())
    review(yard, key, None, contract=True, runner=DryRunRunner())
    submit_test(yard, key)
    _write_qa_yaml(yard)
    return yard


class _DesignRunner(Runner):
    def __init__(self, yard: Path, key: str):
        self.called = 0
        self.yard = yard
        self.key = key
        self.extra: list[Path] = []

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.called += 1
        self.extra = list(extra_read_paths)
        qa = self.yard / "reqs" / self.key / "qa" / "cases" / "mod"
        qa.mkdir(parents=True, exist_ok=True)
        (qa / "case-01.md").write_text(
            "---\nid: case-01\ntitle: happy\npriority: P0\n"
            f"requirement: {self.key}\nrepo: backend\ncovers: [D1]\n---\n\n# body\n",
            encoding="utf-8",
        )
        return RunResult(ok=True, summary="designed")


def test_load_qa_config_rejects_missing(tmp_path: Path):
    init_yard(tmp_path)
    with pytest.raises(TestRejected, match="qa.yaml"):
        load_qa_config(tmp_path)


def test_load_qa_config_rejects_sum_over_8(tmp_path: Path):
    init_yard(tmp_path)
    (tmp_path / "qa.yaml").write_text(
        "envs:\n  local:\n    base_url: http://127.0.0.1:1\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: g\n    concurrency: 5\n"
        "  - id: b\n    provider: rcc\n    model: m\n    concurrency: 4\n",
        encoding="utf-8",
    )
    with pytest.raises(TestRejected, match="max is 8"):
        load_qa_config(tmp_path)


def test_req_test_rejects_wrong_phase(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    req_open(yard, "QA-1", source="none")
    _write_qa_yaml(yard)
    with pytest.raises(TestRejected, match="submit-test"):
        req_test(yard, "QA-1", print_mode=True)


def test_req_test_rejects_no_worktree(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "QA-2", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n",
        encoding="utf-8",
    )
    data = st.load(yard, "QA-2")
    data["phase"] = "testing"
    data["contract_review"] = "passed"
    data["tickets"] = {"T1": {"state": "done", "repo": "backend"}}
    st.save(yard, "QA-2", data)
    _write_qa_yaml(yard)
    with pytest.raises(TestRejected, match="worktree"):
        req_test(yard, "QA-2", print_mode=True)


def test_run_dedicated_qa_stages_rejected(tmp_path, monkeypatch):
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    monkeypatch.chdir(tmp_path)
    for name in ("qa-design", "qa-run", "test"):
        out = cli.invoke(app, ["run", name, "J-1"])
        assert out.exit_code == 2, name
        assert "dev-yard req test" in out.output


def test_design_called_when_no_cases(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-3")
    design = _DesignRunner(yard, "QA-3")
    result = req_test(
        yard,
        "QA-3",
        print_mode=True,
        design_only=True,
        runner=design,
    )
    assert design.called == 1
    assert result["cases"] == 1


def test_design_attaches_requirement_images(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-IMG")
    assets = yard / "reqs" / "QA-IMG" / "assets" / "669971526"
    assets.mkdir(parents=True)
    shot = assets / "entry1.png"
    shot.write_bytes(b"x")
    design = _DesignRunner(yard, "QA-IMG")
    req_test(yard, "QA-IMG", print_mode=True, design_only=True, runner=design)
    assert shot in design.extra


def test_design_runner_uses_qa_yaml_design_model(tmp_path: Path, git_src: Path, monkeypatch):
    """qa-design must run with qa.yaml `design`, not the workspace pi fallback."""
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-DM")
    _write_qa_yaml(yard, "design:\n  provider: rcc\n  model: glm-5.3\n")
    design = _DesignRunner(yard, "QA-DM")
    captured: dict = {}

    def fake_get_runner(root, bundle, **kwargs):
        captured["bundle"] = bundle
        captured.update(kwargs)
        return design

    monkeypatch.setattr("dev_yard.qa.get_runner", fake_get_runner)
    req_test(yard, "QA-DM", print_mode=True, design_only=True)
    assert captured["bundle"] == "qa-design"
    assert captured["provider"] == "rcc"
    assert captured["model"] == "glm-5.3"


def test_design_skipped_when_cases_exist(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-4")
    design = _DesignRunner(yard, "QA-4")
    design.start("", yard, [])
    design.called = 0
    ran = []

    def case_runner(job, pool):
        ran.append(job.id)
        # host creates run dir after design; write after schedule starts via path in job
        return {
            "status": "passed",
            "repo": "backend",
            "model": pool.model,
            "provider": pool.provider,
        }

    approved = req_test(
        yard,
        "QA-4",
        print_mode=True,
        approve=True,
        ingest=False,
        runner=design,
        case_runner=case_runner,
    )
    assert approved["approved"] is True
    assert design.called == 0
    result = req_test(
        yard,
        "QA-4",
        print_mode=True,
        run_only=True,
        ingest=False,
        runner=design,
        case_runner=case_runner,
    )
    assert design.called == 0
    assert ran == ["case-01"]
    assert result["summary"]["passed"] == 1


def test_redesign_forces_design(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-5")
    design = _DesignRunner(yard, "QA-5")
    design.start("", yard, [])
    design.called = 0
    req_test(
        yard,
        "QA-5",
        print_mode=True,
        redesign=True,
        design_only=True,
        runner=design,
    )
    assert design.called == 1


def test_schedule_caps_and_priority_and_deps():
    hold = threading.Event()
    entered = threading.Semaphore(0)
    current = 0
    max_seen = 0
    lock = threading.Lock()
    used_pools: list[str] = []

    def run(job: CaseJob, pool: PoolSlot):
        nonlocal current, max_seen
        with lock:
            current += 1
            max_seen = max(max_seen, current)
            used_pools.append(pool.id)
        entered.release()
        hold.wait(2)
        with lock:
            current -= 1
        if job.id == "case-01":
            return {"status": "failed", "reason": "ui"}
        return {"status": "passed"}

    cases = [
        CaseJob(id="case-01", title="a", repo="backend", priority="P0"),
        CaseJob(id="case-02", title="b", repo="backend", priority="P1"),
        CaseJob(id="case-03", title="c", repo="backend", depends_on=["case-01"]),
        CaseJob(id="case-04", title="d", repo="backend", priority="P2"),
        CaseJob(id="case-05", title="e", repo="backend"),
        CaseJob(id="case-06", title="f", repo="backend"),
    ]
    pools = [
        PoolSlot(id="a", provider="rcc", model="grok-4", concurrency=2, priority=1),
        PoolSlot(id="b", provider="rcc", model="MiniMax-M3", concurrency=2, priority=2),
    ]
    t = threading.Thread(target=lambda: run_schedule(cases, pools, run), daemon=True)
    t.start()
    for _ in range(4):
        assert entered.acquire(timeout=2)
    time.sleep(0.05)
    assert max_seen == 4
    assert used_pools.count("a") >= 2
    hold.set()
    t.join(timeout=2)
    by_id = {c.id: c for c in cases}
    assert by_id["case-03"].state == "skipped"
    assert "case-01" in by_id["case-03"].reason


def test_schedule_prefers_high_priority_pool():
    used: list[str] = []

    def run(job, pool):
        used.append(pool.id)
        return {"status": "passed"}

    cases = [CaseJob(id="case-01", title="a", repo="backend")]
    pools = [
        PoolSlot(id="b", provider="rcc", model="m", concurrency=1, priority=2),
        PoolSlot(id="a", provider="rcc", model="g", concurrency=1, priority=1),
    ]
    run_schedule(cases, pools, run)
    assert used == ["a"]


def test_schedule_rejects_cycle():
    cases = [
        CaseJob(id="case-01", title="a", repo="be", depends_on=["case-02"]),
        CaseJob(id="case-02", title="b", repo="be", depends_on=["case-01"]),
    ]
    pools = [PoolSlot(id="a", provider="rcc", model="g", concurrency=1, priority=1)]
    with pytest.raises(TestRejected, match="cycle"):
        run_schedule(cases, pools, lambda j, p: {"status": "passed"})


def test_schedule_rejects_missing_dep():
    cases = [CaseJob(id="case-01", title="a", repo="be", depends_on=["case-99"])]
    pools = [PoolSlot(id="a", provider="rcc", model="g", concurrency=1, priority=1)]
    with pytest.raises(TestRejected, match="does not exist"):
        run_schedule(cases, pools, lambda j, p: {"status": "passed"})


def test_map_qa_result_failed_findings():
    run = {"summary": {"total": 1, "passed": 0, "failed": 1, "blocked": 0, "skipped": 0}}
    cases = [
        {
            "case": "case-01",
            "title": "boom",
            "repo": "backend",
            "status": "failed",
            "reason": "mismatch",
            "failure": {"step_desc": "click save", "evidence": "screenshots/x.png"},
        }
    ]
    report = map_qa_result(run, cases)
    assert report is not None
    assert report.verdict == "failed"
    assert report.source == "yard"
    assert report.findings[0].id == "case-01"
    assert report.findings[0].repo == "backend"
    assert "click save" in report.findings[0].detail


def test_map_qa_result_passed_and_blocked():
    passed = map_qa_result(
        {"summary": {"total": 2, "passed": 1, "failed": 0, "blocked": 0, "skipped": 1}},
        [
            {"case": "case-01", "status": "passed", "repo": "backend"},
            {"case": "case-02", "status": "skipped", "repo": "backend"},
        ],
    )
    assert passed is not None
    assert passed.verdict == "passed"
    assert passed.findings == []
    blocked = map_qa_result(
        {"summary": {"total": 1, "passed": 0, "failed": 0, "blocked": 1, "skipped": 0}},
        [{"case": "case-01", "status": "blocked", "repo": "backend", "reason": "login"}],
    )
    assert blocked is None
    empty = map_qa_result({"summary": {"total": 0, "passed": 0, "failed": 0, "blocked": 0, "skipped": 0}}, [])
    assert empty is None
    with pytest.raises(ReportRejected, match="repo"):
        map_qa_result(
            {"summary": {"total": 1, "passed": 0, "failed": 1, "blocked": 0, "skipped": 0}},
            [{"case": "case-01", "status": "failed", "title": "x"}],
        )


def test_mutation_gate_skips_ingest(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-6")
    design = _DesignRunner(yard, "QA-6")
    called = {"accept": 0}

    def boom(*args, **kwargs):
        called["accept"] += 1

    monkeypatch.setattr("dev_yard.qa.accept_test_report", boom)

    def mutate(job, pool):
        wt = yard / "reqs" / "QA-6" / "worktrees" / "backend"
        (wt / "hacked.py").write_text("x\n", encoding="utf-8")
        return {"status": "passed", "repo": "backend"}

    req_test(yard, "QA-6", print_mode=True, design_only=True, runner=design)
    req_test(yard, "QA-6", print_mode=True, approve=True)
    with pytest.raises(TestRejected, match="mutated"):
        req_test(
            yard,
            "QA-6",
            print_mode=True,
            run_only=True,
            case_runner=mutate,
        )
    assert called["accept"] == 0
    assert st.load(yard, "QA-6")["phase"] == "testing"


def test_passed_ingests_and_sets_done(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-7")
    design = _DesignRunner(yard, "QA-7")

    def ok(job, pool):
        return {"status": "passed", "repo": "backend", "model": pool.model}

    req_test(yard, "QA-7", print_mode=True, design_only=True, runner=design)
    req_test(yard, "QA-7", print_mode=True, approve=True)
    result = req_test(
        yard, "QA-7", print_mode=True, run_only=True, case_runner=ok
    )
    assert result["ingested"] is True
    data = st.load(yard, "QA-7")
    assert data["phase"] == "done"
    assert data["test"]["latest_verdict"] == "passed"
    assert data["test"]["source"] == "yard"


def test_board_qa_design_enabled(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-8")
    detail = requirement_detail(yard, "QA-8")
    ids = {a.id: a for a in detail.actions}
    assert ids["qa-design"].enabled
    assert ids["qa-run"].enabled is False
    assert "设计用例" in ids["qa-run"].reason
    assert ids["qa-review"].enabled is False
    assert detail.next_label == "qa-design"
    assert "qa-design" not in PIPELINE
    assert "qa-run" not in PIPELINE
    assert {s.id for s in detail.steps} == set(PIPELINE)
    assert detail.qa is not None
    assert detail.qa["envs"] == ["local"]
    assert detail.qa["active_env"] == "local"


def test_context_md_names_the_selected_env(tmp_path: Path):
    from dev_yard.qa import write_context_md

    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "workers:\n  - id: a\n    provider: rcc\n    model: grok-4\n"
        "envs:\n"
        "  local:\n    base_url: http://127.0.0.1:8080\n"
        "  test:\n    base_url: https://test.example.com\n    notes: [test 环境]\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(yard, "test")
    text = write_context_md(yard, "QA-E1", cfg).read_text(encoding="utf-8")
    assert "- env: test" in text
    assert "- available envs: local, test" in text
    assert "- base_url: https://test.example.com" in text
    assert "- test 环境" in text


def test_req_test_uses_the_selected_env(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-E2")
    _write_qa_yaml(
        yard,
        "  test:\n    base_url: https://test.example.com\n"
        "    exec:\n      use: local\n      allow_cross_site: true\n",
    )

    class _Design(_DesignRunner):
        pass

    def ok(job, slot):
        return {"status": "passed", "repo": job.repo}

    req_test(
        yard,
        "QA-E2",
        env="test",
        print_mode=True,
        design_only=True,
        runner=_Design(yard, "QA-E2"),
    )
    req_test(yard, "QA-E2", env="test", print_mode=True, approve=True)
    result = req_test(
        yard,
        "QA-E2",
        env="test",
        print_mode=True,
        run_only=True,
        case_runner=ok,
    )
    assert result["ingested"] is True
    ctx = (yard / "reqs" / "QA-E2" / "qa" / "context.md").read_text(encoding="utf-8")
    assert "- env: test" in ctx
    assert "- base_url: https://test.example.com" in ctx
    run = yaml.safe_load(
        (yard / "reqs" / "QA-E2" / "qa" / "evidence" / result["run_id"] / "result.yaml")
        .read_text(encoding="utf-8")
    )
    assert run["env"] == "test"


def test_req_test_uses_requirement_accounts(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.qa_config import QaAccount, save_req_accounts

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-A1")
    state = yard / ".yard-qa" / "requirements" / "QA-A1" / "auth-local-buyer.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")
    save_req_accounts(
        yard,
        "QA-A1",
        "local",
        "buyer",
        {
            "buyer": QaAccount(
                name="buyer",
                username="buyer01",
                password="pw",
                state_file=".yard-qa/requirements/QA-A1/auth-local-buyer.json",
            )
        },
    )
    qa = yard / "reqs" / "QA-A1" / "qa"
    (qa / "cases" / "mod").mkdir(parents=True)
    (qa / "cases" / "mod" / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: buyer\n---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: None)
    result = req_test(
        yard,
        "QA-A1",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
    )
    assert result["summary"]["passed"] == 1


def test_req_test_rejects_unconfigured_case_account(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-A2")
    qa = yard / "reqs" / "QA-A2" / "qa"
    (qa / "cases" / "mod").mkdir(parents=True)
    (qa / "cases" / "mod" / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\naccount: buyer\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(TestRejected, match="not configured"):
        req_test(
            yard,
            "QA-A2",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_req_test_blocked_while_bug_tickets_open(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.test_report import accept_test_report, parse_inbound

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-R1")
    accept_test_report(
        yard,
        "QA-R1",
        parse_inbound(
            {
                "verdict": "failed",
                "body": "x",
                "findings": [{"id": "F1", "title": "x", "repo": "backend"}],
            },
            "api",
        ),
    )
    with pytest.raises(TestRejected, match="open tickets"):
        req_test(
            yard,
            "QA-R1",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_req_test_runs_same_account_cases_in_parallel(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-S1")
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 2\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: admin\n"
        "      accounts:\n        admin: { username: admin, password: pw, "
        "state_file: .yard-qa/auth-local-admin.json }\n",
        encoding="utf-8",
    )
    stfile = yard / ".yard-qa" / "auth-local-admin.json"
    stfile.parent.mkdir(parents=True, exist_ok=True)
    stfile.write_text("{}", encoding="utf-8")
    mod = yard / "reqs" / "QA-S1" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    for i in (1, 2):
        (mod / f"case-0{i}.md").write_text(
            f"---\nid: case-0{i}\ntitle: t\nrepo: backend\n---\n\nbody\n",
            encoding="utf-8",
        )
    guard = threading.Lock()
    live = {"n": 0}
    peak = {"n": 0}

    def run(job, slot):
        with guard:
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])
        time.sleep(0.05)
        with guard:
            live["n"] -= 1
        return {"status": "passed", "repo": "backend"}

    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: None)
    req_test(yard, "QA-S1", print_mode=True, run_only=True, unsafe_skip_review=True, ingest=False, case_runner=run)
    assert peak["n"] == 2


def test_req_test_serializes_when_configured(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-S2")
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "serialize_accounts: true\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 2\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: admin\n"
        "      accounts:\n        admin: { username: admin, password: pw, "
        "state_file: .yard-qa/auth-local-admin.json }\n",
        encoding="utf-8",
    )
    stfile = yard / ".yard-qa" / "auth-local-admin.json"
    stfile.parent.mkdir(parents=True, exist_ok=True)
    stfile.write_text("{}", encoding="utf-8")
    mod = yard / "reqs" / "QA-S2" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    for i in (1, 2):
        (mod / f"case-0{i}.md").write_text(
            f"---\nid: case-0{i}\ntitle: t\nrepo: backend\n---\n\nbody\n",
            encoding="utf-8",
        )
    guard = threading.Lock()
    live = {"n": 0}
    peak = {"n": 0}

    def run(job, slot):
        with guard:
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])
        time.sleep(0.05)
        with guard:
            live["n"] -= 1
        return {"status": "passed", "repo": "backend"}

    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: None)
    req_test(yard, "QA-S2", print_mode=True, run_only=True, unsafe_skip_review=True, ingest=False, case_runner=run)
    assert peak["n"] == 1


def test_plugin_cannot_use_qa_stage_name(tmp_path: Path):
    from dev_yard import stages

    root = tmp_path
    (root / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    p = root / "plugins" / "qa-design"
    p.mkdir(parents=True)
    (p / "plugin.yaml").write_text("name: qa-design\ntools: [read]\n", encoding="utf-8")
    (p / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (root / "yard.yaml").write_text("plugins: [plugins/qa-design]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dedicated"):
        stages.load_registry(root)


def test_init_ignores_yard_qa(tmp_path: Path):
    init_yard(tmp_path)
    gi = (tmp_path / ".gitignore").read_text().splitlines()
    assert ".yard-qa/" in gi
    assert "qa.yaml" in gi


def test_worktree_png_is_mutation(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-9")
    design = _DesignRunner(yard, "QA-9")
    called = {"accept": 0}
    monkeypatch.setattr("dev_yard.qa.accept_test_report", lambda *a, **k: called.__setitem__("accept", 1))

    def mutate(job, pool):
        wt = yard / "reqs" / "QA-9" / "worktrees" / "backend"
        (wt / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"status": "passed", "repo": "backend"}

    req_test(yard, "QA-9", print_mode=True, design_only=True, runner=design)
    req_test(yard, "QA-9", print_mode=True, approve=True)
    with pytest.raises(TestRejected, match="mutated"):
        req_test(
            yard, "QA-9", print_mode=True, run_only=True, case_runner=mutate
        )
    assert called["accept"] == 0


def test_only_new_root_png_is_moved(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-10")
    keep = yard / "keep.png"
    keep.write_bytes(b"\x89PNG\r\n\x1a\nkeep")
    design = _DesignRunner(yard, "QA-10")

    def drop(job, pool):
        (yard / "tmp.png").write_bytes(b"\x89PNG\r\n\x1a\nnew")
        return {"status": "passed", "repo": "backend"}

    req_test(yard, "QA-10", print_mode=True, design_only=True, runner=design)
    req_test(yard, "QA-10", print_mode=True, approve=True)
    result = req_test(
        yard, "QA-10", print_mode=True, run_only=True, case_runner=drop
    )
    assert keep.is_file()
    assert keep.read_bytes().endswith(b"keep")
    assert not (yard / "tmp.png").is_file()
    moved = yard / "reqs" / "QA-10" / "qa" / "evidence" / result["run_id"] / "_root_png" / "tmp.png"
    assert moved.is_file()


def test_preload_auth_runs_when_account_configured(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-11")
    data = yaml.safe_load((yard / "qa.yaml").read_text(encoding="utf-8"))
    data["envs"]["local"]["auth"] = {
        "default": "default",
        "accounts": {"default": {"state_file": ".yard-qa/auth.json"}},
    }
    (yard / "qa.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    hits: list[str] = []
    seen_names: list[list[str]] = []

    def fake(root, cfg, names=None, on_log=None):
        hits.append(cfg.env.auth_default)
        seen_names.append(list(names or []))

    monkeypatch.setattr("dev_yard.qa._preload_auth", fake)
    design = _DesignRunner(yard, "QA-11")
    req_test(yard, "QA-11", print_mode=True, design_only=True, runner=design)
    req_test(yard, "QA-11", print_mode=True, approve=True)
    req_test(
        yard,
        "QA-11",
        print_mode=True,
        run_only=True,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        ingest=False,
    )
    assert hits == ["default"]
    assert seen_names == [["default"]]


def test_preload_auth_reports_missing_state_file(tmp_path: Path, monkeypatch):
    from dev_yard.qa import _preload_auth
    from dev_yard.qa_config import QaAccount, QaBrowser, QaConfig, QaEnv, QaWorker

    cfg = QaConfig(
        active_env="local",
        env=QaEnv(
            name="local",
            base_url="http://127.0.0.1:1",
            accounts={
                "default": QaAccount(name="default", state_file=".yard-qa/missing.json")
            },
        ),
        browser=QaBrowser(),
        workers=(QaWorker(id="a", provider="rcc", model="g", concurrency=1, priority=1),),
    )
    failures = _preload_auth(tmp_path, cfg)
    assert "default" in failures
    assert "missing auth state_file" in failures["default"]


def test_preload_auth_replays_saved_script_when_state_missing(
    tmp_path: Path, monkeypatch
):
    from dev_yard.qa import _preload_auth
    from dev_yard.qa_config import QaAccount, QaBrowser, QaConfig, QaEnv, QaWorker

    replay_file = tmp_path / ".yard-qa" / "missing.replay.sh"
    replay_file.parent.mkdir(parents=True, exist_ok=True)
    replay_file.write_text("playwright-cli fill ...\n", encoding="utf-8")

    cfg = QaConfig(
        active_env="local",
        env=QaEnv(
            name="local",
            base_url="http://127.0.0.1:1",
            accounts={
                "default": QaAccount(
                    name="default",
                    username="admin",
                    password="s3cret",
                    state_file=".yard-qa/missing.json",
                )
            },
        ),
        browser=QaBrowser(),
        workers=(QaWorker(id="a", provider="rcc", model="g", concurrency=2, priority=1),),
    )
    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda name: "/bin/true")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "state-save" in cmd:
            dest = Path(cmd[-1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("dev_yard.qa_exec.subprocess.run", fake_run)
    failures = _preload_auth(tmp_path, cfg)
    assert failures == {}
    assert any("state-save" in c for c in calls)
    assert (tmp_path / ".yard-qa" / "missing.json").is_file()


def test_preload_auth_delegates_to_ai_when_creds_present_and_no_replay(tmp_path: Path):
    from dev_yard.qa import _preload_auth
    from dev_yard.qa_config import QaAccount, QaBrowser, QaConfig, QaEnv, QaWorker

    cfg = QaConfig(
        active_env="local",
        env=QaEnv(
            name="local",
            base_url="http://127.0.0.1:1",
            accounts={
                "default": QaAccount(
                    name="default",
                    username="admin",
                    password="s3cret",
                    state_file=".yard-qa/missing.json",
                )
            },
        ),
        browser=QaBrowser(),
        workers=(QaWorker(id="a", provider="rcc", model="g", concurrency=2, priority=1),),
    )
    logs: list[str] = []
    failures = _preload_auth(tmp_path, cfg, on_log=logs.append)
    assert failures == {}
    assert any("will be explored by AI worker" in line for line in logs)


def test_schedule_normalizes_status_case():
    cases = [CaseJob(id="case-01", title="a", repo="backend")]
    pools = [PoolSlot(id="a", provider="rcc", model="g", concurrency=1, priority=1)]

    def run(job, pool):
        return {"status": "FAILED", "reason": "ui"}

    run_schedule(cases, pools, run)
    assert cases[0].state == "failed"
    assert normalize_status("PASSED") == "passed"
    assert normalize_status("nope") == "blocked"


def test_env_block_class_skips_setup_fuse():
    from dev_yard.qa_schedule import env_block_class

    assert env_block_class("env fault: no route") is None
    assert env_block_class("setup failed: timeout") is None
    assert env_block_class("login failed") == "login"


def test_env_block_class_ignores_cancellation():
    from dev_yard.qa_schedule import env_block_class

    # A cancel must not trip the breaker (else remaining cases get stamped
    # blocked "worker exit" instead of cancelled).
    assert env_block_class("cancelled: run cancelled") is None
    assert env_block_class("worker exit: qa-run case-03 cancelled") is None


def test_blocked_kind_buckets_reasons():
    from dev_yard.qa_schedule import blocked_kind

    assert blocked_kind("case-defect: setup missed city_id") == "case-defect"
    assert blocked_kind("cancelled: qa-run case-03 cancelled") == "cancelled"
    assert blocked_kind("login failed") == "env"
    assert blocked_kind("POST /contacts/save returned 500") == "env"
    assert blocked_kind("something odd happened") == "other"


def test_summarize_splits_blocked_kinds():
    from dev_yard.qa import _summarize

    cases = [
        CaseJob(id="c1", title="t", repo="r", state="blocked", reason="case-defect: x"),
        CaseJob(id="c2", title="t", repo="r", state="blocked", reason="cancelled: y"),
        CaseJob(id="c3", title="t", repo="r", state="blocked", reason="login failed"),
        CaseJob(id="c4", title="t", repo="r", state="blocked", reason="weird"),
        CaseJob(id="c5", title="t", repo="r", state="passed"),
    ]
    summary = _summarize(cases)
    assert summary["blocked"] == 4
    assert summary["blocked_kind"] == {
        "case-defect": 1,
        "env": 1,
        "cancelled": 1,
        "other": 1,
    }


def test_open_questions_payload_counts_entries(tmp_path: Path):
    from dev_yard.qa import open_questions_payload

    qa = tmp_path / "qa"
    qa.mkdir()
    assert open_questions_payload(qa) == {"count": 0, "body": "", "exists": False}
    (qa / "OPEN-QUESTIONS.md").write_text(
        "# 开放问题\n\nQ1: 问题一 | 默认: a\n- Q2：问题二\n**Q3**: 加粗\n### Q4 标题\n无关噪声\n",
        encoding="utf-8",
    )
    payload = open_questions_payload(qa)
    assert payload["count"] == 4
    assert payload["exists"] is True
    assert "Q1" in payload["body"]

    # The contract says an empty file is still written; `exists` proves it.
    (qa / "OPEN-QUESTIONS.md").write_text("", encoding="utf-8")
    assert open_questions_payload(qa) == {"count": 0, "body": "", "exists": True}


def test_blocked_kind_and_env_block_class_share_one_matcher():
    from dev_yard.qa_schedule import blocked_kind, env_block_class

    reasons = [
        "login failed",
        "unauthorized 401",
        "POST /contacts/save returned 500",
        "5xx on save",
        "db is down",
        "usql not found",
        "worker exit: killed",
        "connection refused",
        "request timeout",
        "something odd happened",
    ]
    for reason in reasons:
        klass = env_block_class(reason)
        kind = blocked_kind(reason)
        # env_block_class may default an unknown reason to "env"; blocked_kind
        # reports those as "other". But never the reverse: an "env" kind must
        # also be an env class for the breaker.
        if kind == "env":
            assert klass is not None, reason
        if klass is None:
            assert kind != "env", reason


def test_env_block_class_ignores_case_defect():
    from dev_yard.qa_schedule import env_block_class

    # A seed gap is the case's own bug; two of them must not abort the run.
    assert env_block_class("case-defect: setup missed city_id") is None


def test_design_only_reports_open_questions(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-OQ")

    class _DesignWithQuestions(Runner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            qa = yard / "reqs" / "QA-OQ" / "qa"
            cases = qa / "cases" / "mod"
            cases.mkdir(parents=True, exist_ok=True)
            (cases / "case-01.md").write_text(
                "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
                encoding="utf-8",
            )
            (qa / "OPEN-QUESTIONS.md").write_text(
                "Q1: 是否需要权限账号？ | 默认取值: 不需要 | 影响: case-01 | 答错后果: 漏测\n",
                encoding="utf-8",
            )
            return RunResult(ok=True, summary="designed")

    result = req_test(
        yard, "QA-OQ", print_mode=True, design_only=True, runner=_DesignWithQuestions()
    )
    assert result["design_only"] is True
    assert result["questions"] == 1
    assert result["open_questions"]["exists"] is True


def test_run_schedule_cancel_marks_remaining_cancelled():
    cases = [CaseJob(id=f"c{i}", title="t", repo="backend") for i in (1, 2, 3)]
    pools = [PoolSlot(id="a", provider=None, model=None, concurrency=1, priority=1)]
    cancel = threading.Event()
    ran: list[str] = []

    def run(job: CaseJob, slot: PoolSlot) -> dict:
        ran.append(job.id)
        cancel.set()
        return {"status": "passed", "repo": job.repo}

    run_schedule(cases, pools, run, cancel_check=cancel.is_set)
    assert ran == ["c1"]
    rest = [c for c in cases if c.id != "c1"]
    assert all(c.state == "blocked" for c in rest)
    assert all(c.reason.startswith("cancelled:") for c in rest)


def test_run_schedule_worker_jobcancelled_is_cancelled_not_env():
    from dev_yard.runners import JobCancelled

    cases = [CaseJob(id="c1", title="t", repo="backend")]
    pools = [PoolSlot(id="a", provider=None, model=None, concurrency=1, priority=1)]

    def run(job: CaseJob, slot: PoolSlot) -> dict:
        raise JobCancelled("qa-run c1 cancelled")

    run_schedule(cases, pools, run)
    assert cases[0].state == "blocked"
    assert cases[0].reason.startswith("cancelled:")


def test_qa_duties_and_skills_stay_in_sync():
    from dev_yard.qa import _duties
    from dev_yard.stages import resolve_skill_dir

    root = Path(__file__).resolve().parents[1]
    design_duties = _duties("design", "J-1")
    run_duties = _duties("run", "J-1")
    # The old blanket "Do not interview." is what let design ship data gaps.
    assert "Do not interview." not in design_duties
    assert "OPEN-QUESTIONS" in design_duties
    assert "accounts-discover.sql" in design_duties
    assert "case-defect" in run_duties
    assert "cancelled:" in run_duties
    assert "qa logs" in run_duties

    design_skill = resolve_skill_dir(root, "qa-design")
    run_skill = resolve_skill_dir(root, "qa-run")
    assert design_skill is not None and run_skill is not None
    design_text = (design_skill / "SKILL.md").read_text(encoding="utf-8")
    run_text = (run_skill / "SKILL.md").read_text(encoding="utf-8")
    assert "OPEN-QUESTIONS" in design_text
    assert "accounts-discover.sql" in design_text
    assert "Do not interview." not in design_text
    assert "case-defect" in run_text
    assert "cancelled:" in run_text
    # qa-powers traps that used to be missing.
    assert "意外成功" in run_text
    assert "qa logs" in run_text


def test_permission_gap_warning_flags_single_account(tmp_path: Path):
    from dev_yard.qa import _permission_gap_warning
    from dev_yard.qa_config import load_qa_config

    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    req = yard / "reqs" / "J-1"
    req.mkdir(parents=True)
    (req / "REQUIREMENT.md").write_text(
        "无权限用户不可见该入口，角色差异需覆盖。", encoding="utf-8"
    )
    cfg = load_qa_config(yard, None, "J-1")
    warn = _permission_gap_warning(yard, "J-1", cfg)
    assert warn is not None
    assert "权限" in warn
    assert "--auto" in warn

    (req / "REQUIREMENT.md").write_text("普通列表页加一列。", encoding="utf-8")
    assert _permission_gap_warning(yard, "J-1", cfg) is None


def _seed_qa_for_report(yard: Path, key: str) -> Path:
    qa = yard / "reqs" / key / "qa"
    (qa / "cases" / "mod").mkdir(parents=True, exist_ok=True)
    (qa / "meta.yaml").write_text(
        "module: J-1-mod\nrequirement: J-1\n"
        "changes:\n"
        "  - id: D1\n    repo: backend\n    desc: 保存接口\n"
        "  - id: D2\n    repo: backend\n    desc: 未覆盖点\n",
        encoding="utf-8",
    )
    (qa / "cases" / "mod" / "case-01.md").write_text(
        "---\nid: case-01\ntitle: 正常保存\nrepo: backend\ncovers: [D1]\n"
        "account: admin\n---\n\nbody\n",
        encoding="utf-8",
    )
    (qa / "cases" / "mod" / "case-02.md").write_text(
        "---\nid: case-02\ntitle: 权限拦截\nrepo: backend\ncovers: [D1]\n---\n\nbody\n",
        encoding="utf-8",
    )
    return qa


def test_render_qa_report_sections(tmp_path: Path):
    from dev_yard.qa_doc import render_qa_report

    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    qa = _seed_qa_for_report(yard, "J-1")
    run = qa / "evidence" / "2020-01-01-000000"
    run.mkdir(parents=True)
    (run / "result.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": "2020-01-01-000000",
                "env": "local",
                "cases": [
                    {"case": "case-01", "status": "passed", "repo": "backend"},
                    {
                        "case": "case-02",
                        "status": "blocked",
                        "repo": "backend",
                        "reason": "case-defect: 缺 firm 关联",
                    },
                ],
                "summary": {
                    "passed": 1,
                    "failed": 0,
                    "blocked": 1,
                    "skipped": 0,
                    "total": 2,
                    "blocked_kind": {
                        "case-defect": 1,
                        "env": 0,
                        "cancelled": 0,
                        "other": 0,
                    },
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    path, summary = render_qa_report(yard, "J-1")
    text = path.read_text(encoding="utf-8")
    assert path.name == "2020-01-01-000000.md"
    assert "覆盖改动点与验证结论" in text
    assert "账号覆盖" in text
    assert "BLOCKED 说明" in text
    assert "case-defect=1" in text
    assert "未覆盖改动点：D2" in text
    assert summary["blocked_kind"]["case-defect"] == 1


def test_render_qa_report_uses_progress_for_cancelled_run(tmp_path: Path):
    from dev_yard.qa_doc import render_qa_report

    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    qa = _seed_qa_for_report(yard, "J-1")
    run = qa / "evidence" / "2020-01-02-000000"
    run.mkdir(parents=True)
    (run / "progress.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": "2020-01-02-000000",
                "env": "local",
                "cases": [
                    {
                        "id": "case-01",
                        "state": "passed",
                        "title": "正常保存",
                        "repo": "backend",
                        "reason": "",
                    },
                    {
                        "id": "case-02",
                        "state": "blocked",
                        "title": "权限拦截",
                        "repo": "backend",
                        "reason": "cancelled: run cancelled",
                    },
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    path, summary = render_qa_report(yard, "J-1", "2020-01-02-000000")
    text = path.read_text(encoding="utf-8")
    assert "cancelled=1" in text
    # progress.yaml is the only source of env for a cancelled run.
    assert "环境：local" in text
    assert summary["blocked_kind"]["cancelled"] == 1


def test_summary_line_breaks_down_blocked():
    from dev_yard.qa_report import summary_line

    line = summary_line(
        {
            "passed": 1,
            "failed": 0,
            "blocked": 2,
            "skipped": 0,
            "blocked_kind": {
                "case-defect": 1,
                "env": 1,
                "cancelled": 0,
                "other": 0,
            },
        }
    )
    assert line == "passed=1 failed=0 blocked=2 skipped=0 (case-defect=1 env=1)"


def test_map_qa_result_body_lists_blocked_kinds():
    run = {
        "summary": {
            "passed": 0,
            "failed": 1,
            "blocked": 1,
            "skipped": 0,
            "total": 2,
            "blocked_kind": {"case-defect": 1, "env": 0, "cancelled": 0, "other": 0},
        }
    }
    cases = [
        {
            "case": "c1",
            "status": "failed",
            "repo": "backend",
            "title": "t1",
            "reason": "boom",
            "failure": {"step_desc": "保存"},
            "assertions": [],
        },
        {
            "case": "c2",
            "status": "blocked",
            "repo": "backend",
            "reason": "case-defect: 缺关联",
        },
    ]
    report = map_qa_result(run, cases)
    assert report is not None
    assert "[case-defect]" in report.body
    assert "case-defect=1" in report.summary


def test_format_blocked_kind_skips_zero_buckets():
    from dev_yard.qa_schedule import format_blocked_kind

    assert format_blocked_kind(None) == ""
    assert format_blocked_kind({}) == ""
    assert format_blocked_kind(
        {"case-defect": 2, "env": 0, "cancelled": 1, "other": 0}
    ) == "case-defect=2 cancelled=1"


def test_md_cell_escapes_pipes_and_newlines():
    from dev_yard.qa_report import md_cell

    assert md_cell(None) == ""
    assert md_cell("a | b") == "a \\| b"
    assert md_cell("line1\nline2") == "line1 line2"


def test_open_questions_payload_unreadable_file(tmp_path: Path, monkeypatch):
    from dev_yard.qa import open_questions_payload

    qa = tmp_path / "qa"
    qa.mkdir()
    path = qa / "OPEN-QUESTIONS.md"
    path.write_text("Q1: 问题\n", encoding="utf-8")
    real = Path.read_text

    def boom(self, *args, **kwargs):
        if self == path:
            raise OSError("nope")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    payload = open_questions_payload(qa)
    assert payload["exists"] is True
    assert payload["error"] == "unreadable"


def test_qa_report_cli_writes_file(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    qa = _seed_qa_for_report(yard, "J-1")
    run = qa / "evidence" / "2020-01-03-000000"
    run.mkdir(parents=True)
    (run / "result.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": "2020-01-03-000000",
                "env": "local",
                "cases": [{"case": "case-01", "status": "passed", "repo": "backend"}],
                "summary": {
                    "passed": 1,
                    "failed": 0,
                    "blocked": 0,
                    "skipped": 0,
                    "total": 1,
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(yard)
    out = cli.invoke(app, ["qa", "report", "J-1"])
    assert out.exit_code == 0, out.output
    assert "报告:" in out.output
    assert (qa / "reports" / "2020-01-03-000000.md").is_file()


def test_qa_logs_cli_local_env_unsupported(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    monkeypatch.chdir(yard)
    out = cli.invoke(app, ["qa", "logs", "J-1", "--request-id", "abc"])
    assert out.exit_code == 1


def test_jms_k8s_logs_filters_locally():
    from types import SimpleNamespace

    from dev_yard import script_exec as se

    class _Fake(se.JmsK8sExecutor):
        def _pick_pod(self, *, timeout: int) -> str:
            return "pod-1"

        def _ssh(self, remote, *, stdin=None, timeout=0, on_log=None):
            self.remote = remote
            return se.ExecResult(0, "line req=abc\nother\nREQ=ABC\n", "")

    ex = _Fake.__new__(_Fake)
    ex.spec = SimpleNamespace(namespace="dev", k8s_container="research", ping_timeout=30)
    text = ex.logs("abc", tail=50)
    assert text.splitlines() == ["line req=abc", "REQ=ABC"]
    assert "kubectl logs" in ex.remote
    assert "--tail=50" in ex.remote


def test_fetch_logs_requires_needle(tmp_path: Path):
    from dev_yard.script_exec import fetch_logs

    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    with pytest.raises(TestRejected):
        fetch_logs(yard, env_name=None, jira=None)


def test_fetch_logs_local_env_unsupported(tmp_path: Path):
    from dev_yard.script_exec import fetch_logs

    yard = tmp_path / "yard"
    init_yard(yard)
    _write_qa_yaml(yard)
    with pytest.raises(TestRejected):
        fetch_logs(yard, env_name=None, jira=None, request_id="abc")


def test_bad_frontmatter_rejects_run(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-12")
    qa = yard / "reqs" / "QA-12" / "qa" / "cases" / "mod"
    qa.mkdir(parents=True)
    (qa / "case-01.md").write_text("---\nid: [oops\n---\nbody\n", encoding="utf-8")
    with pytest.raises(TestRejected, match="unreadable case frontmatter"):
        req_test(
            yard,
            "QA-12",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_cli_req_test_passes_env(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    seen: dict = {}

    def fake(root, jira, **kwargs):
        seen["jira"] = jira
        seen.update(kwargs)
        return {"run_id": "r", "summary": {}, "cases": 0}

    monkeypatch.setattr("dev_yard.qa.req_test", fake)
    out = cli.invoke(app, ["req", "test", "QA-1", "--env", "test"])
    assert out.exit_code == 0, out.output
    assert seen["jira"] == "QA-1"
    assert seen["env"] == "test"


def test_cli_req_test_passes_rerun_cases(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    seen: dict = {}

    def fake(root, jira, **kwargs):
        seen["jira"] = jira
        seen.update(kwargs)
        return {"run_id": "r", "summary": {}, "cases": 0}

    monkeypatch.setattr("dev_yard.qa.req_test", fake)
    out = cli.invoke(
        app,
        ["req", "test", "QA-1", "--rerun-case", "case-01", "--rerun-case", "case-02"],
    )
    assert out.exit_code == 0, out.output
    assert seen["rerun_cases"] == ["case-01", "case-02"]


def test_cli_req_test_rejects_rerun_with_conflicting_flags(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    out = cli.invoke(
        app, ["req", "test", "QA-1", "--rerun-case", "case-01", "--resume"]
    )
    assert out.exit_code != 0
    assert "cannot be combined" in out.output


def test_malformed_assertions_are_rejected():
    from dev_yard.qa_exec import normalize_case_result
    from dev_yard.qa_schedule import CaseJob

    job = CaseJob(id="case-01", title="t", repo="backend")
    assert (
        normalize_case_result(
            {"status": "passed", "assertions": [{"id": "A1", "status": "passed"}]},
            job,
            require_assertions=True,
        )
        is None
    )
    got = normalize_case_result(
        {
            "status": "passed",
            "assertions": [
                {
                    "type": "ui",
                    "expected": "ok",
                    "actual": "ok",
                    "status": "passed",
                }
            ],
        },
        job,
        require_assertions=True,
    )
    assert got is not None
    assert got["status"] == "passed"


def test_req_test_resumes_incomplete_run(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RS")
    mod = yard / "reqs" / "QA-RS" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    for i in (1, 2):
        (mod / f"case-0{i}.md").write_text(
            f"---\nid: case-0{i}\ntitle: t\nrepo: backend\n---\n\nbody\n",
            encoding="utf-8",
        )
    run_dir = yard / "reqs" / "QA-RS" / "qa" / "evidence" / "2026-09-17-160518"
    (run_dir / "case-01").mkdir(parents=True)
    (run_dir / "case-01" / "result.yaml").write_text(
        "case: case-01\nstatus: passed\nrepo: backend\n"
        "assertions:\n  - {type: ui, expected: a, actual: a, status: passed}\n",
        encoding="utf-8",
    )
    (run_dir / "progress.yaml").write_text(
        "run_id: 2026-09-17-160518\nenv: local\n"
        "cases:\n"
        "  - {id: case-01, state: passed, repo: backend}\n"
        "  - {id: case-02, state: running, repo: backend}\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def run(job, slot):
        seen.append(job.id)
        return {"status": "passed", "repo": "backend"}

    result = req_test(
        yard,
        "QA-RS",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
        resume=True,
        case_runner=run,
    )
    assert result["run_id"] == "2026-09-17-160518"
    assert seen == ["case-02"]


def test_req_test_fresh_starts_new_run(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-FR")
    mod = yard / "reqs" / "QA-FR" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    (mod / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
        encoding="utf-8",
    )
    old = yard / "reqs" / "QA-FR" / "qa" / "evidence" / "2026-09-17-160518"
    old.mkdir(parents=True)
    (old / "progress.yaml").write_text(
        "run_id: 2026-09-17-160518\ncases:\n  - {id: case-01, state: running}\n",
        encoding="utf-8",
    )
    result = req_test(
        yard,
        "QA-FR",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
        resume=False,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
    )
    assert result["run_id"] != "2026-09-17-160518"


def _write_case(yard: Path, key: str, name: str, body: str) -> None:
    mod = yard / "reqs" / key / "qa" / "cases" / "mod"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / name).write_text(body, encoding="utf-8")


def test_req_test_resume_errors_without_incomplete_run(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RN")
    _write_case(
        yard,
        "QA-RN",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    with pytest.raises(TestRejected, match="no incomplete run"):
        req_test(yard, "QA-RN", print_mode=True, run_only=True, unsafe_skip_review=True, resume=True)


def test_req_test_resume_ignores_incomplete_run_from_other_env(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RE")
    _write_case(
        yard,
        "QA-RE",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    run_dir = yard / "reqs" / "QA-RE" / "qa" / "evidence" / "2026-09-17-160518"
    run_dir.mkdir(parents=True)
    (run_dir / "progress.yaml").write_text(
        "run_id: 2026-09-17-160518\nenv: test\n"
        "cases:\n  - {id: case-01, state: running, repo: backend}\n",
        encoding="utf-8",
    )
    # active_env is local, so the test-env run is not resumable
    with pytest.raises(TestRejected, match="no incomplete run"):
        req_test(yard, "QA-RE", print_mode=True, run_only=True, unsafe_skip_review=True, resume=True)


def test_req_test_resume_catches_mutation_from_prior_run(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RM")
    _write_case(
        yard,
        "QA-RM",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    run_dir = yard / "reqs" / "QA-RM" / "qa" / "evidence" / "2026-09-17-160518"
    (run_dir / "repo-baseline").mkdir(parents=True)
    (run_dir / "progress.yaml").write_text(
        "run_id: 2026-09-17-160518\nenv: local\n"
        "cases:\n  - {id: case-01, state: running, repo: backend}\n",
        encoding="utf-8",
    )
    # Original run started from a clean worktree.
    (run_dir / "repo-baseline" / "backend.json").write_text("{}", encoding="utf-8")
    wt = yard / "reqs" / "QA-RM" / "worktrees" / "backend"
    (wt / "leftover_by_worker.txt").write_text("mutated\n", encoding="utf-8")
    with pytest.raises(TestRejected, match="mutated worktree"):
        req_test(
            yard,
            "QA-RM",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            resume=True,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_req_test_stale_result_is_not_accepted_on_resume(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-STALE")
    _write_case(
        yard,
        "QA-STALE",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    run_dir = yard / "reqs" / "QA-STALE" / "qa" / "evidence" / "2026-09-17-160518"
    (run_dir / "case-01").mkdir(parents=True)
    (run_dir / "progress.yaml").write_text(
        "run_id: 2026-09-17-160518\nenv: local\n"
        "cases:\n  - {id: case-01, state: running, repo: backend}\n",
        encoding="utf-8",
    )
    # Leftover from the interrupted attempt: must not stand in for this run.
    (run_dir / "case-01" / "result.yaml").write_text(
        "case: case-01\nstatus: passed\nrepo: backend\n"
        "assertions:\n  - {type: ui, expected: a, actual: a, status: passed}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("dev_yard.qa.run_pi_print_tracked", lambda *a, **k: (1, "boom"))
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    result = req_test(
        yard, "QA-STALE", print_mode=True, run_only=True, unsafe_skip_review=True, ingest=False, resume=True
    )
    assert result["summary"]["blocked"] == 1
    assert result["summary"]["passed"] == 0


def test_req_test_blocks_only_cases_on_failed_account(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-AUTH")
    _write_qa_yaml(
        yard,
        "    auth:\n      default: admin\n      accounts:\n"
        "        admin: { username: admin, password: pw, "
        "state_file: .yard-qa/auth-local-admin.json }\n"
        "        buyer: { username: buyer, password: pw, "
        "state_file: .yard-qa/auth-local-buyer.json }\n",
    )
    (yard / ".yard-qa").mkdir(parents=True, exist_ok=True)
    for n in ("auth-local-admin.json", "auth-local-buyer.json"):
        (yard / ".yard-qa" / n).write_text("{}", encoding="utf-8")
    _write_case(
        yard,
        "QA-AUTH",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    _write_case(
        yard,
        "QA-AUTH",
        "case-02.md",
        "---\nid: case-02\ntitle: t\nrepo: backend\naccount: buyer\n---\n\nbody\n",
    )
    monkeypatch.setattr(
        "dev_yard.qa._preload_auth", lambda *a, **k: {"buyer": "login failed"}
    )
    seen: list[str] = []

    def run(job, slot):
        seen.append(job.id)
        return {"status": "passed", "repo": "backend"}

    result = req_test(
        yard,
        "QA-AUTH",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
        case_runner=run,
    )
    assert seen == ["case-01"]
    assert result["summary"]["passed"] == 1
    assert result["summary"]["blocked"] == 1


def test_req_test_setup_failure_still_runs_cleanup(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-SU")
    _write_case(
        yard,
        "QA-SU",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n"
        "data: { setup: setup.sql, cleanup: cleanup.sql }\n---\n\nbody\n",
    )
    kinds: list[str] = []

    def fake_script(root, jira, cfg, job, kind, **kw):
        kinds.append(kind)
        if kind == "setup":
            raise TestRejected("setup blew up")
        return ""

    monkeypatch.setattr("dev_yard.qa.run_case_script", fake_script)
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    result = req_test(
        yard, "QA-SU", print_mode=True, run_only=True, unsafe_skip_review=True, ingest=False
    )
    assert kinds == ["setup", "cleanup"]
    assert result["summary"]["blocked"] == 1


def test_req_test_ping_failure_blocks_setup_only(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.script_exec import ExecErrorClass, ExecUnreachable

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-EF")
    _write_case(
        yard,
        "QA-EF",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n"
        "data: { setup: setup.sql }\n---\n\nbody\n",
    )
    _write_case(
        yard,
        "QA-EF",
        "case-02.md",
        "---\nid: case-02\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )

    class Boom:
        use = "ssh"
        site = "remote"
        label = "ssh"
        cross_site_warning = ""

        def ping(self, timeout=None):
            raise ExecUnreachable("no route", ExecErrorClass.UNREACHABLE)

        def close(self):
            return None

        def run(self, *a, **k):
            raise AssertionError("setup should not run after ping fail")

    monkeypatch.setattr("dev_yard.script_exec.resolve_executor", lambda *a, **k: Boom())
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    seen: list[str] = []

    def run(job, slot):
        seen.append(job.id)
        return {"status": "passed", "repo": "backend"}

    result = req_test(
        yard,
        "QA-EF",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
        case_runner=run,
    )
    assert seen == ["case-02"]
    assert result["summary"]["blocked"] == 1
    assert result["summary"]["passed"] == 1
    evidence = yard / "reqs" / "QA-EF" / "qa" / "evidence"
    run_dir = next(p for p in evidence.iterdir() if p.is_dir())
    doc = yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))
    assert doc["env_fault"]["class"] == "unreachable"


def test_req_test_setup_fuse_spares_no_setup_cases(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.script_exec import ExecErrorClass

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-FUSE")
    for name in ("case-01.md", "case-02.md"):
        _write_case(
            yard,
            "QA-FUSE",
            name,
            f"---\nid: {name[:-3]}\ntitle: t\nrepo: backend\n"
            "data: { setup: setup.rb }\n---\n\nbody\n",
        )
    _write_case(
        yard,
        "QA-FUSE",
        "case-03.md",
        "---\nid: case-03\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    setup_ids: list[str] = []

    def fake_script(root, jira, cfg, job, kind, **kw):
        if kind == "setup":
            setup_ids.append(job.id)
            raise TestRejected("no route", error_class=ExecErrorClass.UNREACHABLE)
        return ""

    monkeypatch.setattr("dev_yard.qa.run_case_script", fake_script)
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})

    def fake_pi(argv, root, prompt, on_line=None, on_spawn=None, on_reap=None):
        evidence = yard / "reqs" / "QA-FUSE" / "qa" / "evidence"
        run_dir = next(p for p in evidence.iterdir() if p.is_dir())
        for child in run_dir.iterdir():
            dest = child / "result.yaml"
            if not child.is_dir() or dest.is_file() or child.name in {"repo-baseline", "_root_png"}:
                continue
            dest.write_text(
                yaml.safe_dump(
                    {
                        "case": child.name,
                        "status": "passed",
                        "repo": "backend",
                        "assertions": [
                            {
                                "type": "ui",
                                "expected": "ok",
                                "actual": "ok",
                                "status": "passed",
                            }
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        return 0, ""

    monkeypatch.setattr("dev_yard.qa.run_pi_print_tracked", fake_pi)
    result = req_test(
        yard,
        "QA-FUSE",
        print_mode=True,
        run_only=True, unsafe_skip_review=True,
        ingest=False,
    )
    assert "case-03" not in setup_ids
    assert set(setup_ids) <= {"case-01", "case-02"}
    assert result["summary"]["blocked"] == 2
    assert result["summary"]["passed"] == 1


def test_req_test_default_case_runner_success(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-DEF")
    _write_case(
        yard,
        "QA-DEF",
        "case-01.md",
        "---\nid: case-01\ntitle: test case 1\nrepo: backend\n---\n\nbody\n",
    )

    def fake_run_pi_print(argv, root, prompt, on_line=None, on_spawn=None, on_reap=None):
        # Locate the evidence dir from prompt or find it under yard/reqs/QA-DEF/qa/evidence
        ev_dirs = list((yard / "reqs" / "QA-DEF" / "qa" / "evidence").iterdir())
        assert ev_dirs
        case_dir = ev_dirs[0] / "case-01"
        assert case_dir.is_dir()
        result_yaml = case_dir / "result.yaml"
        result_yaml.write_text(
            yaml.safe_dump(
                {
                    "case": "case-01",
                    "status": "passed",
                    "repo": "backend",
                    "assertions": [
                        {"type": "ui", "expected": "ok", "actual": "ok", "status": "passed"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return 0, "ok"

    monkeypatch.setattr("dev_yard.qa.run_pi_print_tracked", fake_run_pi_print)
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    result = req_test(
        yard, "QA-DEF", print_mode=True, run_only=True, unsafe_skip_review=True, ingest=False
    )
    assert result["summary"]["passed"] == 1
    assert result["summary"]["blocked"] == 0


def test_req_test_holds_for_review_until_approved(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV1")
    design = _DesignRunner(yard, "QA-RV1")
    ran: list[str] = []

    def case_runner(job, pool):
        ran.append(job.id)
        return {"status": "passed", "repo": "backend"}

    held = req_test(
        yard,
        "QA-RV1",
        print_mode=True,
        runner=design,
        case_runner=case_runner,
        ingest=False,
    )
    assert design.called == 1
    assert held["awaiting_review"] is True
    assert held["review"]["status"] == "awaiting"
    assert ran == []
    assert not (yard / "reqs" / "QA-RV1" / "qa" / "evidence").exists()

    # Approval only marks the cases reviewed; it must not execute them.
    approved = req_test(
        yard,
        "QA-RV1",
        print_mode=True,
        approve=True,
        runner=design,
        case_runner=case_runner,
        ingest=False,
    )
    assert design.called == 1  # cases already exist; approval does not redesign
    assert approved["approved"] is True
    assert approved["review"]["approved"] is True
    assert ran == []
    assert not (yard / "reqs" / "QA-RV1" / "qa" / "evidence").exists()

    # Execution is a separate, explicit step.
    done = req_test(
        yard,
        "QA-RV1",
        print_mode=True,
        run_only=True,
        runner=design,
        case_runner=case_runner,
        ingest=False,
    )
    assert ran == ["case-01"]
    assert done["summary"]["passed"] == 1


def test_req_test_feedback_regenerates_and_awaits_review(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV2")
    _write_case(
        yard,
        "QA-RV2",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    design = _DesignRunner(yard, "QA-RV2")
    result = req_test(
        yard,
        "QA-RV2",
        print_mode=True,
        redesign=True,
        feedback="补齐权限拦截用例",
        runner=design,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        ingest=False,
    )
    assert design.called == 1
    assert result["awaiting_review"] is True
    assert result["review"]["status"] == "rejected"
    assert "权限拦截" in result["review"]["feedback"]


def test_review_approval_is_invalidated_by_case_change(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV3")
    _write_case(
        yard,
        "QA-RV3",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    ran: list[str] = []

    def case_runner(job, pool):
        ran.append(job.id)
        return {"status": "passed", "repo": "backend"}

    req_test(
        yard,
        "QA-RV3",
        print_mode=True,
        approve=True,
        case_runner=case_runner,
        ingest=False,
    )
    # Approval alone does not execute; the explicit run does.
    assert ran == []
    req_test(
        yard,
        "QA-RV3",
        print_mode=True,
        run_only=True,
        case_runner=case_runner,
        ingest=False,
    )
    assert ran == ["case-01"]

    # Editing a case after approval must force a re-review.
    _write_case(
        yard,
        "QA-RV3",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody changed\n",
    )
    held = req_test(
        yard,
        "QA-RV3",
        print_mode=True,
        case_runner=case_runner,
        ingest=False,
    )
    assert held["awaiting_review"] is True
    assert held["review"]["stale"] is True
    assert ran == ["case-01"]


def test_board_qa_run_reason_when_cases_await_review(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV4")
    _write_case(
        yard,
        "QA-RV4",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    detail = requirement_detail(yard, "QA-RV4")
    ids = {a.id: a for a in detail.actions}
    assert ids["qa-run"].enabled is False
    assert "待审核" in ids["qa-run"].reason
    assert ids["qa-review"].enabled
    # 设计用例 is a no-cases entry point; re-design goes through 打回重做.
    assert ids["qa-design"].enabled is False
    assert detail.next_label == "qa-review"
    assert detail.qa is not None
    assert detail.qa["review"]["status"] == "awaiting"
    assert detail.qa["review"]["approved"] is False


def test_approve_requires_existing_cases(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV5")
    design = _DesignRunner(yard, "QA-RV5")
    with pytest.raises(TestRejected, match="no cases to approve"):
        req_test(yard, "QA-RV5", print_mode=True, approve=True, runner=design)
    assert design.called == 0


def test_approve_rejects_feedback_combination(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV6")
    _write_case(
        yard,
        "QA-RV6",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    with pytest.raises(TestRejected, match="cannot be combined"):
        req_test(
            yard,
            "QA-RV6",
            print_mode=True,
            approve=True,
            redesign=True,
            feedback="改一下",
        )


def test_review_approval_tracks_setup_files_but_not_replay(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RV7")
    mod = yard / "reqs" / "QA-RV7" / "qa" / "cases" / "mod"
    mod.mkdir(parents=True)
    (mod / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
        encoding="utf-8",
    )
    (mod / "setup.sql").write_text("select 1;\n", encoding="utf-8")
    req_test(
        yard,
        "QA-RV7",
        print_mode=True,
        approve=True,
        ingest=False,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
    )

    # qa-run's replay output is not a reviewed artifact: approval must survive it.
    (mod / "case-01.replay.sh").write_text("playwright-cli ...\n", encoding="utf-8")
    still = req_test(
        yard,
        "QA-RV7",
        print_mode=True,
        ingest=False,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
    )
    assert "awaiting_review" not in still

    # Editing a setup script does change execution: approval must be invalidated.
    (mod / "setup.sql").write_text("select 2;\n", encoding="utf-8")
    held = req_test(
        yard,
        "QA-RV7",
        print_mode=True,
        ingest=False,
        case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
    )
    assert held["awaiting_review"] is True
    assert held["review"]["stale"] is True


def test_run_schedule_stops_dispatching_after_cancel():
    cases = [
        CaseJob(id="c1", title="t", repo="backend", priority="P1"),
        CaseJob(id="c2", title="t", repo="backend", priority="P1"),
        CaseJob(id="c3", title="t", repo="backend", priority="P1"),
    ]
    pools = [PoolSlot(id="a", provider=None, model=None, concurrency=1, priority=1)]
    cancel = threading.Event()
    ran: list[str] = []

    def run(job: CaseJob, slot: PoolSlot) -> dict:
        ran.append(job.id)
        cancel.set()
        return {"status": "passed", "repo": job.repo}

    run_schedule(cases, pools, run, cancel_check=cancel.is_set)
    # Only the first dispatched case runs; the rest are never scheduled.
    assert ran == ["c1"]
    assert sum(1 for c in cases if c.state == "passed") == 1


def test_run_schedule_inner_dispatch_loop_honours_cancel():
    # concurrency=2 would normally dispatch two ready cases in one pass; the
    # inner-loop check must stop after the first once cancel flips.
    cases = [
        CaseJob(id="c1", title="t", repo="backend", priority="P1"),
        CaseJob(id="c2", title="t", repo="backend", priority="P1"),
    ]
    pools = [PoolSlot(id="a", provider=None, model=None, concurrency=2, priority=1)]
    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    ran: list[str] = []

    def run(job: CaseJob, slot: PoolSlot) -> dict:
        ran.append(job.id)
        return {"status": "passed", "repo": job.repo}

    run_schedule(cases, pools, run, cancel_check=cancel_check)
    assert ran == ["c1"]


def test_req_test_cancel_raises_and_reaps_proc(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.runners import JobCancelled

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-CANCEL")
    _write_case(
        yard,
        "QA-CANCEL",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    cancel = threading.Event()
    spawned: list[object] = []
    reaped: list[object] = []

    def fake_pi(argv, cwd, prompt, on_line=None, timeout=None, on_spawn=None):
        proc = object()
        if on_spawn is not None:
            on_spawn(proc)
        cancel.set()
        return 1, ""

    # Patch the low-level runner so the real tracked wrapper runs and reaps.
    monkeypatch.setattr("dev_yard.runners.run_pi_print", fake_pi)
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})

    with pytest.raises(JobCancelled):
        req_test(
            yard,
            "QA-CANCEL",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            cancel_check=cancel.is_set,
            on_spawn=spawned.append,
            on_reap=reaped.append,
        )
    assert len(spawned) == 1
    assert reaped == spawned
    # The interrupted run must not be recorded as a completed result.
    evidence = yard / "reqs" / "QA-CANCEL" / "qa" / "evidence"
    run_dir = next(p for p in evidence.iterdir() if p.is_dir())
    assert not (run_dir / "result.yaml").is_file()


def test_req_test_cancel_before_design_raises(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.runners import JobCancelled

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-CANCEL-D")
    cancel = threading.Event()
    cancel.set()

    def boom(*a, **k):
        raise AssertionError("design runner must not start when cancelled")

    monkeypatch.setattr("dev_yard.qa.get_runner", boom)
    with pytest.raises(JobCancelled):
        req_test(
            yard,
            "QA-CANCEL-D",
            print_mode=True,
            design_only=True,
            cancel_check=cancel.is_set,
        )


# ---------------------------------------------------------------- rerun cases


def _seed_run_with_cases(
    yard: Path,
    key: str,
    *,
    case_states: dict[str, str],
    design: _DesignRunner,
) -> Path:
    """Design two cases and record a run whose progress holds the given states."""
    qa = yard / "reqs" / key / "qa"
    mod = qa / "cases" / "mod"
    mod.mkdir(parents=True, exist_ok=True)
    for cid, title in (("case-01", "first"), ("case-02", "second")):
        (mod / f"{cid}.md").write_text(
            "---\n"
            f"id: {cid}\ntitle: {title}\npriority: P0\n"
            f"requirement: {key}\nrepo: backend\ncovers: [D1]\n"
            "---\n\n# body\n",
            encoding="utf-8",
        )
    run_id = "2020-01-01-000000"
    run_dir = qa / "evidence" / run_id
    (run_dir / "repo-baseline").mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": run_id,
        "env": "local",
        "pools": [],
        "cases": [
            {
                "id": cid,
                "title": cid,
                "state": case_states[cid],
                "repo": "backend",
                "depends_on": [],
                "pool": "a",
                "model": "grok-4",
                "started_at": "2020-01-01T00:00:00Z",
                "ended_at": "2020-01-01T00:01:00Z",
                "reason": "old",
            }
            for cid in ("case-01", "case-02")
        ],
    }
    (run_dir / "progress.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for cid, state in case_states.items():
        cdir = run_dir / cid
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "result.yaml").write_text(
            yaml.safe_dump(
                {
                    "case": cid,
                    "title": cid,
                    "repo": "backend",
                    "status": state,
                    "reason": "old",
                    "assertions": [
                        {"type": "ui", "expected": "e", "actual": "a", "status": "passed"}
                    ],
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    return run_dir


def test_rerun_case_only_reruns_target(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RR")
    run_dir = _seed_run_with_cases(
        yard,
        "QA-RR",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RR"),
    )
    ran: list[str] = []

    def case_runner(job, pool):
        ran.append(job.id)
        return {"status": "passed", "repo": "backend"}

    result = req_test(
        yard,
        "QA-RR",
        print_mode=True,
        ingest=False,
        rerun_cases=["case-01"],
        case_runner=case_runner,
    )
    # Only the failed case is dispatched; the passed one keeps its result.
    assert ran == ["case-01"]
    assert result["run_id"] == "2020-01-01-000000"
    assert result["summary"] == {
        "passed": 2,
        "failed": 0,
        "blocked": 0,
        "skipped": 0,
        "total": 2,
        "blocked_kind": {"case-defect": 0, "env": 0, "cancelled": 0, "other": 0},
    }
    # Same run dir: the rerun amends the existing run, not a new one.
    assert sorted(p.name for p in (run_dir.parent).iterdir() if p.is_dir()) == [
        "2020-01-01-000000"
    ]


def test_rerun_case_rejects_unknown_id(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RR2")
    _seed_run_with_cases(
        yard,
        "QA-RR2",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RR2"),
    )
    with pytest.raises(TestRejected, match="unknown case"):
        req_test(yard, "QA-RR2", print_mode=True, rerun_cases=["case-99"])


def test_rerun_case_requires_existing_run(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RR3")
    design = _DesignRunner(yard, "QA-RR3")
    design.start("", yard, [])  # creates case-01, but no run yet
    with pytest.raises(TestRejected, match="no run contains"):
        req_test(yard, "QA-RR3", print_mode=True, rerun_cases=["case-01"])


def test_rerun_case_rejects_conflicting_flags(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RR4")
    _seed_run_with_cases(
        yard,
        "QA-RR4",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RR4"),
    )
    with pytest.raises(TestRejected, match="cannot be combined"):
        req_test(yard, "QA-RR4", print_mode=True, rerun_cases=["case-01"], redesign=True)


def test_rerun_case_adopts_run_env(tmp_path: Path, git_src: Path, monkeypatch):
    """The rerun defaults to the env recorded on the run it amends."""
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RR5")
    run_dir = _seed_run_with_cases(
        yard,
        "QA-RR5",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RR5"),
    )
    # Point the run at a non-active env to prove it is adopted.
    doc = yaml.safe_load((run_dir / "progress.yaml").read_text(encoding="utf-8"))
    doc["env"] = "staging"
    (run_dir / "progress.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    _write_qa_yaml(
        yard,
        "  staging:\n    base_url: http://127.0.0.1:9090\n",
    )
    seen: dict = {}

    def case_runner(job, pool):
        seen["env"] = job.id
        return {"status": "passed", "repo": "backend"}

    result = req_test(
        yard,
        "QA-RR5",
        print_mode=True,
        ingest=False,
        rerun_cases=["case-01"],
        case_runner=case_runner,
    )
    assert seen["env"] == "case-01"
    assert result.get("env") == "staging"


def test_reset_cases_drops_artifacts_and_marks_ready(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.qa import reset_cases_in_run

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RST")
    run_dir = _seed_run_with_cases(
        yard,
        "QA-RST",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RST"),
    )
    # A stale aggregate summary + a screenshot that must go with the reset case.
    (run_dir / "result.yaml").write_text("run_id: r\nsummary: {total: 2}\n", encoding="utf-8")
    shots = run_dir / "case-01" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "step-01.png").write_bytes(b"\x89PNG")

    reset = reset_cases_in_run(run_dir, {"case-01"}, {"case-01", "case-02"})
    assert reset == ["case-01"]

    doc = yaml.safe_load((run_dir / "progress.yaml").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in doc["cases"]}
    assert by_id["case-01"]["state"] == "ready"
    assert by_id["case-01"]["reason"] == ""
    # The other case keeps its terminal result.
    assert by_id["case-02"]["state"] == "passed"
    assert not (run_dir / "case-01" / "result.yaml").is_file()
    assert not shots.is_dir()
    assert (run_dir / "case-02" / "result.yaml").is_file()
    # Stale aggregate summary is dropped so the page reads it as in-progress.
    assert not (run_dir / "result.yaml").is_file()


def test_reset_cases_leaves_active_case_alone(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.qa import reset_cases_in_run

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RST2")
    run_dir = _seed_run_with_cases(
        yard,
        "QA-RST2",
        case_states={"case-01": "running", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RST2"),
    )
    reset = reset_cases_in_run(run_dir, {"case-01"}, {"case-01", "case-02"})
    assert reset == []
    doc = yaml.safe_load((run_dir / "progress.yaml").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in doc["cases"]}
    assert by_id["case-01"]["state"] == "running"


def test_reset_cases_rejects_unknown_id(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.qa import reset_cases_in_run

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-RST3")
    run_dir = _seed_run_with_cases(
        yard,
        "QA-RST3",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-RST3"),
    )
    with pytest.raises(TestRejected, match="unknown case"):
        reset_cases_in_run(run_dir, {"case-99"}, {"case-01", "case-02"})


def test_rerun_queues_behind_active_run(tmp_path: Path, git_src: Path, monkeypatch):
    """A re-run job does not conflict with an active qa-run; full runs do."""
    from dev_yard.web.jobs import Job, _jobs_conflict

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    active = Job(id="a", jira="QA-Q", action="qa-run")

    # A plain qa-run still conflicts (one full run at a time).
    assert _jobs_conflict(active, "QA-Q", "qa-run", None, {"label": "执行用例"}) is True
    # A re-run is allowed to queue behind it (req_test serialises on the lock).
    assert (
        _jobs_conflict(active, "QA-Q", "qa-run", None, {"rerun_cases": ["case-01"]})
        is False
    )
    # Re-runs for a different requirement never conflict.
    assert (
        _jobs_conflict(active, "QA-OTHER", "qa-run", None, {"rerun_cases": ["case-01"]})
        is False
    )


def test_rerun_dedupes_identical_jobs(tmp_path: Path, git_src: Path, monkeypatch):
    """A double click must not stack two identical re-run jobs."""
    from dev_yard.web.jobs import JobRunner

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-Q2")
    _seed_run_with_cases(
        yard,
        "QA-Q2",
        case_states={"case-01": "failed", "case-02": "passed"},
        design=_DesignRunner(yard, "QA-Q2"),
    )
    runner = JobRunner(yard, sync=False)
    try:
        runner.submit("qa-run", "QA-Q2", extra={"rerun_cases": ["case-01"]})
        with pytest.raises(ValueError, match="已有重测在排队"):
            runner.submit("qa-run", "QA-Q2", extra={"rerun_cases": ["case-01"]})
    finally:
        for job in runner.running():
            job.cancel()
        for job in list(runner._jobs.values()):
            job.done.wait(timeout=10)


# --- hardening: structured blocked_class, host recheck, mutation HEAD, gates ----


def test_env_block_class_prefers_declared_and_ignores_unknown():
    from dev_yard.qa_schedule import blocked_kind, env_block_class

    assert env_block_class("mystery", "case-defect") is None
    assert env_block_class("mystery", "undeployed") == "undeployed"
    assert env_block_class("mystery", "auth") == "auth"
    # No declared class / declared "other" / unknown string: must not trip.
    assert env_block_class("mystery", "") is None
    assert env_block_class("mystery", "other") is None
    assert env_block_class("mystery", "garbage") is None
    # Host-generated reasons keep their original breaker semantics.
    assert env_block_class("env fault: down", "env") is None
    assert env_block_class("setup failed: x", "env") is None

    assert blocked_kind("mystery", "case-defect") == "case-defect"
    assert blocked_kind("mystery", "auth") == "env"
    assert blocked_kind("mystery", "undeployed") == "env"
    assert blocked_kind("mystery", "") == "other"


def test_unknown_blocked_reasons_do_not_trip_breaker():
    cases = [CaseJob(id=f"c{i}", title="t", repo="be") for i in (1, 2, 3)]
    pools = [PoolSlot(id="p", provider="rcc", model="m", concurrency=1, priority=1)]

    def run(job, slot):
        if job.id in {"c1", "c2"}:
            return {"status": "blocked", "reason": "mystery failure"}
        return {"status": "passed"}

    run_schedule(cases, pools, run)
    # Two unrelated untyped blockers must not abort the rest of the run.
    assert cases[2].state == "passed"


def test_declared_case_defect_does_not_trip_breaker():
    cases = [CaseJob(id=f"c{i}", title="t", repo="be") for i in (1, 2, 3)]
    pools = [PoolSlot(id="p", provider="rcc", model="m", concurrency=1, priority=1)]

    def run(job, slot):
        if job.id in {"c1", "c2"}:
            return {"status": "blocked", "reason": "missing seed", "blocked_class": "case-defect"}
        return {"status": "passed"}

    run_schedule(cases, pools, run)
    assert cases[2].state == "passed"


def test_recheck_db_assertions_flags_false_pass(tmp_path: Path, monkeypatch):
    from dev_yard.qa_exec import recheck_db_assertions

    _write_qa_yaml(tmp_path, "    db:\n      url: postgres://u:p@h/db\n")
    cfg = load_qa_config(tmp_path)
    job = CaseJob(id="c1", title="t", repo="be")
    result = {
        "status": "passed",
        "assertions": [
            {
                "type": "db",
                "expected": "3",
                "actual": "3",
                "status": "passed",
                "sql": "SELECT count(*) FROM projects",
            }
        ],
    }

    monkeypatch.setattr("dev_yard.qa_exec.run_sql_value", lambda cfg, sql, on_log=None: "3")
    assert recheck_db_assertions(cfg, job, result) == []

    monkeypatch.setattr("dev_yard.qa_exec.run_sql_value", lambda cfg, sql, on_log=None: "0")
    problems = recheck_db_assertions(cfg, job, result)
    assert len(problems) == 1
    assert problems[0]["actual"] == "0"


def test_recheck_skips_db_assertion_without_sql(tmp_path: Path, monkeypatch):
    from dev_yard.qa_exec import recheck_db_assertions

    _write_qa_yaml(tmp_path, "    db:\n      url: postgres://u:p@h/db\n")
    cfg = load_qa_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run sql without a sql field")

    monkeypatch.setattr("dev_yard.qa_exec.run_sql_value", boom)
    job = CaseJob(id="c1", title="t", repo="be")
    result = {
        "status": "passed",
        "assertions": [{"type": "db", "expected": "x", "actual": "x", "status": "passed"}],
    }
    assert recheck_db_assertions(cfg, job, result) == []


def test_head_move_counts_as_worker_mutation(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-HEAD")
    _write_case(
        yard,
        "QA-HEAD",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    heads = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr("dev_yard.qa._head_sha", lambda wt: next(heads))
    with pytest.raises(TestRejected, match="worker mutated"):
        req_test(
            yard,
            "QA-HEAD",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_case_without_repo_rejected_before_run(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-NOREPO")
    _write_case(
        yard,
        "QA-NOREPO",
        "case-01.md",
        "---\nid: case-01\ntitle: t\n---\n\nbody\n",
    )
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    with pytest.raises(TestRejected, match="has no repo"):
        req_test(
            yard,
            "QA-NOREPO",
            print_mode=True,
            run_only=True, unsafe_skip_review=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_run_only_requires_unsafe_skip_review(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-UNSAFE")
    _write_case(
        yard,
        "QA-UNSAFE",
        "case-01.md",
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\nbody\n",
    )
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    with pytest.raises(TestRejected, match="unsafe-skip-review"):
        req_test(
            yard,
            "QA-UNSAFE",
            print_mode=True,
            run_only=True,
            ingest=False,
            case_runner=lambda j, p: {"status": "passed", "repo": "backend"},
        )


def test_map_qa_result_refuses_design_blocked_skip():
    run = {"summary": {"passed": 1, "failed": 0, "blocked": 0, "skipped": 1, "total": 2}}
    cases = [
        {"case": "c1", "status": "passed"},
        {"case": "c2", "status": "skipped", "reason": "design-blocked: 0 rows"},
    ]
    assert map_qa_result(run, cases) is None


def test_uncovered_changes_lists_change_without_case(tmp_path: Path):
    from dev_yard import paths
    from dev_yard.qa import uncovered_changes

    qa = paths.qa_dir(tmp_path, "J-1")
    qa.mkdir(parents=True)
    (qa / "meta.yaml").write_text(
        "changes:\n  - {id: D1}\n  - {id: D2}\n", encoding="utf-8"
    )
    cases = [CaseJob(id="case-01", title="t", repo="be", covers=["D1"])]
    assert uncovered_changes(qa, cases) == ["D2"]


def test_verify_lint_requires_column_in_body():
    from dev_yard.qa_schedule import CaseJob as _Job
    from dev_yard.qa_verify import lint_verify

    job = _Job(id="c", title="t", repo="be", body="## 预期\n- DB: projects 存在\n")
    bad = lint_verify(job, "SELECT id FROM projects WHERE id=1")
    assert not bad["ok"]
    assert "id" in bad["detail"]


def test_verify_case_uses_verify_db_url(tmp_path: Path, monkeypatch):
    from dev_yard import paths
    from dev_yard.qa_verify import verify_case

    _write_qa_yaml(
        tmp_path,
        "    db:\n      url: postgres://w:p@h/db\n"
        "      verify_url: postgres://ro:p@h/db\n",
    )
    cfg = load_qa_config(tmp_path)
    assert cfg.env.verify_db_url == "postgres://ro:p@h/db"
    case_dir = paths.qa_dir(tmp_path, "J-1") / "cases" / "mod"
    case_dir.mkdir(parents=True)
    path = case_dir / "case-01.md"
    path.write_text(
        "---\nid: case-01\ntitle: t\nrepo: be\n---\n\n## 预期\n- DB: projects.id=1\n",
        encoding="utf-8",
    )
    (case_dir / "verify.sql").write_text(
        "SELECT id FROM projects WHERE id=1", encoding="utf-8"
    )
    job = CaseJob(
        id="case-01",
        title="t",
        repo="be",
        body="## 预期\n- DB: projects.id=1\n",
        path=str(path),
        verify="verify.sql",
    )
    seen: dict[str, str] = {}

    def fake_count(c, sql, on_log=None):
        seen["url"] = c.env.db_url
        return 1

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", fake_count)
    assert verify_case(tmp_path, "J-1", cfg, job).status == "passed"
    assert seen["url"] == "postgres://ro:p@h/db"


def test_run_prompt_hides_password(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard.qa import _case_auth_env, _run_prompt

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-PW")
    (yard / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    auth:\n      default: admin\n"
        "      accounts:\n        admin: { username: admin, password: s3cret }\n",
        encoding="utf-8",
    )
    cfg = load_qa_config(yard, jira="QA-PW")
    job = CaseJob(id="case-01", title="t", repo="backend", account="admin")
    prompt = _run_prompt(yard, "QA-PW", cfg, job, "2026-01-01-000000")
    assert "s3cret" not in prompt
    assert "YARD_QA_PASSWORD" in prompt
    env = _case_auth_env(cfg, job)
    assert env["YARD_QA_PASSWORD"] == "s3cret"
