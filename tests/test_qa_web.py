from pathlib import Path

from fastapi.testclient import TestClient

from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.app import create_app
from dev_yard.web.board import DOC_FILES


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
    run = qa / "evidence" / "2026-09-16-153000"
    (run / "case-01" / "screenshots").mkdir(parents=True)
    (run / "result.yaml").write_text(
        "run_id: 2026-09-16-153000\nenv: local\n"
        "summary: {total: 1, passed: 0, failed: 1, blocked: 0, skipped: 0}\n"
        "workers: [{id: a, model: grok-4, concurrency: 1, priority: 1}]\n"
        "cases: [{case: case-01, status: failed, repo: backend, model: grok-4, reason: x}]\n",
        encoding="utf-8",
    )
    (run / "case-01" / "result.yaml").write_text(
        "case: case-01\ntitle: t\nrepo: backend\nstatus: failed\nreason: x\n"
        "failure: {step: 1, step_desc: boom, evidence: screenshots/step-01.png}\n",
        encoding="utf-8",
    )
    (run / "case-01" / "screenshots" / "step-01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "progress.yaml").write_text(
        "run_id: 2026-09-16-153000\nenv: local\n"
        "pools: [{id: a, model: grok-4, concurrency: 1, priority: 1, inflight: 1}]\n"
        "cases:\n  - id: case-01\n    title: t\n    state: running\n    repo: backend\n"
        "    model: grok-4\n    depends_on: []\n",
        encoding="utf-8",
    )
    client = _client(yard)
    page = client.get("/api/requirements/QA-W1/qa").json()
    assert page["meta"]["changes"][0]["id"] == "D1"
    assert page["cases"][0]["id"] == "case-01"
    assert "step" in page["cases"][0]["html"]
    assert page["runs"][0]["summary"]["failed"] == 1
    assert page["runs"][0]["cases"][0]["screenshots"] == ["step-01.png"]
    detail = client.get("/api/requirements/QA-W1").json()
    assert detail["qa"]["has_cases"] is True
    assert detail["qa"]["has_meta"] is True
    assert detail["qa"]["latest_run"]["run_id"] == "2026-09-16-153000"
    assert detail["qa"]["progress"]["cases"][0]["state"] == "running"
    assert detail["qa"]["progress"]["pools"][0]["inflight"] == 1

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
