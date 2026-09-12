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
        "run",
        "stages",
        "review-override",
        "version",
    }
)
ALLOWED_TOOLS = ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp")
BUILTIN_PHASES = ("open", "frozen", "testing", "done")
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
    )
}


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
    for item in raw:
        text = str(item).strip()
        if not text:
            raise ValueError("yard.yaml plugins entries must be non-empty strings")
        out.append((root / text).resolve())
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
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError(
            f"plugin {plugin_dir}: invalid name {name!r} (^[a-z][a-z0-9-]*$, <=32)"
        )
    if name in RESERVED_STAGE_NAMES:
        raise ValueError(f"plugin {plugin_dir}: name {name!r} is a reserved CLI word")
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
    skill_dir = plugin_dir / skill if skill else plugin_dir
    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"plugin {plugin_dir}: SKILL.md not found in {skill_dir}")
    requires_phase = raw.get("requires_phase")
    sets_phase = raw.get("sets_phase")
    return StageSpec(
        name=name,
        skill=skill or name,
        bundles=tuple(str(b) for b in (raw.get("bundles") or ())),
        tools=tools,
        protects=tuple(str(p) for p in (raw.get("protects") or ())),
        requires_phase=(str(requires_phase) if requires_phase else None),
        sets_phase=(str(sets_phase) if sets_phase else None),
        lists_sources=bool(raw.get("lists_sources", False)),
        order=int(raw.get("order", 50)),
        builtin=False,
        guidance=str(raw.get("guidance") or ""),
        skill_dir=skill_dir,
        title=str(raw.get("title") or ""),
    )


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
    declared = {s.sets_phase for s in specs if s.sets_phase}
    for spec in specs:
        req = spec.requires_phase
        if req and req not in BUILTIN_PHASES and req not in declared:
            raise ValueError(
                f"plugin {plugin_dirs[specs.index(spec)]}: unknown requires_phase {req!r}; "
                f"known: {list(BUILTIN_PHASES)} or a sets_phase value from enabled plugins"
            )
    registry = dict(BUILTIN_STAGES)
    for spec in specs:
        registry[spec.name] = spec
    return registry
