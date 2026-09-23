import subprocess
from pathlib import Path

from dev_yard.config import DevSettings, save_dev_settings
from dev_yard.runners import assistant_pi_argv, pi_argv, run_pi_print
from dev_yard.service import init_yard
from dev_yard.skillbind import session_prompt


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


def test_pi_argv_loads_safety_extension(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="implement", prompt="go", binary="pi")
    assert "--extension" in argv
    guard = Path(argv[argv.index("--extension") + 1])
    assert guard.is_file()
    assert guard.name == "yard-guard.ts"
    assert "/mnt" in guard.read_text()
    assert argv[-1] == "go"


def test_pi_argv_guard_survives_bare_workspace(tmp_path: Path, monkeypatch):
    """A workspace without .pi/extensions still gets the packaged guard."""
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    (tmp_path / "repos.yaml").write_text("repos: {}\n")
    argv = pi_argv(root=tmp_path, bundle="review", prompt="r", binary="pi")
    guard = Path(argv[argv.index("--extension") + 1])
    assert guard.is_file()
    assert guard.name == "yard-guard.ts"


def test_assistant_pi_argv_loads_safety_extension(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    (tmp_path / "repos.yaml").write_text("repos: {}\n")
    argv = assistant_pi_argv(
        root=tmp_path, session_id="s", session_dir=tmp_path / "sessions"
    )
    assert "--extension" in argv
    assert Path(argv[argv.index("--extension") + 1]).name == "yard-guard.ts"


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


def test_run_pi_print_sends_prompt_on_stdin(tmp_path, monkeypatch):
    class FakeStdin:
        def __init__(self):
            self.written = ""

        def write(self, data):
            self.written += data

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = iter(["ok\n"])

        def wait(self):
            return 0

    proc = FakeProc()
    captured = {}

    def fake_popen(*a, **k):
        captured["argv"] = a[0]
        captured["stdin"] = k.get("stdin")
        return proc

    monkeypatch.setattr("dev_yard.runners.subprocess.Popen", fake_popen)
    prompt = "x" * 10000
    code, raw = run_pi_print(["pi", "-p"], tmp_path, prompt)
    assert code == 0
    assert raw == "ok\n"
    assert captured["argv"] == ["pi", "-p"]
    assert captured["stdin"] is subprocess.PIPE
    assert proc.stdin.written == prompt
    assert proc.stdin.closed


def test_pi_argv_print_mode_can_omit_prompt(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="contract", prompt=None, print_mode=True, binary="pi")
    assert argv[-1] == "-p"
    assert "huge" not in argv


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
    p_contract = session_prompt(Path("/tmp"), "contract", "AB-1")
    assert "SPEC.md" in p_contract
    assert "code-review" in p_contract


def test_pi_argv_contract_bundle(monkeypatch):
    monkeypatch.delenv("YARD_PI_PROVIDER", raising=False)
    monkeypatch.delenv("YARD_PI_MODEL", raising=False)
    root = Path(__file__).resolve().parents[1]
    argv = pi_argv(root=root, bundle="contract", prompt="c", print_mode=True, binary="pi")
    assert "-p" in argv
    tools = argv[argv.index("--tools") + 1]
    assert "read" in tools
    assert "grep" in tools
    assert "edit" not in tools
    assert "bash" not in tools
    joined = " ".join(argv)
    assert "code-review" in joined


def test_implement_prompt_keeps_context_out_of_worktree():
    p = session_prompt(Path("/tmp"), "implement", "AB-1")
    assert "Do not add CONTEXT.md or docs/adr to the business repo" in p
    assert "SPEC.md" in p
    assert "TICKETS.md" in p


def test_tickets_prompt_starts_at_spec():
    p = session_prompt(Path("/tmp"), "tickets", "AB-1")
    spec = Path("/tmp") / "reqs" / "AB-1" / "SPEC.md"
    assert f"starting with {spec}" in p
    assert "REQUIREMENT.md" not in p


def test_implement_prompt_tdd_enabled_by_default(tmp_path: Path):
    init_yard(tmp_path)
    p = session_prompt(tmp_path, "implement", "AB-1")
    assert "TDD mode is OFF" not in p


def test_implement_prompt_tdd_disabled(tmp_path: Path):
    init_yard(tmp_path)
    save_dev_settings(tmp_path, DevSettings(tdd=False))
    p = session_prompt(tmp_path, "implement", "AB-1")
    assert "TDD mode is OFF (dev.tdd=false)" in p
    assert "Do NOT write test files" in p


def test_review_prompt_tdd_disabled(tmp_path: Path):
    init_yard(tmp_path)
    save_dev_settings(tmp_path, DevSettings(tdd=False))
    p = session_prompt(tmp_path, "review", "AB-1")
    assert "TDD mode is OFF (dev.tdd=false)" in p
    assert "Do NOT require test files" in p



def test_resolve_merge_prompt_follows_tdd(tmp_path: Path):
    from dev_yard.skillbind import session_prompt_for
    from dev_yard.stages import RESOLVE_MERGE_SPEC

    yard = tmp_path / "yard"
    init_yard(yard)
    prompt = session_prompt_for(RESOLVE_MERGE_SPEC, yard, "AB-1")
    assert "TDD mode is ON" in prompt
    save_dev_settings(yard, DevSettings(tdd=False))
    prompt = session_prompt_for(RESOLVE_MERGE_SPEC, yard, "AB-1")
    assert "TDD mode is OFF" in prompt


def test_prompt_omits_uploads_hint_when_none(tmp_path: Path):
    from dev_yard.service import req_open

    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-1", source="none")
    p = session_prompt(yard, "spec", "AB-1")
    assert "uploads/" not in p
    assert "Human-added attachments" not in p


def test_downstream_prompts_list_uploads(tmp_path: Path):
    from dev_yard.service import req_attach, req_open

    yard = tmp_path / "yard"
    init_yard(yard)
    req_open(yard, "AB-1", source="none")
    src = tmp_path / "原型.html"
    src.write_text("<html>proto</html>")
    req_attach(yard, "AB-1", [src])

    for stage in ("grill", "spec", "tickets", "implement", "contract", "review"):
        p = session_prompt(yard, stage, "AB-1")
        assert "Human-added attachments" in p, stage
        assert "Never delete or modify anything under uploads/" in p, stage
        assert str(yard / "reqs" / "AB-1" / "uploads" / "原型.html") in p, stage

