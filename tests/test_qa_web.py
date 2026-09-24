import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.app import create_app
from dev_yard.web.board import DOC_FILES
from dev_yard.web.jobs import JobRunner


def _client(yard: Path) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True))


def _req(tmp_path: Path, git_src: Path, monkeypatch, key: str = "QA-W1") -> Path:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    req_open(yard, key, source="none")
    return yard


def _seed_qa(yard: Path, *, progress: bool = True) -> None:
    qa = yard / "reqs" / "QA-W1" / "qa"
    (qa / "cases" / "mod").mkdir(parents=True)
    (qa / "meta.yaml").write_text(
        "module: QA-W1-x\nrequirement: QA-W1\nchanges:\n"
        "  - id: D1\n    repo: backend\n    ref: a.py\n    desc: x\n",
        encoding="utf-8",
    )
    (qa / "cases" / "mod" / "case-01.md").write_text(
        "---\nid: case-01\ntitle: t\npriority: P0\nrepo: backend\ncovers: [D1]\n---\n\nstep\n",
        encoding="utf-8",
    )
    (qa / "cases" / "mod" / "case-02.md").write_text(
        "---\nid: case-02\ntitle: t2\npriority: P1\nrepo: backend\ncovers: [D1]\n---\n\nstep2\n",
        encoding="utf-8",
    )
    run = qa / "evidence" / "2026-09-16-153000"
    (run / "case-01" / "screenshots").mkdir(parents=True)
    (run / "case-02").mkdir(parents=True)
    (run / "result.yaml").write_text(
        "run_id: 2026-09-16-153000\nenv: local\n"
        "summary: {total: 2, passed: 1, failed: 1, blocked: 0, skipped: 0}\n"
        "workers: [{id: a, model: grok-4, concurrency: 1, priority: 1}]\n"
        "cases: [{case: case-01, status: failed, repo: backend, model: grok-4, reason: x}, "
        "{case: case-02, status: passed, repo: backend, model: grok-4, reason: y}]\n",
        encoding="utf-8",
    )
    (run / "case-01" / "result.yaml").write_text(
        "case: case-01\ntitle: t\nrepo: backend\nstatus: failed\nreason: x\n"
        "failure: {step: 1, step_desc: boom, evidence: screenshots/step-01.png}\n"
        "assertions:\n"
        "  - {type: ui, expected: e1, actual: a1, status: passed}\n"
        "  - {type: net, expected: e2, actual: a2, status: failed}\n",
        encoding="utf-8",
    )
    (run / "case-02" / "result.yaml").write_text(
        "case: case-02\ntitle: t2\nrepo: backend\nstatus: passed\nreason: y\n",
        encoding="utf-8",
    )
    (run / "case-01" / "screenshots" / "step-01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    if progress:
        (run / "progress.yaml").write_text(
            "run_id: 2026-09-16-153000\nenv: local\n"
            "pools: [{id: a, model: grok-4, concurrency: 1, priority: 1, inflight: 1}]\n"
            "cases:\n  - id: case-01\n    title: t\n    state: running\n    repo: backend\n"
            "    model: grok-4\n    depends_on: []\n"
            "  - id: case-02\n    title: t2\n    state: pending\n    repo: backend\n"
            "    model: grok-4\n    depends_on: []\n",
            encoding="utf-8",
        )


def test_qa_board_keeps_file_pass_while_sibling_reruns(
    tmp_path: Path, git_src: Path, monkeypatch
):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    run = yard / "reqs" / "QA-W1" / "qa" / "evidence" / "2026-09-16-153000"
    (run / "case-01" / "result.yaml").write_text(
        "case: case-01\nstatus: passed\nreason: 三层一致\n"
        "assertions:\n  - {type: db, expected: 'col = 0', actual: '0', status: passed}\n",
        encoding="utf-8",
    )
    (run / "progress.yaml").write_text(
        "run_id: 2026-09-16-153000\nenv: local\n"
        "cases:\n"
        "  - {id: case-01, state: failed, reason: 三层一致, repo: backend, model: grok-4}\n"
        "  - {id: case-02, state: running, reason: '', repo: backend, model: grok-4}\n",
        encoding="utf-8",
    )
    detail = _client(yard).get("/api/requirements/QA-W1").json()
    states = {c["id"]: c["state"] for c in detail["qa"]["cases"]}
    assert states["case-01"] == "passed"
    assert states["case-02"] == "running"
    # The requirement cards overlay qa.progress, not qa.cases, while a sibling runs.
    progress = {c["id"]: c["state"] for c in detail["qa"]["progress"]["cases"]}
    assert progress["case-01"] == "passed"
    assert progress["case-02"] == "running"
    case = _client(yard).get("/api/requirements/QA-W1/qa/cases/case-01").json()
    assert case["latest_run"]["state"] == "passed"
    assert case["live"]["state"] == "passed"


def test_qa_api_empty_is_200(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    r = _client(yard).get("/api/requirements/QA-W1/qa")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"] is None
    assert body["cases"] == []
    assert body["runs"] == []


def test_qa_api_has_cases_and_run(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    client = _client(yard)
    page = client.get("/api/requirements/QA-W1/qa").json()
    assert page["meta"]["changes"][0]["id"] == "D1"
    assert page["cases"][0]["id"] == "case-01"
    assert "step" in page["cases"][0]["html"]
    assert page["runs"][0]["summary"]["failed"] == 1
    assert page["runs"][0]["cases"][0]["screenshots"] == ["step-01.png"]
    assert page["runs"][0]["cases"][0]["assertions"][1]["status"] == "failed"
    assert page["runs"][0]["cases"][0]["assertions"][1]["type"] == "net"
    detail = client.get("/api/requirements/QA-W1").json()
    assert detail["qa"]["has_cases"] is True
    assert detail["qa"]["has_meta"] is True
    assert detail["qa"]["latest_run"]["run_id"] == "2026-09-16-153000"
    assert detail["qa"]["progress"]["cases"][0]["state"] == "running"
    assert detail["qa"]["progress"]["pools"][0]["inflight"] == 1
    assert detail["qa"]["incomplete_run"]["run_id"] == "2026-09-16-153000"
    assert len(detail["qa"]["cases"]) == 2
    assert detail["qa"]["cases"][0]["id"] == "case-01"
    assert detail["qa"]["cases"][0]["priority"] == "P0"
    assert detail["qa"]["cases"][0]["repo"] == "backend"
    assert detail["qa"]["cases"][0]["covers"] == ["D1"]
    assert detail["qa"]["cases"][0]["state"] == "running"
    assert detail["qa"]["cases"][0]["model"] == "grok-4"
    assert detail["qa"]["cases"][0]["run_id"] == "2026-09-16-153000"

    png = client.get(
        "/r/QA-W1/qa/evidence/2026-09-16-153000/case-01/screenshots/step-01.png"
    )
    assert png.status_code == 200
    assert png.headers["content-type"].startswith("image/")

    assert (
        client.get("/r/QA-W1/qa/evidence/2026-09-16-153000/case-01/screenshots/../STATUS.yaml").status_code
        == 404
    )
    assert (
        client.get("/r/QA-W1/qa/evidence/2026-09-16-153000/case-01/screenshots/noext").status_code
        == 404
    )
    assert client.get("/r/QA-W1/qa/evidence/../QA-W1/STATUS.yaml").status_code == 404

    assert client.post("/api/requirements/QA-W1/qa").status_code in {405, 404}
    assert "qa" not in DOC_FILES.values()
    assert "qa" not in DOC_FILES


def test_qa_review_exposes_feedback_html_and_verify_details(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard.qa_review import cases_fingerprint, reject_cases
    from dev_yard.qa_verify import VerifyResult, write_summary

    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    qa = yard / "reqs" / "QA-W1" / "qa"
    verify_sql = "SELECT id FROM t WHERE id=1"
    write_summary(
        yard,
        "QA-W1",
        {
            "case-01": VerifyResult(
                case="case-01",
                status="failed",
                verify_sql=verify_sql,
                rows=0,
                error="0 rows",
            ),
            "case-02": VerifyResult(case="case-02", status="passed"),
        },
        cases_fingerprint(qa),
    )
    reject_cases(qa, f"## case-01\n- verify.sql: `{verify_sql}`\n- 实际行数: 0")

    review = _client(yard).get("/api/requirements/QA-W1/qa").json()["review"]
    assert review["status"] == "rejected"
    # The card renders markdown, not the raw agent-facing text.
    assert "<h2>case-01</h2>" in review["feedback_html"]
    verify = review["verify"]
    assert verify["present"] is True and verify["stale"] is False
    assert verify["failed"] == ["case-01"]
    detail = verify["details"][0]
    assert detail["case"] == "case-01"
    assert detail["verify_sql"] == verify_sql
    assert detail["error"] == "0 rows"


def test_qa_detail_case_carries_assertions(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    detail = _client(yard).get("/api/requirements/QA-W1").json()
    row = detail["qa"]["cases"][0]
    assert row["state"] == "failed"
    assert row["failure"]["step_desc"] == "boom"
    assert len(row["assertions"]) == 2
    assert row["assertions"][0]["expected"] == "e1"
    assert row["assertions"][0]["actual"] == "a1"
    assert row["assertions"][0]["type"] == "ui"
    assert detail["qa"]["cases"][1]["assertions"] == []


def test_qa_case_endpoint(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    client = _client(yard)

    r = client.get("/api/requirements/QA-W1/qa/cases/case-01")
    assert r.status_code == 200
    body = r.json()
    assert body["jira"] == "QA-W1"
    assert body["case"]["id"] == "case-01"
    assert body["case"]["priority"] == "P0"
    assert body["case"]["covers"] == ["D1"]
    assert "step" in body["html"]
    assert "body" not in body
    # Live run wins: running state, stale failure/assertions hidden.
    assert body["live"]["state"] == "running"
    assert body["latest_run"]["run_id"] == "2026-09-16-153000"
    assert body["latest_run"]["state"] == "running"
    assert body["latest_run"]["failure"] is None
    assert body["latest_run"]["assertions"] == []

    # case-02 waits in the live map too: pending state, stale row hidden.
    r2 = client.get("/api/requirements/QA-W1/qa/cases/case-02")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["live"]["state"] == "pending"
    assert body2["latest_run"]["state"] == "pending"
    assert body2["latest_run"]["assertions"] == []

    # Terminal run (progress gone): rows show as-is, assertions included.
    (yard / "reqs" / "QA-W1" / "qa" / "evidence" / "2026-09-16-153000" / "progress.yaml").unlink()
    done1 = client.get("/api/requirements/QA-W1/qa/cases/case-01").json()
    assert done1["live"] is None
    assert done1["latest_run"]["state"] == "failed"
    assert done1["latest_run"]["failure"]["step_desc"] == "boom"
    assert done1["latest_run"]["assertions"][1]["status"] == "failed"
    assert done1["latest_run"]["screenshots"] == ["step-01.png"]
    done2 = client.get("/api/requirements/QA-W1/qa/cases/case-02").json()
    assert done2["live"] is None
    assert done2["latest_run"]["state"] == "passed"
    assert done2["latest_run"]["assertions"] == []

    assert client.get("/api/requirements/QA-W1/qa/cases/case-99").status_code == 404
    assert client.get("/api/requirements/QA-W1/qa/cases/..%2F..%2FSTATUS.yaml").status_code == 404
    assert client.get("/api/requirements/QA-W1/qa/cases/../meta").status_code == 404
    assert client.get("/api/requirements/NOPE-9/qa/cases/case-01").status_code == 404


def test_qa_rerun_requires_case_ids(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    r = _client(yard).post("/api/requirements/QA-W1/qa/rerun", json={"case_ids": []})
    assert r.status_code == 400
    assert "case_ids" in r.json()["detail"]


def test_qa_rerun_unknown_jira_404(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    r = _client(yard).post(
        "/api/requirements/NOPE-1/qa/rerun", json={"case_ids": ["case-01"]}
    )
    assert r.status_code in {400, 404}


def test_qa_rerun_submits_job(tmp_path: Path, git_src: Path, monkeypatch):
    """The endpoint fans out into a qa-run job."""
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    client = _client(yard)
    r = client.post(
        "/api/requirements/QA-W1/qa/rerun",
        json={"case_ids": ["case-01"], "env": "local"},
    )
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["action"] == "qa-run"
    assert jobs[0]["jira"] == "QA-W1"


def _wait_state(job, state: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while job.state != state and time.time() < deadline:
        time.sleep(0.01)
    assert job.state == state


def test_qa_case_bug_files_ticket(tmp_path: Path, git_src: Path, monkeypatch):
    """一键下 bug: a failed case opens one B ticket and records the finding."""
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    client = _client(yard)
    r = client.post("/api/requirements/QA-W1/qa/cases/case-01/bug")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket_id"] == "B1"
    assert body["repo"] == "backend"
    tickets = (yard / "reqs" / "QA-W1" / "TICKETS.md").read_text(encoding="utf-8")
    assert "## B1" in tickets
    assert "- source: test" in tickets
    # Re-clicking is idempotent: same ticket, no duplicate block.
    again = client.post("/api/requirements/QA-W1/qa/cases/case-01/bug")
    assert again.status_code == 200, again.text
    assert again.json()["ticket_id"] == "B1"
    tickets = (yard / "reqs" / "QA-W1" / "TICKETS.md").read_text(encoding="utf-8")
    assert tickets.count("## B1:") == 1


def test_qa_case_bug_files_only_the_clicked_case(
    tmp_path: Path, git_src: Path, monkeypatch
):
    """Regression: an ingested report has many findings; one click files one.

    A run records every failed case as a finding (spawn=False). The manual 下 bug
    must scope to the picked case — not drain the whole stored report — and must
    return that case's ticket, not the first in creation order.
    """
    from dev_yard import status as st
    from dev_yard.test_report import accept_test_report, parse_inbound

    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    # Both cases failed on disk so either is eligible for 下 bug.
    run_dir = yard / "reqs" / "QA-W1" / "qa" / "evidence" / "2026-09-16-153000"
    (run_dir / "case-02" / "result.yaml").write_text(
        "case: case-02\ntitle: t2\nrepo: backend\nstatus: failed\nreason: boom\n",
        encoding="utf-8",
    )
    data = st.load(yard, "QA-W1")
    data["contract_review"] = "passed"
    data["phase"] = "testing"
    data["repos"] = ["backend"]
    st.save(yard, "QA-W1", data)
    accept_test_report(
        yard,
        "QA-W1",
        parse_inbound(
            {
                "verdict": "failed",
                "body": "run",
                "findings": [
                    {"id": "case-01", "title": "t", "repo": "backend"},
                    {"id": "case-02", "title": "t2", "repo": "backend"},
                ],
            },
            "api",
        ),
        spawn=False,
    )
    tickets_before = (
        (yard / "reqs" / "QA-W1" / "TICKETS.md").read_text(encoding="utf-8")
        if (yard / "reqs" / "QA-W1" / "TICKETS.md").is_file()
        else ""
    )
    assert "## B" not in tickets_before

    resp = _client(yard).post("/api/requirements/QA-W1/qa/cases/case-02/bug")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    tickets = (yard / "reqs" / "QA-W1" / "TICKETS.md").read_text(encoding="utf-8")
    assert tickets.count("## B") == 1
    assert body["ticket_id"] == "B1"
    # The ticket cites the clicked case, not case-01 (the first stored finding).
    assert "manual-case-02:case-02" in tickets
    assert "case-01" not in tickets


def test_qa_case_bug_rejects_passed_case(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    r = _client(yard).post("/api/requirements/QA-W1/qa/cases/case-02/bug")
    assert r.status_code == 400
    assert "only failed cases" in r.json()["detail"]


def test_qa_case_bug_unknown_case_is_400(tmp_path: Path, git_src: Path, monkeypatch):
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard, progress=False)
    r = _client(yard).post("/api/requirements/QA-W1/qa/cases/case-99/bug")
    assert r.status_code == 400
    assert "no run result" in r.json()["detail"]


def test_qa_active_jobs_hold_review_gate(tmp_path: Path, git_src: Path, monkeypatch):
    """An in-flight design/run job is surfaced so the UI keeps the gate closed."""
    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(5)

    runner = JobRunner(yard, execute=execute, sync=False)
    client = TestClient(create_app(yard, job_runner=runner))
    try:
        job = runner.submit("qa-run", "QA-W1")
        _wait_state(job, "running")

        detail = client.get("/api/requirements/QA-W1").json()
        assert [j["id"] for j in detail["qa"]["active_jobs"]] == [job.id]

        qa_page = client.get("/api/requirements/QA-W1/qa").json()
        assert qa_page["active_jobs"][0]["action"] == "qa-run"

        gate.set()
        _wait_state(job, "ok")
        assert client.get("/api/requirements/QA-W1/qa").json()["active_jobs"] == []
    finally:
        gate.set()


def test_qa_page_payload_carries_phase_and_triage(
    tmp_path: Path, git_src: Path, monkeypatch
):
    from dev_yard import qa_state as qa_st

    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    qa_st.record_triage(yard, "QA-W1", ["case-01"], ["case-02"])
    client = _client(yard)
    page = client.get("/api/requirements/QA-W1/qa").json()
    assert page["triage"]["pending"] == ["case-01"]
    assert page["triage"]["auto_recycled"] == ["case-02"]
    assert page["phase"]  # derived from the seeded running progress
    assert page["next"]
    detail = client.get("/api/requirements/QA-W1").json()
    assert detail["qa"]["triage"]["pending"] == ["case-01"]
    assert detail["qa"]["next"]


def test_qa_triage_endpoint_files_pending(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard import qa_state as qa_st

    yard = _req(tmp_path, git_src, monkeypatch)
    _seed_qa(yard)
    run = yard / "reqs" / "QA-W1" / "qa" / "evidence" / "2026-09-16-153000"
    (run / "case-01" / "result.yaml").write_text(
        "case: case-01\ntitle: t\nrepo: backend\nstatus: failed\nreason: x\n"
        "defect_class: product\n"
        "failure: {step: 1, step_desc: boom, evidence: screenshots/step-01.png}\n"
        "assertions:\n  - {type: ui, expected: e1, actual: a1, status: failed}\n",
        encoding="utf-8",
    )
    qa_st.record_triage(yard, "QA-W1", ["case-01"], [])
    client = _client(yard)
    out = client.post("/api/requirements/QA-W1/qa/triage").json()
    assert list(out["filed"]) == ["case-01"]
    assert out["skipped"] == []
    assert qa_st.triage(yard, "QA-W1")["filed"]["case-01"]
