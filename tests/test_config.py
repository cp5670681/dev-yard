from pathlib import Path

import pytest

from dev_yard.config import (
    PiSettings,
    StageModel,
    git_project_name,
    load_pi_settings,
    load_repos,
    resolve_pi_choice,
    save_pi_settings,
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
