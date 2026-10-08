"""Human review notes can rewrite conflicting requirement docs, and nothing else."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dev_yard import status as st
from dev_yard.doc_align import _hash, apply, propose
from dev_yard.runners import RunResult
from dev_yard.service import init_yard, req_open
from dev_yard.tickets import load_tickets
from dev_yard.web.app import create_app

_DOCS = {
    "REQUIREMENT.md": "# WD-1\n\n列表标题和筛选都改用 Widget 资源名。\n",
    "GRILL.md": "# Grill\n\n4. 筛选和列都改用 Widget 资源名。\n",
    "SPEC.md": "# Spec\n\n列表标题列和标题筛选都按 Widget 资源名工作。\n",
    "TICKETS.md": (
        "# Tickets\n\n"
        "## T3: 列表标题改资源名（筛选与列值）\n\n"
        "- repo: legacy\n"
        "- 筛选与列值都改为 Widget 资源名。\n"
    ),
}
_DECISION = "筛选仍用 Widget 地块名，只改列文案。"
_READY = {
    "REQUIREMENT.md": "# WD-1\n\n筛选仍用 Widget 地块名，列文案改为资源名。\n",
    "GRILL.md": "# Grill\n\n4. 筛选仍用 Widget 地块名，列文案改为资源名。\n",
    "SPEC.md": "# Spec\n\n标题筛选仍用 Widget 地块名，列文案改为资源名。\n",
    "TICKETS.md": (
        "# Tickets\n\n"
        "## T3: 列表标题改资源名（筛选与列值）\n\n"
        "- repo: legacy\n"
        "- 筛选仍用 Widget 地块名，列文案改为资源名。\n"
    ),
}


class _Writer:
    def __init__(
        self,
        payload: dict,
        *,
        corrupt: bool = False,
        fail: bool = False,
        tamper: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.corrupt = corrupt
        self.fail = fail
        self.tamper = tamper or {}
        self.prompt = ""

    def start(self, prompt: str, cwd: Path, extra_read_paths: list[Path], repo: str | None = None):
        self.prompt = prompt
        path = _proposal_path(prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        req = path.parents[1]
        if self.corrupt:
            (req / "REQUIREMENT.md").write_text("WIPED\n", encoding="utf-8")
        for rel, text in self.tamper.items():
            dest = req / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        if self.fail:
            return RunResult(ok=False, summary="pi failed", exit_code=1)
        return RunResult(ok=True, summary="ok")


def _proposal_path(prompt: str) -> Path:
    for line in prompt.splitlines():
        if line.startswith("提案只写到这个文件："):
            return Path(line.split("：", 1)[1].strip())
    raise AssertionError("prompt missing proposal path")


def _yard(tmp_path: Path) -> tuple[Path, Path]:
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "WD-1", source="none")
    req = yard / "reqs" / "WD-1"
    for name, text in _DOCS.items():
        (req / name).write_text(text, encoding="utf-8")
    data = st.sync_tickets(st.load(yard, "WD-1"), load_tickets(req))
    data["tickets"]["T3"]["state"] = "blocked"
    data["tickets"]["T3"]["last_verdict"] = "failed"
    st.save(yard, "WD-1", data)
    return yard, req


def _ready_payload() -> dict:
    return {"status": "ready", "decision": _DECISION, "files": _READY}


def test_clarify_does_not_touch_docs_or_ticket_state(tmp_path: Path):
    yard, req = _yard(tmp_path)
    before = {name: (req / name).read_text(encoding="utf-8") for name in _DOCS}
    status_before = (req / "STATUS.yaml").read_bytes()
    writer = _Writer(
        {"status": "clarify", "decision": "筛选仍用 Widget 地块名，只改列文案。"}
    )
    out = propose(
        yard,
        "WD-1",
        "T3",
        "后端不要改，只改前端文案。",
        runner=writer,
    )
    assert out["status"] == "clarify"
    assert out["files"] == {}
    assert out["diffs"] == []
    for name, text in before.items():
        assert (req / name).read_text(encoding="utf-8") == text
    assert (req / "STATUS.yaml").read_bytes() == status_before
    assert not (req / ".doc-align").exists()


def test_ready_proposal_is_a_diff_until_confirm(tmp_path: Path):
    yard, req = _yard(tmp_path)
    before = (req / "SPEC.md").read_text(encoding="utf-8")
    writer = _Writer(_ready_payload(), corrupt=True)
    out = propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=writer)
    assert out["status"] == "ready"
    assert [item["file"] for item in out["diffs"]] == list(_DOCS)
    assert "Widget 地块名" in out["diffs"][0]["diff"]
    assert (req / "REQUIREMENT.md").read_text(encoding="utf-8") != "WIPED\n"
    assert (req / "SPEC.md").read_text(encoding="utf-8") == before
    slot = st.load(yard, "WD-1")["tickets"]["T3"]
    assert slot["state"] == "blocked"
    assert slot["last_verdict"] == "failed"


def test_confirmed_decision_is_in_the_prompt(tmp_path: Path):
    yard, _req = _yard(tmp_path)
    writer = _Writer(
        {"status": "clarify", "decision": "筛选仍用 Widget 地块名，只改列文案。"}
    )
    propose(
        yard,
        "WD-1",
        "T3",
        "后端不要改，只改前端文案。",
        _DECISION,
        runner=writer,
    )
    assert f"已确认的决定：{_DECISION}" in writer.prompt
    assert "不要再返回 clarify" in writer.prompt


def test_apply_writes_docs_only_and_marks_cases_stale(tmp_path: Path):
    yard, req = _yard(tmp_path)
    case = req / "qa" / "cases" / "case-01" / "case.md"
    case.parent.mkdir(parents=True)
    case.write_text("期望：筛选命中 Widget 资源名。\n", encoding="utf-8")
    writer = _Writer(_ready_payload())
    out = propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=writer)
    applied = apply(
        yard,
        "WD-1",
        "T3",
        decision=out["decision"],
        base=out["base"],
        files=out["files"],
    )
    assert applied["changed"] == list(_DOCS)
    assert applied["qa_stale"] is True
    assert (req / "REQUIREMENT.md").read_text(encoding="utf-8") == _READY["REQUIREMENT.md"]
    assert case.read_text(encoding="utf-8") == "期望：筛选命中 Widget 资源名。\n"
    review = (req / "qa" / "review.yaml").read_text(encoding="utf-8")
    assert "审查意见改文档 T3" in review
    slot = st.load(yard, "WD-1")["tickets"]["T3"]
    assert slot["state"] == "blocked"
    assert slot["last_verdict"] == "failed"


def test_apply_rejects_a_stale_base_and_a_new_ticket(tmp_path: Path):
    import pytest

    yard, req = _yard(tmp_path)
    writer = _Writer(_ready_payload())
    out = propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=writer)
    (req / "SPEC.md").write_text("# Spec\n\n别人刚改过。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="又被改过"):
        apply(
            yard,
            "WD-1",
            "T3",
            decision=out["decision"],
            base=out["base"],
            files=out["files"],
        )
    assert "别人刚改过" in (req / "SPEC.md").read_text(encoding="utf-8")

    moved = dict(_READY)
    moved["TICKETS.md"] = _READY["TICKETS.md"].replace("## T3:", "## T9:")
    with pytest.raises(ValueError, match="编号和仓"):
        apply(
            yard,
            "WD-1",
            "T3",
            decision=out["decision"],
            base={
                name: _hash((req / name).read_text(encoding="utf-8")) for name in _DOCS
            },
            files=moved,
        )


def test_doc_align_http_proposes_then_apply_keeps_the_ticket(tmp_path: Path, monkeypatch):
    yard, req = _yard(tmp_path)

    def fake_propose(root, jira, ticket_id, summary, decision="", *, runner=None):
        return {
            "ticket_id": ticket_id,
            "status": "clarify",
            "decision": _DECISION,
            "diffs": [],
            "files": {},
            "base": {},
        }

    monkeypatch.setattr("dev_yard.doc_align.propose", fake_propose)
    client = TestClient(create_app(yard, sync_jobs=True))
    empty = client.post(
        "/api/requirements/WD-1/tickets/T3/doc-align",
        json={"summary": "  "},
    )
    assert empty.status_code == 400
    started = client.post(
        "/api/requirements/WD-1/tickets/T3/doc-align",
        json={"summary": "后端不要改，只改前端文案。"},
    )
    assert started.status_code == 200
    job = started.json()["jobs"][0]
    assert job["state"] == "ok"
    assert job["doc_align"]["status"] == "clarify"
    assert job["label"] == "按审查意见改文档"
    assert st.load(yard, "WD-1")["tickets"]["T3"]["state"] == "blocked"

    applied = client.post(
        "/api/requirements/WD-1/tickets/T3/doc-align/apply",
        json={
            "decision": _DECISION,
            "base": {name: _hash((req / name).read_text(encoding="utf-8")) for name in _DOCS},
            "files": _READY,
        },
    )
    assert applied.status_code == 200
    assert applied.json()["qa_stale"] is False
    assert "Widget 地块名" in (req / "REQUIREMENT.md").read_text(encoding="utf-8")
    assert st.load(yard, "WD-1")["tickets"]["T3"]["state"] == "blocked"


def test_review_dialog_offers_doc_align_apart_from_the_verdict():
    root = Path(__file__).resolve().parents[1]
    dialog = (root / "web" / "src" / "components" / "TicketReviewDialog.vue").read_text()
    view = (root / "web" / "src" / "views" / "RequirementView.vue").read_text()
    labels = (root / "web" / "src" / "composables" / "labels.ts").read_text()
    assert "按审查意见改文档" in dialog
    assert "startDocAlign" in dialog
    assert "applyDocAlign" in dialog
    assert "不改代码，也不改这张票的状态" in dialog
    assert "按这个理解生成修改" in dialog
    assert "|| applyBusy" in dialog
    assert "有两种改法" not in dialog
    assert "已设计的用例会标成待复核" not in dialog
    assert "prev[1] === id" in dialog
    assert "reviewDialog.ticket?.id !== ticketId" in view
    assert '"doc-align": "按审查意见改文档"' in labels
    from dev_yard.actions import BOARD_ACTION_IDS

    assert "doc-align" not in BOARD_ACTION_IDS


def test_ticket_identity_locks_deps_and_allows_a_new_title():
    import pytest

    from dev_yard.doc_align import _require_same_tickets

    before = _DOCS["TICKETS.md"]
    retitled = before.replace("列表标题改资源名", "列表标题只改文案")
    _require_same_tickets(before, retitled)
    moved = before.replace("- repo: legacy\n", "- repo: legacy\n- depends_on: T1\n")
    with pytest.raises(ValueError, match="编号和仓、依赖和来源"):
        _require_same_tickets(before, moved)


def test_propose_refuses_writes_outside_the_proposal(tmp_path: Path):
    import pytest

    yard, req = _yard(tmp_path)
    case = req / "qa" / "cases" / "case.md"
    case.parent.mkdir(parents=True)
    case.write_text("期望：筛选命中 Widget 资源名。\n", encoding="utf-8")
    status_before = (req / "STATUS.yaml").read_bytes()
    writer = _Writer(
        {"status": "clarify", "decision": "筛选仍用 Widget 地块名，只改列文案。"},
        tamper={
            "qa/cases/case.md": "WIPED\n",
            "leak.md": "nope\n",
            "STATUS.yaml": "tampered\n",
        },
    )
    with pytest.raises(RuntimeError, match="提案以外"):
        propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=writer)
    assert case.read_text(encoding="utf-8") == "期望：筛选命中 Widget 资源名。\n"
    assert not (req / "leak.md").exists()
    assert (req / "STATUS.yaml").read_bytes() == status_before
    assert not (req / ".doc-align").exists()


def test_failed_propose_removes_the_proposal_file(tmp_path: Path):
    import pytest

    yard, req = _yard(tmp_path)
    writer = _Writer(
        {"status": "clarify", "decision": "筛选仍用 Widget 地块名，只改列文案。"},
        fail=True,
    )
    with pytest.raises(RuntimeError, match="pi failed"):
        propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=writer)
    assert not (req / ".doc-align").exists()


def test_grill_only_apply_does_not_mark_cases_stale(tmp_path: Path):
    yard, req = _yard(tmp_path)
    case = req / "qa" / "cases" / "case-01" / "case.md"
    case.parent.mkdir(parents=True)
    case.write_text("期望：筛选命中 Widget 资源名。\n", encoding="utf-8")
    files = {name: (req / name).read_text(encoding="utf-8") for name in _DOCS}
    files["GRILL.md"] = "# Grill\n\n4. 只改了这句对齐记录。\n"
    base = {name: _hash((req / name).read_text(encoding="utf-8")) for name in _DOCS}
    applied = apply(yard, "WD-1", "T3", decision=_DECISION, base=base, files=files)
    assert applied["changed"] == ["GRILL.md"]
    assert applied["qa_stale"] is False
    assert not (req / "qa" / "review.yaml").exists()

    files["SPEC.md"] = "# Spec\n\n标题筛选仍用 Widget 地块名。\n"
    base = {name: _hash((req / name).read_text(encoding="utf-8")) for name in _DOCS}
    applied = apply(yard, "WD-1", "T3", decision=_DECISION, base=base, files=files)
    assert applied["changed"] == ["SPEC.md"]
    assert applied["qa_stale"] is True
    assert "审查意见改文档 T3" in (req / "qa" / "review.yaml").read_text(encoding="utf-8")


def test_apply_rolls_back_when_status_changes(tmp_path: Path, monkeypatch):
    import pytest

    from dev_yard import doc_align
    from dev_yard.reqboard import save_doc as real_save

    yard, req = _yard(tmp_path)
    before = {name: (req / name).read_bytes() for name in _DOCS}
    status_before = (req / "STATUS.yaml").read_bytes()

    def wrapped(root, jira, slug, text):
        path = real_save(root, jira, slug, text)
        if slug == "requirement":
            (req / "STATUS.yaml").write_text("tampered\n", encoding="utf-8")
        return path

    monkeypatch.setattr(doc_align, "save_doc", wrapped)
    files = dict(_READY)
    base = {name: _hash((req / name).read_text(encoding="utf-8")) for name in _DOCS}
    with pytest.raises(ValueError, match="不应改 STATUS.yaml"):
        apply(yard, "WD-1", "T3", decision=_DECISION, base=base, files=files)
    for name, data in before.items():
        assert (req / name).read_bytes() == data
    assert (req / "STATUS.yaml").read_bytes() == status_before


def test_propose_holds_the_doc_lock_until_restore(tmp_path: Path):
    import threading

    from dev_yard.reqboard import save_doc

    yard, req = _yard(tmp_path)
    started = threading.Event()
    release = threading.Event()
    knocking = threading.Event()
    saved = threading.Event()

    class _Hold:
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            path = _proposal_path(prompt)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"status": "clarify", "decision": "筛选仍用 Widget 地块名，只改列文案。"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (req / "REQUIREMENT.md").write_text("WIPED\n", encoding="utf-8")
            started.set()
            assert release.wait(3)
            return RunResult(ok=True, summary="ok")

    def saver():
        assert started.wait(3)
        knocking.set()
        save_doc(yard, "WD-1", "requirement", "SAVED\n")
        saved.set()

    threading.Thread(target=saver, daemon=True).start()
    box: dict[str, object] = {}

    def run():
        try:
            box["out"] = propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。", runner=_Hold())
        except Exception as exc:
            box["err"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(3)
    assert knocking.wait(3)
    assert not saved.wait(0.1)
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert saved.wait(3)
    assert "err" not in box
    assert (req / "REQUIREMENT.md").read_text(encoding="utf-8") == "SAVED\n"


def test_doc_align_argv_loads_the_write_guard(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    from dev_yard.doc_align import DOC_ALIGN_SPEC
    from dev_yard.runners import pi_argv

    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(
        root=root,
        bundle="doc-align",
        prompt="p",
        print_mode=True,
        binary="pi",
        spec=DOC_ALIGN_SPEC,
    )
    extensions = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--extension"]
    assert any(path.endswith("yard-doc-align.ts") for path in extensions)


def test_propose_refuses_when_the_write_guard_is_missing(tmp_path: Path, monkeypatch):
    import pytest

    yard, _req = _yard(tmp_path)
    monkeypatch.setattr("dev_yard.doc_align.resolve_doc_align_extension_path", lambda _root: None)
    with pytest.raises(RuntimeError, match="写保护扩展缺失"):
        propose(yard, "WD-1", "T3", "后端不要改，只改前端文案。")
