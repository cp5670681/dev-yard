import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard.qa_config import MASK, load_qa_config
from dev_yard.service import init_yard, repo_add, req_open
from dev_yard.web.app import check_bind_host, create_app, render_markdown
from dev_yard.web.board import asset_file
from dev_yard.web.jobs import JobRunner

SPA_INDEX = Path(__file__).resolve().parents[1] / "src" / "dev_yard" / "web" / "spa" / "index.html"
needs_spa = pytest.mark.skipif(
    not SPA_INDEX.is_file(),
    reason="frontend not built; run: pnpm --dir web build",
)


def _client(yard: Path, **kwargs) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True, **kwargs))


def _assert_spa_shell(resp) -> None:
    if not SPA_INDEX.is_file():
        return
    assert resp.status_code == 200
    assert 'id="app"' in resp.text


def _app_paths(app) -> set[str]:
    """All route paths, descending into lazily-included routers."""
    paths: set[str] = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        inner = getattr(route, "original_router", None)
        if inner is not None:
            stack.extend(inner.routes)
    return paths


def test_open_form_defaults_to_pi(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    page = _client(yard).get("/open")
    _assert_spa_shell(page)
    text = (Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "OpenView.vue").read_text()
    assert 'actualSource = "pi"' in text
    assert "Claude" not in text
    assert "query: job ? { job } : {}" in text


def test_job_cancel_endpoint(tmp_path: Path):
    import time

    from dev_yard.web.jobs import JobCancelled

    yard = tmp_path / "yard"
    init_yard(yard)

    def execute(root: Path, job) -> None:
        while not job.cancel_requested.is_set():
            time.sleep(0.01)
        raise JobCancelled("implement cancelled (pi exit -9)")

    runner = JobRunner(yard, execute=execute, sync=False)
    client = _client(yard, job_runner=runner)
    assert client.post("/api/jobs/nope/cancel").status_code == 404

    job = runner.submit("implement", "AB-1", ticket_ids=["T1"])
    r = client.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert job.done.wait(timeout=2)
    assert job.state == "cancelled"
    again = client.post(f"/api/jobs/{job.id}/cancel")
    assert again.status_code == 200
    assert again.json()["state"] == "cancelled"


def test_dashboard_lists_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-30", source="none")
    client = _client(yard)
    r = client.get("/")
    _assert_spa_shell(r)
    listed = client.get("/api/requirements").json()
    assert listed[0]["jira"] == "AB-30"
    assert listed[0]["title"] is None


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
    _assert_spa_shell(r)
    listed = client.get("/api/requirements").json()
    assert [row["jira"] for row in listed] == ["AB-30"]
    assert client.get("/r/docs").status_code == 404
    assert client.get("/api/requirements/docs").status_code == 404
    opened = client.post("/api/open", json={"jira": "docs", "source": "none"})
    assert opened.status_code == 400
    assert "reserved" in opened.json()["detail"]


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
    _assert_spa_shell(page)
    r = client.post("/api/requirements/AB-36/actions/open", json={})
    assert r.status_code == 200
    job = r.json()["jobs"][0]
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
    _assert_spa_shell(html)
    data = client.get("/api/requirements/AB-31").json()
    assert data["jira"] == "AB-31"
    assert data["tickets"][0]["id"] == "T1"
    assert data["next"] == "freeze"
    assert data["stage_runs"] == {}
    vue = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "RequirementView.vue"
    ).read_text()
    assert "query: { ...route.query, job }" in vue


def test_reset_grill_from_web(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-37", source="none")
    (d / ".grill-round.json").write_text(
        json.dumps({"round": 1, "questions": [{"id": "Q1", "title": "x"}]})
    )
    client = _client(yard)
    data = client.get("/api/requirements/AB-37").json()
    assert any(a["id"] == "reset-grill" and a["enabled"] for a in data["actions"])
    r = client.post("/api/requirements/AB-37/actions/reset-grill", json={})
    assert r.status_code == 200
    assert r.json()["jobs"][0]["state"] == "ok"
    assert not (d / ".grill-round.json").exists()


def test_reset_grill_preempts_waiting_grill(tmp_path: Path, monkeypatch):
    import time

    from dev_yard.service import GRILL_SKELETON

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-38", source="none")
    round_payload = {
        "done": False,
        "round": 1,
        "questions": [
            {
                "id": "Q1",
                "title": "范围",
                "why_ask": "需求没写清楚",
                "evidence": "REQUIREMENT.md 无此描述",
                "options": [{"id": "A", "label": "只这张票"}],
            }
        ],
    }
    (d / ".grill-round.json").write_text(json.dumps(round_payload))

    # A non-sync runner resumes the pending round on startup into a waiting job,
    # which is exactly the state a user hits after restarting the console.
    runner = JobRunner(yard)
    app = create_app(yard, job_runner=runner)
    client = TestClient(app)
    grill = next(j for j in runner.running() if j.action == "grill")
    for _ in range(200):
        if grill.state == "waiting":
            break
        time.sleep(0.01)
    assert grill.state == "waiting"

    r = client.post("/api/requirements/AB-38/actions/reset-grill", json={})
    assert r.status_code == 200, r.text
    reset_job = runner.get(r.json()["jobs"][0]["id"])
    assert reset_job.done.wait(timeout=5)
    assert reset_job.state == "ok"

    # The waiting grill is preempted, not left to re-apply its stale round.
    assert grill.done.wait(timeout=5)
    assert runner.get(grill.id).state == "cancelled"
    assert not (d / ".grill-round.json").exists()
    assert (d / "GRILL.md").read_text() == GRILL_SKELETON.format(key="AB-38")


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
    r = client.post("/api/requirements/AB-32/actions/freeze", json={})
    assert r.status_code == 200
    assert (d / "worktrees" / "backend").exists()
    data = client.get("/api/requirements/AB-32").json()
    assert data["next"] == "implement"
    assert any(a["id"] == "implement" and a["enabled"] for a in data["actions"])


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
        "/api/requirements/AB-80/actions/implement",
        json={"ticket_id": "T1"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/requirements/AB-80/actions/implement",
        json={"ticket_id": "T2"},
    )
    assert second.status_code == 200
    listed = client.get("/api/jobs").json()
    assert len(listed) == 2
    assert {tuple(row.get("ticket_ids") or []) for row in listed} == {("T1",), ("T2",)}
    again = client.post(
        "/api/requirements/AB-80/actions/implement",
        json={"ticket_id": "T1"},
    )
    assert again.status_code == 400
    assert "already" in again.json()["detail"]
    from dev_yard import status as st

    assert st.load(yard, "AB-80")["tickets"]["T1"]["state"] == "implementing"
    assert st.load(yard, "AB-80")["tickets"]["T2"]["state"] == "implementing"
    board = client.get("/api/requirements/AB-80").json()
    impl = {t["id"] for t in board["tickets"] if t["state"] == "implementing"}
    assert impl == {"T1", "T2"}
    gate.set()
    for job in runner.running():
        job.done.wait(timeout=5)


def test_web_rejects_review_while_same_ticket_implementing(
    tmp_path: Path, git_src: Path, monkeypatch
):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-82", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    from dev_yard.service import req_freeze

    req_freeze(yard, "AB-82")
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    live = TestClient(create_app(yard, job_runner=runner))
    impl = live.post(
        "/api/requirements/AB-82/actions/implement",
        json={"ticket_id": "T1"},
    )
    assert impl.status_code == 200
    blocked = live.post(
        "/api/requirements/AB-82/actions/review",
        json={"ticket_id": "T1"},
    )
    assert blocked.status_code == 400
    assert "already" in blocked.json()["detail"]
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
        "/api/requirements/AB-81/actions/implement",
        json={"ticket_id": "T1"},
    )
    bulk = client.post("/api/requirements/AB-81/actions/implement", json={})
    assert bulk.status_code == 200
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
    r = client.put(
        "/api/requirements/AB-33/docs/grill",
        json={"body": "# Grill — AB-33\n\nSee assets/ui.png\n\n![ui](assets/ui.png)\n"},
    )
    assert r.status_code == 200
    page = client.get("/r/AB-33/docs/grill")
    _assert_spa_shell(page)
    doc = client.get("/api/requirements/AB-33/docs/grill").json()
    assert "/r/AB-33/assets/ui.png" in doc["html"]
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
    client = _client(yard)
    _assert_spa_shell(client.get("/r/NO-1"))
    assert client.get("/api/requirements/NO-1").status_code == 404


def test_finished_job_does_not_poll_on_ok_page(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/api/open",
        json={"jira": "AB-40", "source": "none"},
    )
    assert r.status_code == 200
    job_id = r.json()["jobs"][0]["id"]
    ok_page = client.get("/r/AB-40?ok=1")
    _assert_spa_shell(ok_page)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "ok"
    assert client.get("/api/jobs").json() == []


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
    client = TestClient(create_app(yard, job_runner=runner))
    page = client.get("/r/AB-41")
    _assert_spa_shell(page)
    listed = client.get("/api/jobs").json()
    assert listed[0]["id"] == job.id
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


def test_spa_icons_use_mdi_path_constants():
    """Icon props must be @mdi/js path data, not "mdi-x" names.

    Vuetify renders a bare string as an SVG path, so a name like
    "mdi-plus" fails at runtime with an <path> d error — invisible to
    vue-tsc and to the build, hence this guard.
    """
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    pattern = re.compile(r'(?:^|\s):?(?:prepend-|append-)?icon="(mdi-[^"]+)"')
    for path in sorted((root / "web" / "src").rglob("*.vue")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            found = pattern.search(line)
            if found:
                offenders.append(f"{path.relative_to(root)}:{lineno}: {found.group(1)}")
    assert offenders == []


def test_pi_drawer_keeps_scroll_anchor_and_ignores_done_error():
    src = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "components"
        / "PiDrawer.vue"
    ).read_text()
    assert "anchor.isConnected" in src
    assert "el2.firstElementChild" not in src
    assert "anchor.getBoundingClientRect().top - before" in src
    assert "finished = true" in src
    assert "if (finished || es !== source) return;" in src
    assert 'empty.value = "没有找到对话记录"' in src
    assert 'empty.value = "对话流连接失败"' in src


def test_assistant_drawer_is_wired():
    root = Path(__file__).resolve().parents[1]
    app_vue = (root / "web" / "src" / "App.vue").read_text()
    drawer = (root / "web" / "src" / "components" / "AssistantDrawer.vue").read_text()
    assert "AssistantDrawer" in app_vue
    assert "openAssistant" in app_vue
    assert "/api/assistant/sessions" in drawer
    assert "suggested_actions" in drawer
    assert "确认执行" in drawer
    assert "新会话" in drawer
    assert "dropAssistant" in drawer
    assert "session.value = null" in drawer
    assert "composeLocked" in drawer
    assert "ApiError" in drawer
    assert "e.status === 404" in drawer
    assert "entry.html" in drawer
    assert "v-html" in drawer


def test_case_detail_dialog_is_wired():
    """Board and QA page open case details in place, not via page jumps."""
    root = Path(__file__).resolve().parents[1]
    req_view = (root / "web" / "src" / "views" / "RequirementView.vue").read_text()
    qa_view = (root / "web" / "src" / "views" / "QaView.vue").read_text()
    board = (root / "web" / "src" / "components" / "TicketBoard.vue").read_text()
    assert "CaseDetailDialog" in req_view
    assert "openCaseDetail" in req_view
    assert "CaseDetailDialog" in qa_view
    assert "openCaseDetail" in qa_view
    # The three former /qa?case= jump sites now emit/click instead of :to.
    assert "qa?case=" not in (root / "web" / "src" / "components" / "TicketCard.vue").read_text()
    assert "qa?case=" not in (root / "web" / "src" / "components" / "QaTestCard.vue").read_text()
    assert "qa?case=" not in req_view
    assert '"open-case"' in board


def test_screenshot_viewer_replaces_preview_dialogs():
    root = Path(__file__).resolve().parents[1]
    for name in ("RequirementView.vue", "QaView.vue"):
        src = (root / "web" / "src" / "views" / name).read_text()
        assert "ScreenshotViewer" in src, f"{name} must use ScreenshotViewer"
        assert 'v-model="previewOpen"' not in src, f"{name} still has the old preview dialog"


def test_board_has_no_assets_thumbnail_card():
    """Requirement screenshots live in the docs (inline + asset index), not the board."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "web" / "src" / "views" / "RequirementView.vue").read_text()
    assert "detail.assets" not in src, "board still renders the assets thumbnail card"


def test_rerun_waits_for_the_job_before_claiming_success():
    root = Path(__file__).resolve().parents[1] / "web" / "src"
    req = (root / "views" / "RequirementView.vue").read_text()
    qa = (root / "views" / "QaView.vue").read_text()
    helper = (root / "composables" / "qaRerun.ts").read_text()
    assert "settleJob" in req
    assert "settleJob" in qa
    assert "settleJob" in helper
    assert 'addEventListener("state"' not in helper
    assert "已重测 ${caseId}" not in req
    assert "已重测 ${caseId}" not in qa
    assert "showCaseBanner" in req


def test_job_panel_can_cancel_running_jobs():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "web" / "src" / "components" / "JobPanel.vue").read_text()
    client_ts = (root / "web" / "src" / "api" / "client.ts").read_text()
    req_view = (root / "web" / "src" / "views" / "RequirementView.vue").read_text()
    assert "cancelJob" in panel, "JobPanel must offer a stop button"
    assert "/cancel" in client_ts, "client must POST /api/jobs/{id}/cancel"
    assert '"cancelled"' in panel, "JobPanel terminal states must include cancelled"
    assert '"cancelled"' in req_view, "RequirementView must treat cancelled as terminal"


def test_inconclusive_review_is_not_presented_as_failed():
    root = Path(__file__).resolve().parents[1]
    web = root / "web" / "src"
    board = (web / "components" / "TicketBoard.vue").read_text()
    ticket_dialog = (web / "components" / "TicketReviewDialog.vue").read_text()
    contract_dialog = (web / "components" / "ContractReviewDialog.vue").read_text()
    req = (web / "views" / "RequirementView.vue").read_text()
    dash = (web / "views" / "DashboardView.vue").read_text()
    labels = (web / "composables" / "labels.ts").read_text()
    assert "repeat(8, minmax(180px, 1fr))" in board
    assert 'ticket.state === "inconclusive"' in ticket_dialog
    assert "verdict.value = null" in ticket_dialog
    assert 'props.contract === "inconclusive"' in contract_dialog
    assert "contractTone" in req
    assert "重跑契约审查" in req
    assert "契约审查无结论" in labels
    assert "contractTone" in dash


def test_app_js_uses_event_source():
    from dev_yard.web.app import HERE

    js = (HERE / "static" / "app.js").read_text()
    css = (HERE / "static" / "app.css").read_text()
    vue_jobs = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "state" / "jobs.ts"
    ).read_text()
    assert "EventSource" in vue_jobs
    assert "/api/jobs/events" in vue_jobs
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
    _assert_spa_shell(page)
    vue = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "JobPanel.vue"
    ).read_text()
    assert "查看对话" in vue
    assert "openPi" in vue
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
    _assert_spa_shell(page)
    listed = client.get("/api/jobs").json()
    assert listed[0]["id"] == job.id
    assert listed[0]["state"] == "waiting"
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
    _assert_spa_shell(home)
    shell = (Path(__file__).resolve().parents[1] / "web" / "src" / "App.vue").read_text()
    assert "job.state === 'waiting'" in shell
    assert "/api/jobs/events" in _app_paths(client.app)
    client.post(
        f"/api/jobs/{job.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A", "text": ""}]},
    )
    assert job.done.wait(timeout=5)
    left = client.get("/api/jobs").json()
    assert [row["jira"] for row in left] == ["AB-72"]
    assert left[0]["state"] == "waiting"
    client.post(
        f"/api/jobs/{other.id}/answers",
        json={"answers": [{"id": "Q1", "option": "A", "text": ""}]},
    )
    assert other.done.wait(timeout=5)
    assert client.get("/api/jobs").json() == []


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
    r = client.post("/api/requirements/AB-35/actions/grill", json={})
    assert r.status_code == 200
    job = r.json()["jobs"][0]
    assert "web-grill" in job["log"]


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
                "questions": [
                    {
                        "id": "Q1",
                        "title": "范围",
                        "options": [{"id": "A", "label": "只这张票"}],
                        "why_ask": "范围决定票的拆分，属产品意图级",
                        "evidence": "REQUIREMENT.md 未写范围，代码里也查不到",
                    }
                ],
            }
        )
    )

    def fake_launch(*args, **kwargs):
        raise AssertionError("pi should not start on resume")

    monkeypatch.setattr("dev_yard.web.jobs.service.run_stage", fake_launch)
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
    _assert_spa_shell(page)
    assert items[0]["id"] in [row["id"] for row in client.get("/api/jobs").json()]
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


def test_sanitize_html_strips_entity_and_tab_javascript():
    from dev_yard.web.sanitize import sanitize_html

    cleaned = sanitize_html('<a href="java&#x09;script:alert(1)">x</a>')
    assert "javascript:" not in cleaned.lower()
    assert "href" not in cleaned.lower()
    cleaned_tab = sanitize_html('<a href="java\tscript:alert(1)">x</a>')
    assert "href" not in cleaned_tab.lower()


def test_check_bind_host_refuses_non_loopback():
    import pytest

    check_bind_host("127.0.0.1")
    check_bind_host("0.0.0.0", allow_remote=True)
    with pytest.raises(ValueError, match="allow-remote"):
        check_bind_host("0.0.0.0")


@pytest.mark.spa
@needs_spa
def test_spa_shell_and_assets(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/assets/" in page.text
    meta = client.get("/api/meta").json()
    assert meta["root_name"] == yard.name
    css_name = page.text.split('href="/assets/')[1].split('"')[0]
    css = client.get(f"/assets/{css_name}")
    assert css.status_code == 200


def test_pi_settings_api(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setattr(
        "dev_yard.web.context.list_pi_catalog",
        lambda: {
            "providers": [{"id": "rcc", "models": ["glm-5.3", "MiniMax-M3"]}],
            "error": None,
        },
    )
    client = _client(yard)
    empty = client.get("/api/pi").json()
    assert empty["provider"] == ""
    assert empty["model"] == ""
    assert empty["stage_ids"] == [
        "open",
        "grill",
        "spec",
        "tickets",
        "implement",
        "review",
        "contract",
        "assistant",
    ]
    assert empty["catalog"]["providers"][0]["id"] == "rcc"
    saved = client.put(
        "/api/pi",
        json={
            "provider": "rcc",
            "model": "MiniMax-M3",
            "stages": {"grill": {"provider": "", "model": "gpt-5"}},
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["provider"] == "rcc"
    assert body["stages"]["grill"]["model"] == "gpt-5"
    assert body["stages"]["spec"]["model"] == ""
    bad = client.put(
        "/api/pi",
        json={"provider": "", "model": "", "stages": {"nope": {"provider": "", "model": "x"}}},
    )
    assert bad.status_code == 400

    pdir = yard / "plugins" / "example"
    pdir.mkdir(parents=True)
    (pdir / "plugin.yaml").write_text(
        "name: example\ntools: [read]\n", encoding="utf-8"
    )
    (pdir / "SKILL.md").write_text("# example\n", encoding="utf-8")
    (yard / "yard.yaml").write_text("plugins: [plugins/example]\n", encoding="utf-8")
    again = client.get("/api/pi").json()
    assert again["stage_ids"] == empty["stage_ids"]
    assert "example" not in again["stage_ids"]
    plugin_put = client.put(
        "/api/pi",
        json={"provider": "", "model": "", "stages": {"example": {"provider": "", "model": "x"}}},
    )
    assert plugin_put.status_code == 400


def test_git_settings_api(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    empty = client.get("/api/git").json()
    assert empty["freeze_branch"] == "req/{jira}"
    assert empty["preview"] == "req/PROJ-101"
    assert empty["ticket_preview"] == "req/PROJ-101-T1"
    saved = client.put("/api/git", json={"freeze_branch": "feature/{jira}"})
    assert saved.status_code == 200
    assert saved.json()["preview"] == "feature/PROJ-101"
    assert saved.json()["ticket_preview"] == "feature/PROJ-101-T1"
    again = client.get("/api/git").json()
    assert again["freeze_branch"] == "feature/{jira}"
    bad = client.put("/api/git", json={"freeze_branch": "no-placeholder"})
    assert bad.status_code == 400
    vue = (Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "SettingsView.vue").read_text()
    assert "freeze_branch" in vue
    assert 'value="dev"' in vue
    assert 'value="git"' in vue
    assert 'value="pi"' in vue
    assert "getDevSettings()" in vue
    assert "getGitSettings()" in vue
    assert "getPiSettings()" in vue
    assert "Promise.all([getGitSettings()" not in vue
    nav = (Path(__file__).resolve().parents[1] / "web" / "src" / "App.vue").read_text()
    assert 'title="配置"' in nav


def test_dev_settings_api(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    default_res = client.get("/api/dev").json()
    assert default_res["tdd"] is True
    saved = client.put("/api/dev", json={"tdd": False})
    assert saved.status_code == 200
    assert saved.json()["tdd"] is False
    again = client.get("/api/dev").json()
    assert again["tdd"] is False


def test_qa_config_page_serves_spa(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    _assert_spa_shell(_client(yard).get("/qa-config"))


def test_qa_config_api(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setattr(
        "dev_yard.web.context.list_pi_catalog",
        lambda: {
            "providers": [{"id": "rcc", "models": ["glm-5.3", "MiniMax-M3"]}],
            "error": None,
        },
    )
    client = _client(yard)
    empty = client.get("/api/qa-config")
    assert empty.status_code == 200
    body = empty.json()
    assert body["exists"] is False
    assert body["parse_error"] == ""
    assert body["payload"]["envs"]["local"]["base_url"] == ""
    assert len(body["payload"]["workers"]) == 1
    assert body["catalog"]["providers"][0]["id"] == "rcc"

    payload = {
        "active_env": "local",
        "browser": {"channel": "chrome", "headed": True},
        "workers": [
            {
                "id": "a",
                "provider": "rcc",
                "model": "MiniMax-M3",
                "concurrency": 2,
                "priority": 1,
            }
        ],
        "envs": {
            "local": {
                "base_url": "http://127.0.0.1:8080",
                "auth": {"default": "default", "accounts": {}},
                "db": {"url": "postgres://127.0.0.1:5432/app"},
                "script": {"runner": ""},
                "notes": ["先起前端"],
            }
        },
    }
    saved = client.put("/api/qa-config", json=payload)
    assert saved.status_code == 200
    assert saved.json()["exists"] is True
    cfg = load_qa_config(yard)
    assert cfg.env.base_url == "http://127.0.0.1:8080"
    assert cfg.browser.headed is True
    assert cfg.headed is False  # 总并发 > 1 时宿主强制无头
    assert cfg.workers[0].id == "a"

    again = client.get("/api/qa-config").json()
    assert again["payload"]["envs"]["local"]["notes"] == ["先起前端"]
    assert "base_url: http://127.0.0.1:8080" in again["raw"]

    bad = dict(payload)
    bad["envs"] = {"local": {**payload["envs"]["local"], "base_url": ""}}
    assert client.put("/api/qa-config", json=bad).status_code == 400
    assert load_qa_config(yard).env.base_url == "http://127.0.0.1:8080"

    multi = {
        **payload,
        "active_env": "test",
        "envs": {
            **payload["envs"],
            "test": {
                "base_url": "https://test.example.com",
                "auth": {"default": "default", "accounts": {}},
                "db": {"url": ""},
                "script": {"runner": ""},
                "notes": [],
            },
        },
    }
    saved_multi = client.put("/api/qa-config", json=multi)
    assert saved_multi.status_code == 200
    assert saved_multi.json()["payload"]["env_names"] == ["local", "test"]
    assert load_qa_config(yard).active_env == "test"
    assert load_qa_config(yard, "local").env.base_url == "http://127.0.0.1:8080"
    assert load_qa_config(yard, "test").env.base_url == "https://test.example.com"

    (yard / "qa.yaml").write_text("envs: [unclosed\n", encoding="utf-8")
    broken = client.get("/api/qa-config")
    assert broken.status_code == 200
    assert "qa.yaml" in broken.json()["parse_error"]
    assert broken.json()["raw"] == "envs: [unclosed\n"
    assert broken.json()["payload"] is None
    assert client.put("/api/qa-config", json=payload).status_code == 409
    assert (yard / "qa.yaml").read_text(encoding="utf-8") == "envs: [unclosed\n"


def test_qa_config_api_never_exposes_secrets(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setattr(
        "dev_yard.web.context.list_pi_catalog",
        lambda: {"providers": [], "error": None},
    )
    client = _client(yard)
    payload = {
        "active_env": "local",
        "browser": {"channel": "chrome", "headed": False},
        "workers": [
            {"id": "a", "provider": "rcc", "model": "grok-4", "concurrency": 1, "priority": 1}
        ],
        "envs": {
            "local": {
                "base_url": "http://127.0.0.1:8080",
                "auth": {
                    "default": "default",
                    "accounts": {
                        "default": {
                            "username": "admin",
                            "password": "s3cret",
                            "state_file": ".yard-qa/auth.json",
                        }
                    },
                },
                "db": {"url": "postgres://user:pw@host/db"},
                "script": {"runner": ""},
                "notes": [],
            }
        },
    }
    assert client.put("/api/qa-config", json=payload).status_code == 200
    got = client.get("/api/qa-config").json()
    assert "s3cret" not in got["raw"]
    assert "user:pw@host" not in got["raw"]
    assert got["payload"]["envs"]["local"]["auth"]["accounts"]["default"]["password"] == MASK
    assert got["payload"]["envs"]["local"]["db"]["url"] == MASK
    masked = got["payload"]
    masked["envs"]["local"]["notes"] = ["改过"]
    assert client.put("/api/qa-config", json=masked).status_code == 200
    cfg = load_qa_config(yard)
    assert cfg.env.accounts["default"].password == "s3cret"
    assert cfg.env.db_url == "postgres://user:pw@host/db"

    # A broken file leaks nothing through raw or the parse error.
    (yard / "qa.yaml").write_text(
        'envs:\n  local:\n    db:\n      url: "postgres://u:LEAK@h/db\n',
        encoding="utf-8",
    )
    broken = client.get("/api/qa-config").json()
    assert broken["payload"] is None
    assert "LEAK" not in broken["raw"]
    assert "LEAK" not in broken["parse_error"]


def test_qa_config_api_survives_a_non_utf8_file(tmp_path: Path):
    """A GBK-saved qa.yaml must answer like any other unreadable file, not 500."""
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "qa.yaml").write_bytes(
        "envs:\n  local:\n    base_url: http://x\n# 中文注释\n".encode("gbk")
    )
    client = _client(yard)
    r = client.get("/api/qa-config")
    assert r.status_code == 200
    assert "qa.yaml" in r.json()["parse_error"]
    assert r.json()["payload"] is None
    assert client.put("/api/qa-config", json={}).status_code == 409


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
    _assert_spa_shell(page)
    vue = (Path(__file__).resolve().parents[1] / "web" / "src" / "views" / "ReposView.vue").read_text()
    assert 'v-model="role"' in vue
    assert 'label="role' in vue
    assert 'v-text-field v-model="role"' in vue
    r = client.post(
        "/api/repos",
        json={
            "alias": "app",
            "url": str(git_src),
            "default_base": "main",
            "role": "mobile",
            "path": str(git_src),
        },
    )
    assert r.status_code == 200
    listed = client.get("/api/repos").json()
    assert listed == [
        {
            "alias": "app",
            "url": str(git_src),
            "default_base": "main",
            "role": "mobile",
            "path": str(git_src),
            "provider": "",
            "model": "",
            "test_branch": "",
        }
    ]


def test_repo_form_blank_alias_uses_git_project_name(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    page = client.get("/repos")
    assert 'name="alias" required' not in page.text
    r = client.post(
        "/api/repos",
        json={
            "alias": "",
            "url": f"git@host:leads-in/{git_src.name}.git",
            "default_base": "main",
            "role": "svc",
            "path": str(git_src),
        },
    )
    assert r.status_code == 200
    listed = client.get("/api/repos").json()
    assert listed[0]["alias"] == git_src.name


def test_repo_add_job_shows_clone_progress(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/api/repos",
        json={
            "alias": "backend",
            "url": str(git_src),
            "default_base": "main",
            "role": "svc",
            "path": "",
        },
    )
    assert r.status_code == 200
    job_id = r.json()["jobs"][0]["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["state"] == "ok"
    assert "clone" in job["log"].lower()
    page = client.get(f"/repos?job={job_id}")
    _assert_spa_shell(page)
    assert (yard / ".repos" / "backend" / ".git").exists()


def test_api_open_and_reserved_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post("/api/open", json={"jira": "AB-90", "source": "none"})
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["action"] == "open"
    assert jobs[0]["jira"] == "AB-90"
    assert jobs[0]["state"] == "ok"
    detail = client.get("/api/requirements/AB-90").json()
    assert detail["jira"] == "AB-90"
    assert detail["steps"]
    assert detail["actions"][0]["label"]
    reserved = client.post("/api/open", json={"jira": "docs", "source": "none"})
    assert reserved.status_code == 400
    assert "reserved" in reserved.json()["detail"]

    # Test opening with URL target and auto-extracted key
    url_resp = client.post(
        "/api/open",
        json={"target": "https://github.com/my-org/my-repo/issues/101", "source": "none"},
    )
    assert url_resp.status_code == 200
    assert url_resp.json()["jobs"][0]["jira"] == "my-repo-101"

    # Test opening with text payload directly
    text_resp = client.post(
        "/api/open",
        json={"key": "TEXT-REQ", "source": "text", "payload": "# Custom Title\n\nContent"},
    )
    assert text_resp.status_code == 200
    assert text_resp.json()["jobs"][0]["jira"] == "TEXT-REQ"
    assert (yard / "reqs" / "TEXT-REQ" / "REQUIREMENT.md").read_text() == "# Custom Title\n\nContent"


def test_api_save_doc_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    d, _ = req_open(yard, "AB-91", source="none")
    (d / "assets").mkdir()
    (d / "assets" / "ui.png").write_bytes(b"\x89PNG\r\n")
    client = _client(yard)
    saved = client.put(
        "/api/requirements/AB-91/docs/grill",
        json={"body": "# Grill — AB-91\n\n![ui](assets/ui.png)\n"},
    )
    assert saved.status_code == 200
    data = saved.json()
    assert data["slug"] == "grill"
    assert "AB-91" in data["text"]
    assert "/r/AB-91/assets/ui.png" in data["html"]
    got = client.get("/api/requirements/AB-91/docs/grill").json()
    assert got["text"] == data["text"]
    assert got["filled"] is True


def test_api_freeze_and_duplicate_implement(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "AB-92", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## T2: y\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    client = _client(yard)
    frozen = client.post("/api/requirements/AB-92/actions/freeze", json={})
    assert frozen.status_code == 200
    assert (d / "worktrees" / "backend").exists()
    gate = threading.Event()

    def execute(root: Path, job) -> None:
        gate.wait(timeout=5)

    runner = JobRunner(yard, execute=execute, sync=False)
    live = TestClient(create_app(yard, job_runner=runner))
    first = live.post(
        "/api/requirements/AB-92/actions/implement",
        json={"ticket_id": "T1"},
    )
    assert first.status_code == 200
    assert first.json()["jobs"][0]["ticket_ids"] == ["T1"]
    again = live.post(
        "/api/requirements/AB-92/actions/implement",
        json={"ticket_id": "T1"},
    )
    assert again.status_code == 400
    assert "already" in again.json()["detail"]
    bulk = live.post("/api/requirements/AB-92/actions/implement", json={})
    assert bulk.status_code == 200
    assert bulk.json()["jobs"][0]["ticket_ids"] == ["T2"]
    gate.set()
    for job in runner.running():
        job.done.wait(timeout=5)


def test_api_repo_add(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/api/repos",
        json={
            "alias": "backend",
            "url": str(git_src),
            "default_base": "main",
            "role": "svc",
            "path": str(git_src),
        },
    )
    assert r.status_code == 200
    assert r.json()["jobs"][0]["action"] == "repo_add"
    listed = client.get("/api/repos").json()
    assert listed[0]["alias"] == "backend"


def test_api_repo_set_pi(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    client = _client(yard)
    bad = client.put("/api/repos/backend", json={"provider": "rcc", "model": ""})
    assert bad.status_code == 400
    ok = client.put("/api/repos/backend", json={"provider": "rcc", "model": "glm-5.3"})
    assert ok.status_code == 200
    row = next(r for r in ok.json() if r["alias"] == "backend")
    assert row["provider"] == "rcc"
    assert row["model"] == "glm-5.3"
    cleared = client.put("/api/repos/backend", json={"provider": "", "model": ""})
    assert cleared.json()[0]["provider"] == ""
    assert cleared.json()[0]["model"] == ""


def test_api_ticket_diff_and_requirement_diff(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "DIFF-01", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: add auth feature\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    client = _client(yard)

    # Before freeze / pending: returns friendly pending message
    r_pending = client.get("/api/requirements/DIFF-01/tickets/T1/diff")
    assert r_pending.status_code == 200
    data_pending = r_pending.json()
    assert data_pending["ticket_id"] == "T1"
    assert data_pending["state"] == "pending"
    assert "尚未开始实现" in data_pending["message"]

    # Unknown ticket returns 400
    r_bad = client.get("/api/requirements/DIFF-01/tickets/T999/diff")
    assert r_bad.status_code == 400

    # Unknown req returns 404
    r_bad_req = client.get("/api/requirements/UNKNOWN-99/tickets/T1/diff")
    assert r_bad_req.status_code == 404

    # Freeze requirement to create worktrees
    client.post("/api/requirements/DIFF-01/actions/freeze", json={})
    wt = d / "worktrees" / "backend"
    assert wt.is_dir()

    # Modify a file and create a new file in worktree
    (wt / "new_module.py").write_text("def hello():\n    return 'world'\n")
    (wt / "README.md").write_text("# Updated README\n")

    # Set ticket state to implemented in status
    from dev_yard import status as st
    status_data = st.load(yard, "DIFF-01")
    status_data["tickets"]["T1"]["state"] = "implemented"
    st.save(yard, "DIFF-01", status_data)

    r_diff = client.get("/api/requirements/DIFF-01/tickets/T1/diff")
    assert r_diff.status_code == 200
    data_diff = r_diff.json()
    assert data_diff["ticket_id"] == "T1"
    assert data_diff["repo"] == "backend"
    assert data_diff["state"] == "implemented"
    assert "hello" in data_diff["diff"]
    assert "diff --git a/new_module.py b/new_module.py" in data_diff["diff"]
    assert "+def hello():" in data_diff["diff"]
    assert any(f["path"] == "new_module.py" for f in data_diff["files"])
    assert any(f["path"] == "README.md" for f in data_diff["files"])

    # Requirement level diff
    r_req_diff = client.get("/api/requirements/DIFF-01/diff")
    assert r_req_diff.status_code == 200
    req_diff_data = r_req_diff.json()
    assert req_diff_data["jira"] == "DIFF-01"
    assert len(req_diff_data["repos"]) == 1
    assert req_diff_data["repos"][0]["repo"] == "backend"
    assert "hello" in req_diff_data["repos"][0]["diff"]


def test_ticket_review_api(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "REV-01", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: add auth\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )

    executed_jobs = []

    def mock_execute(root: Path, job) -> None:
        executed_jobs.append(job)

    runner = JobRunner(yard, execute=mock_execute, sync=False)
    client = TestClient(create_app(yard, job_runner=runner))

    from dev_yard.service import req_freeze
    req_freeze(yard, "REV-01")

    from dev_yard import status as st
    status_data = st.load(yard, "REV-01")
    status_data["tickets"]["T1"]["state"] = "implemented"
    st.save(yard, "REV-01", status_data)

    # Review override: fail with feedback without auto_implement
    r1 = client.post(
        "/api/requirements/REV-01/tickets/T1/review",
        json={"verdict": "failed", "summary": "Missing error handling", "auto_implement": False},
    )
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["ticket"]["state"] == "blocked"
    assert "Missing error handling" in data1["ticket"]["last_summary"]
    assert len(data1["jobs"]) == 0

    # Requirement payload carries server-rendered markdown for the review dialog
    detail = client.get("/api/requirements/REV-01").json()
    t1 = next(t for t in detail["tickets"] if t["id"] == "T1")
    assert "Missing error handling" in t1["last_summary_html"]
    assert "<p>" in t1["last_summary_html"]

    # Review override: fail with feedback and auto_implement
    r_auto = client.post(
        "/api/requirements/REV-01/tickets/T1/review",
        json={"verdict": "failed", "summary": "Fix validation", "auto_implement": True},
    )
    assert r_auto.status_code == 200
    data_auto = r_auto.json()
    assert len(data_auto["jobs"]) == 1
    assert data_auto["jobs"][0]["action"] == "implement"

    # Review override: pass
    r2 = client.post(
        "/api/requirements/REV-01/tickets/T1/review",
        json={"verdict": "passed", "summary": "Approved by human reviewer"},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["ticket"]["state"] == "done"
    assert data2["ticket"]["last_summary"] == "Approved by human reviewer"

    # Test error cases
    r_bad_ticket = client.post(
        "/api/requirements/REV-01/tickets/T999/review",
        json={"verdict": "passed"},
    )
    assert r_bad_ticket.status_code == 400

    r_bad_verdict = client.post(
        "/api/requirements/REV-01/tickets/T1/review",
        json={"verdict": "invalid"},
    )
    assert r_bad_verdict.status_code == 400


def test_contract_review_api(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "CREV-01", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: add auth\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )

    executed_jobs = []

    def mock_execute(root: Path, job) -> None:
        executed_jobs.append(job)

    runner = JobRunner(yard, execute=mock_execute, sync=False)
    client = TestClient(create_app(yard, job_runner=runner))

    from dev_yard.service import req_freeze
    req_freeze(yard, "CREV-01")

    # Contract review override: fail with feedback without auto_implement
    r1 = client.post(
        "/api/requirements/CREV-01/contract/review",
        json={"verdict": "failed", "summary": "Missing RPC contract", "auto_implement": False},
    )
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["contract"]["contract_review"] == "failed"
    assert "Missing RPC contract" in data1["contract"]["contract_summary"]
    assert len(data1["jobs"]) == 0

    # Contract review override: fail with auto_implement (triggers fix-contract action)
    r_auto = client.post(
        "/api/requirements/CREV-01/contract/review",
        json={"verdict": "failed", "summary": "Fix RPC payload", "auto_implement": True},
    )
    assert r_auto.status_code == 200
    data_auto = r_auto.json()
    assert len(data_auto["jobs"]) == 1
    assert data_auto["jobs"][0]["action"] == "fix-contract"

    # Contract review override: pass
    r2 = client.post(
        "/api/requirements/CREV-01/contract/review",
        json={"verdict": "passed", "summary": "Contracts approved"},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["contract"]["contract_review"] == "passed"
    assert data2["contract"]["contract_summary"] == "Contracts approved"

    # Verify requirement detail endpoint returns updated contract fields
    r_detail = client.get("/api/requirements/CREV-01")
    assert r_detail.status_code == 200
    detail_data = r_detail.json()
    assert detail_data["contract"] == "passed"
    assert detail_data["contract_summary"] == "Contracts approved"
    assert "Contracts approved" in detail_data["contract_summary_html"]


def test_ticket_delete_api(tmp_path: Path, git_src: Path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "DEL-01", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: add auth\n- repo: backend\n- depends_on:\n- parallel: false\n\n"
        "## B1: 契约缺口 (backend)\n- repo: backend\n- depends_on: T1\n"
        "- parallel: true\n- source: contract\n- finding: repo:backend\n\n"
        "### 做什么\n1. fix it\n\n"
        "## B2: 契约缺口 (frontend)\n- repo: backend\n- depends_on:\n"
        "- parallel: true\n- source: contract\n- finding: repo:frontend\n"
    )
    client = _client(yard)

    # Non-bug tickets are rejected.
    assert client.delete("/api/requirements/DEL-01/tickets/T1").status_code == 400
    # Unknown ticket.
    assert client.delete("/api/requirements/DEL-01/tickets/B9").status_code == 404

    r = client.delete("/api/requirements/DEL-01/tickets/B2")
    assert r.status_code == 200
    assert r.json()["ticket_id"] == "B2"

    text = (d / "TICKETS.md").read_text()
    assert "## B2" not in text
    assert "## B1" in text
    assert "## T1" in text

    detail = client.get("/api/requirements/DEL-01").json()
    assert [t["id"] for t in detail["tickets"]] == ["T1", "B1"]


def test_create_app_recovers_stale_reviewing(tmp_path: Path, git_src: Path, monkeypatch):
    from dev_yard import status as st
    from dev_yard.service import req_freeze

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "backend", str(git_src), "main", "be", str(git_src))
    d, _ = req_open(yard, "REC-01", source="none")
    (d / "TICKETS.md").write_text(
        "## T1: x\n- repo: backend\n- depends_on:\n- parallel: false\n"
    )
    req_freeze(yard, "REC-01")
    data = st.load(yard, "REC-01")
    data["tickets"]["T1"]["state"] = "reviewing"
    st.save(yard, "REC-01", data)

    # A fresh console process has no job yet, so the slot is stale and must be
    # recovered at startup instead of spinning as "审查中".
    client = TestClient(create_app(yard, job_runner=JobRunner(yard, sync=False)))
    detail = client.get("/api/requirements/REC-01").json()
    t1 = next(t for t in detail["tickets"] if t["id"] == "T1")
    assert t1["state"] == "implemented"
    assert t1["can_review"] is True



