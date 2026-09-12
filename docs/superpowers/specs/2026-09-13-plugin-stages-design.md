# dev-yard 插件化阶段（Plugin Stages）设计

- 日期：2026-09-13
- 状态：设计已确认（方案 A），待写实施计划
- 决策记录：扩展点=新增流程阶段；形态=声明式（零 Python）；发现=workspace 本地

## 1. 背景与动机

dev-yard 的核心是一条固定流水线
`open → grill → spec → tickets → freeze → implement → review → contract → push → submit-test`。
skill 层已经是"目录即插件"（`.pi/skills/<name>/SKILL.md`），但编排层全部硬编码：

| 层 | 位置 | 硬编码内容 |
|---|---|---|
| 阶段列表 | `config.py:14` `PI_STAGES` | 7 个阶段名 |
| 阶段→skill 映射 | `skillbind.py:7-27` `SKILL_NAMES`/`SKILL_BUNDLES` | 每阶段绑哪个 skill 目录 |
| 工具白名单 | `runners/__init__.py:59-72` `_tools_for()` | 每阶段 pi 可用工具 |
| 写文件约束/快照回滚 | `skillbind.py:42-61` `STAGE_WRITE`、`service.py:507` `STAGE_PROTECT` | 每阶段保护哪些产物 |
| CLI 命令 | `cli.py` | 每阶段一个 typer 命令 |
| Web 看板阶段按钮 | `web/board.py:19-25` | 硬编码阶段名列表 |

第三方因此无法新增 `deploy`、`e2e-test`、`security-scan` 之类的阶段。

另发现一个现有分发缺口：wheel 只打包 `src/dev_yard`（`pyproject.toml:30-31`），
仓库根 `.pi/skills/` 不随包分发。新初始化的工作区没有 `.pi/skills/` 时
`skill_dirs()` 静默返回空列表，内置阶段实际上无 skill 可加载。本设计一并修复。

## 2. 目标与非目标

### 目标

1. 第三方**零 Python 代码**新增 requirement 级流水线阶段：一个目录 =
   `plugin.yaml`（编排元数据）+ `SKILL.md`（prompt）。
2. **自举**：内置 7 阶段迁移为同一份 `StageSpec` 数据、走同一条执行路径；
   插件与内置完全等权，插件可覆盖同名内置阶段（如换掉 `review` 的 skill）。
3. CLI 提供 `dev-yard run <stage>` 通用命令与 `dev-yard stages` 列表命令；
   现有命令（`grill`/`spec`/…）保留为薄包装，完全向后兼容。
4. Web 看板的阶段按钮与任务调度从 registry 动态生成。
5. 内置 skill 随 wheel 分发（skill 三级解析），修复新工作区缺口。
6. 散落的硬编码（映射/白名单/保护表）收敛到 `stages.py` 一处。

### 非目标（第一版明确排除）

- **ticket 级插件阶段**（`exec_scope: ticket`）：child worktree、merge、DAG、
  `_CLAIM` 状态机是 implement/review 的核心复杂度，插件接入等真实需求再开。
- git URL / pip / entry_points 插件发现方式。
- pre/post shell 钩子（用户已明确选择纯声明式，不含 hooks）。
- 外部需求源（Jira 之外）与提测出口的可替换集成——另案。
- `repos.yaml` 里现有 `pi:` provider/model 配置迁往 `yard.yaml`——另案。

## 3. 核心抽象：`StageSpec` + `StageRegistry`

新文件 `src/dev_yard/stages.py`：

```python
@dataclass(frozen=True)
class StageSpec:
    name: str                  # 阶段名，同时是 run 子命令名；[a-z][a-z0-9-]*
    skill: str                 # 入口 skill 目录名
    skill_dir: Path | None     # None = 按 skill 名走三级解析；插件 = 插件目录绝对路径
    bundles: tuple[str, ...]   # 附带加载的 skill 名（三级解析）
    tools: tuple[str, ...]     # pi 工具白名单
    protects: tuple[str, ...]  # 跑前快照、跑后回滚的 req 文件名
    requires_phase: str | None # 前置 phase 相等检查；None = 不检查
    sets_phase: str | None     # 成功后写入 STATUS.yaml 的 phase；None = 不改
    lists_sources: bool        # prompt 是否注入源码 clone 清单（grill/spec/tickets=True）
    order: int                 # 展示顺序（status/web）；内置 open=10…review=50
    builtin: bool              # 内置 or 插件
    guidance: str              # 注入 prompt 的该阶段写文件约束（现 STAGE_WRITE）
```

`load_registry(root)` 合并顺序：

1. `BUILTIN_STAGES`（Python 常量，逐字段对应现 `SKILL_NAMES`/`SKILL_BUNDLES`/
   `_tools_for`/`STAGE_PROTECT`/`STAGE_WRITE`，机械对照，语义零变化）；
2. `yard.yaml` 的 `plugins:` 按声明顺序逐个加载 plugin.yaml 并注册。

**同名覆盖规则**：插件名 == 内置名 → 覆盖（特性，用于替换内置阶段）；
两个插件同名 → 加载错误（防手滑）。所有校验失败均 fail fast 报错退出，不静默跳过。

## 4. `plugin.yaml` schema

```yaml
name: deploy                    # 必填；^[a-z][a-z0-9-]*$，≤32 字符
title: 部署到预发               # 可选，展示用
description: 把 review 通过的分支部署到预发环境   # 可选
skill: deploy                   # 可选，默认 = name；skill 目录在本插件目录内
bundles: []                     # 可选；按名字走三级解析（引用 yard/内置 skill）
tools: [read, bash, grep, find, ls]   # 必填
protects: [REQUIREMENT.md, SPEC.md, TICKETS.md]  # 可选，默认 []
requires_phase: frozen          # 可选，默认 null（不检查）
sets_phase: null                # 可选，默认 null
lists_sources: false            # 可选，默认 false
order: 55                       # 可选，默认 50（排在 review 之后的位置）
guidance: 只做部署动作；不要修改 reqs/ 下任何文档。  # 可选，注入 prompt 的写约束
```

校验规则：

- `name` 不得命中 CLI 保留字：`init`、`repo`、`req`、`ticket`、`web`、`status`、
  `push`、`run`、`stages`、`review-override`、`version`。
- `tools` 每项必须在 pi 支持集合 `{read, bash, grep, find, ls, edit, write, mcp}` 内。
- `requires_phase` 必须是内置 phase `{open, frozen, testing, done}` 之一，或
  同 registry 内某插件 `sets_phase` 声明过的值（加载期即可全量校验）。
- 插件目录必须存在 `plugin.yaml`；`skill` 目录必须存在 `SKILL.md`。

## 5. 发现与启用

```
my-yard/
├── repos.yaml          # 不变（find_root 仍锚定它）
├── yard.yaml           # 新增：workspace 级配置，第一版只有 plugins 键
├── plugins/            # 约定目录（非强制）；插件 = 任意相对路径均可
│   └── deploy/
│       ├── plugin.yaml
│       └── SKILL.md    # 插件自带 skill，不要求放进 .pi/skills/
└── .pi/skills/         # workspace 级 skill 覆盖仍在（dogfooding 用）
```

```yaml
# yard.yaml
plugins:
  - plugins/deploy
```

- 启用是**显式声明**（列出才启用），不做 `plugins/` 目录自动发现——隐式启用容易失控，
  显式列表同时定义了覆盖顺序。
- 路径相对 yard root；第一版只支持本地路径。
- `dev-yard init` 不生成 yard.yaml（无插件时无需此文件，registry 只剩内置阶段）。

## 6. 执行模型：`service.run_stage(root, spec, jira, ...)`

`launch_skill` 的推广版，插件阶段与迁移后的内置阶段共用：

1. **phase 门禁**：`spec.requires_phase` 非空时与 `STATUS.yaml` 当前 phase 做
   **相等**比较；不等则报错并提示当前 phase。不做先后推断，保持简单。
2. **cwd**：yard root。插件阶段第一版不绑定 worktree；需要看代码的场景在
   prompt 里给绝对路径让 pi 用 `read`/`grep` 查看冻结 worktree。
3. **prompt 组装**：`session_prompt` 推广为接受 `StageSpec`——注入 req dir、
   共享术语/ADR 路径、`spec.guidance`、（`lists_sources` 时）源码 clone 清单。
4. **快照回滚**：`spec.protects` 非空则跑前 `_snapshot`、跑后 `_restore`
   （与内置同一套机制）。
5. **成功后**（`jira_lock` 内）：`spec.sets_phase` 写入 phase（如有）；
   追加 `stage_runs` 记录：`stage_runs: {deploy: {at: <ISO8601>, ok: true, summary: <clip 后>}}`。
   失败同样记录 `ok: false`，phase 不变。
6. **返回** `RunResult`，CLI/Web 统一消费。

`stage_runs` 只记录**经 `run_stage` 执行**的阶段（插件 + 迁移后的
grill/spec/tickets）。`implement`/`review`/`contract` 保留专用 service 函数，
其结果仍落在现有字段（`last_summary` 等），不记 `stage_runs`。
这是 additive 字段，`status.load` 天然容忍未知键，不改变 phase/tickets 语义；
`dev-yard status` 与 web 看板读取展示。

## 7. CLI 接入

- `dev-yard run <stage> JIRA [--print] [--dry-run]`：registry 解析任意阶段
  （含插件）调 `run_stage`；`--print`/`--dry-run` 语义与现有命令一致。
- `dev-yard stages`：列出 registry 全部阶段，内置标 `[builtin]`，
  插件标来源路径——插件作者的快速验证入口。
- 现有 `grill`/`spec`/`tickets` 命令保留，改为 `run_stage` 薄包装（批次 1）；
  `implement`/`review` 保留专用 service 函数（ticket 循环/merge 逻辑不动），
  仅 skill 映射、tools、protects 三项改由 registry 提供（批次 2）。
  `open` 的 skill/tools 数据同样来自 registry，但 `req_open` 函数本身不动
  （多 source 模式是其特有逻辑）。

## 8. Web 接入

- `web/board.py:19-25` 硬编码阶段列表改为 registry 生成（按 `order` 排序）；
  插件阶段按钮按 `requires_phase` 与当前 phase 匹配决定可用性。
- `web/jobs.py` 新增 run_stage 任务类型（现有 job 都走 service 层，套用同一模式）。
- 看板可展示 `stage_runs` 最近一次结果（批次 2 一起做）。

## 9. 内置阶段迁移（自举）

- `BUILTIN_STAGES` 与现有四张表**逐字段机械对照**；迁移是数据搬家不是重写。
- 分两批：批次 1 迁 `grill`/`spec`/`tickets`（纯文档阶段，风险低）；
  批次 2 迁 `implement`/`review`/`contract` 的映射三项（ticket 交互不动）。
- **等价性硬保证**：测试断言迁移前后每个内置阶段生成的 pi argv 完全一致。
- 迁移后删除 `SKILL_NAMES`/`SKILL_BUNDLES`/`STAGE_WRITE`/`STAGE_PROTECT`/
  `_tools_for`，旧引用改查 registry。

## 10. skill 解析与分发（三级搜索）

按 skill 名解析目录的顺序：

1. **插件目录**：`spec.skill_dir` 直接指向插件内 skill 目录（绝对路径，免安装）；
2. **workspace `.pi/skills/<name>/`**：存在即用（dogfooding / 用户覆盖）；
3. **包内 `dev_yard/skills/<name>/`**：兜底，`importlib.resources` 读取。

分发修复：`pyproject.toml` 增加force-include，把仓库根 `.pi/skills/`
映射进 wheel 的 `dev_yard/skills/`（单一事实源仍在 `.pi/skills/`，不搬文件、
不破坏本仓库 dogfooding）：

```toml
[tool.hatch.build.targets.wheel.force-include]
".pi/skills" = "dev_yard/skills"

[tool.hatch.build.targets.sdist.force-include]
".pi/skills" = "dev_yard/skills"
```

（sdist 同样包含，保证从 sdist 构建的 wheel 不缺 skill。）

## 11. 安全边界

- 插件阶段与内置阶段跑在同样的 `pi --approve --no-skills` 之下；能力上限 =
  `tools` 白名单 + prompt。声明 `bash` 即可改文件系统——信任模型与现状一致：
  **本地工具，装插件 = 信任其作者**，README 明示。
- `protects` 的快照集合默认不含 `STATUS.yaml` 本身（防 pi 篡改状态）；
  phase 只由宿主在 `sets_phase` 里写入。
- name 保留字校验防 CLI 命令覆盖；plugin.yaml 校验失败即报错，
  不存在"半加载"状态。

## 12. 测试策略

- `stages.py` 单元测试：坏 yaml、缺 plugin.yaml、插件重名、保留字冲突、
  非法 tools、非法 requires_phase、同名覆盖内置（应成功）。
- `run_stage` 测试（fake runner）：phase 门禁拒绝/放行、sets_phase 写入、
  `stage_runs` 记录成功与失败、protects 快照回滚、dry-run 不落盘。
- **argv 等价性测试**：对每个内置阶段断言迁移前后 `pi_argv` 输出逐项一致。
- 现有 pytest 套件全绿 + `ruff check` 干净。

## 13. 交付切分

- **批次 1（核心）**：`stages.py`（spec/registry/加载/校验）+ `yard.yaml`
  读取 + `run_stage` + CLI `run`/`stages` + 三级 skill 解析与 force-include +
  迁移 grill/spec/tickets + 全部测试。
- **批次 2（界面与收尾）**：web 看板动态阶段 + jobs 接 run_stage +
  implement/review/contract 映射迁移 + status 展示 `stage_runs`。
- **批次 3（文档与示例）**：README"插件开发指南"一节 + 仓库内示例插件
  （如 `plugins/example/`）作为活文档。

## 14. 后续可扩展方向（不在本 spec 范围）

- `exec_scope: ticket`（插件接入 ticket 循环与状态机）
- git URL / pip 插件源
- 生命周期事件钩子（freeze 后、ticket done 后的 webhook）
- 外部需求源/提测出口插件化
