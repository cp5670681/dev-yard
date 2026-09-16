from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RESERVED_STAGE_NAMES = frozenset(
    {
        "init",
        "repo",
        "req",
        "ticket",
        "web",
        "status",
        "push",
        "sync",
        "assistant",
        "run",
        "stages",
        "review-override",
        "version",
    }
)
DEDICATED_STAGE_NAMES = frozenset({"qa-design", "qa-run"})
ALLOWED_TOOLS = ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp")
BUILTIN_PHASES = ("open", "frozen", "testing", "done")
PLUGIN_YAML_KEYS = frozenset(
    {
        "name",
        "title",
        "description",
        "skill",
        "bundles",
        "tools",
        "protects",
        "requires_phase",
        "lists_sources",
        "order",
        "guidance",
    }
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class StageSpec:
    name: str
    skill: str
    bundles: tuple[str, ...]
    tools: tuple[str, ...]
    protects: tuple[str, ...] = ()
    requires_phase: str | None = None
    sets_phase: str | None = None
    lists_sources: bool = False
    order: int = 50
    builtin: bool = False
    guidance: str = ""
    skill_dir: Path | None = None
    title: str = ""
    description: str = ""


_GUIDANCE = {
    "open": "Write REQUIREMENT.md and optional assets/ for this requirement only.",
    "grill": (
        "Write only reqs/<REQ>/GRILL.md. If a term or ADR is settled, write "
        "reqs/CONTEXT.md and reqs/docs/adr/ (shared across requirements). "
        "Do not write workspace-root CONTEXT.md or docs/adr. "
        "Do not copy them into source clones or freeze worktrees. "
        "Do not write SPEC.md or TICKETS.md. "
        "Focus on P0/P1 decisions, apply sensible defaults for minor details, "
        "group questions by theme for large tasks, and cap grilling strictly within 1-3 rounds."
    ),
    "spec": "Write only SPEC.md from GRILL.md. Do not interview. Do not write TICKETS.md.",
    "tickets": "Write only TICKETS.md from SPEC.md.",
    "implement": (
        "Write code in the current worktree only. "
        "Do not add CONTEXT.md or docs/adr to the business repo."
    ),
    "review": "Do not implement; report Standards and Spec axes.",
    "contract": "Do not implement; report Spec contract gaps across worktrees.",
    "qa-design": (
        "Write only reqs/<REQ>/qa/** (meta.yaml and cases/). "
        "Read REQUIREMENT.md, SPEC.md, TICKETS.md. Do not fetch Jira. "
        "Diff freeze worktrees vs default_base. Do not interview. "
        "Do not write STATUS.yaml or the four requirement markdown files."
    ),
    "qa-run": (
        "Run only the one case in the prompt. Write only reqs/<REQ>/qa/**. "
        "Do not change worktree files. Do not git checkout/commit/push. "
        "Do not change case expected values. Failed cases collect evidence only."
    ),
}


def _spec(
    name: str,
    skill: str,
    bundles: tuple[str, ...],
    tools: tuple[str, ...],
    protects: tuple[str, ...] = (),
    sets_phase: str | None = None,
    lists_sources: bool = False,
    order: int = 50,
) -> StageSpec:
    return StageSpec(
        name=name,
        skill=skill,
        bundles=bundles,
        tools=tools,
        protects=protects,
        sets_phase=sets_phase,
        lists_sources=lists_sources,
        order=order,
        builtin=True,
        guidance=_GUIDANCE[name],
    )


BUILTIN_STAGES: dict[str, StageSpec] = {
    s.name: s
    for s in (
        _spec(
            "open",
            "fetch-requirement",
            ("fetch-requirement",),
            ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp"),
            protects=("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml"),
            sets_phase="open",
            order=10,
        ),
        _spec(
            "grill",
            "grill-with-docs",
            ("grill-with-docs", "grilling", "domain-modeling"),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("SPEC.md", "TICKETS.md"),
            lists_sources=True,
            order=20,
        ),
        _spec(
            "spec",
            "to-spec",
            ("to-spec",),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("TICKETS.md",),
            lists_sources=True,
            order=30,
        ),
        _spec(
            "tickets",
            "to-tickets",
            ("to-tickets",),
            ("read", "grep", "find", "ls", "edit", "write"),
            protects=("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml"),
            lists_sources=True,
            order=40,
        ),
        _spec(
            "implement",
            "implement",
            ("implement", "tdd", "codebase-design"),
            ("read", "bash", "grep", "find", "ls", "edit", "write"),
            order=50,
        ),
        _spec(
            "review",
            "code-review",
            ("code-review",),
            ("read", "grep", "find", "ls"),
            order=55,
        ),
        _spec(
            "contract",
            "code-review",
            ("code-review",),
            ("read", "grep", "find", "ls"),
            order=56,
        ),
        _spec(
            "qa-design",
            "qa-design",
            ("qa-design",),
            ("read", "bash", "grep", "find", "ls", "edit", "write"),
            order=57,
        ),
        _spec(
            "qa-run",
            "qa-run",
            ("qa-run",),
            ("read", "bash", "grep", "find", "ls", "edit", "write"),
            order=58,
        ),
    )
}


def _is_under(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _yard_yaml_plugins(root: Path) -> list[Path]:
    yml = root / "yard.yaml"
    if not yml.exists():
        return []
    data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("yard.yaml must be a mapping")
    raw = data.get("plugins") or []
    if not isinstance(raw, list):
        raise ValueError("yard.yaml plugins must be a list")
    out: list[Path] = []
    root_resolved = root.resolve()
    for item in raw:
        text = str(item).strip()
        if not text:
            raise ValueError("yard.yaml plugins entries must be non-empty strings")
        if Path(text).is_absolute():
            raise ValueError(
                f"yard.yaml plugins entry {text!r} must be relative to the yard root"
            )
        resolved = (root / text).resolve()
        if not _is_under(root_resolved, resolved):
            raise ValueError(
                f"yard.yaml plugins entry {text!r} must stay under the yard root"
            )
        out.append(resolved)
    return out


def _load_plugin_spec(plugin_dir: Path) -> StageSpec:
    if not plugin_dir.is_dir():
        raise ValueError(f"plugin {plugin_dir}: directory not found")
    yml = plugin_dir / "plugin.yaml"
    if not yml.is_file():
        raise ValueError(f"plugin {plugin_dir}: plugin.yaml not found")
    raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"plugin {plugin_dir}: plugin.yaml must be a mapping")
    if "sets_phase" in raw:
        raise ValueError(
            f"plugin {plugin_dir}: sets_phase is not allowed; "
            "plugin progress is recorded in STATUS.yaml stage_runs"
        )
    unknown = sorted(str(k) for k in raw if k not in PLUGIN_YAML_KEYS)
    if unknown:
        raise ValueError(
            f"plugin {plugin_dir}: unknown keys {unknown}; "
            f"allowed: {sorted(PLUGIN_YAML_KEYS)}"
        )
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError(
            f"plugin {plugin_dir}: invalid name {name!r} (^[a-z][a-z0-9-]*$, <=32)"
        )
    if name in RESERVED_STAGE_NAMES:
        raise ValueError(f"plugin {plugin_dir}: name {name!r} is a reserved CLI word")
    if name in DEDICATED_STAGE_NAMES:
        raise ValueError(
            f"plugin {plugin_dir}: name {name!r} is a dedicated test stage; "
            "use `dev-yard req test`"
        )
    tools_raw = raw.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        raise ValueError(f"plugin {plugin_dir}: tools must be a non-empty list")
    tools = tuple(str(t) for t in tools_raw)
    bad = [t for t in tools if t not in ALLOWED_TOOLS]
    if bad:
        raise ValueError(
            f"plugin {plugin_dir}: unknown tools {bad}; allowed: {list(ALLOWED_TOOLS)}"
        )
    skill = str(raw.get("skill") or "").strip()
    if skill:
        skill_path = Path(skill)
        if skill_path.is_absolute() or ".." in skill_path.parts:
            raise ValueError(
                f"plugin {plugin_dir}: skill {skill!r} must be a relative directory "
                "inside the plugin"
            )
        skill_dir = (plugin_dir / skill).resolve()
        if not _is_under(plugin_dir, skill_dir):
            raise ValueError(
                f"plugin {plugin_dir}: skill {skill!r} must stay under the plugin directory"
            )
    else:
        skill_dir = plugin_dir
    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"plugin {plugin_dir}: SKILL.md not found in {skill_dir}")
    bundles = tuple(str(b) for b in (raw.get("bundles") or ()))
    bad_bundles = [b for b in bundles if not _NAME_RE.match(b)]
    if bad_bundles:
        raise ValueError(
            f"plugin {plugin_dir}: invalid bundles {bad_bundles} "
            "(^[a-z][a-z0-9-]*$, <=32)"
        )
    protects_raw = tuple(str(p) for p in (raw.get("protects") or ()))
    bad_protects = [p for p in protects_raw if Path(p).name != p or p in {".", "..", ""}]
    if bad_protects:
        raise ValueError(
            f"plugin {plugin_dir}: protects entries must be basenames, not {bad_protects}"
        )
    requires_raw = raw.get("requires_phase")
    if requires_raw is None or str(requires_raw).strip() == "":
        requires_phase = None
    else:
        requires_phase = str(requires_raw).strip()
        if requires_phase not in BUILTIN_PHASES:
            raise ValueError(
                f"plugin {plugin_dir}: unknown requires_phase {requires_phase!r}; "
                f"must be one of {list(BUILTIN_PHASES)}"
            )
    return StageSpec(
        name=name,
        skill=skill or name,
        bundles=bundles,
        tools=tools,
        protects=protects_raw,
        requires_phase=requires_phase,
        lists_sources=bool(raw.get("lists_sources", False)),
        order=int(raw.get("order", 50)),
        builtin=False,
        guidance=str(raw.get("guidance") or ""),
        skill_dir=skill_dir,
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
    )


def _packaged_skills_root() -> Path:
    # Installed wheel: force-include maps .pi/skills into dev_yard/skills/.
    pkg = Path(__file__).resolve().parent / "skills"
    if pkg.is_dir():
        return pkg
    # Source checkout: src/dev_yard/stages.py -> repo root/.pi/skills.
    return Path(__file__).resolve().parents[2] / ".pi" / "skills"


def plugin_root(spec: StageSpec) -> Path | None:
    """The plugin directory (holds plugin.yaml); None for builtin stages."""
    if spec.skill_dir is None:
        return None
    if (spec.skill_dir / "plugin.yaml").is_file():
        return spec.skill_dir
    return spec.skill_dir.parent


def resolve_skill_dir(root: Path, name: str) -> Path | None:
    """workspace .pi/skills/<name> -> packaged dev_yard/skills/<name> -> None."""
    ws = root / ".pi" / "skills" / name
    if (ws / "SKILL.md").is_file():
        return ws
    pkg = _packaged_skills_root() / name
    if (pkg / "SKILL.md").is_file():
        return pkg
    return None


def spec_skill_dirs(root: Path, spec: StageSpec) -> list[Path]:
    """Skill dirs to pass to pi --skill: the plugin's own dir first, then bundles."""
    out: list[Path] = []
    if spec.skill_dir is not None:
        out.append(spec.skill_dir)
    for name in spec.bundles:
        d = resolve_skill_dir(root, name)
        if d is not None and d not in out:
            out.append(d)
    return out


def load_registry(root: Path) -> dict[str, StageSpec]:
    """Built-in stages merged with yard.yaml plugins; later plugins override by name.

    A plugin sharing a builtin name replaces it (intentional override feature);
    two plugins declaring the same name is a load error.
    """
    plugin_dirs = _yard_yaml_plugins(root)
    specs = [_load_plugin_spec(d) for d in plugin_dirs]
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(
                f"plugin name {spec.name!r} declared by two enabled plugins"
            )
        seen.add(spec.name)
    registry = dict(BUILTIN_STAGES)
    for spec in specs:
        registry[spec.name] = spec
    return registry
