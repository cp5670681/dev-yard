# dev-yard

多仓需求工作区：一个 Jira 需求对应一份跨仓 Spec、一组 git worktree。对齐用文档，实现用隔离分支；无依赖的票可以并行，同一仓并行时用子 worktree，做完就拆掉。

适合：前端 / 后端 / 其它服务数量不固定，需求经常跨仓，又不希望各仓的 `main` 工作副本被需求分支互相踩。

---

## 它解决什么问题

平时用 AI 开发的流程是：产品给需求 → grill 对齐 → 写成 spec → 拆成带依赖的 tickets → implement → code review。

如果每个仓单独开会话、直接在源码目录改：

- 多个需求会抢同一份 checkout
- 跨仓契约没有单一真相
- 无依赖的票不知道能不能并行、并行完怎么合

dev-yard 把「需求」当成隔离边界：仓只是实现面。

---

## 和 AI 技能怎么配合

[mattpocock/skills](https://github.com/mattpocock/skills) **已经绑在** `.pi/skills/`（pi 的项目技能目录）。不要再去插件或 skills.sh 另装一套，也不要跑 `/setup-matt-pocock-skills`。产物一律进 `reqs/<JIRA>/`。运行时只有 **pi**。

| 步骤 | 命令 | 读什么 | 写什么 |
|------|------|--------|--------|
| 对齐 | `yard grill` | `REQUIREMENT.md` + 各仓 `default_base` | `GRILL.md`，术语进根目录 `CONTEXT.md` |
| 写 spec | `yard spec` | `GRILL.md` | `SPEC.md`（不发 issue tracker） |
| 拆票 | `yard tickets` | `SPEC.md` | **一份** `TICKETS.md`（yard 格式，不是 `.scratch` 多文件） |
| 实现 | `yard implement` | Spec + 票 | 票的 worktree；pi 加载 implement + tdd |
| 审查 | `yard review` | diff vs `default_base` | 双轴 Standards + Spec |

运行时固定为 **`pi`**（二进制路径可用 `YARD_PI` 覆盖，不能换成别的 agent）。`yard grill|spec|tickets|implement|review` 会启动：

```text
pi --approve --append-system-prompt AGENTS.md --skill .pi/skills/<绑定技能> … "<启动提示>"
```

grill / spec / tickets 默认进 **pi TUI**（多轮对话）。implement / review 同样默认交互；加 `--print` 则 `pi -p` 跑完退出。`--dry-run` 只打印将要执行的 argv，不启动。

```bash
uv run yard grill PROJ-123
uv run yard spec PROJ-123
uv run yard tickets PROJ-123
uv run yard implement PROJ-123
uv run yard review PROJ-123
```

原语副本：`grilling`、`domain-modeling`、`tdd`、`codebase-design`。来源说明见 `.pi/skills/ORIGIN.md`。

没有合格的 `TICKETS.md` 不能 `freeze`。Grill 阶段不要在需求 worktree 里改业务代码。

---

## 推荐流程

```text
产品给需求
    → yard req open PROJ-123          # 建目录；有 Jira 凭证则拉标题/描述
    → yard grill PROJ-123             # pi TUI：grill-with-docs → GRILL.md
    → yard spec PROJ-123              # pi：to-spec → SPEC.md
    → yard tickets PROJ-123           # pi：to-tickets → TICKETS.md
    → yard req freeze PROJ-123        # 按票里出现过的仓建 req/PROJ-123 worktree
    → yard implement PROJ-123         # DAG：ready 的票各开一次 pi
    → yard review PROJ-123            # 每张票 implement 完立刻 review
    → yard review PROJ-123 --contract # 全票 done 后跨仓契约审查
    → 每仓一个 PR：req/PROJ-123 → default_base
```

需求级 worktree **一直留着**，方便回看和开 PR。从它拆出去的子 worktree（同仓并行）在票 `done` 时删除。

---

## 安装

需要 Python 3.12+、git、[uv](https://docs.astral.sh/uv/)、以及 PATH 上的 [pi](https://pi.dev)（`YARD_PI` 可指定绝对路径）。

```bash
cd /path/to/dev-yard
uv sync --group dev
uv run yard --help
uv run pytest
```

命令从「含有 `repos.yaml` 的目录」往上找 workspace 根。一般在本仓库根目录执行。

```bash
uv run yard init          # 已执行过可再跑，不会覆盖已有 repos.yaml
```

---

## 目录布局

```text
dev-yard/
  AGENTS.md                           # 给 pi 的项目说明
  repos.yaml                          # 仓注册表
  .pi/skills/                         # 绑定后的 mattpocock 技能（pi 会自动发现）
  .repos/<alias>/                     # 源 clone（gitignore）；repos.yaml 可写 path 覆盖
  reqs/<JIRA>/
    REQUIREMENT.md                    # 产品原文 / Jira
    GRILL.md                          # 多轮对齐
    SPEC.md                           # 跨仓契约（唯一真相）
    TICKETS.md                        # 人读的 DAG
    STATUS.yaml                       # 机读状态，由 CLI 维护
    worktrees/<alias>/                # 需求级 worktree，对应分支 req/<JIRA>，不清
  .yard-worktrees/<JIRA>/<alias>/<票id>/   # 子 worktree，票完成后删除
```

`.repos/`、`.yard-worktrees/`、`reqs/*/worktrees/` 已写入 `.gitignore`。

---

## 注册仓库

```yaml
# repos.yaml
repos:
  frontend:
    url: git@host:team/frontend.git
    default_base: main
    role: fe
  backend:
    url: git@host:team/backend.git
    default_base: main
    role: be
    # path: /home/you/src/backend   # 可选：复用已有 clone
```

```bash
uv run yard repo add backend git@host:team/backend.git --default-base main --role be
uv run yard repo add frontend git@host:team/frontend.git --role fe
uv run yard repo add backend git@host:team/backend.git --path /home/you/src/backend
uv run yard repo list
```

- `alias`：tickets 里的 `repo` 必须等于这个名字
- `role`：目前只是标记（`fe` / `be` / `svc`），不参与调度
- 未指定 `path` 时 clone 到 `.repos/<alias>/`
- **开需求时不会给注册表里所有仓建 worktree**，只给 `TICKETS.md` 里出现过的 alias 建

---

## 打开需求

```bash
uv run yard req open PROJ-123
```

创建 `reqs/PROJ-123/` 和骨架文件。若配置了 Jira 环境变量，会把标题和描述写入 `REQUIREMENT.md`；拉不到则骨架 + 警告，不失败。

环境变量（任选一组命名）：

| 变量 | 含义 |
|------|------|
| `JIRA_BASE_URL` 或 `JIRA_URL` | 例如 `https://jira.example.com` |
| `JIRA_EMAIL` 或 `JIRA_USER` | 账号 |
| `JIRA_API_TOKEN` 或 `JIRA_TOKEN` | API token |

---

## Tickets 格式

`TICKETS.md` 用人能改的 Markdown，CLI 按二级标题解析。

```markdown
# Tickets — PROJ-123

## T1: 后端下单接口
- repo: backend
- depends_on:
- parallel: false

## T2: 前端下单页
- repo: frontend
- depends_on: T1
- parallel: false

## T3: 后端库存校验
- repo: backend
- depends_on:
- parallel: true
```

规则：

- 一张票只改 **一个** `repo` alias
- `depends_on`：空格或逗号分隔的票 id；上游必须 **implement + review 都过（done）** 才进入 `ready`
- `parallel: true`：同仓还要同时干别的票时，才从需求 worktree 再拆子分支 `req/<JIRA>/<票id>`
- 串行票直接在需求级 worktree 上改，不生子 worktree

`freeze` 之后状态写在 `STATUS.yaml`，不要当常规流程手改。

票状态：`pending → ready → implementing → implemented → reviewing → done | blocked`

---

## Git 拓扑

```text
源仓 clone
  └── 需求分支 req/PROJ-123          ← worktree 常留
        └── 并行票 req/PROJ-123/T3   ← 子 worktree，合回需求分支后删除
```

- `freeze` 才建需求 worktree（从 `origin/<default_base>`，没有 remote 则用本地 `default_base`）
- 子 worktree **永远从当前需求分支** 建，合回同一条 `req/<JIRA>`
- 每仓 **一个 PR**：`req/PROJ-123` → 该仓 `default_base`
- 并行票若改同一文件，冲突在需求 worktree 里解，不要把子 worktree 当主线留着

```bash
uv run yard req freeze PROJ-123
uv run yard ticket start PROJ-123 T3    # 通常 implement 在需要并行时会自动建
uv run yard ticket done PROJ-123 T3     # merge 进需求分支并删除子 worktree
```

---

## Implement 与 Review

```bash
uv run yard implement PROJ-123              # 所有 ready 的票
uv run yard implement PROJ-123 T1 T3        # 指定票
uv run yard implement PROJ-123 --dry-run    # 不调 agent，只走状态机

uv run yard review PROJ-123
uv run yard review PROJ-123 T1
uv run yard review PROJ-123 --contract
uv run yard status
uv run yard status PROJ-123
```

调度：

- 入度为 0（依赖全是 `done`）的票为 `ready`，可并行
- 一票一次 agent 调用，`cwd` 为该票 worktree（需求 wt 或子 wt）
- 只读挂上该需求的 `SPEC.md`、`TICKETS.md`
- implement 成功 → `implemented`，立刻可以 review；失败 → `blocked`，不放行下游
- review 成功 → `done`（若有子 wt 则合回并删除）；失败 → `blocked`
- `--contract`：整需求跨仓契约审查，不改单票状态

运行时只有 `pi`。`YARD_PI` 仅在 `pi` 不在 PATH 上时用来指定绝对路径。`--dry-run` 不启动进程。

---

## 命令一览

| 命令 | 说明 |
|------|------|
| `yard init [目录]` | 建 `repos.yaml`、`reqs/`、gitignore 条目 |
| `yard repo add <alias> <url>` | `--default-base` `--role` `--path` |
| `yard repo list` | 列出已注册仓 |
| `yard req open <JIRA>` | 建需求目录与文档骨架 |
| `yard req freeze <JIRA>` | 为 tickets 中的仓建需求 worktree |
| `yard ticket start <JIRA> <票id>` | 建同仓并行子 worktree |
| `yard ticket done <JIRA> <票id>` | 合进需求分支并删子 worktree |
| `yard grill <JIRA>` | 启动 pi + grill-with-docs |
| `yard spec <JIRA>` | 启动 pi + to-spec |
| `yard tickets <JIRA>` | 启动 pi + to-tickets |
| `yard implement <JIRA> [票id…]` | `--dry-run` `--print` |
| `yard review <JIRA> [票id…]` | `--contract` `--dry-run` `--print` |
| `yard status [JIRA]` | 阶段、票状态、子 worktree |

---

## 第一期明确不做

- Web 界面（CLI 不绑 HTTP，以后可另做）
- 用 LangGraph 当编排内核
- 自动 `gh pr create`、自动建 Jira 子票
- 同仓并行改同一文件的自动语义合并
- 清理需求级 worktree（子 worktree 会清）
- 换成 Claude / Grok / 其它 agent 当运行时（固定 pi）

---

## 开发本仓库

```bash
uv sync --group dev
uv run pytest
```
