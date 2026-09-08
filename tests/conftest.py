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
