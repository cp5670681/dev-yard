"""Web export / import-bundle endpoints (requirement page 导出 + 外部导入)."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dev_yard.service import init_yard, req_open
from dev_yard.web.app import create_app


def _client(yard: Path) -> TestClient:
    return TestClient(create_app(yard, sync_jobs=True))


@pytest.fixture(autouse=True)
def _no_jira_env(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)


def _yard(tmp_path: Path) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-9", source="none")
    return yard


def _export(client: TestClient, jira: str, **opts) -> str:
    """Run the export job (sync) and return its id."""
    r = client.post(f"/api/requirements/{jira}/export", json=opts)
    assert r.status_code == 200, r.text
    job = r.json()["jobs"][0]
    assert job["state"] == "ok", job
    return job["id"]


def test_export_download_and_import_bundle_round_trip(tmp_path: Path):
    yard = _yard(tmp_path)
    client = _client(yard)

    # Export runs as a sync job; the download is pinned to that exact job.
    job_id = _export(client, "AB-9")
    dl = client.get(f"/api/jobs/{job_id}/export/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/gzip"
    archive = tmp_path / "AB-9-bundle.tar.gz"
    archive.write_bytes(dl.content)
    assert tarfile.is_tarfile(archive)

    # Restore into a fresh workspace through the upload endpoint.
    yard2 = tmp_path / "yard2"
    init_yard(yard2)
    client2 = _client(yard2)
    with archive.open("rb") as fh:
        r = client2.post(
            "/api/requirements/import-bundle",
            files={"file": ("AB-9-bundle.tar.gz", fh, "application/gzip")},
            data={"force": "false"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["jira"] == "AB-9"
    assert r.json()["jobs"][0]["state"] == "ok"
    assert client2.get("/api/requirements/AB-9").status_code == 200


def test_export_download_is_bound_to_its_job(tmp_path: Path):
    yard = _yard(tmp_path)
    client = _client(yard)
    _export(client, "AB-9")

    # A non-export job never exposes an export artifact.
    other = client.post("/api/requirements/AB-9/actions/reset-phase", json={})
    other_id = other.json()["jobs"][0]["id"]
    assert client.get(f"/api/jobs/{other_id}/export/download").status_code == 409

    # A fresh export keeps its own file downloadable.
    job2 = _export(client, "AB-9")
    assert client.get(f"/api/jobs/{job2}/export/download").status_code == 200


def test_import_bundle_refuses_existing_without_force(tmp_path: Path):
    yard = _yard(tmp_path)
    client = _client(yard)
    job_id = _export(client, "AB-9")
    dl = client.get(f"/api/jobs/{job_id}/export/download")
    assert dl.status_code == 200

    r = client.post(
        "/api/requirements/import-bundle",
        files={"file": ("AB-9-bundle.tar.gz", dl.content, "application/gzip")},
        data={"force": "false"},
    )
    assert r.status_code == 409
    assert "强制覆盖" in r.json()["detail"]


def test_import_bundle_rejects_garbage(tmp_path: Path):
    yard = _yard(tmp_path)
    client = _client(yard)
    r = client.post(
        "/api/requirements/import-bundle",
        files={"file": ("bundle.tar.gz", b"not a tarball", "application/gzip")},
        data={"force": "false"},
    )
    assert r.status_code == 400
    assert "无法读取 bundle" in r.json()["detail"]


def test_export_download_404_unknown_job(tmp_path: Path):
    client = _client(_yard(tmp_path))
    assert client.get("/api/jobs/nope/export/download").status_code == 404
