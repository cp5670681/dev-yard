from pathlib import Path

import pytest

from dev_yard import attachments
from dev_yard.runners import Runner, RunResult
from dev_yard.service import init_yard, req_attach, req_detach, req_open


def _yard(tmp_path: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    return yard


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_safe_name_keeps_unicode_strips_paths():
    assert attachments.safe_name("2026-09-21-添加项目类别原型.html") == (
        "2026-09-21-添加项目类别原型.html"
    )
    assert attachments.safe_name("../../etc/passwd") == "passwd"
    assert attachments.safe_name("a\\b\\c.png") == "c.png"
    assert attachments.safe_name(".hidden") == "hidden"
    with pytest.raises(ValueError):
        attachments.safe_name("..")
    with pytest.raises(ValueError):
        attachments.safe_name("")


def test_add_list_resolve_remove(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    stored = attachments.add_bytes(yard, "AB-1", "原型.html", b"<html>hi</html>")
    assert stored == "原型.html"
    assert attachments.list_names(yard, "AB-1") == ["原型.html"]
    assert attachments.resolve(yard, "AB-1", "原型.html").read_bytes() == b"<html>hi</html>"
    attachments.remove(yard, "AB-1", "原型.html")
    assert attachments.list_names(yard, "AB-1") == []


def test_duplicate_names_get_suffixed(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    assert attachments.add_bytes(yard, "AB-1", "a.png", b"1") == "a.png"
    assert attachments.add_bytes(yard, "AB-1", "a.png", b"2") == "a-1.png"
    assert attachments.add_bytes(yard, "AB-1", "a.png", b"3") == "a-2.png"


def test_resolve_rejects_traversal(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    with pytest.raises((ValueError, FileNotFoundError)):
        attachments.resolve(yard, "AB-1", "../REQUIREMENT.md")


def test_size_limit(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    monkeypatch.setattr(attachments, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="too large"):
        attachments.add_bytes(yard, "AB-1", "big.bin", b"12345")


def test_list_images_assets_then_uploads_capped(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-1", source="none")
    assets = d / "assets" / "669971526"
    assets.mkdir(parents=True)
    (assets / "b.png").write_bytes(b"x")
    (assets / "a.jpg").write_bytes(b"x")
    (assets / "notes.txt").write_text("not an image")
    attachments.add_bytes(yard, "AB-1", "shot.PNG", b"x")
    attachments.add_bytes(yard, "AB-1", "spec.md", b"x")

    got = [p.name for p in attachments.list_images(yard, "AB-1")]
    assert got == ["a.jpg", "b.png", "shot.PNG"]

    monkeypatch.setattr(attachments, "MAX_PROMPT_IMAGES", 2)
    assert len(attachments.list_images(yard, "AB-1")) == 2


def test_list_images_empty_when_none(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    assert attachments.list_images(yard, "AB-1") == []


def test_with_images_appends_requirement_images(tmp_path: Path):
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-1", source="none")
    assets = d / "assets" / "669971526"
    assets.mkdir(parents=True)
    shot = assets / "entry1.png"
    shot.write_bytes(b"x")
    base = [d / "SPEC.md"]
    got = attachments.with_images(yard, "AB-1", base)
    assert got[0] == base[0]
    assert shot in got


def test_req_attach_writes_uploads_and_doc_section(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    src = tmp_path / "2026-09-21-添加项目类别原型.html"
    src.write_text("<html>proto</html>")

    added = req_attach(yard, "AB-1", [src])
    assert added == ["2026-09-21-添加项目类别原型.html"]
    req = yard / "reqs" / "AB-1"
    assert (req / "uploads" / added[0]).read_text() == "<html>proto</html>"

    doc = (req / "REQUIREMENT.md").read_text()
    assert "## 补充附件" in doc
    assert "uploads/2026-09-21-%E6%B7%BB%E5%8A%A0%E9%A1%B9%E7%9B%AE%E7%B1%BB%E5%88%AB%E5%8E%9F%E5%9E%8B.html" in doc


def test_req_attach_is_idempotent(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    src = tmp_path / "note.md"
    src.write_text("x")
    req_attach(yard, "AB-1", [src])
    doc_once = (yard / "reqs" / "AB-1" / "REQUIREMENT.md").read_text()
    req_attach(yard, "AB-1", [src])
    doc_twice = (yard / "reqs" / "AB-1" / "REQUIREMENT.md").read_text()
    assert doc_once == doc_twice


def test_req_detach_refreshes_section(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    src = tmp_path / "note.md"
    src.write_text("x")
    req_attach(yard, "AB-1", [src])
    remaining = req_detach(yard, "AB-1", ["note.md"])
    assert remaining == []
    doc = (yard / "reqs" / "AB-1" / "REQUIREMENT.md").read_text()
    assert "## 补充附件" not in doc


def test_uploads_survive_pi_reopen(tmp_path: Path):
    """The whole point: `req open --force` wipes assets/ but never uploads/."""
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-1", source="none")
    (d / "assets").mkdir(parents=True, exist_ok=True)
    (d / "assets" / "stale.png").write_text("old")
    req_attach(yard, "AB-1", [_write(tmp_path / "proto.html", "<html>keep</html>")])

    class Rewrites(Runner):
        def start(self, prompt, cwd, extra_read_paths):
            (d / "REQUIREMENT.md").write_text("# AB-1\n\nrefetched\n")
            return RunResult(ok=True, summary="ok", exit_code=0)

    req_open(yard, "AB-1", source="pi", force=True, runner=Rewrites())
    assert (d / "uploads" / "proto.html").read_text() == "<html>keep</html>"
    assert not (d / "assets" / "stale.png").exists()
    # the re-extract rewrote REQUIREMENT.md, but the reference is re-injected
    doc = (d / "REQUIREMENT.md").read_text()
    assert "refetched" in doc
    assert "## 补充附件" in doc
    assert "uploads/proto.html" in doc


def test_open_agent_cannot_drop_uploads(tmp_path: Path):
    """Even if the open agent deletes uploads/, the host restores it."""
    yard = _yard(tmp_path)
    d, _ = req_open(yard, "AB-1", source="none")
    req_attach(yard, "AB-1", [_write(tmp_path / "proto.html", "<html>keep</html>")])

    class Deletes(Runner):
        def start(self, prompt, cwd, extra_read_paths):
            (d / "REQUIREMENT.md").write_text("# AB-1\n\nrefetched\n")
            (d / "uploads" / "proto.html").unlink()
            (d / "uploads").rmdir()
            return RunResult(ok=True, summary="ok", exit_code=0)

    req_open(yard, "AB-1", source="pi", force=True, runner=Deletes())
    assert (d / "uploads" / "proto.html").read_text() == "<html>keep</html>"


def test_sync_doc_rewrites_and_clears(tmp_path: Path):
    yard = _yard(tmp_path)
    req_open(yard, "AB-1", source="none")
    req = yard / "reqs" / "AB-1"
    assert attachments.sync_doc(yard, "AB-1") is True  # normalizes; nothing to list
    assert "## 补充附件" not in (req / "REQUIREMENT.md").read_text()
    assert attachments.sync_doc(yard, "AB-1") is False  # idempotent once clean
    attachments.add_bytes(yard, "AB-1", "a.png", b"x")
    assert attachments.sync_doc(yard, "AB-1") is True
    assert "## 补充附件" in (req / "REQUIREMENT.md").read_text()
    assert attachments.sync_doc(yard, "AB-1") is False  # idempotent
    attachments.remove(yard, "AB-1", "a.png")
    assert attachments.sync_doc(yard, "AB-1") is True
    assert "## 补充附件" not in (req / "REQUIREMENT.md").read_text()
