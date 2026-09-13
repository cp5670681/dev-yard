from pathlib import Path

from typer.testing import CliRunner

from dev_yard.cli import app

runner = CliRunner()


def _workspace(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _plugin(root: Path, name: str = "deploy") -> Path:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(
        "name: deploy\ntools: [read]\n", encoding="utf-8"
    )
    (d / "SKILL.md").write_text("# deploy\n", encoding="utf-8")
    (root / "yard.yaml").write_text("plugins: [plugins/deploy]\n", encoding="utf-8")
    return d


def test_stages_lists_builtin(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    out = runner.invoke(app, ["stages"])
    assert out.exit_code == 0
    assert "grill" in out.stdout
    assert "[builtin]" in out.stdout
    assert "review-override" not in out.stdout  # 保留字不是阶段


def test_stages_lists_plugin(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    _plugin(root)
    out = runner.invoke(app, ["stages"])
    assert out.exit_code == 0
    assert "deploy" in out.stdout
    assert "plugins/deploy" in out.stdout


def test_run_unknown_stage_fails(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    out = runner.invoke(app, ["run", "nope", "J-1"])
    assert out.exit_code == 1
    assert "unknown stage" in out.output


def test_run_builtin_dry_run(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    d = root / "reqs" / "J-1"
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    out = runner.invoke(app, ["run", "spec", "J-1", "--dry-run"])
    assert out.exit_code == 0
    assert out.output.strip() != ""


def test_run_plugin_dry_run(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    _plugin(root)
    d = root / "reqs" / "J-1"
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    out = runner.invoke(app, ["run", "deploy", "J-1", "--dry-run"])
    assert out.exit_code == 0
    assert "dry-run" in out.output


def test_grill_spec_tickets_are_thin_wrappers(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    d = root / "reqs" / "J-1"
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    for cmd in ("grill", "spec", "tickets"):
        out = runner.invoke(app, [cmd, "J-1", "--dry-run"])
        assert out.exit_code == 0, f"{cmd}: {out.output}"
