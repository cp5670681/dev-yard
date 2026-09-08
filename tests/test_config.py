from pathlib import Path

import pytest

from dev_yard.config import git_project_name, load_repos
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
