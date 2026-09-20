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

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.called += 1
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

    result = req_test(
        yard,
        "QA-4",
        print_mode=True,
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

    with pytest.raises(TestRejected, match="mutated"):
        req_test(
            yard,
            "QA-6",
            print_mode=True,
            runner=design,
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

    result = req_test(yard, "QA-7", print_mode=True, runner=design, case_runner=ok)
    assert result["ingested"] is True
    data = st.load(yard, "QA-7")
    assert data["phase"] == "done"
    assert data["test"]["latest_verdict"] == "passed"
    assert data["test"]["source"] == "yard"


def test_board_run_test_enabled(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = _testing_req(tmp_path, git_src, "QA-8")
    detail = requirement_detail(yard, "QA-8")
    ids = {a.id: a for a in detail.actions}
    assert ids["run-test"].enabled
    assert detail.next_label == "run-test"
    assert "run-test" not in PIPELINE
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

    result = req_test(
        yard,
        "QA-E2",
        env="test",
        print_mode=True,
        runner=_Design(yard, "QA-E2"),
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
        run_only=True,
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
            run_only=True,
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
            run_only=True,
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
    req_test(yard, "QA-S1", print_mode=True, run_only=True, ingest=False, case_runner=run)
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
    req_test(yard, "QA-S2", print_mode=True, run_only=True, ingest=False, case_runner=run)
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

    with pytest.raises(TestRejected, match="mutated"):
        req_test(yard, "QA-9", print_mode=True, runner=design, case_runner=mutate)
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

    result = req_test(yard, "QA-10", print_mode=True, runner=design, case_runner=drop)
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
    req_test(
        yard,
        "QA-11",
        print_mode=True,
        runner=design,
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
            run_only=True,
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
        run_only=True,
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
        run_only=True,
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
        req_test(yard, "QA-RN", print_mode=True, run_only=True, resume=True)


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
        req_test(yard, "QA-RE", print_mode=True, run_only=True, resume=True)


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
            run_only=True,
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
    monkeypatch.setattr("dev_yard.qa.run_pi_print", lambda *a, **k: (1, "boom"))
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    result = req_test(
        yard, "QA-STALE", print_mode=True, run_only=True, ingest=False, resume=True
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
        run_only=True,
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
        yard, "QA-SU", print_mode=True, run_only=True, ingest=False
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
        run_only=True,
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

    def fake_pi(argv, root, prompt, on_line=None):
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

    monkeypatch.setattr("dev_yard.qa.run_pi_print", fake_pi)
    result = req_test(
        yard,
        "QA-FUSE",
        print_mode=True,
        run_only=True,
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

    def fake_run_pi_print(argv, root, prompt, on_line=None):
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

    monkeypatch.setattr("dev_yard.qa.run_pi_print", fake_run_pi_print)
    monkeypatch.setattr("dev_yard.qa._preload_auth", lambda *a, **k: {})
    result = req_test(
        yard, "QA-DEF", print_mode=True, run_only=True, ingest=False
    )
    assert result["summary"]["passed"] == 1
    assert result["summary"]["blocked"] == 0


