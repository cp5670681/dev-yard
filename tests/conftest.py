import subprocess
from pathlib import Path

import pytest


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.check_call(["git", "init"], cwd=path)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=path)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=path)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=path)
    (path / "README").write_text("x")
    subprocess.check_call(["git", "add", "."], cwd=path)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=path)
    return path


@pytest.fixture
def git_src(tmp_path: Path) -> Path:
    return make_git_repo(tmp_path / "srcbe")


SPA_INDEX = Path(__file__).resolve().parents[1] / "src" / "dev_yard" / "web" / "spa" / "index.html"

needs_spa = pytest.mark.skipif(
    not SPA_INDEX.is_file(),
    reason="frontend not built; run: pnpm --dir web build",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if SPA_INDEX.is_file():
        return
    skip = pytest.mark.skip(reason="frontend not built; run: pnpm --dir web build")
    for item in items:
        if item.get_closest_marker("spa"):
            item.add_marker(skip)
