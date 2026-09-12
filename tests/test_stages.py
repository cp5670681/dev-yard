from pathlib import Path

import pytest

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
}
EXPECTED_SKILLS = {
    "open": ["fetch-requirement"],
    "grill": ["grill-with-docs", "grilling", "domain-modeling"],
    "spec": ["to-spec"],
    "tickets": ["to-tickets"],
    "implement": ["implement", "tdd", "codebase-design"],
    "review": ["code-review"],
    "contract": ["code-review"],
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
    assert reg["grill"].protects == ("SPEC.md", "TICKETS.md")
    assert reg["spec"].protects == ("TICKETS.md",)
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
