# 插件阶段：小而完整

- 日期：2026-09-15
- 状态：现行设计（取代 09-13 中尚未收紧的部分）
- 取代：[2026-09-13-plugin-stages-design.md](2026-09-13-plugin-stages-design.md) 中尚未收紧的部分（已落地的加载/run_stage/CLI 保留）
- 撤回：[2026-09-15-plugin-capabilities-design.md](2026-09-15-plugin-capabilities-design.md)（exec/hook/views/MCP 不实施）
- 决策：插件 = 一条 requirement 级 **agent 阶段**。内核 phase 四值封闭。契约封闭、未知键失败。不把部署/e2e/复杂 UI 当插件系统的目标。

## 1. 一句话

第三方用一个目录（`plugin.yaml` + `SKILL.md`）给某条需求多跑一段 pi。
宿主负责启用、门禁、保护 `STATUS.yaml`、记 `stage_runs`、在看板加一颗按钮。
插件不拥有状态机、worktree、票循环、页面。

这就是全部。做完即完整。

## 2. 目标与非目标

### 目标

1. 能新增一条 agent 阶段，行为可预测、可测试、不破坏内核。
2. 能覆盖同名内置阶段的 **skill / tools / guidance / protects / bundles**（换提示词和工具），换不了票循环。
3. 契约封闭：`plugin.yaml` 合法键是固定集合，多一个键就加载失败。
4. 对外说法与机制一致：加的是 agent 阶段，不是通用插件平台。

### 非目标（冻结，有真实需求再开另一份设计）

- 确定性脚本执行器、生命周期 hook、MCP 注入、CLI 子命令、Web 自定义视图
- ticket 级阶段、需求源/提测出口、git/pip 分发
- 插件发明 phase、插件改 PIPELINE 步骤条、插件配独立 pi 模型
- 把 qa-powers 或任何带自己目录/面试/页面的产品收成插件

## 3. 内核不变量（由构造保证，不是约定）

| 不变量 | 保证方式 |
|---|---|
| `phase ∈ {open, frozen, testing, done}` | 插件 yaml **禁止** `sets_phase`。跑插件前后强制快照/恢复 `STATUS.yaml`，然后由宿主写入 `stage_runs` |
| 票状态 / `contract_review` / 提测槽 | 同上：`STATUS.yaml` 对插件不可变 |
| 主干步骤条 | `PIPELINE` 仍只含内置九步；插件永不进入 |
| freeze / implement 循环 / review 循环 / push / submit-test | 专用 service，不经 `run_stage` |

插件进度只出现在 `STATUS.yaml` 的 `stage_runs.<name>`：`{at, ok, summary}`。看板已有该字段即可，不新造 annotations。

## 4. 插件是什么

```
my-yard/
├── yard.yaml                 # plugins: [plugins/example]
└── plugins/example/
    ├── plugin.yaml
    └── SKILL.md
```

一个插件包 = **恰好一条**阶段。阶段名 = `plugin.yaml` 的 `name`。
启用 = 出现在 `yard.yaml` 的 `plugins:` 列表（相对 yard root 的目录）。不扫盘。

合法用途（按推荐顺序）：

- 只读检查（文档就绪、清单、diff 摘要）——仓库 `plugins/example/` 就是这个
- 多跑一轮审查类 agent（额外的安全/无障碍/文案审）
- 覆盖内置 `review`/`spec` 等的 skill，换提示词

不宣传、不举例：部署、e2e、安全扫描二进制、带复杂产物的子产品。

## 5. `plugin.yaml` 封闭契约

允许的键（仅此 11 个；出现其它键 → 加载失败，消息列出非法键）：

```yaml
name: example                 # 必填；^[a-z][a-z0-9-]{0,31}$
title: 产物自检               # 可选；看板按钮、`dev-yard stages` 展示
description: 需求文档就绪度检查   # 可选；仅展示，不参与执行
skill: example                # 可选；默认 = name；目录相对插件根，内必须有 SKILL.md
bundles: []                   # 可选；按名走 workspace .pi/skills → 包内 skills
tools: [read, grep, find, ls] # 必填；非空；每项 ∈ {read,bash,grep,find,ls,edit,write,mcp}
protects: [REQUIREMENT.md, GRILL.md, SPEC.md, TICKETS.md]  # 可选；默认 []
requires_phase: frozen        # 可选；缺省或不写 = 不检查；若写则必须 ∈ {open,frozen,testing,done}
lists_sources: false          # 可选；默认 false
order: 45                     # 可选；默认 50；只影响 stages 列表和额外按钮排序
guidance: 只读检查，不改文件。 # 可选；注入 prompt
```

禁止：

- `sets_phase`（写了就失败，提示改用 `stage_runs`）
- 未知键（含拼错的 `require_phase`、`tool`）
- `name` 命中 CLI 保留字：`init`、`repo`、`req`、`ticket`、`web`、`status`、`push`、`run`、`stages`、`review-override`、`version`（与现网一致；`grill`/`spec` 等内置阶段名允许，表示覆盖）

`requires_phase` 不再接受「其它插件发明的 phase」。`null` / 缺省 / 空字符串都视为不检查。

`yard.yaml` 只解释 `plugins:` 列表；其它顶层键忽略（工作区配置以后可加，不在本设计）。

## 6. 覆盖内置

插件 `name` 等于内置阶段名 → 替换 registry 里那条 `StageSpec` 的 skill/tools/guidance/protects/bundles/lists_sources/title。这是故意的。

覆盖的含义收窄为：

| 还走插件的 | 仍走专用 service 的 |
|---|---|
| 该阶段被 `run_stage` 执行时的 prompt/tools/skill（`grill`/`spec`/`tickets`） | `open` 的 Jira 采集与写盘 |
| `implement`/`review`/`contract` 查 registry 得到的 skill 与 tools | 这三者的票循环、child worktree、merge、契约覆盖 |

两个已启用插件不得同名。覆盖不是合并：插件必须写全它要用的 `tools` 等，缺的就是缺的。

## 7. 执行

`service.run_stage` 对**非内置**阶段固定为：

1. 需求目录必须存在
2. `requires_phase` 与当前 phase **相等**才放行（未声明则跳过）
3. 快照 `protects` **再加 `STATUS.yaml`**（宿主强制，插件不能关掉）
4. cwd = yard root；prompt 仍走 `session_prompt_for`（guidance、可选源码清单）
5. pi `--approve --no-skills` + 声明的 tools/skill
6. 恢复快照（含 `STATUS.yaml`）
7. 宿主在 `jira_lock` 内写入 `stage_runs.<name>`，**不改 phase**

内置阶段的 `protects` / `sets_phase` 保持现状（`open` 仍可 `sets_phase: open`），本设计不改内置语义。

dry-run：不跑 pi、不写盘。`--print`：pi `-p`，与现命令一致。

## 8. CLI 与 Web（把话说死）

| 入口 | 行为 |
|---|---|
| `dev-yard stages` | 全部 StageSpec。内置标 `[builtin]`，插件标路径，有 title 则打印 title |
| `dev-yard run <插件名> JIRA` | `run_stage` |
| `dev-yard run grill\|spec\|tickets JIRA` | 同上（可被覆盖） |
| `dev-yard run open\|implement\|review\|contract JIRA` | **退出码 2**，提示用对应专用命令。覆盖了 `review` 也不能用 `run review` 当票循环 |
| `dev-yard grill/spec/tickets/...` | 不变 |
| 看板主干步骤条 | 不变，不含插件 |
| 看板额外按钮 | 仅 **name 不是已有 Action id** 的插件。覆盖 `grill` 不会出现第二颗「对齐」 |
| 看板按钮可用 | `requires_phase` 与当前 phase 相等；未声明则始终可点 |
| 设置页 pi 模型 | 仍只列出内置七阶段。插件用工作区默认 provider/model |
| Job | 新插件名 → `run_stage`；覆盖后的 grill/spec/tickets 仍走原来的 job 分支（内部已是 `run_stage`） |

## 9. 安全

与现在同一句：启用插件 = 信任作者。`tools` 含 `bash` 就能改工作区。

本设计多保证的只有：`STATUS.yaml` 必回滚，所以信任不等于「能改内核 phase」。文档产物靠 `protects` 回滚；业务仓 / worktree **不在**保护范围——需要改代码的阶段本就不该做成这种插件。

## 10. 文档与示例

README「插件」节只保留：

- 一个目录 = yaml + SKILL.md
- 封闭字段表（无 `sets_phase`，无部署示例）
- `dev-yard stages` / `dev-yard run example JIRA`
- 覆盖内置 = 换 skill，票循环不动
- 信任模型 + `STATUS.yaml` 由宿主保护

`plugins/example/` 继续当活文档：只读自检，`requires_phase` 不写（文档阶段随时可跑），`protects` 四份 md，**不得**含 `sets_phase`。`description` 保留并真正用于 `stages` 展示。

## 11. 相对 09-13 / 现网的差分

| 项 | 现网 / 09-13 | 本设计 |
|---|---|---|
| `sets_phase` | 插件可写任意值，并可被其它插件 require | **禁止** |
| 自定义 phase | 加载期承认 | **删除** |
| 未知 yaml 键 | 静默忽略（example 的 `description` 未读） | **失败** |
| `STATUS.yaml` | 默认可被 agent 改、随后被 host load 固化 | 插件阶段**强制**进快照 |
| `run implement/review/contract/open` | 会走 `run_stage`，与专用命令分叉 | **拒绝** |
| 覆盖内置后看板 | 可能出现重复按钮 | 不重复 |
| README 例子 | 部署 / e2e / 扫描 | 自检 / 加审 |
| exec/hook/UI | 09-15 草案 | **撤回** |

保留：显式启用、同名覆盖、fail-fast、三级 skill 解析、`run_stage` 共用、argv 等价性、example 插件。

## 12. 实施切分（一次做完，不再分能力批次）

1. `stages.py`：封闭键集合；拒绝 `sets_phase` 与未知键；`requires_phase` 只许四值；读入 `description`（可挂在 StageSpec 上仅供展示）。
2. `run_stage`：非内置阶段强制保护 `STATUS.yaml`；去掉插件路径上的 `sets_phase` 写入（内置不受影响）。
3. CLI：`run` 对 `open/implement/review/contract` 拒绝；`stages` 打印 title/description。
4. Web：额外按钮排除已有 Action id；不改 PIPELINE / `PI_STAGES`。
5. README + example：去掉 `sets_phase: null`；description 生效。
6. 测试替换：删除「插件发明 phase」用例，改为拒绝；补未知键、强制恢复 STATUS.yaml、`run review` 拒绝、覆盖 grill 不出现双按钮。

不改 ticket 状态机、不改 `req_open`、不加新文件层、不实施 09-15。

## 13. 完成标准

- 新插件不能把 phase 写成四值以外，也不能靠改 `STATUS.yaml` 留下 phase 变化
- `plugin.yaml` 多一个键或写 `sets_phase` → `load_registry` 抛 `ValueError`
- `dev-yard run review` 提示专用命令，退出码 2
- README 不再把部署/e2e 当插件头条
- 现有内置 argv 等价性与 grill/spec/tickets 行为保持
- 09-15 能力模型不在代码里出现
