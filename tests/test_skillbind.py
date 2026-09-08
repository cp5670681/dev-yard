from pathlib import Path

from dev_yard.runners import pi_argv
from dev_yard.skillbind import load_skill, session_prompt


def test_load_missing_skill(tmp_path: Path):
    text = load_skill(tmp_path, "grill")
    assert "not found" in text


def test_session_prompt_includes_jira(tmp_path: Path):
    p = session_prompt(tmp_path, "spec", "AB-1")
    assert "AB-1" in p
    assert "to-spec" in p


def test_pi_argv_binds_skills():
    root = Path("/home/chengpeng/pythonProjects/dev-yard")
    argv = pi_argv(root=root, bundle="grill", prompt="go", binary="pi")
    assert argv[0] == "pi"
    assert "--approve" in argv
    assert "--skill" in argv
    assert "--append-system-prompt" in argv
    joined = " ".join(argv)
    assert ".pi/skills" in joined
    assert "grill-with-docs" in joined
    assert "grilling" in joined
    assert argv[-1] == "go"


def test_pi_argv_print_mode():
    root = Path("/home/chengpeng/pythonProjects/dev-yard")
    argv = pi_argv(root=root, bundle="review", prompt="r", print_mode=True, binary="pi")
    assert "-p" in argv
