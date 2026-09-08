from pathlib import Path

from dev_yard.config import load_repos
from dev_yard.service import init_yard


def test_relative_path_resolves_against_yard_root(tmp_path: Path, git_src: Path):
    yard = tmp_path / "yard"
    init_yard(yard)
    (yard / "repos.yaml").write_text(
        "repos:\n  backend:\n    url: git@x:y.git\n    default_base: main\n    path: ../srcbe\n"
    )
    repo = load_repos(yard)["backend"]
    assert repo.source_path(yard) == git_src.resolve()
