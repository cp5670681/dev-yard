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
    assert "Write only SPEC.md" in p


def test_grill_prompt_forbids_spec():
    p = session_prompt(Path("/tmp"), "grill", "AB-1")
    assert "Do not write SPEC.md" in p
    assert "WEB_GRILL_ROUND" not in p


def test_grill_prompt_puts_context_under_reqs():
    root = Path("/tmp")
    p = session_prompt(root, "grill", "AB-1")
    assert str(root / "reqs" / "CONTEXT.md") in p
    assert str(root / "reqs" / "docs" / "adr") in p
    assert "reqs/CONTEXT.md" in p
    assert "workspace-root" in p
    assert "freeze worktrees" in p


def test_pi_argv_implement_keeps_bash(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="implement", prompt="go", binary="pi")
    tools = argv[argv.index("--tools") + 1]
    assert "bash" in tools
    assert "edit" in tools


def test_pi_argv_binds_skills(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="grill", prompt="go", binary="pi")
    assert argv[0] == "pi"
    assert "--approve" in argv
    assert "--no-skills" in argv
    assert "--skill" in argv
    assert "--append-system-prompt" not in argv
    joined = " ".join(argv)
    assert ".pi/skills" in joined
    assert "grill-with-docs" in joined
    assert "grilling" in joined
    assert "@" not in joined
    assert argv[-1] == "go"
    assert "--tools" in argv
    tools = argv[argv.index("--tools") + 1]
    assert "edit" in tools
    assert "bash" not in tools


def test_pi_argv_open_includes_mcp(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="open", prompt="go", print_mode=True, binary="pi")
    assert "-p" in argv
    tools = argv[argv.index("--tools") + 1]
    assert "mcp" in tools
    assert "edit" in tools
    assert "bash" in tools
    joined = " ".join(argv)
    assert "fetch-requirement" in joined


def test_open_prompt():
    p = session_prompt(Path("/tmp"), "open", "AB-1")
    assert "AB-1" in p
    assert "fetch-requirement" in p
    assert "REQUIREMENT.md" in p


def test_pi_argv_print_mode(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="review", prompt="r", print_mode=True, binary="pi")
    assert "-p" in argv
    tools = argv[argv.index("--tools") + 1]
    assert "read" in tools
    assert "grep" in tools
    assert "edit" not in tools
    assert "bash" not in tools


def test_pi_argv_model_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("YARD_PI_PROVIDER", "rcc")
    monkeypatch.setenv("YARD_PI_MODEL", "MiniMax-M3")
    (tmp_path / "repos.yaml").write_text("repos: {}\n")
    argv = pi_argv(root=tmp_path, bundle="review", prompt="r", binary="pi")
    assert argv[argv.index("--provider") + 1] == "rcc"
    assert argv[argv.index("--model") + 1] == "MiniMax-M3"


def test_pi_argv_model_from_yaml_stage(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    (tmp_path / "repos.yaml").write_text(
        "repos: {}\n"
        "pi:\n"
        "  provider: rcc\n"
        "  model: MiniMax-M3\n"
        "  stages:\n"
        "    grill:\n"
        "      model: gpt-5\n"
    )
    grill = pi_argv(root=tmp_path, bundle="grill", prompt="g", binary="pi")
    spec = pi_argv(root=tmp_path, bundle="spec", prompt="s", binary="pi")
    assert grill[grill.index("--provider") + 1] == "rcc"
    assert grill[grill.index("--model") + 1] == "MiniMax-M3"
    assert spec[spec.index("--model") + 1] == "MiniMax-M3"


def test_pi_argv_implement_uses_repo_pair(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    (tmp_path / "repos.yaml").write_text(
        "repos:\n"
        "  backend:\n"
        "    url: git@x:y.git\n"
        "    provider: repo-p\n"
        "    model: repo-m\n"
        "pi:\n"
        "  provider: rcc\n"
        "  model: MiniMax-M3\n"
        "  stages:\n"
        "    implement:\n"
        "      provider: stage-p\n"
        "      model: stage-m\n"
        "    review:\n"
        "      provider: rev-p\n"
        "      model: rev-m\n"
    )
    impl = pi_argv(root=tmp_path, bundle="implement", prompt="g", binary="pi", repo="backend")
    review = pi_argv(root=tmp_path, bundle="review", prompt="r", binary="pi", repo="backend")
    assert impl[impl.index("--provider") + 1] == "repo-p"
    assert impl[impl.index("--model") + 1] == "repo-m"
    assert review[review.index("--provider") + 1] == "rev-p"
    assert review[review.index("--model") + 1] == "rev-m"


def test_review_prompt_starts_at_spec():
    p = session_prompt(Path("/tmp"), "review", "AB-1")
    assert "SPEC.md" in p
    assert "REQUIREMENT.md" not in p


def test_implement_prompt_keeps_context_out_of_worktree():
    p = session_prompt(Path("/tmp"), "implement", "AB-1")
    assert "Do not add CONTEXT.md or docs/adr to the business repo" in p
