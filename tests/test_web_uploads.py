from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard import attachments
from dev_yard.service import init_yard, req_open
from dev_yard.web.app import create_app


def _client(yard: Path) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True))


def _opened(tmp_path: Path) -> tuple[Path, TestClient]:
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-1", source="none")
    return yard, _client(yard)


def test_upload_list_download_delete(tmp_path: Path):
    yard, client = _opened(tmp_path)
    r = client.post(
        "/api/requirements/AB-1/uploads",
        files=[("files", ("2026-09-21-原型.html", b"<html>proto</html>", "text/html"))],
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["2026-09-21-原型.html"]

    detail = client.get("/api/requirements/AB-1").json()
    assert detail["uploads"] == ["2026-09-21-原型.html"]

    got = client.get("/r/AB-1/uploads/2026-09-21-原型.html")
    assert got.status_code == 200
    assert got.content == b"<html>proto</html>"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers.get("content-security-policy", "")

    doc = (yard / "reqs" / "AB-1" / "REQUIREMENT.md").read_text()
    assert "## 补充附件" in doc

    d = client.delete("/api/requirements/AB-1/uploads/2026-09-21-原型.html")
    assert d.status_code == 200
    assert d.json()["uploads"] == []


def test_upload_sanitizes_traversal_name(tmp_path: Path):
    _, client = _opened(tmp_path)
    r = client.post(
        "/api/requirements/AB-1/uploads",
        files=[("files", ("../../evil.html", b"x", "text/html"))],
    )
    assert r.status_code == 200
    assert r.json()["added"] == ["evil.html"]


def test_download_rejects_traversal(tmp_path: Path):
    _, client = _opened(tmp_path)
    r = client.get("/r/AB-1/uploads/..%2FREQUIREMENT.md")
    assert r.status_code == 404


def test_upload_rejects_empty(tmp_path: Path):
    _, client = _opened(tmp_path)
    r = client.post(
        "/api/requirements/AB-1/uploads",
        files=[("files", ("empty.html", b"", "text/html"))],
    )
    assert r.status_code == 400


def test_upload_rejects_oversize(tmp_path: Path, monkeypatch):
    _, client = _opened(tmp_path)
    monkeypatch.setattr(attachments, "MAX_BYTES", 4)
    r = client.post(
        "/api/requirements/AB-1/uploads",
        files=[("files", ("big.bin", b"12345", "application/octet-stream"))],
    )
    assert r.status_code == 400
    assert "too large" in r.json()["detail"]


def test_upload_unknown_requirement_404(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    client = _client(yard)
    r = client.post(
        "/api/requirements/NOPE/uploads",
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 404


@pytest.mark.parametrize("bad", ["../REQUIREMENT.md", "REQUIREMENT.md"])
def test_delete_unknown_or_traversal_404(tmp_path: Path, bad: str):
    _, client = _opened(tmp_path)
    r = client.delete(f"/api/requirements/AB-1/uploads/{bad}")
    assert r.status_code in (400, 404)
