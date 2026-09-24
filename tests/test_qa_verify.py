from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dev_yard import paths
from dev_yard.qa import req_test
from dev_yard.qa_config import TestRejected, load_qa_config
from dev_yard.qa_review import approve_cases, cases_fingerprint, review_gate
from dev_yard.qa_schedule import CaseJob
from dev_yard.qa_verify import (
    VerifyResult,
    env_lock,
    failed_cases,
    lint_verify,
    needs_verify,
    prior_defects,
    read_summary,
    render_feedback,
    verify_case,
    verify_gate,
    write_summary,
)
from dev_yard.runners import DryRunRunner, Runner, RunResult
from dev_yard.service import (
    implement,
    init_yard,
    repo_add,
    req_freeze,
    req_open,
    review,
)
from dev_yard.test_report import submit_test

JIRA = "QA-1"


def _write_qa_yaml(root: Path, extra: str = "") -> None:
    (root / "qa.yaml").write_text(
        "active_env: local\n"
        "browser:\n  channel: chrome\n  headed: false\n"
        "workers:\n"
        "  - id: a\n    provider: rcc\n    model: grok-4\n"
        "    concurrency: 1\n    priority: 1\n"
        "envs:\n  local:\n    base_url: http://127.0.0.1:8080\n"
        "    db:\n      url: postgres://u:p@127.0.0.1:5432/qa\n"
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


_CASE = """---
id: case-01
title: t
priority: P0
requirement: {key}
repo: backend
covers: [D1]
data: {{ verify: verify.sql }}
---

## 前置
- 项目 669215 存在

## 步骤
1. 打开项目

## 预期
- DB: projects.id=669215 的记录存在
"""


class _CaseWriter(Runner):
    """Writes a data-asserting case; each call may use a different verify.sql."""

    def __init__(self, yard: Path, key: str, bodies: list[str] | None = None):
        self.yard = yard
        self.key = key
        self.bodies = bodies or ["SELECT id FROM projects WHERE id=669215"]
        self.called = 0

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        body = self.bodies[min(self.called, len(self.bodies) - 1)]
        self.called += 1
        d = self.yard / "reqs" / self.key / "qa" / "cases" / "mod"
        d.mkdir(parents=True, exist_ok=True)
        (d / "case-01.md").write_text(_CASE.format(key=self.key), encoding="utf-8")
        (d / "verify.sql").write_text(body, encoding="utf-8")
        return RunResult(ok=True, summary="designed")


def _cfg(tmp_path: Path):
    _write_qa_yaml(tmp_path)
    return load_qa_config(tmp_path)


def _case_dir(tmp_path: Path, jira: str = JIRA) -> Path:
    case_dir = paths.qa_dir(tmp_path, jira) / "cases" / "mod"
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _case_job(tmp_path: Path, *, verify_sql: str | None, setup: str = "") -> CaseJob:
    case_dir = _case_dir(tmp_path)
    path = case_dir / "case-01.md"
    body = "## 预期\n- DB: projects.id=669215\n"
    path.write_text(
        "---\nid: case-01\ntitle: t\nrepo: backend\n---\n\n" + body,
        encoding="utf-8",
    )
    if verify_sql is not None:
        (case_dir / "verify.sql").write_text(verify_sql, encoding="utf-8")
    return CaseJob(
        id="case-01",
        title="t",
        repo="backend",
        body=body,
        path=str(path),
        setup=setup,
        verify="verify.sql" if verify_sql is not None else "",
    )


def test_lint_verify_requires_identifier_in_body():
    job = CaseJob(id="c", title="t", repo="be", body="## 预期\n- DB: projects.id=1\n")
    ok = lint_verify(job, "SELECT id FROM projects WHERE id=1")
    assert ok["ok"] and not ok["empty"]
    assert "projects" in ok["matched"]

    empty = lint_verify(job, "SELECT 1")
    assert empty["ok"] and empty["empty"]

    bad = lint_verify(job, "SELECT id FROM unrelated_table WHERE id=1")
    assert not bad["ok"]
    assert "unrelated_table" in bad["detail"]


def test_lint_verify_exempts_internal_seed_table():
    # The seed registry is a host convention, never named in a case body; a
    # verify that locates its own seed through it must still pass.
    job = CaseJob(
        id="c",
        title="t",
        repo="be",
        body="## 预期\n- DB: projects.id=1 由 _qa 种子定位\n",
    )
    ok = lint_verify(
        job,
        "SELECT p.id FROM _qa_exec_seeds s JOIN projects p ON p.id = s.entity_id",
    )
    assert ok["ok"], ok
    assert not ok["empty"]

    # A real business table is still required to be named in the body.
    bad = lint_verify(
        job,
        "SELECT p.id FROM _qa_exec_seeds s JOIN other_table p ON p.id = s.entity_id",
    )
    assert not bad["ok"]
    assert "other_table" in bad["detail"]
    assert "_qa_exec_seeds" not in bad["detail"]

    # A verify that only reads the seed registry proves nothing about business
    # data: pass, but surface it as an exemption like `SELECT 1`.
    only_internal = lint_verify(
        CaseJob(id="c", title="t", repo="be", body="## 预期\n- DB: projects.id=1\n"),
        "SELECT 1 FROM _qa_exec_seeds WHERE jira = 'X'",
    )
    assert only_internal["ok"] and only_internal["empty"]


def test_needs_verify_exempts_pure_ui():
    ui = CaseJob(id="c", title="t", repo="be", body="## 预期\n- UI: ok\n")
    assert not needs_verify(ui)
    assert needs_verify(CaseJob(id="c", title="t", repo="be", setup="a.sql"))
    assert needs_verify(CaseJob(id="c", title="t", repo="be", body="- DB: x\n"))


def test_verify_case_missing_verify_is_failure(tmp_path: Path):
    job = _case_job(tmp_path, verify_sql=None, setup="setup.sql")
    result = verify_case(tmp_path, JIRA, _cfg(tmp_path), job)
    assert result.status == "failed"
    assert "missing verify.sql" in result.error


def test_verify_case_zero_rows_fails_and_writes_artifact(tmp_path: Path, monkeypatch):
    job = _case_job(tmp_path, verify_sql="SELECT id FROM projects WHERE id=669215")
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 0
    )
    result = verify_case(tmp_path, JIRA, _cfg(tmp_path), job, fingerprint="fp")
    assert result.status == "failed"
    assert result.error == "0 rows"
    artifact = paths.qa_dir(tmp_path, JIRA) / "design-verify" / "case-01.yaml"
    data = yaml.safe_load(artifact.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert data["fingerprint"] == "fp"


def test_verify_case_passes_with_rows(tmp_path: Path, monkeypatch):
    job = _case_job(tmp_path, verify_sql="SELECT id FROM projects WHERE id=669215")
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 1
    )
    assert verify_case(tmp_path, JIRA, _cfg(tmp_path), job).status == "passed"


def test_verify_case_exempt_marks_skipped(tmp_path: Path):
    case_dir = _case_dir(tmp_path)
    path = case_dir / "case-01.md"
    path.write_text("---\nid: case-01\n---\n\n## 预期\n- UI: ok\n", encoding="utf-8")
    job = CaseJob(
        id="case-01", title="t", repo="backend", body="## 预期\n- UI: ok\n", path=str(path)
    )
    assert verify_case(tmp_path, JIRA, _cfg(tmp_path), job).status == "skipped"


def test_verify_gate_blocks_matching_failures(tmp_path: Path):
    qa = paths.qa_dir(tmp_path, JIRA)
    case_dir = _case_dir(tmp_path)
    (case_dir / "case-01.md").write_text("---\nid: case-01\n---\n", encoding="utf-8")
    fp = cases_fingerprint(qa)
    write_summary(
        tmp_path,
        JIRA,
        {"case-01": VerifyResult(case="case-01", status="failed", error="0 rows")},
        fp,
    )
    assert failed_cases(qa, fp) == ["case-01"]
    assert read_summary(qa)["summary"]["failed"] == 1
    ok, why = verify_gate(qa, fp)
    assert not ok and "case-01" in why
    assert verify_gate(qa, fp, allow_unverified=True)[0]
    # A changed case set invalidates the verdict instead of blocking on it.
    (case_dir / "case-02.md").write_text("---\nid: case-02\n---\n", encoding="utf-8")
    assert verify_gate(qa, cases_fingerprint(qa))[0]


def test_review_gate_reads_verify_summary(tmp_path: Path):
    qa = paths.qa_dir(tmp_path, JIRA)
    (qa / "cases" / "mod").mkdir(parents=True)
    (qa / "cases" / "mod" / "case-01.md").write_text(
        "---\nid: case-01\n---\n", encoding="utf-8"
    )
    approve_cases(qa)
    # Requiring verification means "not run" must not read as "passed".
    can_run, why = review_gate(qa)
    assert not can_run and "尚未做数据核实" in why
    assert review_gate(qa, require_verify=False)[0]

    fp = cases_fingerprint(qa)
    write_summary(
        tmp_path,
        JIRA,
        {"case-01": VerifyResult(case="case-01", status="passed")},
        fp,
    )
    assert review_gate(qa) == (True, "")
    write_summary(
        tmp_path,
        JIRA,
        {"case-01": VerifyResult(case="case-01", status="failed", error="0 rows")},
        fp,
    )
    can_run, why = review_gate(qa)
    assert not can_run and "核实未通过" in why
    assert review_gate(qa, allow_unverified=True)[0]
    assert review_gate(qa, require_verify=False)[0]


def test_write_blocked_artifact(tmp_path: Path):
    from dev_yard.qa_verify import write_blocked

    results = {
        "case-01": VerifyResult(case="case-01", status="failed", error="0 rows"),
        "case-02": VerifyResult(case="case-02", status="passed"),
    }
    path = write_blocked(tmp_path, JIRA, results)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "case-01" in text and "case-02" not in text
    assert write_blocked(tmp_path, JIRA, {"case-02": results["case-02"]}) is None


def test_render_feedback_lists_only_failures():
    text = render_feedback(
        {
            "case-06": VerifyResult(
                case="case-06",
                status="failed",
                verify_sql="SELECT id FROM projects WHERE id=669215",
                error="0 rows",
            ),
            "case-07": VerifyResult(case="case-07", status="passed"),
        }
    )
    assert "case-06" in text and "0 rows" in text
    assert "case-07" not in text


def test_prior_defects_reads_previous_runs(tmp_path: Path):
    qa = tmp_path / "qa"
    run = qa / "evidence" / "2026-01-01-000000" / "case-02"
    run.mkdir(parents=True)
    (run / "result.yaml").write_text(
        yaml.safe_dump({"reason": "case-defect: 缺 province_id"}), encoding="utf-8"
    )
    other = qa / "evidence" / "2026-01-01-000000" / "case-09"
    other.mkdir(parents=True)
    (other / "result.yaml").write_text(
        yaml.safe_dump({"reason": "env fault: down"}), encoding="utf-8"
    )
    history = prior_defects(qa, [CaseJob(id="case-02", title="t", repo="be")])
    assert history == {"case-02": ["case-defect: 缺 province_id"]}


def test_prior_defects_includes_design_blocked(tmp_path: Path):
    qa = paths.qa_dir(tmp_path, JIRA)
    (qa / "cases" / "mod").mkdir(parents=True)
    (qa / "cases" / "mod" / "case-02.md").write_text(
        "---\nid: case-02\n---\n", encoding="utf-8"
    )
    write_summary(
        tmp_path,
        JIRA,
        {"case-02": VerifyResult(case="case-02", status="failed", error="0 rows")},
        cases_fingerprint(qa),
    )
    history = prior_defects(qa, [CaseJob(id="case-02", title="t", repo="be")])
    assert history == {"case-02": ["design-blocked: 0 rows"]}


def test_env_lock_is_exclusive(tmp_path: Path):
    with env_lock(tmp_path, "local"):
        with pytest.raises(TestRejected, match="another design verification"):
            with env_lock(tmp_path, "local"):
                pass


def test_assert_readonly_sql_allows_literals_rejects_writes():
    from dev_yard.qa_exec import assert_readonly_sql

    assert assert_readonly_sql("SELECT 1;") == "SELECT 1"
    assert "update" in assert_readonly_sql("SELECT id FROM t WHERE note = 'update me'")
    for bad in ("DELETE FROM t", "SELECT 1; SELECT 2", "UPDATE t SET x=1"):
        with pytest.raises(TestRejected):
            assert_readonly_sql(bad)


def test_assert_readonly_sql_ignores_comments():
    from dev_yard.qa_exec import assert_readonly_sql

    # A leading prose header must not be mistaken for the statement head, and
    # the returned statement is executable (comments gone, strings kept).
    headered = "-- case-01 verify: 被测项目落库\nSELECT id FROM projects"
    assert assert_readonly_sql(headered) == "SELECT id FROM projects"
    # A write keyword / `;` inside a comment is documentation, not SQL.
    assert (
        assert_readonly_sql("-- setup 阶段已 delete 旧数据；不要慌\nSELECT 1")
        == "SELECT 1"
    )
    assert assert_readonly_sql("/* delete this note */ SELECT 1") == "SELECT 1"
    assert assert_readonly_sql("SELECT 1 -- trailing; note") == "SELECT 1"
    # Nested block comments and dollar-quoted literals are valid read-only SQL.
    assert (
        assert_readonly_sql("SELECT 1 /* a /* b */ DELETE */ FROM t")
        == "SELECT 1   FROM t"
    )
    assert assert_readonly_sql("SELECT $$delete$$") == "SELECT $$delete$$"
    # A `/*` opening inside a `--` comment must not swallow the next statement.
    for bad in (
        "-- ok\nDELETE FROM t",
        "/* note */ UPDATE t SET x=1",
        "-- only a comment",
        "SELECT 1 -- /*\n; DROP TABLE t\n-- */",
    ):
        with pytest.raises(TestRejected):
            assert_readonly_sql(bad)


def test_run_sql_count_counts_rows(tmp_path: Path, monkeypatch):
    import subprocess

    from dev_yard.qa_exec import run_sql_count

    monkeypatch.setattr("dev_yard.qa_exec.shutil.which", lambda name: "/usr/bin/usql")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        stdout = "7\n" if "count(*)" in cmd[-1] else "alpha\nbeta\n"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr("dev_yard.qa_exec._run", fake_run)
    cfg = _cfg(tmp_path)
    assert run_sql_count(cfg, "SELECT id FROM projects") == 7
    assert "count(*)" in seen[-1][-1]
    # A comment header / trailing `;` must not defeat the count(*) wrapping.
    assert run_sql_count(cfg, "-- header\nSELECT id FROM projects") == 7
    assert "count(*)" in seen[-1][-1]
    assert run_sql_count(cfg, "SELECT id FROM projects; -- done") == 7
    assert "count(*)" in seen[-1][-1]
    assert run_sql_count(cfg, "DESC projects") == 2


def test_verify_only_reports_summary(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 1
    )
    yard = _testing_req(tmp_path, git_src, "QA-V1")
    writer = _CaseWriter(yard, "QA-V1")
    req_test(yard, "QA-V1", print_mode=True, design_only=True, runner=writer)
    result = req_test(yard, "QA-V1", print_mode=True, verify_only=True)
    assert result["verify_only"] is True
    assert result["verify"]["summary"]["passed"] == 1


def test_design_loop_repairs_data_gap(tmp_path: Path, git_src: Path, monkeypatch):
    calls = {"n": 0}

    def rows(cfg, sql, on_log=None):
        calls["n"] += 1
        return 0 if calls["n"] == 1 else 1

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", rows)
    yard = _testing_req(tmp_path, git_src, "QA-V2")
    writer = _CaseWriter(
        yard,
        "QA-V2",
        bodies=[
            "SELECT id FROM projects WHERE id=669215",
            "SELECT id FROM projects WHERE id=1",
        ],
    )
    result = req_test(yard, "QA-V2", print_mode=True, runner=writer)
    assert result["awaiting_review"] is True
    assert writer.called == 2  # initial design + one redesign after the failure
    assert result["verify"]["summary"]["passed"] == 1
    assert result["review"]["approved"] is False
    # The host's findings are recorded as review feedback for the human.
    assert result["review"]["status"] == "rejected"
    assert "核实" in result["review"]["feedback"]


def test_exhausted_verify_loop_records_final_failures(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 0
    )
    yard = _testing_req(tmp_path, git_src, "QA-V6")
    _write_qa_yaml(yard, "\ndesign:\n  verify_attempts: 2\n")
    writer = _CaseWriter(
        yard,
        "QA-V6",
        bodies=[
            "SELECT id FROM projects WHERE id=669215",
            "SELECT id FROM projects WHERE id=1",
        ],
    )
    result = req_test(yard, "QA-V6", print_mode=True, runner=writer)
    assert result["awaiting_review"] is True
    assert writer.called == 2  # initial design + one redesign after the failure
    assert result["review"]["status"] == "rejected"
    # The feedback must describe the verdict the human has to act on — the
    # retry that is still failing — not the findings that triggered the (already
    # applied) redesign.
    assert "id=1" in result["review"]["feedback"]
    assert "id=669215" not in result["review"]["feedback"]
    # The UI only renders `failed` when the summary is present and fresh, so
    # lock those fields down too.
    verify = result["review"]["verify"]
    assert verify["present"] is True
    assert verify["stale"] is False
    assert verify["failed"] == ["case-01"]


def test_approve_refused_until_verified(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 0
    )
    yard = _testing_req(tmp_path, git_src, "QA-V3")
    writer = _CaseWriter(yard, "QA-V3")
    req_test(yard, "QA-V3", print_mode=True, design_only=True, runner=writer)
    with pytest.raises(TestRejected, match="被拒绝"):
        req_test(yard, "QA-V3", print_mode=True, approve=True, runner=writer)
    # Explicit override approves, but the unverified case is skipped at run time.
    approved = req_test(
        yard,
        "QA-V3",
        print_mode=True,
        approve=True,
        allow_unverified=True,
        runner=writer,
        ingest=False,
    )
    assert approved["approved"] is True
    result = req_test(
        yard,
        "QA-V3",
        print_mode=True,
        run_only=True,
        allow_unverified=True,
        runner=writer,
        ingest=False,
        case_runner=lambda job, slot: {"status": "passed", "reason": ""},
    )
    assert result["summary"]["skipped"] == 1
    assert result["summary"]["passed"] == 0


def test_approve_after_verify_redesign_awaits_review(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.setattr(
        "dev_yard.qa_verify.run_sql_count", lambda cfg, sql, on_log=None: 0
    )
    yard = _testing_req(tmp_path, git_src, "QA-V5")
    writer = _CaseWriter(
        yard,
        "QA-V5",
        bodies=[
            "SELECT id FROM projects WHERE id=1",
            "SELECT id FROM projects WHERE id=2",
            "SELECT id FROM projects WHERE id=3",
            "SELECT id FROM projects WHERE id=4",
        ],
    )
    req_test(yard, "QA-V5", print_mode=True, design_only=True, runner=writer)
    # The approve invocation redesigns the still-failing cases; those must go
    # back for human review rather than being auto-approved.
    result = req_test(yard, "QA-V5", print_mode=True, approve=True, runner=writer)
    assert result["awaiting_review"] is True
    assert "自动重做" in result["reason"]
    assert result.get("run_id") is None
    assert result["review"]["approved"] is False


def test_no_verify_skips_the_loop(tmp_path: Path, git_src: Path, monkeypatch):
    def boom(cfg, sql, on_log=None):
        raise AssertionError("run_sql_count must not run with --no-verify")

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", boom)
    yard = _testing_req(tmp_path, git_src, "QA-V4")
    writer = _CaseWriter(yard, "QA-V4")
    result = req_test(yard, "QA-V4", print_mode=True, runner=writer, verify=False)
    assert result["awaiting_review"] is True


def test_verify_env_error_blocks_not_fails(tmp_path: Path, monkeypatch):
    """A DB/usql failure is an environment block, not a case data gap (M5)."""
    job = _case_job(tmp_path, verify_sql="SELECT id FROM projects WHERE id=669215")

    def boom(cfg, sql, on_log=None):
        raise TestRejected("usql not found; cannot verify data")

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", boom)
    result = verify_case(tmp_path, JIRA, _cfg(tmp_path), job)
    assert result.status == "blocked"
    assert result.blocked_class == "env"
    # A blocked case must not be reported as a design-blocked failure.
    assert failed_cases(paths.qa_dir(tmp_path, JIRA), "") == []


def test_verify_env_error_does_not_block_design_loop(tmp_path: Path, git_src: Path, monkeypatch):
    """An env-blocked verify must not trigger a design redesign (M5)."""
    calls = {"n": 0}

    def boom(cfg, sql, on_log=None):
        calls["n"] += 1
        raise TestRejected("connection refused")

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", boom)
    yard = _testing_req(tmp_path, git_src, "QA-V7")
    writer = _CaseWriter(yard, "QA-V7")
    result = req_test(yard, "QA-V7", print_mode=True, runner=writer)
    assert writer.called == 1  # design ran once; no redesign on an env error
    assert result["awaiting_review"] is True
    assert result["verify"]["summary"]["blocked"] == 1
    assert result["verify"]["failed"] == []


def test_env_lock_waits_for_holder_when_asked(tmp_path: Path):
    """A wait_timeout lets a second run queue instead of failing (M8)."""
    import threading

    from dev_yard.qa_verify import env_lock

    held = threading.Event()
    acquired = threading.Event()

    def holder():
        with env_lock(tmp_path, "local"):
            acquired.set()
            held.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(2)
    order: list[str] = []

    def waiter():
        with env_lock(tmp_path, "local", wait_timeout=5):
            order.append("waiter")
        held.set()

    w = threading.Thread(target=waiter)
    w.start()
    w.join(timeout=8)
    assert order == ["waiter"]  # it waited, then acquired, then released
    t.join(timeout=2)


def test_env_lock_reports_wait_state(tmp_path: Path):
    """`on_wait` flips True while queued and back to False once held (M8)."""
    import threading

    from dev_yard.qa_verify import env_lock

    held = threading.Event()
    acquired = threading.Event()

    def holder():
        with env_lock(tmp_path, "local"):
            acquired.set()
            held.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(2)
    seen: list[bool] = []

    def waiter():
        with env_lock(tmp_path, "local", wait_timeout=5, on_wait=seen.append):
            seen.append(False)  # marker: the lock is held here
        held.set()

    w = threading.Thread(target=waiter)
    w.start()
    w.join(timeout=8)
    assert seen[:2] == [True, False]  # waited, then acquired
    t.join(timeout=2)


def test_env_lock_no_wait_leaves_flag_unset(tmp_path: Path):
    from dev_yard.qa_verify import env_lock

    seen: list[bool] = []
    with env_lock(tmp_path, "local", on_wait=seen.append):
        pass
    assert seen == []  # never contended -> no wait signal


def test_verify_env_block_is_retried(tmp_path: Path, git_src: Path, monkeypatch):
    calls = {"n": 0}

    def boom(cfg, sql, on_log=None):
        calls["n"] += 1
        raise TestRejected("connection refused")

    monkeypatch.setattr("dev_yard.qa_verify.run_sql_count", boom)
    yard = _testing_req(tmp_path, git_src, "QA-VR")
    _write_qa_yaml(yard, "\ndesign:\n  verify_retry_attempts: 2\n")
    writer = _CaseWriter(yard, "QA-VR")
    result = req_test(yard, "QA-VR", print_mode=True, runner=writer)
    assert writer.called == 1  # an env error must not trigger a redesign
    assert calls["n"] == 3  # initial verify + 2 retries
    assert result["verify"]["summary"]["blocked"] == 1
