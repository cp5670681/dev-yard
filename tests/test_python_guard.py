import pytest

from dev_yard.guards.command_guard import (
    dangerous_search_target,
    is_dangerous_path,
    is_image_path,
    is_pure_search_command,
)
from test_yard_guard import BASH_CASES, IMAGE_CASES, PATH_CASES, PURE_CASES


@pytest.mark.parametrize(("command", "cwd", "blocked"), BASH_CASES)
def test_python_bash_guard(command: str, cwd: str, blocked: bool):
    hit = dangerous_search_target(command, cwd)
    assert (hit is not None) == blocked, f"cmd={command!r} hit={hit!r} expected_blocked={blocked}"


@pytest.mark.parametrize(("target", "dangerous"), PATH_CASES)
def test_python_path_guard(target: str, dangerous: bool):
    assert is_dangerous_path(target, ".") == dangerous


@pytest.mark.parametrize(("command", "pure"), PURE_CASES)
def test_python_pure_search(command: str, pure: bool):
    assert is_pure_search_command(command) == pure


@pytest.mark.parametrize(("target", "is_image"), IMAGE_CASES)
def test_python_image_guard(target: str, is_image: bool):
    assert is_image_path(target) == is_image
