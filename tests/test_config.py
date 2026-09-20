from pathlib import Path

import pytest

from dev_yard.config import (
    DevSettings,
    GitSettings,
    PiSettings,
    StageModel,
    git_project_name,
    load_dev_settings,
    load_git_settings,
    load_pi_settings,
    load_repos,
    render_freeze_branch,
    resolve_freeze_branch,
    resolve_pi_choice,
    save_dev_settings,
    save_git_settings,
    save_pi_settings,
    ticket_branch_name,
)
from dev_yard.service import init_yard, repo_add


def test_relative_path_resolves_against_yard_root(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text(
        "repos:\n  backend:\n    url: git@x:y.git\n    default_base: main\n    path: ../srcbe\n"
    )
    repo = load_repos(yard)["backend"]
    assert repo.source_path(yard) == git_src.resolve()


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("git@host:leads-in/research.git", "research"),
        ("git@host:leads-in/research", "research"),
        ("https://git.example.com/leads-in/research.git", "research"),
        ("https://git.example.com/leads-in/research.git/", "research"),
        ("ssh://git@host/leads-in/research.git", "research"),
        ("/home/you/src/research-front", "research-front"),
    ],
)
def test_git_project_name(url: str, name: str):
    assert git_project_name(url) == name


def test_git_project_name_rejects_empty():
    with pytest.raises(ValueError, match="cannot derive alias"):
        git_project_name("git@host:")


def test_repo_add_accepts_equivalent_origin_url(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "be", str(git_src), "main", "be", None)
    again = repo_add(yard, "be", str(git_src).rstrip("/") + ".git", "main", "be", None)
    assert again.alias == "be"
    with pytest.raises(ValueError, match="origin"):
        repo_add(yard, "be", str(tmp_path / "other-repo.git"), "main", "be", None)


def test_repo_add_blank_alias_uses_project_name(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    repo = repo_add(
        yard,
        "",
        f"git@host:leads-in/{git_src.name}.git",
        "main",
        "be",
        str(git_src),
    )
    assert repo.alias == git_src.name
    assert git_src.name in load_repos(yard)


def test_save_repos_keeps_pi_section(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    save_pi_settings(
        yard,
        PiSettings(provider="rcc", model="MiniMax-M3", stages={"grill": StageModel(model="gpt-5")}),
    )
    repo_add(yard, "be", str(git_src), "main", "be", str(git_src))
    pi = load_pi_settings(yard)
    assert pi.provider == "rcc"
    assert pi.model == "MiniMax-M3"
    assert pi.stages["grill"].model == "gpt-5"


def test_resolve_pi_choice_stage_beats_env(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setenv("YARD_PI_PROVIDER", "env-p")
    monkeypatch.setenv("YARD_PI_MODEL", "env-m")
    save_pi_settings(
        yard,
        PiSettings(
            provider="yaml-p",
            model="yaml-m",
            stages={"implement": StageModel(provider="stage-p", model="stage-m")},
        ),
    )
    assert resolve_pi_choice(yard, "implement") == ("stage-p", "stage-m")
    assert resolve_pi_choice(yard, "grill") == ("yaml-p", "yaml-m")


def test_resolve_pi_choice_falls_back_to_env(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.setenv("YARD_PI_PROVIDER", "rcc")
    monkeypatch.setenv("YARD_PI_MODEL", "MiniMax-M3")
    assert resolve_pi_choice(yard, "review") == ("rcc", "MiniMax-M3")


def test_resolve_pi_choice_skips_incomplete_pair(tmp_path: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    save_pi_settings(
        yard,
        PiSettings(
            provider="yaml-p",
            model="yaml-m",
            stages={"grill": StageModel(model="only-model")},
        ),
    )
    assert resolve_pi_choice(yard, "grill") == ("yaml-p", "yaml-m")


def test_resolve_implement_repo_pair_beats_stage(tmp_path: Path, git_src: Path, monkeypatch):
    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    save_pi_settings(
        yard,
        PiSettings(
            provider="yaml-p",
            model="yaml-m",
            stages={"implement": StageModel(provider="stage-p", model="stage-m")},
        ),
    )
    repo_add(
        yard,
        "be",
        str(git_src),
        "main",
        "be",
        str(git_src),
        provider="repo-p",
        model="repo-m",
    )
    assert resolve_pi_choice(yard, "implement", repo="be") == ("repo-p", "repo-m")
    assert resolve_pi_choice(yard, "implement") == ("stage-p", "stage-m")
    assert resolve_pi_choice(yard, "review", repo="be") == ("yaml-p", "yaml-m")
    assert resolve_pi_choice(yard, "contract", repo="be") == ("yaml-p", "yaml-m")


def test_repo_incomplete_pair_rejected(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(ValueError, match="together"):
        repo_add(yard, "be", str(git_src), "main", "be", str(git_src), provider="rcc")


def test_cli_repo_set_model(tmp_path: Path, git_src: Path, monkeypatch):
    from typer.testing import CliRunner

    from dev_yard.cli import app

    yard = tmp_path / "yard"
    init_yard(yard)
    repo_add(yard, "be", str(git_src), "main", "be", str(git_src))
    monkeypatch.chdir(yard)

    runner = CliRunner()
    # Bad: incomplete pair
    res_bad = runner.invoke(app, ["repo", "set-model", "be", "--provider", "rcc"])
    assert res_bad.exit_code != 0

    # Good: set pair
    res_ok = runner.invoke(app, ["repo", "set-model", "be", "--provider", "rcc", "--model", "glm-5.3"])
    assert res_ok.exit_code == 0
    assert "be implement rcc/glm-5.3" in res_ok.output

    # Good: repo list displays provider/model
    res_list = runner.invoke(app, ["repo", "list"])
    assert "rcc/glm-5.3" in res_list.output

    # Good: clear pair (inherit)
    res_clear = runner.invoke(app, ["repo", "set-model", "be"])
    assert res_clear.exit_code == 0
    assert "be implement (inherit)" in res_clear.output


def test_git_settings_default_and_roundtrip(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    assert load_git_settings(yard).freeze_branch == "req/{jira}"
    assert render_freeze_branch("req/{jira}", "AB-1") == "req/AB-1"
    assert ticket_branch_name("req/AB-1", "T1") == "req/AB-1-T1"
    save_git_settings(yard, GitSettings(freeze_branch="feature/{jira}"))
    assert load_git_settings(yard).freeze_branch == "feature/{jira}"
    save_pi_settings(yard, PiSettings(provider="rcc", model="glm-5.3"))
    assert load_git_settings(yard).freeze_branch == "feature/{jira}"
    save_git_settings(yard, GitSettings(freeze_branch="req/{jira}"))
    text = (yard / "repos.yaml").read_text(encoding="utf-8")
    assert "freeze_branch" not in text
    assert "pi:" in text


def test_git_settings_rejects_bad_template(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    with pytest.raises(ValueError, match=r"\{jira\}"):
        save_git_settings(yard, GitSettings(freeze_branch="req/fixed"))
    with pytest.raises(ValueError, match="unknown placeholders"):
        save_git_settings(yard, GitSettings(freeze_branch="req/{jira}/{ticket}"))


def test_load_git_settings_blank_section(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text("repos: {}\ngit:\n", encoding="utf-8")
    assert load_git_settings(yard).freeze_branch == "req/{jira}"


def test_resolve_freeze_branch_prefers_status(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    save_git_settings(yard, GitSettings(freeze_branch="feature/{jira}"))
    assert resolve_freeze_branch(yard, "AB-1") == "feature/AB-1"
    assert resolve_freeze_branch(yard, "AB-1", {"branch": "req/AB-1"}) == "req/AB-1"


def test_dump_workspace_preserves_unicode(tmp_path: Path):
    from dev_yard.config import dump_workspace
    yard = tmp_path / "yard"
    init_yard(yard)
    dump_workspace(yard, {"repos": {"be": {"role": "后端", "summary": "中文测试"}}})
    content = (yard / "repos.yaml").read_text(encoding="utf-8")
    assert "后端" in content
    assert "\\u" not in content


def test_dev_settings_default_tdd_is_true(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    settings = load_dev_settings(yard)
    assert settings.tdd is True


def test_dev_settings_load_from_dev_section(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text("repos: {}\ndev:\n  tdd: false\n", encoding="utf-8")
    assert load_dev_settings(yard).tdd is False


def test_dev_settings_reject_non_mapping_dev(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text("repos: {}\ndev: nope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dev must be a mapping"):
        load_dev_settings(yard)


def test_dev_settings_reject_non_bool_tdd(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text('repos: {}\ndev:\n  tdd: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="dev.tdd must be a boolean"):
        load_dev_settings(yard)


def test_dev_settings_save_and_reload(tmp_path: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    save_dev_settings(yard, DevSettings(tdd=False))
    assert "tdd: false" in (yard / "repos.yaml").read_text(encoding="utf-8")
    assert load_dev_settings(yard).tdd is False
    save_dev_settings(yard, DevSettings(tdd=True))
    assert "dev:" not in (yard / "repos.yaml").read_text(encoding="utf-8")
    assert load_dev_settings(yard).tdd is True


def test_cli_tdd_command(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from dev_yard.cli import app

    yard = tmp_path / "yard"
    init_yard(yard)
    monkeypatch.chdir(yard)
    runner = CliRunner()

    # Status (default: on)
    res = runner.invoke(app, ["tdd"])
    assert res.exit_code == 0
    assert "tdd: on" in res.output

    # Turn off
    res = runner.invoke(app, ["tdd", "off"])
    assert res.exit_code == 0
    assert "tdd set to off" in res.output
    assert load_dev_settings(yard).tdd is False

    # Check status
    res = runner.invoke(app, ["tdd", "status"])
    assert res.exit_code == 0
    assert "tdd: off" in res.output

    # Turn on
    res = runner.invoke(app, ["tdd", "on"])
    assert res.exit_code == 0
    assert "tdd set to on" in res.output
    assert load_dev_settings(yard).tdd is True

    # Bad argument
    res = runner.invoke(app, ["tdd", "invalid"])
    assert res.exit_code != 0
