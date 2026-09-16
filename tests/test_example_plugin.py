import shutil
from pathlib import Path

from dev_yard import stages

REPO_EXAMPLE = Path(__file__).resolve().parents[1] / "plugins" / "example"


def test_example_plugin_loads(tmp_path):
    root = tmp_path
    (root / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    dst = root / "plugins" / "example"
    dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_EXAMPLE, dst)
    (root / "yard.yaml").write_text("plugins: [plugins/example]\n", encoding="utf-8")
    reg = stages.load_registry(root)
    spec = reg["example"]
    assert not spec.builtin
    assert spec.skill_dir == dst
    assert spec.requires_phase is None
    assert spec.sets_phase is None
    assert spec.description
    assert spec.tools == ("read", "grep", "find", "ls")
    assert spec.protects == (
        "REQUIREMENT.md",
        "GRILL.md",
        "SPEC.md",
        "TICKETS.md",
    )
    assert spec.order == 45
    assert "bash" not in spec.tools
    assert "edit" not in spec.tools
    assert "write" not in spec.tools
    assert "只读" in spec.guidance
