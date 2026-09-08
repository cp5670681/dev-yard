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
