from pathlib import Path
from typer.testing import CliRunner

from dev_yard import __version__
from dev_yard.cli import app
from dev_yard.service import init_yard
from dev_yard.web.app import create_app
from fastapi.testclient import TestClient

runner = CliRunner()


def test_version_string():
    assert __version__ == "0.1.0"


def test_cli_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "dev-yard 0.1.0" in result.stdout

    result_v = runner.invoke(app, ["-V"])
    assert result_v.exit_code == 0
    assert "dev-yard 0.1.0" in result_v.stdout


def test_api_meta_version(tmp_path: Path):
    init_yard(tmp_path)
    client = TestClient(create_app(tmp_path))
    res = client.get("/api/meta")
    assert res.status_code == 200
    data = res.json()
    assert data.get("version") == "0.1.0"
