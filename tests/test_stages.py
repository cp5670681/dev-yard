from pathlib import Path

import pytest
import yaml as _yaml

from dev_yard import stages
from dev_yard.runners import pi_argv


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    return tmp_path


def _make_skills(root: Path, names: list[str]) -> None:
    for n in names:
        d = root / ".pi" / "skills" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {n}\n---\n# {n}\n", encoding="utf-8")


EXPECTED_TOOLS = {
    "open": "read,bash,grep,find,ls,edit,write,mcp",
    "grill": "read,grep,find,ls,edit,write",
    "spec": "read,grep,find,ls,edit,write",
    "tickets": "read,grep,find,ls,edit,write",
    "implement": "read,bash,grep,find,ls,edit,write",
    "review": "read,grep,find,ls",
    "contract": "read,grep,find,ls",
    "qa-design": "read,bash,grep,find,ls,edit,write",
    "qa-run": "read,bash,grep,find,ls,edit,write",
}
EXPECTED_SKILLS = {
    "open": ["fetch-requirement"],
    "grill": ["grill-with-docs", "grilling", "domain-modeling"],
    "spec": ["to-spec"],
    "tickets": ["to-tickets"],
    "implement": ["implement", "tdd", "codebase-design"],
    "review": ["code-review"],
    "contract": ["code-review"],
    "qa-design": ["qa-design"],
    "qa-run": ["qa-run"],
}


def test_builtin_registry_fields(tmp_path):
    root = _workspace(tmp_path)
    reg = stages.load_registry(root)
    assert set(reg) == set(EXPECTED_TOOLS)
    for name, spec in reg.items():
        assert spec.builtin is True
        assert ",".join(spec.tools) == EXPECTED_TOOLS[name]
        assert list(spec.bundles) == EXPECTED_SKILLS[name]
        assert spec.skill == EXPECTED_SKILLS[name][0]
    assert reg["open"].protects == ("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml")
    assert reg["grill"].protects == ("REQUIREMENT.md", "SPEC.md", "TICKETS.md")
    assert reg["spec"].protects == ("REQUIREMENT.md", "GRILL.md", "TICKETS.md")
    assert reg["tickets"].protects == ("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml")
    assert reg["implement"].protects == ()
    assert reg["review"].protects == ()
    assert reg["contract"].protects == ()
    assert reg["open"].sets_phase == "open"
    assert reg["open"].requires_phase is None
    assert reg["grill"].lists_sources is True
    assert reg["spec"].lists_sources is True
    assert reg["tickets"].lists_sources is True
    assert reg["implement"].lists_sources is False
    assert reg["qa-design"].lists_sources is False
    assert reg["qa-run"].lists_sources is False
    assert reg["qa-design"].sets_phase is None
    assert reg["qa-run"].sets_phase is None
    assert reg["open"].order < reg["grill"].order < reg["spec"].order
    assert reg["spec"].order < reg["tickets"].order < reg["implement"].order


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_argv_equivalence_builtin(tmp_path, name):
    """Golden: builtin stage argv must stay identical across the registry refactor."""
    root = _workspace(tmp_path)
    _make_skills(root, sorted({n for v in EXPECTED_SKILLS.values() for n in v}))
    argv = pi_argv(root=root, bundle=name, prompt="(p)")
    assert argv[1:3] == ["--approve", "--no-skills"]
    tools = argv[argv.index("--tools") + 1]
    assert tools == EXPECTED_TOOLS[name]
    skill_args = [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"]
    assert [Path(p).name for p in skill_args] == EXPECTED_SKILLS[name]


def test_get_runner_forwards_explicit_pair(tmp_path):
    """An explicit pair (qa.yaml design) must land in the dry-run argv."""
    from dev_yard.runners import get_runner

    root = _workspace(tmp_path)
    _make_skills(root, EXPECTED_SKILLS["qa-design"])
    runner = get_runner(
        root,
        "qa-design",
        dry_run=True,
        print_mode=True,
        provider="rcc",
        model="glm-5.3",
    )
    assert runner.argv[runner.argv.index("--provider") + 1] == "rcc"
    assert runner.argv[runner.argv.index("--model") + 1] == "glm-5.3"


# ---- plugin loading ----


def _plugin(root: Path, name: str = "deploy", **over) -> Path:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "tools": ["read", "bash"],
        "requires_phase": "frozen",
    }
    meta.update(over)
    (d / "plugin.yaml").write_text(
        _yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
    )
    (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return d


def _enable(root: Path, *plugins: Path) -> None:
    root.joinpath("yard.yaml").write_text(
        _yaml.safe_dump({"plugins": [str(p.relative_to(root)) for p in plugins]}),
        encoding="utf-8",
    )


def test_registry_loads_plugin(tmp_path):
    root = _workspace(tmp_path)
    p = _plugin(root)
    _enable(root, p)
    reg = stages.load_registry(root)
    spec = reg["deploy"]
    assert spec is not None and spec.builtin is False
    assert spec.tools == ("read", "bash")
    assert spec.requires_phase == "frozen"
    assert spec.skill_dir == p
    assert spec.guidance == ""
    assert spec.title == ""


def test_plugin_overrides_builtin(tmp_path):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, name="review", tools=["read"]))
    spec = stages.load_registry(root)["review"]
    assert spec.builtin is False
    assert spec.tools == ("read",)
    assert spec.bundles == ()
    assert spec.skill == "review"
    assert spec.skill_dir == root / "plugins" / "review"
    assert spec.guidance == ""
    assert spec.protects == ()
    assert spec.lists_sources is False
    assert spec.title == ""


def test_duplicate_plugin_names_rejected(tmp_path):
    root = _workspace(tmp_path)
    a = _plugin(root, "deploy")
    b = root / "more" / "deploy"
    b.mkdir(parents=True)
    (b / "plugin.yaml").write_text("name: deploy\ntools: [read]\n", encoding="utf-8")
    (b / "SKILL.md").write_text("# d\n", encoding="utf-8")
    _enable(root, a, b)
    with pytest.raises(ValueError, match="deploy"):
        stages.load_registry(root)


@pytest.mark.parametrize(
    "over,match",
    [
        ({"name": "Init"}, "name"),
        ({"name": "web"}, "reserved"),
        ({"tools": ["teleport"]}, "tools"),
        ({"requires_phase": "alpha"}, "phase"),
        ({"requires_phase": "deployed"}, "phase"),
        ({"sets_phase": "deployed"}, "sets_phase"),
        ({"foo": 1}, "foo"),
        ({"require_phase": "frozen"}, "require_phase"),
        ({"tool": ["read"]}, "tool"),
        ({"tools": []}, "tools"),
        ({"protects": ["../CONTEXT.md"]}, "protects"),
        ({"bundles": ["Not-a-name"]}, "bundles"),
    ],
)
def test_plugin_validation_errors(tmp_path, over, match):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, **over))
    with pytest.raises(ValueError, match=match):
        stages.load_registry(root)


def test_plugin_requires_plugin_yaml_and_skill_md(tmp_path):
    root = _workspace(tmp_path)
    d = root / "plugins" / "empty"
    d.mkdir(parents=True)
    _enable(root, d)
    with pytest.raises(ValueError, match="plugin.yaml"):
        stages.load_registry(root)


def test_plugin_yaml_without_skill_md(tmp_path):
    root = _workspace(tmp_path)
    d = root / "plugins" / "noskill"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text("name: noskill\ntools: [read]\n", encoding="utf-8")
    _enable(root, d)
    with pytest.raises(ValueError, match="SKILL.md"):
        stages.load_registry(root)


def test_absolute_plugin_path_rejected(tmp_path):
    root = _workspace(tmp_path)
    p = _plugin(root)
    (root / "yard.yaml").write_text(
        _yaml.safe_dump({"plugins": [str(p.resolve())]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="relative"):
        stages.load_registry(root)


def test_unlisted_plugin_dir_ignored(tmp_path):
    root = _workspace(tmp_path)
    _plugin(root, "orphan")
    assert "orphan" not in stages.load_registry(root)


def test_plugin_cannot_invent_phase_via_sets_phase(tmp_path):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, "deploy", requires_phase=None, sets_phase="deployed"))
    with pytest.raises(ValueError, match="sets_phase"):
        stages.load_registry(root)


def test_plugin_cannot_require_invented_phase(tmp_path):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, "verify", requires_phase="deployed", tools=["read"]))
    with pytest.raises(ValueError, match="phase"):
        stages.load_registry(root)


def test_plugin_reads_description(tmp_path):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, "scan", description="只读自检", requires_phase=None))
    spec = stages.load_registry(root)["scan"]
    assert spec.description == "只读自检"


def test_missing_yard_yaml_means_builtin_only(tmp_path):
    root = _workspace(tmp_path)
    assert set(stages.load_registry(root)) == set(stages.BUILTIN_STAGES)


def test_plugin_defaults(tmp_path):
    root = _workspace(tmp_path)
    _plugin(root, "scan", requires_phase=None)
    _enable(root, root / "plugins" / "scan")
    spec = stages.load_registry(root)["scan"]
    assert spec.protects == ()
    assert spec.sets_phase is None
    assert spec.lists_sources is False
    assert spec.order == 50
    assert spec.bundles == ()


# ---- skill resolution (plugin dir -> workspace .pi/skills -> packaged) ----


def test_resolve_prefers_workspace_over_packaged(tmp_path):
    root = _workspace(tmp_path)
    _make_skills(root, ["to-spec"])
    got = stages.resolve_skill_dir(root, "to-spec")
    assert got == root / ".pi" / "skills" / "to-spec"


def test_resolve_falls_back_to_packaged(tmp_path, monkeypatch):
    packaged = tmp_path / "pkg" / "skills" / "to-spec"
    packaged.mkdir(parents=True)
    (packaged / "SKILL.md").write_text("# to-spec\n", encoding="utf-8")
    monkeypatch.setattr(stages, "_packaged_skills_root", lambda: tmp_path / "pkg" / "skills")
    ws = tmp_path / "ws"
    ws.mkdir()
    root = _workspace(ws)
    got = stages.resolve_skill_dir(root, "to-spec")
    assert got == packaged


def test_pi_argv_plugin_uses_plugin_dir_and_tools(tmp_path):
    root = _workspace(tmp_path)
    p = _plugin(root, "scan", tools=["read", "grep"], requires_phase=None)
    _enable(root, p)
    spec = stages.load_registry(root)["scan"]
    argv = pi_argv(root=root, bundle="scan", prompt="(p)", spec=spec)
    tools = argv[argv.index("--tools") + 1]
    assert tools == "read,grep"
    skill_args = [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"]
    assert skill_args[0] == str(p)


def test_resolve_unknown_returns_none(tmp_path):
    root = _workspace(tmp_path)
    assert stages.resolve_skill_dir(root, "no-such-skill") is None


def test_spec_skill_dirs_plugin_first(tmp_path):
    root = _workspace(tmp_path)
    p = _plugin(root, "deploy", bundles=["to-spec"])
    _enable(root, p)
    spec = stages.load_registry(root)["deploy"]
    _make_skills(root, ["to-spec"])
    dirs = stages.spec_skill_dirs(root, spec)
    assert dirs[0] == p
    assert dirs[1].name == "to-spec"
