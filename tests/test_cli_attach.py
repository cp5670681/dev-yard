from pathlib import Path

from typer.testing import CliRunner

from dev_yard.cli import app
from dev_yard.service import init_yard, req_open

runner = CliRunner()


def _yard(tmp_path: Path, monkeypatch) -> Path:
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    req_open(yard, "AB-1", source="none")
    return yard


def test_req_attach_and_detach_cli(tmp_path: Path, monkeypatch):
    yard = _yard(tmp_path, monkeypatch)
    src = tmp_path / "2026-09-21-原型.html"
    src.write_text("<html>proto</html>")

    out = runner.invoke(app, ["req", "attach", "AB-1", str(src)])
    assert out.exit_code == 0, out.output
    assert "attached AB-1/uploads/2026-09-21-原型.html" in out.stdout
    assert (yard / "reqs" / "AB-1" / "uploads" / "2026-09-21-原型.html").exists()

    out = runner.invoke(app, ["req", "detach", "AB-1", "2026-09-21-原型.html"])
    assert out.exit_code == 0, out.output
    assert "(none)" in out.stdout


def test_req_attach_missing_file_fails(tmp_path: Path, monkeypatch):
    _yard(tmp_path, monkeypatch)
    out = runner.invoke(app, ["req", "attach", "AB-1", str(tmp_path / "nope.html")])
    assert out.exit_code == 1
    assert "not found" in out.output
