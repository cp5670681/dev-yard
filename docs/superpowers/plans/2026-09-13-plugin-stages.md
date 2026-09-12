# 插件化阶段（Plugin Stages）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把硬编码的 7 阶段流水线重构为统一 Stage Registry，支持第三方用 `plugin.yaml + SKILL.md` 零 Python 新增 requirement 级阶段。

**Architecture:** 新建 `stages.py` 承载 `StageSpec` 数据结构、内置阶段常量与插件加载/校验；`pi_argv`/`session_prompt`/执行入口改为 registry 驱动；CLI 与 web 看板从 registry 动态生成阶段。内置阶段与插件走同一条 `run_stage` 路径（自举），插件可覆盖同名内置阶段。

**Tech Stack:** Python 3.12+ / typer / PyYAML / pytest / ruff；运行时 agent 为 pi（`pi --approve --no-skills -p`）。

**Spec:** `docs/superpowers/specs/2026-09-13-plugin-stages-design.md`（本计划从 spec 推导，执行者需同时读 spec）

## Global Constraints

- Python ≥3.12；包管理 uv；测试 `uv run pytest -q`；lint `uv run ruff check src tests`。
- 本仓库自身是 dogfooding workspace（根目录有 `repos.yaml` 与 `.pi/skills/`），workspace skill 覆盖优先级必须保持高于包内兜底。
- argv 等价性：内置阶段迁移前后 `pi_argv` 输出必须逐项一致（Task 1 的 golden 测试是硬保证，任何任务不得使其变红）。
- 迁移是数据搬家：`BUILTIN_STAGES` 的字段值逐字来自现有 `SKILL_NAMES`/`SKILL_BUNDLES`/`_tools_for`/`STAGE_PROTECT`/`STAGE_WRITE`，不得"顺手优化"文案。
- 提交信息用中文 conventional commits（仓库惯例，如 `feat: 支持将需求冻结的各仓 worktree 分支一键推送到远端`）。
- spec 文件 `docs/superpowers/specs/2026-09-13-plugin-stages-design.md` 按用户要求暂未提交；随 Task 1 的 commit 一并入库。
- 顺序执行批次 1 → 2 → 3；批次内任务按编号执行（Task 5 依赖 1-4，Task 6 依赖 5，等等）。

---

## 批次 1（核心）

### Task 1: `stages.py` — StageSpec + 内置阶段 + golden argv 测试

**Files:**
- Create: `src/dev_yard/stages.py`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: 无（纯新增）。
- Produces: `StageSpec`（frozen dataclass，字段见下）；`BUILTIN_STAGES: dict[str, StageSpec]`；`load_registry(root: Path) -> dict[str, StageSpec]`（本任务返回内置副本，Task 2 扩展插件合并）；`RESERVED_STAGE_NAMES: frozenset[str]`；`ALLOWED_TOOLS: tuple[str, ...]`。

- [ ] **Step 1: 写失败测试（内置 registry 内容 + golden argv）**

`tests/test_stages.py`：

```python
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
        assert [Path(d).name for d in spec.bundles_dirs(root)] == EXPECTED_SKILLS[name]
        assert spec.skill == EXPECTED_SKILLS[name][0]
    assert reg["open"].protects == ("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml")
    assert reg["grill"].protects == ("SPEC.md", "TICKETS.md")
    assert reg["spec"].protects == ("TICKETS.md",)
    assert reg["tickets"].protects == ("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml")
    assert reg["implement"].protects == ()
    assert reg["open"].sets_phase == "open"
    assert reg["grill"].lists_sources is True
    assert reg["spec"].lists_sources is True
    assert reg["tickets"].lists_sources is True
    assert reg["implement"].lists_sources is False


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_argv_equivalence_builtin(tmp_path, name):
    """Golden：迁移前后内置阶段 argv 逐项一致。"""
    root = _workspace(tmp_path)
    _make_skills(root, sorted({n for v in EXPECTED_SKILLS.values() for n in v}))
    argv = pi_argv(root=root, bundle=name, prompt="(p)")
    assert argv[1:3] == ["--approve", "--no-skills"]
    tools = argv[argv.index("--tools") + 1]
    assert tools == EXPECTED_TOOLS[name]
    skill_args = [argv[i + 1] for i, a in enumerate(argv) if a == "--skill"]
    assert [Path(p).name for p in skill_args] == EXPECTED_SKILLS[name]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_stages.py -q`
Expected: FAIL（`ModuleNotFoundError: dev_yard.stages`）

- [ ] **Step 3: 实现 `stages.py`（本任务只含内置，暂无插件加载）**

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from dev_yard import paths

RESERVED_STAGE_NAMES = frozenset(
    {"init", "repo", "req", "ticket", "web", "status", "push", "run",
     "stages", "review-override", "version"}
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

    def bundles_dirs(self, root: Path) -> list[Path]:
        """按名字三级解析 bundles（Task 3 引入完整解析，本任务先查 workspace）。"""
        out: list[Path] = []
        for name in self.bundles:
            p = paths.skills_root_placeholder(root) / name  # Task 3 替换为三级解析
            if p.exists():
                out.append(p)
        return out


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


def _spec(name, skill, bundles, tools, protects=(), sets_phase=None,
          lists_sources=False, order=50):
    return StageSpec(
        name=name, skill=skill, bundles=bundles, tools=tools, protects=protects,
        sets_phase=sets_phase, lists_sources=lists_sources, order=order,
        builtin=True, guidance=_GUIDANCE[name],
    )


BUILTIN_STAGES: dict[str, StageSpec] = {
    s.name: s
    for s in (
        _spec("open", "fetch-requirement", ("fetch-requirement",),
              ("read", "bash", "grep", "find", "ls", "edit", "write", "mcp"),
              protects=("GRILL.md", "SPEC.md", "TICKETS.md", "STATUS.yaml"),
              sets_phase="open", order=10),
        _spec("grill", "grill-with-docs",
              ("grill-with-docs", "grilling", "domain-modeling"),
              ("read", "grep", "find", "ls", "edit", "write"),
              protects=("SPEC.md", "TICKETS.md"), lists_sources=True, order=20),
        _spec("spec", "to-spec", ("to-spec",),
              ("read", "grep", "find", "ls", "edit", "write"),
              protects=("TICKETS.md",), lists_sources=True, order=30),
        _spec("tickets", "to-tickets", ("to-tickets",),
              ("read", "grep", "find", "ls", "edit", "write"),
              protects=("REQUIREMENT.md", "GRILL.md", "SPEC.md", "STATUS.yaml"),
              lists_sources=True, order=40),
        _spec("implement", "implement", ("implement", "tdd", "codebase-design"),
              ("read", "bash", "grep", "find", "ls", "edit", "write"), order=50),
        _spec("review", "code-review", ("code-review",),
              ("read", "grep", "find", "ls"), order=55),
        _spec("contract", "code-review", ("code-review",),
              ("read", "grep", "find", "ls"), order=56),
    )
}


def load_registry(root: Path) -> dict[str, StageSpec]:
    """内置阶段 → yard.yaml plugins 顺序覆盖合并（插件加载在 Task 2 加入）。"""
    return dict(BUILTIN_STAGES)
```

注意：`bundles_dirs` 里引用的 `paths.skills_root_placeholder` 本任务直接写成
`root / ".pi" / "skills"`（等价于 `skillbind.skills_root`；Task 3 重构时统一）。
实现时直接内联 `root / ".pi" / "skills" / name`，不留 placeholder 名字。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_stages.py -q`
Expected: PASS（argv golden 测试此刻跑的是**未改造**的 `pi_argv`，捕获的就是现状行为）

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/stages.py tests/test_stages.py docs/superpowers/specs/2026-09-13-plugin-stages-design.md
git commit -m "feat: 新增 StageSpec 与内置阶段 registry（含 argv golden 测试）"
```

---

### Task 2: 插件加载 — yard.yaml + plugin.yaml 解析与校验

**Files:**
- Modify: `src/dev_yard/stages.py`（`load_registry` 扩展）
- Test: `tests/test_stages.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `StageSpec`/`BUILTIN_STAGES`/常量。
- Produces: `load_registry(root)` 完整版（内置+插件合并、同名覆盖）；`_load_plugin_spec(plugin_dir: Path) -> StageSpec`（模块内私有，测试经由 `load_registry` 覆盖）；错误类型 `ValueError`（消息以 `plugin <路径>: ` 开头）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_stages.py`：

```python
import yaml as _yaml


def _plugin(root: Path, name: str = "deploy", **over) -> Path:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "tools": ["read", "bash"],
        "requires_phase": "frozen",
        "order": 55,
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


def test_plugin_overrides_builtin(tmp_path):
    root = _workspace(tmp_path)
    _enable(root, _plugin(root, name="review", tools=["read"]))
    spec = stages.load_registry(root)["review"]
    assert spec.builtin is False
    assert spec.tools == ("read",)


def test_duplicate_plugin_names_rejected(tmp_path):
    root = _workspace(tmp_path)
    a = _plugin(root, "deploy"); b = root / "more" / "deploy"
    b.mkdir(parents=True)
    (b / "plugin.yaml").write_text("name: deploy\ntools: [read]\n", encoding="utf-8")
    (b / "SKILL.md").write_text("# d\n", encoding="utf-8")
    _enable(root, a, b)
    with pytest.raises(ValueError, match="deploy"):
        stages.load_registry(root)


@pytest.mark.parametrize(
    "over,match",
    [
        ({"name": "Init"}, "name"),            # 大写非法
        ({"name": "web"}, "reserved"),          # CLI 保留字
        ({"tools": ["teleport"]}, "tools"),     # 非法工具
        ({"requires_phase": "alpha"}, "phase"), # 未知 phase
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


def test_plugin_requires_phase_from_other_plugin_sets_phase(tmp_path):
    root = _workspace(tmp_path)
    _plugin(root, "deploy", requires_phase=None, sets_phase="deployed", tools=["read"])
    _plugin(root, "verify", requires_phase="deployed", tools=["read"])
    _enable(root, root / "plugins" / "deploy", root / "plugins" / "verify")
    reg = stages.load_registry(root)  # 不抛错
    assert reg["verify"].requires_phase == "deployed"


def test_missing_yard_yaml_means_builtin_only(tmp_path):
    root = _workspace(tmp_path)
    assert set(stages.load_registry(root)) == set(stages.BUILTIN_STAGES)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_stages.py -q`
Expected: 新增用例 FAIL（`load_registry` 仍只返回内置）

- [ ] **Step 3: 实现插件加载**

在 `stages.py` 中替换 `load_registry` 并新增：

```python
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
        raise ValueError(f"plugin {plugin_dir}: invalid name {name!r} (^[a-z][a-z0-9-]*$, <=32)")
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
    skill = str(raw.get("skill") or name)
    skill_dir = plugin_dir / skill
    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"plugin {plugin_dir}: SKILL.md not found in {skill_dir}")
    requires_phase = raw.get("requires_phase")
    sets_phase = raw.get("sets_phase")
    protects = tuple(str(p) for p in (raw.get("protects") or ()))
    return StageSpec(
        name=name,
        skill=skill,
        bundles=tuple(str(b) for b in (raw.get("bundles") or ())),
        tools=tools,
        protects=protects,
        requires_phase=(str(requires_phase) if requires_phase else None),
        sets_phase=(str(sets_phase) if sets_phase else None),
        lists_sources=bool(raw.get("lists_sources", False)),
        order=int(raw.get("order", 50)),
        builtin=False,
        guidance=str(raw.get("guidance") or ""),
        skill_dir=skill_dir,
    )


def load_registry(root: Path) -> dict[str, StageSpec]:
    plugin_dirs = _yard_yaml_plugins(root)
    specs = [_load_plugin_spec(d) for d in plugin_dirs]
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"plugin name {spec.name!r} declared by two enabled plugins")
        seen.add(spec.name)
    declared = {s.sets_phase for s in specs if s.sets_phase}
    for spec in specs:
        req = spec.requires_phase
        if req and req not in BUILTIN_PHASES and req not in declared:
            raise ValueError(
                f"plugin {spec.name}: unknown requires_phase {req!r}; "
                f"known: {list(BUILTIN_PHASES)} or a sets_phase value from enabled plugins"
            )
    registry = dict(BUILTIN_STAGES)
    for spec in specs:
        registry[spec.name] = spec  # 同名覆盖内置是特性
    return registry
```

同步给 `StageSpec` 增加 `skill_dir: Path | None = None` 字段（Task 1 漏列，本任务补上，
字段顺序放在 `guidance` 之后、带默认值，保持 frozen dataclass 可默认构造）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_stages.py -q`
Expected: PASS（含 Task 1 golden）

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/stages.py tests/test_stages.py
git commit -m "feat: 插件加载——yard.yaml 启用、plugin.yaml 校验与同名覆盖"
```

---

### Task 3: skill 三级解析 + wheel 打包修复

**Files:**
- Modify: `src/dev_yard/stages.py`（`bundles_dirs` 三级解析）
- Modify: `pyproject.toml`（force-include）
- Test: `tests/test_stages.py`（追加）

**Interfaces:**
- Consumes: `StageSpec.skill_dir`/`bundles`。
- Produces: `resolve_skill_dir(root: Path, name: str) -> Path | None`（workspace `.pi/skills/<n>` → 包内 `dev_yard/skills/<n>` → 源码仓 `.pi/skills/<n>` → None）；`spec_skill_dirs(root, spec) -> list[Path]`（runner 用，Task 4 消费）。

- [ ] **Step 1: 写失败测试**

```python
def test_resolve_prefers_workspace_over_packaged(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    _make_skills(root, ["to-spec"])
    got = stages.resolve_skill_dir(root, "to-spec")
    assert got == root / ".pi" / "skills" / "to-spec"


def test_resolve_falls_back_to_packaged(tmp_path):
    root = _workspace(tmp_path)  # workspace 无该 skill
    got = stages.resolve_skill_dir(root, "to-spec")
    # 源码仓兜底（测试环境未装 wheel，落到仓库根 .pi/skills）
    assert got is not None and (got / "SKILL.md").is_file()


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_stages.py -q`
Expected: 新用例 FAIL（函数不存在）

- [ ] **Step 3: 实现三级解析**

在 `stages.py`：

```python
def _packaged_skills_root() -> Path:
    # wheel 安装：force-include 把 .pi/skills 放进包内 dev_yard/skills/
    pkg = Path(__file__).resolve().parent / "skills"
    if pkg.is_dir():
        return pkg
    # 源码检出兜底：src/dev_yard/stages.py → 仓库根/.pi/skills
    return Path(__file__).resolve().parents[2] / ".pi" / "skills"


def resolve_skill_dir(root: Path, name: str) -> Path | None:
    ws = root / ".pi" / "skills" / name
    if (ws / "SKILL.md").is_file():
        return ws
    pkg = _packaged_skills_root() / name
    if (pkg / "SKILL.md").is_file():
        return pkg
    return None


def spec_skill_dirs(root: Path, spec: StageSpec) -> list[Path]:
    out: list[Path] = []
    if spec.skill_dir is not None:
        out.append(spec.skill_dir)
    for name in spec.bundles:
        if spec.skill_dir is not None and name == spec.skill and name not in (
            p.name for p in out
        ):
            continue  # 插件自带入口 skill，不再按名解析同名项
        d = resolve_skill_dir(root, name)
        if d is not None:
            out.append(d)
    return out
```

（若 Step 3 的去重逻辑与测试期望不符，以测试为准修实现：语义 = 入口 skill 只出现一次且插件目录优先。）

`pyproject.toml` 在 `[tool.hatch.build.targets.wheel]` 与
`[tool.hatch.build.targets.sdist]` 各加：

```toml
[tool.hatch.build.targets.wheel.force-include]
".pi/skills" = "dev_yard/skills"

[tool.hatch.build.targets.sdist.force-include]
".pi/skills" = "dev_yard/skills"
```

- [ ] **Step 4: 跑测试 + 验证打包**

```bash
uv run pytest tests/test_stages.py -q
uv build --wheel && unzip -l dist/*.whl | grep "dev_yard/skills" | head -5
```
Expected: 测试 PASS；whl 内出现 `dev_yard/skills/fetch-requirement/SKILL.md` 等条目。

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/stages.py tests/test_stages.py pyproject.toml
git commit -m "feat: skill 三级解析（插件目录/workspace/包内）并随 wheel 分发"
```

---

### Task 4: `pi_argv` 与 `_tools_for` 改为 registry 驱动

**Files:**
- Modify: `src/dev_yard/runners/__init__.py:59-96`（删 `_REVIEW_TOOLS` 等常量与 `_tools_for`，`pi_argv` 内部改查 registry）
- Test: 已有 golden 测试（`tests/test_stages.py::test_argv_equivalence_builtin`）+ `tests/test_skillbind.py`（更新对 `skill_dirs` 的引用）

**Interfaces:**
- Consumes: `stages.load_registry`/`spec_skill_dirs`（Task 3）。
- Produces: `pi_argv(*, root, bundle, prompt, print_mode, binary, repo)` 签名不变（bundle=阶段名，可传插件名）；内部 `--tools` 取 `spec.tools`、`--skill` 取 `spec_skill_dirs`。`clip_summary(raw, bundle)` 不变。

- [ ] **Step 1: 先跑 golden 确认基线绿**

Run: `uv run pytest tests/test_stages.py -q`
Expected: PASS（记录基线）

- [ ] **Step 2: 改 `pi_argv`**

```python
from dev_yard import stages as stage_registry  # 顶部新增
from dev_yard.skillbind import spec_skill_dirs  # 替换原 skill_dirs 导入


def pi_argv(*, root, bundle, prompt=None, print_mode=False, binary=None, repo=None):
    cmd = binary or agent_binary()
    spec = stage_registry.load_registry(root).get(bundle)
    if spec is None:
        raise ValueError(f"unknown stage {bundle!r}; run: dev-yard stages")
    argv = [cmd, "--approve", "--no-skills"]
    provider, model = resolve_pi_choice(root, bundle, repo=repo)
    if provider:
        argv.extend(["--provider", provider])
    if model:
        argv.extend(["--model", model])
    argv.extend(["--tools", ",".join(spec.tools)])
    for d in spec_skill_dirs(root, spec):
        argv.extend(["--skill", str(d)])
    if print_mode:
        argv.append("-p")
    if prompt is not None:
        argv.append(prompt)
    return argv
```

删除 `_REVIEW_TOOLS`/`_DOC_TOOLS`/`_OPEN_TOOLS`/`_IMPLEMENT_TOOLS`/`_tools_for`。

- [ ] **Step 3: 更新 `tests/test_skillbind.py` 等旧引用**

`grep -rn "skill_dirs\|_tools_for\|SKILL_BUNDLES" src tests`——`skillbind.skill_dirs`
的调用点只剩 runners（已改）；`test_skillbind.py::test_pi_argv_binds_skills` 断言
`.pi/skills` 出现在 `--skill` 路径里，行为不变应仍绿；若有直接 import
`_tools_for` 的测试，改为断言 registry。运行：

Run: `uv run pytest tests/test_stages.py tests/test_skillbind.py -q`
Expected: PASS（golden 不变 = 等价性成立）

- [ ] **Step 4: 全量回归**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/runners/__init__.py tests/test_skillbind.py
git commit -m "refactor: pi_argv 由 StageSpec 驱动，删除硬编码工具白名单"
```

---

### Task 5: `run_stage` 执行模型 + `session_prompt` 推广

**Files:**
- Modify: `src/dev_yard/skillbind.py`（`session_prompt` 拆出 `session_prompt_for(spec, ...)`，旧函数变 registry 查询包装）
- Modify: `src/dev_yard/service.py`（新增 `run_stage`；`launch_skill` 改为其薄委托）
- Test: `tests/test_run_stage.py`（新建）

**Interfaces:**
- Consumes: `StageSpec`、`Runner`/`RunResult`、`_snapshot`/`_restore`、`st.jira_lock`。
- Produces: `service.run_stage(root: Path, stage: str | StageSpec, jira: str, dry_run=False, print_mode=False, runner: Runner | None = None, prompt_extra: str = "") -> RunResult`；`skillbind.session_prompt_for(spec: StageSpec, root: Path, jira: str, extra: str = "", target: str = "") -> str`。

- [ ] **Step 1: 写失败测试**

`tests/test_run_stage.py`：

```python
from pathlib import Path

import pytest

from dev_yard import service, stages
from dev_yard.runners import RunResult, Runner


class FakeRunner(Runner):
    def __init__(self, ok: bool = True, summary: str = "did the thing") -> None:
        self.ok = ok
        self.summary = summary
        self.calls: list[tuple[str, Path, list[Path]]] = []

    def start(self, prompt, cwd, extra_read_paths, repo=None):
        self.calls.append((prompt, cwd, list(extra_read_paths)))
        return RunResult(ok=self.ok, summary=self.summary)


def _req(root: Path, jira: str, phase: str = "open") -> Path:
    d = root / "reqs" / jira
    d.mkdir(parents=True)
    (d / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    (d / "STATUS.yaml").write_text(f"phase: {phase}\ntickets: {{}}\n", encoding="utf-8")
    return d


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    return tmp_path


def test_phase_gate_blocks(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="open")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen",
    )
    with pytest.raises(ValueError, match="frozen"):
        service.run_stage(root, spec, "J-1", runner=FakeRunner())


def test_phase_gate_allows_and_records_run(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen", sets_phase="deployed",
    )
    r = FakeRunner()
    result = service.run_stage(root, spec, "J-1", runner=r)
    assert result.ok
    assert r.calls[0][1] == root  # cwd = yard root
    import yaml
    data = yaml.safe_load((root / "reqs" / "J-1" / "STATUS.yaml").read_text())
    assert data["phase"] == "deployed"
    assert data["stage_runs"]["deploy"]["ok"] is True
    assert data["stage_runs"]["deploy"]["summary"] == "did the thing"


def test_failure_records_run_keeps_phase(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        requires_phase="frozen", sets_phase="deployed",
    )
    service.run_stage(root, spec, "J-1", runner=FakeRunner(ok=False, summary="boom"))
    import yaml
    data = yaml.safe_load((root / "reqs" / "J-1" / "STATUS.yaml").read_text())
    assert data["phase"] == "frozen"
    assert data["stage_runs"]["deploy"]["ok"] is False


def test_protects_snapshot_restore(tmp_path):
    root = _workspace(tmp_path)
    d = _req(root, "J-1")
    (d / "SPEC.md").write_text("before\n", encoding="utf-8")

    class MutatingRunner(FakeRunner):
        def start(self, prompt, cwd, extra_read_paths, repo=None):
            (d / "SPEC.md").write_text("clobbered\n", encoding="utf-8")
            return super().start(prompt, cwd, extra_read_paths, repo)

    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",),
        protects=("SPEC.md",),
    )
    result = service.run_stage(root, spec, "J-1", runner=MutatingRunner())
    assert "restored" in result.summary
    assert (d / "SPEC.md").read_text() == "before\n"


def test_dry_run_no_status_write(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1", phase="frozen")
    spec = stages.StageSpec(
        name="deploy", skill="deploy", bundles=(), tools=("read",), sets_phase="deployed",
    )
    service.run_stage(root, spec, "J-1", dry_run=True)
    import yaml
    data = yaml.safe_load((root / "reqs" / "J-1" / "STATUS.yaml").read_text())
    assert "stage_runs" not in data and data["phase"] == "frozen"


def test_prompt_contains_guidance_and_req_dir(tmp_path):
    root = _workspace(tmp_path)
    _req(root, "J-1")
    spec = stages.StageSpec(
        name="scan", skill="scan", bundles=(), tools=("read",),
        guidance="只输出结论，不改文件。",
    )
    r = FakeRunner()
    service.run_stage(root, spec, "J-1", runner=r)
    prompt = r.calls[0][0]
    assert "只输出结论，不改文件。" in prompt
    assert "reqs/J-1" in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_run_stage.py -q`
Expected: FAIL（`service.run_stage` 不存在）

- [ ] **Step 3: 实现 `session_prompt_for` 与 `run_stage`**

`skillbind.py`：

```python
def session_prompt_for(
    spec: StageSpec, root: Path, jira: str, extra: str = "", target: str = ""
) -> str:
    from dev_yard.stages import StageSpec  # 仅供类型；实际放模块顶部 import
    req = paths.req_dir(root, jira)
    ctx = paths.context_md(root)
    adr = paths.adr_dir(root)
    target_str = (target or jira).strip()
    if spec.name == "open":
        start = (
            f"Requirement target: `{target_str}`.\n"
            f"Autonomously inspect available tools/MCPs/fetchers to retrieve requirement details for `{target_str}`. "
            f"Write `{req / 'REQUIREMENT.md'}` and optional `{req / 'assets'}`."
        )
    else:
        start_file = req / ("SPEC.md" if spec.name in {"review", "contract"} else "REQUIREMENT.md")
        start = (
            f"Read files with the read tool as needed, starting with {start_file}. "
            f"Shared glossary: `{ctx}`. ADRs: `{adr}`."
        )
    return (
        f"Run skill `{spec.skill}` (already loaded via --skill) for {jira}.\n"
        f"Req dir: {req}\n"
        f"{start}\n"
        f"{spec.guidance}\n"
        f"Do not dump unrelated historical documents. Do not use ~/.pi/agent/skills copies.\n"
        f"{extra}"
    ).strip()


def session_prompt(root, name, jira, extra="", target=""):
    from dev_yard.stages import load_registry
    spec = load_registry(root).get(name)
    if spec is None:
        raise ValueError(f"unknown stage {name!r}")
    return session_prompt_for(spec, root, jira, extra=extra, target=target)
```

`service.py` 新增（放在 `launch_skill` 旁）：

```python
def run_stage(
    root: Path,
    stage: str | StageSpec,
    jira: str,
    dry_run: bool = False,
    print_mode: bool = False,
    runner: Runner | None = None,
    prompt_extra: str = "",
) -> RunResult:
    from dev_yard.stages import StageSpec, load_registry

    spec = stage if isinstance(stage, StageSpec) else load_registry(root)[stage]
    req = paths.req_dir(root, jira)
    if not req.exists():
        raise FileNotFoundError(f"missing {req}; run: dev-yard req open {jira}")
    bases = ""
    if spec.lists_sources and not dry_run:
        mapping = ensure_on_default_base(root)
        repos = load_repos(root)
        lines = "\n".join(
            f"- {a}: {p}  (on {repos[a].default_base}"
            + ("; path-mapped, not moved" if repos[a].path else "")
            + ")"
            for a, p in mapping.items()
        )
        bases = (
            "Read application code from these source clones (on default_base). "
            "Do not switch their branches. Requirement worktrees are created later by freeze.\n"
            f"{lines}\n"
        )
    if prompt_extra:
        bases = (bases + "\n" + prompt_extra).strip() if bases else prompt_extra
    with st.jira_lock(jira):
        data = st.load(root, jira)
        phase = data.get("phase") or "open"
        if spec.requires_phase and phase != spec.requires_phase:
            raise ValueError(
                f"{spec.name} requires phase={spec.requires_phase}, current phase={phase}"
            )
    extra_paths = [
        req / "REQUIREMENT.md", req / "GRILL.md", req / "SPEC.md", req / "TICKETS.md",
        paths.context_md(root), paths.adr_dir(root),
    ]
    prompt = session_prompt_for(spec, root, jira, extra=bases)
    r = runner or get_runner(root, spec.name, dry_run=dry_run, print_mode=print_mode)
    snap = _snapshot(req, spec.protects) if spec.protects and not dry_run else {}
    result = r.start(prompt, root, extra_paths)
    restored = _restore(req, snap) if snap else []
    if restored:
        note = "restored (not this stage's job): " + ", ".join(restored)
        result = RunResult(
            ok=result.ok,
            summary=(result.summary + "\n" + note).strip(),
            exit_code=result.exit_code,
        )
    if not dry_run:
        from datetime import datetime, timezone

        with st.jira_lock(jira):
            data = st.load(root, jira)
            runs = data.setdefault("stage_runs", {})
            runs[spec.name] = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ok": bool(result.ok),
                "summary": (result.summary or "")[:4000],
            }
            if result.ok and spec.sets_phase:
                data["phase"] = spec.sets_phase
            st.save(root, jira, data)
    return result
```

`launch_skill` 改为委托（保持旧签名与返回，供 web grill 分支等继续调用）：

```python
def launch_skill(root, name, jira, dry_run=False, print_mode=False, runner=None, prompt_extra=""):
    return run_stage(root, name, jira, dry_run=dry_run, print_mode=print_mode,
                     runner=runner, prompt_extra=prompt_extra)
```

- [ ] **Step 4: 跑测试 + 回归**

Run: `uv run pytest tests/test_run_stage.py tests/test_stage_protect.py tests/test_implement.py tests/test_review_override.py -q`
Expected: PASS（`launch_skill` 委托后旧行为不变；注意 `launch_skill` 原 grill/spec/tickets 的 extra 列表与新 `run_stage` 一致）

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/skillbind.py src/dev_yard/service.py tests/test_run_stage.py
git commit -m "feat: run_stage 统一执行入口——phase 门禁、快照回滚、stage_runs"
```

---

### Task 6: CLI — `run`/`stages` 命令 + grill/spec/tickets 薄包装

**Files:**
- Modify: `src/dev_yard/cli.py:291-330`（`_launch`/`grill`/`spec`/`tickets` 改薄包装）、文件尾部加 `run`/`stages`
- Test: `tests/test_cli_stages.py`（新建）

**Interfaces:**
- Consumes: `service.run_stage`、`stages.load_registry`。
- Produces: 命令 `dev-yard run <stage> <jira> [--print]`、`dev-yard stages`。

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

import yaml
from typer.testing import CliRunner

from dev_yard.cli import app

runner = CliRunner()


def _workspace(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "repos.yaml").write_text("repos: {}\n", encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_stages_lists_builtin(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    out = runner.invoke(app, ["stages"])
    assert out.exit_code == 0
    assert "grill" in out.stdout and "[builtin]" in out.stdout


def test_stages_lists_plugin(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    d = root / "plugins" / "deploy"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        "name: deploy\ntools: [read]\n", encoding="utf-8"
    )
    (d / "SKILL.md").write_text("# deploy\n", encoding="utf-8")
    (root / "yard.yaml").write_text("plugins: [plugins/deploy]\n", encoding="utf-8")
    out = runner.invoke(app, ["stages"])
    assert "deploy" in out.stdout and "plugins/deploy" in out.stdout


def test_run_unknown_stage_fails(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    out = runner.invoke(app, ["run", "nope", "J-1"])
    assert out.exit_code == 1
    assert "unknown stage" in out.output


def test_run_dry_run_plugin(tmp_path, monkeypatch):
    root = _workspace(tmp_path, monkeypatch)
    (root / "reqs" / "J-1").mkdir(parents=True)
    (root / "reqs" / "J-1" / "REQUIREMENT.md").write_text("# t\n", encoding="utf-8")
    out = runner.invoke(app, ["run", "spec", "J-1", "--dry-run"])
    assert out.exit_code == 0
    assert "dry-run" in out.output or out.output.strip() != ""
```

（`run` 子命令名与 typer 参数顺序：`dev-yard run <stage> <jira>`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cli_stages.py -q`
Expected: FAIL（无 stages/run 命令）

- [ ] **Step 3: 实现**

`cli.py` 把 `_launch`/`grill`/`spec`/`tickets`（原 291-330 行区域）替换为：

```python
def _run_registered(name: str, jira: str, dry_run: bool, print_mode: bool) -> None:
    from dev_yard import stages as stage_registry

    root = root_opt()
    spec = stage_registry.load_registry(root).get(name)
    if spec is None:
        typer.echo(f"unknown stage {name}; run: dev-yard stages", err=True)
        raise typer.Exit(1)
    try:
        result = service.run_stage(root, spec, jira, dry_run=dry_run, print_mode=print_mode)
    except (FileNotFoundError, GitError, ValueError) as e:
        _die(e)
    if dry_run or print_mode or "restored" in (result.summary or ""):
        typer.echo(result.summary)
    if not result.ok:
        raise typer.Exit(result.exit_code or 1)


@app.command()
def grill(jira: str, dry_run: bool = False,
          print_mode: bool = typer.Option(False, "--print")) -> None:
    """Start pi with grill-with-docs for this requirement."""
    _run_registered("grill", jira, dry_run, print_mode)


@app.command()
def spec(jira: str, dry_run: bool = False,
         print_mode: bool = typer.Option(False, "--print")) -> None:
    """Start pi with to-spec for this requirement."""
    _run_registered("spec", jira, dry_run, print_mode)


@app.command()
def tickets(jira: str, dry_run: bool = False,
            print_mode: bool = typer.Option(False, "--print")) -> None:
    """Start pi with to-tickets for this requirement."""
    _run_registered("tickets", jira, dry_run, print_mode)


@app.command()
def run(
    stage: str,
    jira: str,
    dry_run: bool = False,
    print_mode: bool = typer.Option(False, "--print", help="pi -p one-shot instead of TUI"),
) -> None:
    """Run any registered stage (builtin or plugin) for this requirement."""
    _run_registered(stage, jira, dry_run, print_mode)


@app.command(name="stages")
def stages_cmd() -> None:
    """List all registered stages."""
    from dev_yard import stages as stage_registry

    root = root_opt()
    for spec in sorted(stage_registry.load_registry(root).values(), key=lambda s: s.order):
        tag = "builtin" if spec.builtin else f"plugin {spec.skill_dir.parent}"
        typer.echo(f"{spec.name}\t[{tag}]\torder={spec.order}")
```

注意：`run` 与函数名 `run` 不与现有 import 冲突；`stages_cmd` 函数名避免与
模块级 `stages` import 撞名（函数内局部 import）。

- [ ] **Step 4: 跑测试 + 回归**

Run: `uv run pytest tests/test_cli_stages.py tests/test_stages.py -q && uv run ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/cli.py tests/test_cli_stages.py
git commit -m "feat: dev-yard run/stages 通用命令，grill/spec/tickets 迁移为薄包装"
```

---

### Task 7: 批次 1 收尾 — 全量回归

**Files:** 无新增。

- [ ] **Step 1: 全量测试与 lint**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: 全绿。若有失败，修复后重跑（不得跳过）。

- [ ] **Step 2: 手工冒烟（dogfooding workspace）**

```bash
dev-yard stages
dev-yard run spec <某真实 JIRA> --dry-run
```
Expected: stages 列出 7 内置；dry-run 输出 argv 摘要不报错。

- [ ] **Step 3: Commit（如冒烟产生修复）**

```bash
git add -A && git commit -m "fix: 批次 1 冒烟修复"
```

---

## 批次 2（界面与收尾）

### Task 8: web jobs 支持插件阶段动作

**Files:**
- Modify: `src/dev_yard/web/jobs.py:366-411`（`default_execute` 的 bundle 分派）
- Test: `tests/test_jobs.py`（追加）

**Interfaces:**
- Consumes: `service.run_stage`、`stages.load_registry`。
- Produces: `default_execute` 接受任意 registry 阶段名作为 `job.action`（builtin 特判在前，插件走通用分支）。

- [ ] **Step 1: 写失败测试**

参照 `tests/test_jobs.py` 现有构造（JobRunner(sync=True) + monkeypatch `pi_argv`），追加：

```python
def test_plugin_stage_job_executes(tmp_path, monkeypatch):
    # 造 workspace + 插件 + J-1（phase frozen），JobRunner(sync=True).submit("deploy", "J-1")
    # monkeypatch dev_yard.service.get_runner 返回 FakeRunner(ok=True)
    # 断言 job.state == "ok"，STATUS.yaml 出现 stage_runs.deploy
```

（按 test_jobs.py 里既有 fixture/写法补全为可运行代码——该文件 568 行附近已有
`monkeypatch.setattr("dev_yard.web.jobs.pi_argv", ...)` 的模式可套。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_jobs.py -q`
Expected: 新用例 FAIL（unknown action deploy）

- [ ] **Step 3: 实现**

`default_execute` 中，在 `bundle = {...}.get(job.action)` 之前插入：

```python
    from dev_yard import stages as stage_registry

    registry = stage_registry.load_registry(root)
    spec = registry.get(job.action)
    if spec is not None and not spec.builtin:
        runner = JobLogRunner(job, root, spec.name)
        result = service.run_stage(root, spec, job.jira, print_mode=True, runner=runner)
        if not result.ok:
            raise RuntimeError(f"{spec.name} failed")
        job.append(f"{spec.name} finished")
        return
```

同时把 `spec`/`tickets` 两个分支里的 `service.launch_skill(...)` 换成
`service.run_stage(root, spec_obj, job.jira, print_mode=True, runner=runner)`
（`spec_obj = registry["spec"]` 等；grill 分支的 round 循环里也换成 run_stage，
保留 `prompt_extra` 传递）。

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/test_jobs.py tests/test_web_app.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/web/jobs.py tests/test_jobs.py
git commit -m "feat(web): 任务执行器支持插件阶段动作"
```

---

### Task 9: 看板动态阶段按钮 + stage_runs 展示

**Files:**
- Modify: `src/dev_yard/web/board.py:236-330`（`available_actions` 增加插件动作、`ReqDetail` 增 `stage_runs`）
- Modify: `src/dev_yard/web/app.py:49,75,201,328`（动作白名单、`action_labels` 合并插件标题）
- Modify: `src/dev_yard/service.py:1130`（`status_text` 展示 `stage_runs`）
- Test: `tests/test_board.py`（追加）

**Interfaces:**
- Consumes: `stages.load_registry`。
- Produces: `ReqDetail.stage_runs: dict`；`available_actions(detail, root)` 新签名（加 root 参数）。

- [ ] **Step 1: 写失败测试**

```python
def test_plugin_action_appended(tmp_path):
    # workspace + deploy 插件（requires_phase: frozen）
    # 构造 ReqDetail（phase="frozen"）后：
    #   actions = available_actions(detail, root)
    #   deploy_action = next(a for a in actions if a.id == "deploy")
    #   assert deploy_action.enabled and deploy_action.label == "部署"
    # phase="open" 的用例断言 enabled is False 且 reason 含 "frozen"


def test_stage_runs_surfaced(tmp_path):
    # STATUS.yaml 带 stage_runs.deploy 后 build detail 断言 detail.stage_runs["deploy"]["ok"] is True
```

（按 test_board.py 既有构造方式补全为可运行代码。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_board.py -q`

- [ ] **Step 3: 实现**

`board.py`：

```python
from dev_yard import stages as stage_registry  # 顶部


@dataclass
class ReqDetail:
    # ...现有字段不动，追加：
    stage_runs: dict = field(default_factory=dict)


def available_actions(detail: ReqDetail, root: Path) -> list[Action]:
    builtin = [ ...现有返回列表原样... ]
    plugin: list[Action] = []
    for spec in sorted(stage_registry.load_registry(root).values(),
                       key=lambda s: s.order):
        if spec.builtin:
            continue
        enabled = (detail.phase == spec.requires_phase) if spec.requires_phase else True
        reason = "" if enabled else (
            f"需要 phase={spec.requires_phase}（当前 {detail.phase}）"
        )
        plugin.append(Action(spec.name, spec.title if hasattr(spec, "title") else spec.name,
                             enabled, reason))
    return builtin + plugin
```

（`StageSpec` 无 `title` 字段——在 Task 2 的 `StageSpec` 里补
`title: str = ""`，`plugin.yaml` 的 `title` 读入；此处用 `spec.title or spec.name`。
构造 ReqDetail 处（`detail = ReqDetail(...)`）传入
`stage_runs=dict(data.get("stage_runs") or {})`，并把
`detail.actions = available_actions(detail)` 改为 `available_actions(detail, root)`
——该构造函数需确认已有 `root` 在手（`_detail(root, jira)` 链路），调用点含
`app.py:328` 附近与 test_board.py。）

`app.py`：

- 动作白名单（约 49 行的元组）：`_submit_action`/`run_action` 对
  `action not in ALLOWED` 时追加判断 `load_registry(root).get(action) is not None`
  即放行。
- `ACTION_LABELS`（约 75 行）改为模板函数：

```python
templates.env.globals["action_labels"] = lambda a: (
    ACTION_LABELS.get(a)
    or next((s.title for s in load_registry(root).values() if s.name == a), None)
    or a
)
```

`service.status_text` 在 test 行后追加：

```python
        runs = data.get("stage_runs") or {}
        for sname, run in sorted(runs.items()):
            lines.append(
                f"  {sname} ok={run.get('ok')} at={run.get('at')}"
            )
```

- [ ] **Step 4: 跑测试 + 回归**

Run: `uv run pytest tests/test_board.py tests/test_web_app.py tests/test_jobs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/web/board.py src/dev_yard/web/app.py src/dev_yard/service.py tests/test_board.py
git commit -m "feat(web): 看板动态渲染插件阶段按钮并展示 stage_runs"
```

---

### Task 10: open/implement/review/contract 映射迁移 + 删除旧表

**Files:**
- Modify: `src/dev_yard/service.py:507-512`（`STAGE_PROTECT` 删除，`req_open` 的 `_snapshot` 改用 registry）
- Modify: `src/dev_yard/skillbind.py:7-61`（删 `SKILL_NAMES`/`SKILL_BUNDLES`/`STAGE_WRITE`/`load_skill`/`skill_dirs`）
- Test: `tests/test_skillbind.py`（更新/删除对应断言）

**Interfaces:**
- Consumes: `stages.load_registry`。
- Produces: `skillbind` 只剩 `session_prompt`/`session_prompt_for`；阶段数据唯一来源 = `stages.py`。

- [ ] **Step 1: 全量基线**

Run: `uv run pytest -q`（记录绿基线）

- [ ] **Step 2: 改 `req_open` 快照来源**

`service.py` 中 `snap = _snapshot(d, STAGE_PROTECT["open"])` 改为：

```python
    from dev_yard.stages import load_registry
    snap = _snapshot(d, load_registry(root)["open"].protects)
```

删除 `STAGE_PROTECT` 常量。

- [ ] **Step 3: 删除 skillbind 旧表与死代码**

删除 `SKILL_NAMES`、`SKILL_BUNDLES`、`STAGE_WRITE`、`load_skill`、`skill_dirs`、
`skills_root`（先 `grep -rn "skill_dirs\|load_skill\|skills_root\|SKILL_NAMES\|SKILL_BUNDLES\|STAGE_WRITE" src tests`
确认无残余调用方；`tests/test_skillbind.py` 中针对已删函数的用例删除，
保留/改写 `session_prompt` 相关断言为经 registry 的等价断言）。

- [ ] **Step 4: 全量回归**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/dev_yard/service.py src/dev_yard/skillbind.py tests/test_skillbind.py
git commit -m "refactor: 阶段映射唯一来源收敛到 stages registry，删除旧硬编码表"
```

---

## 批次 3（文档与示例）

### Task 11: 仓库内示例插件

**Files:**
- Create: `plugins/example/plugin.yaml`、`plugins/example/SKILL.md`
- Test: `tests/test_example_plugin.py`

**Interfaces:**
- Consumes: `stages.load_registry`。
- Produces: 可被任何 yard 引用的示例插件（`plugins/example`，相对路径引用）。

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

import shutil

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_example_plugin.py -q`
Expected: FAIL（目录不存在）

- [ ] **Step 3: 写示例插件**

`plugins/example/plugin.yaml`：

```yaml
name: example
title: 产物自检
description: 需求文档就绪度检查示例插件
tools: [read, grep, find, ls]
protects: [REQUIREMENT.md, GRILL.md, SPEC.md, TICKETS.md]
requires_phase: null
sets_phase: null
order: 45
guidance: >
  只读检查并输出结论：REQUIREMENT.md 是否有正文、GRILL.md 是否有决策记录、
  SPEC.md 是否有 Goals/Non-goals/Per-repo/Contracts 四节、TICKETS.md 票是否都带 repo。
  不要修改任何文件。
```

`plugins/example/SKILL.md`：

```markdown
---
name: example
description: >
  产物自检示例阶段。Use when the user runs dev-yard run example <JIRA>.
---

# example（产物自检）

读取 `reqs/<JIRA>/` 下的 REQUIREMENT.md / GRILL.md / SPEC.md / TICKETS.md，
按 plugin.yaml guidance 里的清单逐项给出 ✅/❌ 与一句理由，最后输出
"READY" 或 "NOT READY: <缺口列表>"。只读，不写任何文件。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_example_plugin.py -q`

- [ ] **Step 5: Commit**

```bash
git add plugins/example tests/test_example_plugin.py
git commit -m "feat: 新增示例插件 plugins/example（产物自检）"
```

---

### Task 12: README 插件开发指南 + AGENTS.md 路由

**Files:**
- Modify: `README.md`（核心流程图后新增"插件：自定义阶段"一节）
- Modify: `AGENTS.md`（路由表加一行）

- [ ] **Step 1: README 新增章节**

插入位置：`## 核心流程` 代码块之后。内容（可直接使用）：

```markdown
## 插件：自定义阶段

内置阶段之外，可以用**声明式插件**新增流程阶段（如部署、e2e、安全扫描），
零 Python 代码——一个目录 = `plugin.yaml` + `SKILL.md`：

​```
my-yard/
├── yard.yaml            # 启用插件（显式列表，按序覆盖）
│                        #   plugins: [plugins/deploy]
└── plugins/deploy/
    ├── plugin.yaml
    └── SKILL.md
​```

`plugin.yaml` 字段：

​```yaml
name: deploy              # ^[a-z][a-z0-9-]*$，不得用 CLI 保留字
title: 部署到预发          # 看板按钮文案
tools: [read, bash]       # pi 工具白名单（必填）
requires_phase: frozen    # 前置 phase 相等检查（可选）
sets_phase: null          # 成功后写入的 phase（可选）
protects: [SPEC.md]       # 跑前快照、跑后回滚的文件（可选）
order: 55                 # 流水线展示顺序（可选）
guidance: 只做部署；不改 reqs/ 文档。   # 注入 prompt 的写约束（可选）
​```

使用：

​```bash
dev-yard stages           # 列出全部阶段（内置 + 插件）
dev-yard run deploy PROJ-101
​```

规则：插件名与内置同名即**覆盖**内置阶段（可用来换掉 review 的 skill）；
两个插件同名会报错。插件阶段跑在 `pi --approve --no-skills` 下，能力上限
= `tools` 白名单——**装插件即信任其作者**。执行结果记录在
`STATUS.yaml` 的 `stage_runs`，web 看板可见。
```

- [ ] **Step 2: AGENTS.md 路由表追加一行**

`| dev-yard run <stage>` | 任意 registry 阶段（含 plugins/ 插件） |`（插入表格末尾）

- [ ] **Step 3: 验证文档中的命令真实可跑**

Run: `dev-yard stages`（dogfooding workspace）
Expected: 输出 7 个内置阶段

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: 插件开发指南与 run/stages 路由说明"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3 StageSpec→Task 1；§4 schema/校验→Task 2；§5 发现→Task 2/6；§6 执行模型→Task 5；§7 CLI→Task 6；§8 web→Task 8/9；§9 迁移→Task 4/5/10 + golden 测试；§10 三级解析/打包→Task 3；§11 安全（保留字/tools 校验/STATUS 不入 protects 默认）→Task 2/5；§12 测试→各任务 TDD；§13 批次→本计划三批次；§14 明示不做。无缺口。
- **占位符**：Task 8/9 的测试步骤以"按既有模式补全"描述并给出断言要点与参照行号——因 test_jobs.py/test_board.py 的 fixture 形态需现场对齐，属可执行指令而非 TBD；其余任务代码完整。
- **类型一致性**：`StageSpec.skill_dir`/`title` 字段在 Task 2/9 补齐并在 Interfaces 注明；`run_stage(root, stage: str | StageSpec, ...)` 全计划一致；`available_actions(detail, root)` 新签名在 Task 9 定义并同步调用点。
