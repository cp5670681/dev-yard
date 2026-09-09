import threading
from pathlib import Path

from fastapi.testclient import TestClient

from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.app import check_bind_host, create_app, render_markdown
from dev_yard.web.board import asset_file
from dev_yard.web.jobs import JobRunner


def _client(yard: Path, **kwargs) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True, **kwargs))


def test_open_form_defaults_to_pi(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    page = _client(yard).get("/open")
    assert page.status_code == 200
    assert 'value="pi"' in page.text
    assert "Claude" not in page.text
    assert "mcp-atlassian-pro" in page.text


def test_dashboard_lists_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-30", source="none")
    r = _client(yard).get("/")
    assert r.status_code == 200
    assert "AB-30" in r.text
    assert "打开需求" in r.text


def test_dashboard_skips_shared_docs(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-30", source="none")
    adr = yard / "reqs" / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001.md").write_text("# adr\n")
    client = _client(yard)
    r = client.get("/")
    assert r.status_code == 200
    assert "AB-30" in r.text
    assert 'href="/r/docs"' not in r.text
    assert client.get("/r/docs").status_code == 404
    opened = client.post("/open", data={"jira": "docs", "source": "none"}, follow_redirects=False)
    assert opened.status_code == 303
    assert "reserved" in opened.headers["location"]


def test_board_reopen_has_no_hidden_force(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-36", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    from dev_yard.service import req_freeze

    req_freeze(yard, "AB-36")
    client = _client(yard)
    page = client.get("/r/AB-36")
    assert 'type="hidden" name="force"' not in page.text
    assert "重置阶段" in page.text
    r = client.post("/r/AB-36/actions/open", follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].split("job=")[-1]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "error"
    assert "phase=frozen" in job["log"]
    from dev_yard import status as st

    assert st.load(yard, "AB-36")["phase"] == "frozen"


def test_requirement_page_and_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-31", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: api\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    client = _client(yard)
    html = client.get("/r/AB-31")
    assert html.status_code == 200
    assert "T1" in html.text
    assert "冻结" in html.text
    data = client.get("/api/requirements/AB-31").json()
    assert data["jira"] == "AB-31"
    assert data["tickets"][0]["id"] == "T1"
    assert data["next"] == "freeze"


def test_freeze_from_web(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-32", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    client = _client(yard)
    r = client.post("/r/AB-32/actions/freeze", follow_redirects=False)
    assert r.status_code == 303
    assert (d / "worktrees" / "backend").exists()
    page = client.get("/r/AB-32")
    assert "implement" in page.text.lower() or "实现" in page.text


def test_web_implement_runs_ready_tickets_in_parallel(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-80", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    from dev_yard.service import req_freeze

    req_freeze(yard, "AB-80")
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    client = TestClient(create_app(yard, job_runner=runner))
    first = client.post(
        "/r/AB-80/actions/implement",
        data={"ticket_id": "T1"},
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = client.post(
        "/r/AB-80/actions/implement",
        data={"ticket_id": "T2"},
        follow_redirects=False,
    )
    assert second.status_code == 303
    listed = client.get("/api/jobs").json()
    assert len(listed) == 2
    assert {tuple(row.get("ticket_ids") or []) for row in listed} == {("T1",), ("T2",)}
    again = client.post(
        "/r/AB-80/actions/implement",
        data={"ticket_id": "T1"},
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert "already" in again.headers["location"]
    page = client.get("/r/AB-80")
    assert page.text.count("data-job-id=") == 2
    from dev_yard import status as st

    assert st.load(yard, "AB-80")["tickets"]["T1"]["state"] == "implementing"
    assert st.load(yard, "AB-80")["tickets"]["T2"]["state"] == "implementing"
    assert 'data-ticket="T1"' in page.text
    impl_col = page.text.split('data-col="implementing"')[1].split("data-col=")[0]
    assert 'data-ticket="T1"' in impl_col
    assert 'data-ticket="T2"' in impl_col
    gate.set()
    for job in runner.running():
        job.done.wait(timeout=5)


def test_web_implement_ready_fans_out_remaining_tickets(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-81", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    from dev_yard.service import req_freeze

    req_freeze(yard, "AB-81")
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    client = TestClient(create_app(yard, job_runner=runner))
    client.post(
        "/r/AB-81/actions/implement",
        data={"ticket_id": "T1"},
        follow_redirects=False,
    )
    bulk = client.post("/r/AB-81/actions/implement", follow_redirects=False)
    assert bulk.status_code == 303
    listed = client.get("/api/jobs").json()
    assert len(listed) == 2
    assert {tuple(row.get("ticket_ids") or []) for row in listed} == {("T1",), ("T2",)}
    gate.set()
    for job in runner.running():
        job.done.wait(timeout=5)


def test_save_doc_and_markdown_assets(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-33", source="none")
    (d / "assets").mkdir()
    (d / "assets" / "ui.png").write_bytes(b"\x89PNG\r\n")
    client = _client(yard)
    r = client.post(
        "/r/AB-33/docs/grill",
        data={"body": "# Grill — AB-33\n\nSee assets/ui.png\n\n![ui](assets/ui.png)\n"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get("/r/AB-33/docs/grill")
    assert page.status_code == 200
    assert "/r/AB-33/assets/ui.png" in page.text
    img = client.get("/r/AB-33/assets/ui.png")
    assert img.status_code == 200
    assert img.content.startswith(b"\x89PNG")


def test_asset_escape(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-34", source="none")
    (d / "assets").mkdir()
    (d / "assets" / "ok.png").write_bytes(b"x")
    assert asset_file(yard, "AB-34", "ok.png").name == "ok.png"
    import pytest

    with pytest.raises((ValueError, FileNotFoundError)):
        asset_file(yard, "AB-34", "../REQUIREMENT.md")
    client = _client(yard)
    assert client.get("/r/AB-34/assets/../REQUIREMENT.md").status_code in {404, 422}


def test_unknown_requirement_404(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    r = _client(yard).get("/r/NO-1")
    assert r.status_code == 404


def test_finished_job_does_not_poll_on_ok_page(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/open",
        data={"jira": "AB-40", "source": "none"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    job_id = r.headers["location"].split("job=")[-1]
    ok_page = client.get("/r/AB-40?ok=1")
    assert ok_page.status_code == 200
    assert 'data-job="' not in ok_page.text
    assert "data-job-log" not in ok_page.text
    plain = client.get("/r/AB-40")
    assert 'data-job="' not in plain.text
    job_page = client.get(f"/r/AB-40?job={job_id}")
    assert "data-job-log" in job_page.text
    assert 'data-job="' not in job_page.text


def test_running_job_is_bound_without_query(tmp_path: Path):
    import threading

    yard = tmp_path / "yard"
    init_yard(yard)
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        job.append("working")
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    job = runner.submit("open", "AB-41")
    page = TestClient(create_app(yard, job_runner=runner)).get("/r/AB-41")
    assert page.status_code == 200
    assert f'data-job="{job.id}"' in page.text
    gate.set()
    job.done.wait(timeout=5)


def _parse_sse(body: str) -> list[tuple[str, object]]:
    import json

    events: list[tuple[str, object]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event = "message"
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


def test_job_events_sse_for_finished_job(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)

    def execute(root: Path, job) -> None:
        job.append("hello from sse")

    runner = JobRunner(yard, execute=execute, sync=True)
    job = runner.submit("open", "AB-60")
    client = TestClient(create_app(yard, job_runner=runner))
    r = client.get(f"/api/jobs/{job.id}/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    names = [name for name, _ in events]
    assert names[0] == "snapshot"
    assert names[-1] == "done"
    assert events[0][1]["log"] == "hello from sse\n"
    assert events[-1][1]["state"] == "ok"
    snap = client.get(f"/api/jobs/{job.id}").json()
    assert "seq" not in snap


def test_job_events_unknown_404(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    r = _client(yard).get("/api/jobs/nope/events")
    assert r.status_code == 404


def test_app_js_uses_event_source():
    from dev_yard.web.app import HERE

    js = (HERE / "static" / "app.js").read_text()
    css = (HERE / "static" / "app.css").read_text()
    assert "EventSource" in js
    assert "/api/jobs/events" in js
    assert "/api/requirements/" in js
    assert "data-board" in js
    assert "nav-dot" in js
    assert "setTimeout(tick, 1000)" not in js
    assert "setInterval" not in js
    assert "/pi/" in js
    assert "data-open-pi" in js
    assert "pi-drawer" in css
    assert ".nav-jobs { display: none; }" not in css


def test_pi_chat_api_streams_session_and_rejects_outside_cwd(tmp_path: Path, monkeypatch):
    import json

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("YARD_PI_SESSIONS", str(sessions))

    def execute(root: Path, job) -> None:
        job.record_pi_run(root)
        job.append("pi done")

    runner = JobRunner(yard, execute=execute, sync=True)
    job = runner.submit("spec", "AB-90")
    path = sessions / "d" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 3,
                "id": "sid-90",
                "timestamp": "2099-01-01T00:00:00.000Z",
                "cwd": str(yard.resolve()),
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "c1",
                            "name": "read",
                            "arguments": {"path": "REQUIREMENT.md"},
                        }
                    ],
                },
            }
        )
        + "\n"
    )
    client = TestClient(create_app(yard, job_runner=runner))
    snap = client.get(f"/api/jobs/{job.id}").json()
    assert snap["pi_runs"][0]["cwd"] == str(yard.resolve())
    page = client.get(f"/r/AB-90?job={job.id}")
    assert "查看对话" in page.text
    assert "data-open-pi" in page.text
    data = client.get(f"/api/jobs/{job.id}/pi/0").json()
    assert data["found"] is True
    assert data["session_id"] == "sid-90"
    assert data["entries"][0]["role"] == "assistant"
    assert data["entries"][0]["tools"][0]["name"] == "read"
    events = client.get(f"/api/jobs/{job.id}/pi/0/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    parsed = _parse_sse(events.text)
    assert parsed[0][0] == "snapshot"
    assert parsed[-1][0] == "done"
    assert any(name == "entry" for name, _ in parsed)
    assert client.get(f"/api/jobs/{job.id}/pi/9").status_code == 404
    job.record_pi_run(tmp_path / "elsewhere")
    denied = client.get(f"/api/jobs/{job.id}/pi/1")
    assert denied.status_code == 404


def test_waiting_job_is_bound_and_answers_api(tmp_path: Path):
    import time

    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-51", source="none")
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        job.set_waiting(
            {
                "done": False,
                "round": 1,
                "intro": "范围",
                "questions": [
                    {
                        "id": "Q1",
                        "title": "范围",
                        "body": "只做这张票？",
                        "options": [{"id": "A", "label": "只这张票"}],
                        "suggested": "A",
                        "suggested_text": "只这张票",
                    }
                ],
            }
        )
        job.wait_answers()
        gate.set()

    runner = JobRunner(yard, execute=execute, sync=False)
    job = runner.submit("grill", "AB-51")
    deadline = time.time() + 5
    while job.state != "waiting" and time.time() < deadline:
        time.sleep(0.05)
    client = TestClient(create_app(yard, job_runner=runner))
    page = client.get("/r/AB-51")
    assert f'data-job="{job.id}"' in page.text
    assert "data-grill-form" in page.text
    bad = client.post(f"/api/jobs/{job.id}/answers", json={"answers": "nope"})
    assert bad.status_code == 422
    snap = client.get(f"/api/jobs/{job.id}").json()
    assert snap["state"] == "waiting"
    assert snap["grill"]["questions"][0]["id"] == "Q1"
    ok = client.post(
        f"/api/jobs/{job.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A", "text": ""}]},
    )
    assert ok.status_code == 200
    assert job.done.wait(timeout=5)
    assert gate.is_set()
    idle = client.post(
        f"/api/jobs/{job.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A"}]},
    )
    assert idle.status_code == 400


def test_api_jobs_lists_waiting_with_sidebar_dot(tmp_path: Path):
    import time

    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-71", source="none")
    req_open(yard, "AB-72", source="none")

    def execute(root: Path, job) -> None:
        job.set_waiting(
            {
                "done": False,
                "round": 1,
                "questions": [{"id": "Q1", "title": "范围", "options": []}],
            }
        )
        job.wait_answers()

    runner = JobRunner(yard, execute=execute, sync=False)
    job = runner.submit("grill", "AB-71")
    other = runner.submit("spec", "AB-72")
    deadline = time.time() + 5
    while (job.state != "waiting" or other.state != "waiting") and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "waiting" and other.state == "waiting"
    client = TestClient(create_app(yard, job_runner=runner))
    listed = client.get("/api/jobs").json()
    assert {row["jira"]: row["state"] for row in listed} == {
        "AB-71": "waiting",
        "AB-72": "waiting",
    }
    home = client.get("/")
    assert home.status_code == 200
    assert home.text.count('class="nav-dot"') == 2
    assert "/r/AB-71" in home.text
    assert "/r/AB-72" in home.text
    assert any(getattr(r, "path", None) == "/api/jobs/events" for r in client.app.routes)
    client.post(
        f"/api/jobs/{job.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A", "text": ""}]},
    )
    assert job.done.wait(timeout=5)
    left = client.get("/api/jobs").json()
    assert [row["jira"] for row in left] == ["AB-72"]
    assert left[0]["state"] == "waiting"
    half = client.get("/")
    assert half.text.count('class="nav-dot"') == 1
    assert "AB-72" in half.text
    client.post(
        f"/api/jobs/{other.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A", "text": ""}]},
    )
    assert other.done.wait(timeout=5)
    assert client.get("/api/jobs").json() == []
    gone = client.get("/")
    assert "nav-dot" not in gone.text
    assert "is-waiting" not in gone.text


def test_grill_action_uses_injected_execute(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-35", source="none")

    def execute(root: Path, job) -> None:
        job.append("web-grill")

    app = create_app(yard, job_runner=JobRunner(yard, execute=execute, sync=True))
    client = TestClient(app)
    r = client.post("/r/AB-35/actions/grill", follow_redirects=True)
    assert r.status_code == 200
    assert "web-grill" in r.text


def test_create_app_resumes_pending_grill(tmp_path: Path, monkeypatch):
    import json
    import time

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-53", source="none")
    (d / "GRILL.md").write_text("# Grill — AB-53\n\n## Round 1 — answers\n\n- **Q1**：选 A\n")
    (d / ".grill-round.json").write_text(
        json.dumps(
            {
                "done": False,
                "round": 2,
                "questions": [{"id": "Q1", "title": "范围", "options": [{"id": "A", "label": "只这张票"}]}],
            }
        )
    )

    def fake_launch(*args, **kwargs):
        raise AssertionError("pi should not start on resume")

    monkeypatch.setattr("dev_yard.web.jobs.service.launch_skill", fake_launch)
    client = TestClient(create_app(yard))
    deadline = time.time() + 5
    items = []
    while time.time() < deadline:
        items = client.get("/api/jobs").json()
        if items and items[0].get("state") == "waiting":
            break
        time.sleep(0.05)
    assert items and items[0]["action"] == "grill"
    assert items[0]["jira"] == "AB-53"
    assert items[0]["state"] == "waiting"
    page = client.get("/r/AB-53")
    assert f'data-job="{items[0]["id"]}"' in page.text
    assert client.get("/api/requirements/AB-53").json()["next"] == "grill"


def test_render_markdown_rewrites_assets():
    html = render_markdown("![x](assets/a.png)", "PG-1")
    assert "/r/PG-1/assets/a.png" in html


def test_render_markdown_strips_raw_html():
    html = render_markdown(
        'hello <script>alert(1)</script> <img src="javascript:alert(1)" alt="x">',
        "PG-1",
    )
    assert "<script" not in html.lower()
    assert "javascript:" not in html.lower()
    assert "alert(1)" not in html


def test_check_bind_host_refuses_non_loopback():
    import pytest

    check_bind_host("127.0.0.1")
    check_bind_host("0.0.0.0", allow_remote=True)
    with pytest.raises(ValueError, match="allow-remote"):
        check_bind_host("0.0.0.0")


def test_cli_web_help():
    from typer.testing import CliRunner

    from dev_yard.cli import app

    result = CliRunner().invoke(app, ["web", "--help"])
    assert result.exit_code == 0
    assert "8765" in result.stdout


def test_repo_form_role_is_free_text(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    page = client.get("/repos")
    assert page.status_code == 200
    assert '<input name="role"' in page.text
    assert "<select name=\"role\">" not in page.text
    r = client.post(
        "/repos",
        data={
            "alias": "app",
            "url": str(git_src),
            "default_base": "main",
            "role": "mobile",
            "path": str(git_src),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    listed = client.get("/api/repos").json()
    assert listed == [
        {
            "alias": "app",
            "url": str(git_src),
            "default_base": "main",
            "role": "mobile",
            "path": str(git_src),
        }
    ]


def test_repo_form_blank_alias_uses_git_project_name(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    page = client.get("/repos")
    assert 'name="alias" required' not in page.text
    r = client.post(
        "/repos",
        data={
            "alias": "",
            "url": f"git@host:leads-in/{git_src.name}.git",
            "default_base": "main",
            "role": "svc",
            "path": str(git_src),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    listed = client.get("/api/repos").json()
    assert listed[0]["alias"] == git_src.name


def test_repo_add_job_shows_clone_progress(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/repos",
        data={
            "alias": "backend",
            "url": str(git_src),
            "default_base": "main",
            "role": "svc",
            "path": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "job=" in r.headers.get("location", "")
    job_id = r.headers["location"].split("job=")[-1]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "ok"
    assert "clone" in job["log"].lower()
    page = client.get(f"/repos?job={job_id}")
    assert "data-job-log" in page.text
    assert (yard / ".repos" / "backend" / ".git").exists()
