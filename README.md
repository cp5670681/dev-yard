# dev-yard

多仓需求工作区：一个 Jira 对应一份跨仓 Spec、一组 git worktree。对齐用文档，实现用隔离分支。

CLI 入口是 **`dev-yard`**（别名 `devyard`）。不要用 `yard`：那是 Ruby 的文档工具，RVM 下会抢 PATH。

---

## 0. 先看这一页：跑完一个需求要按什么顺序

在 **本仓库根目录**（含 `repos.yaml` 的目录）执行。把 `PG-13068` 换成你的 Jira key。

```bash
# —— 只做一次（第 1 节）——
uv sync --group dev
uv run dev-yard init
uv run dev-yard repo add …     # 每个业务仓登记一次
# 确认：claude、pi 在 PATH 上；Claude Code 已配 Atlassian MCP

# —— 每个需求 ——
uv run dev-yard req open PG-13068          # Claude 抽本票产品说明 → reqs/PG-13068/REQUIREMENT.md
uv run dev-yard grill PG-13068             # pi TUI：对齐，写 GRILL.md
uv run dev-yard spec PG-13068              # pi TUI：写 SPEC.md
uv run dev-yard tickets PG-13068           # pi TUI：写 TICKETS.md（每张票一个 repo alias）
uv run dev-yard req freeze PG-13068        # 按票里出现过的仓建 req/PG-13068 worktree
uv run dev-yard implement PG-13068         # 所有 ready 票；成功后停在 implemented
uv run dev-yard review PG-13068            # 审查 implemented 的票 → done
uv run dev-yard review PG-13068 --contract # 跨仓契约（必须已经 freeze）
uv run dev-yard status PG-13068

# 然后自己开 PR：每个仓  req/<JIRA>  →  该仓 default_base（如 master）
```

`grill` / `spec` / `tickets` / `implement` / `review` 默认进 **pi 交互 TUI**。加 `--print` 变成 `pi -p` 跑完退出。加 `--dry-run` 只打印将执行的命令，不启动 agent、不改 git。

---

## 1. 一次性准备

### 1.1 本机依赖

| 工具 | 用途 | 怎么确认 |
|------|------|----------|
| Python 3.12+ | 本 CLI | `python --version` |
| [uv](https://docs.astral.sh/uv/) | 安装本仓库 | `uv --version` |
| git | clone / worktree | `git --version` |
| [pi](https://pi.dev) | grill/spec/tickets/implement/review | `which pi`；可用 `YARD_PI` 指绝对路径 |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) `claude` | 仅 `req open` | `which claude`；可用 `YARD_CLAUDE` 指绝对路径 |

```bash
cd /path/to/dev-yard
uv sync --group dev
uv run dev-yard --help
uv run pytest
```

激活 `.venv` 后可直接敲 `dev-yard …`，否则始终用 `uv run dev-yard …`。下文一律写 `uv run dev-yard`。

命令从当前目录往上找 **含 `repos.yaml` 的目录** 当作 workspace 根。请在本仓库根执行，不要进 `reqs/` 或业务仓目录再跑。

### 1.2 初始化工作区

```bash
uv run dev-yard init
```

可重复执行：已有 `repos.yaml` 不会被覆盖。会补 `.gitignore` 条目（`.repos/`、`.yard-worktrees/`、`reqs/`、`.env`），建空的 `reqs/`、`.repos/`，若没有 `.env.example` 会写一份。

### 1.3 登记业务仓（必须，否则 grill/freeze 没有代码可读/可建 worktree）

仓库里的 `repos.yaml` 默认是空的（`repos: {}`）。本机路径不要提交进 git。模板见 `repos.yaml.example`。

**两种登记方式，二选一（或混用）：**

1. **托管 clone（推荐给「我没有日常在改的副本」）**  
   不写 `--path`。CLI 把仓 clone 到 `.repos/<alias>/`，`grill` 时会把它切到 `default_base` 并尽量 ff `origin`。

2. **复用你已经有的工作副本**  
   加 `--path /你的/本地仓`。这只是 **worktree 的母仓**。CLI **不会**帮你 `git checkout`。跑 grill/spec/tickets 前，这个目录必须已经在 `default_base` 上（这些仓经常是 `master`），否则命令失败。

```bash
# 例：RCC research 三仓（按你本机路径改）
uv run dev-yard repo add research \
  git@git.rccchina.com:leads-in/research.git \
  --default-base master --role be \
  --path /home/chengpeng/rcc3/research

uv run dev-yard repo add research-front \
  git@git.rccchina.com:leads-in/research-front.git \
  --default-base master --role fe \
  --path /home/chengpeng/rcc3/research-front

uv run dev-yard repo add research-report-service \
  git@git.rccchina.com:leads-in/research-report-service.git \
  --default-base master --role svc \
  --path /home/chengpeng/rcc3/research-report-service

uv run dev-yard repo list
```

没有现成副本、让 CLI 自己 clone：

```bash
uv run dev-yard repo add research \
  git@git.rccchina.com:leads-in/research.git \
  --default-base master --role be
# clone 落在 .repos/research/
```

`repo list` 一行：`alias  role  default_base  url`。

登记规则：

- `alias` 就是后面 `TICKETS.md` 里 `- repo:` 的值，必须完全一致。
- `role`（`be` / `fe` / `svc`）只是标记，不参与调度。
- `--path` 可以是相对路径，相对 **workspace 根** 解析，必须是已有 git 仓。
- 开需求 **不会** 给注册表里所有仓建 worktree；只给 `TICKETS.md` 里出现过的 alias 建。

跑 grill 前检查 path 仓是否在基线上：

```bash
git -C /home/chengpeng/rcc3/research rev-parse --abbrev-ref HEAD
# 期望 master（或你登记的 default_base）
# 有未提交改动先自己处理干净，dev-yard 不会帮你 stash
```

### 1.4 Claude Code（`req open` 用）

默认 `dev-yard req open` 会启动本机 `claude -p`，走你已经配好的 **Atlassian MCP（Jira + Confluence）**。不读 `.env` 里的 Jira 密码。

请先在 Claude Code 里能手动查到目标 Jira。二进制不在 PATH 时：

```bash
export YARD_CLAUDE=/绝对路径/claude
```

只想看将执行的命令、不写盘不联网：

```bash
uv run dev-yard req open PG-13068 --dry-run
```

### 1.5 pi（其余阶段用）

grill / spec / tickets / implement / review **只跑 pi**，不要换成 Claude。技能已经在 `.pi/skills/`，不要再去插件或 skills.sh 另装一套，也不要跑 `/setup-matt-pocock-skills`。

```bash
export YARD_PI=/绝对路径/pi          # 仅当 `pi` 不在 PATH
export YARD_PI_PROVIDER=rcc          # 可选，传给 pi --provider
export YARD_PI_MODEL=MiniMax-M3      # 可选，传给 pi --model
```

可写进仓库根 `.env`（已 gitignore）。`dev-yard` 启动时会加载它，不覆盖已经 export 的变量。

### 1.6 `.env`（只有 `--http` 才需要）

一般 **不用**。仅当 `req open --http`（旧 HTTP 爬取）时才要 Jira 账号：

```bash
cp .env.example .env
# 填 JIRA_BASE_URL / JIRA_USERNAME / JIRA_PASSWORD
# CONFLUENCE_BASE_URL 可选；能从 issue 里的 wiki URL 推断时可不填
```

| 变量 | 含义 |
|------|------|
| `JIRA_BASE_URL` 或 `JIRA_URL` | 如 `https://jira.example.com` |
| `JIRA_USERNAME`、`JIRA_EMAIL` 或 `JIRA_USER` | 账号 |
| `JIRA_PASSWORD`、`JIRA_API_TOKEN` 或 `JIRA_TOKEN` | 密码或 token |
| `CONFLUENCE_BASE_URL` | 可选 |
| `CONFLUENCE_USERNAME` / `CONFLUENCE_PASSWORD` | 可选，缺省复用 Jira 账号 |
| `YARD_CLAUDE` / `YARD_PI` / `YARD_PI_PROVIDER` / `YARD_PI_MODEL` | 见上 |

---

## 2. 每个需求：逐步做什么、你会看到什么

以下都在 **dev-yard 仓库根** 执行。

### 2.1 打开需求 — `req open`

```bash
uv run dev-yard req open PG-13068
```

- 创建 `reqs/PG-13068/`（整棵 `reqs/` 已 gitignore，只留本机）。
- 调 Claude + MCP：只抽 **这一张 Jira** 的产品说明和相关截图，写 `REQUIREMENT.md`，图片在 `assets/`。
- 不爬历史 Confluence、不把上级模块文档整页拉下来。
- 若还没有，会补骨架 `GRILL.md` / `SPEC.md` / `TICKETS.md`，并把 `STATUS.yaml` 的 `phase` 设为 `open`。

成功时 stdout 打印需求目录路径。Claude 没写 `REQUIREMENT.md` 时会写骨架并在 stderr 提示。

| 情况 | 命令 |
|------|------|
| 先看命令、不写盘 | `req open PG-13068 --dry-run` |
| 需求已经 grill/freeze 过，要重新抽 | `req open PG-13068 --force`（否则会拒绝把 phase 打回去） |
| 不用 Claude、走 HTTP（会带历史页，一般不要） | `req open PG-13068 --http` |

`claude` 找不到会直接失败，**不会**先删掉已有 `assets/`。

### 2.2 对齐 — `grill`

```bash
uv run dev-yard grill PG-13068
```

打开 pi TUI。你要做的事：按提示读 `REQUIREMENT.md` 和源码（`default_base`），把问答和拍板写进 `GRILL.md`。术语进仓库根 `CONTEXT.md`，难逆决策进 `docs/adr/`。

- 只应改 `GRILL.md`（以及术语/ADR）。若模型误写了 `SPEC.md` / `TICKETS.md`，CLI 会 **回滚** 并在退出时打印 `restored …`。
- 不要在业务仓里改代码、不要自己切分支。
- 托管 clone 会被切到 `default_base`；`--path` 仓必须你已经在基线上。
- 对齐完退出 TUI，再跑下一阶段。下一步是 `spec`。

一次性非交互：`uv run dev-yard grill PG-13068 --print`。

### 2.3 写 spec — `spec`

```bash
uv run dev-yard spec PG-13068
```

根据 `GRILL.md` 写 `SPEC.md`。跨仓 API / 事件 / 字段写在 **Contracts**。不要访谈、不要写 `TICKETS.md`。误写票文件同样会被回滚。

### 2.4 拆票 — `tickets`

```bash
uv run dev-yard tickets PG-13068
```

只写一份 `reqs/PG-13068/TICKETS.md`。不要发 GitHub/Jira 子票，不要写成多文件 `.scratch`。

CLI **只解析** 这种形状（其它二级标题如「验收」会被忽略；没有 `- repo:` 的票不入列）：

```markdown
# Tickets — PG-13068

## T1: 后端下单接口
- repo: research
- depends_on:
- parallel: false

## T2: 前端下单页
- repo: research-front
- depends_on: T1
- parallel: false

## T3: 同仓可并行的另一刀
- repo: research
- depends_on:
- parallel: true
```

规则：

- 标题必须是 `## T<数字>:`（`T1`、`T2`…）。
- 一张票只绑 **一个** 已 `repo add` 的 alias。跨仓拆多张，用 `depends_on`。
- `depends_on`：空格或逗号分隔的票 id。上游必须 **implement + review 都过（`done`）** 下游才 `ready`。
- `parallel: true`：同仓还要同时干别的票时，implement 会再拆子 worktree。串行票直接在需求级 worktree 上改。
- 票上的 `repo` 若还没登记，下一步 `freeze` 会报 `unknown repo alias`。

人可以改 `TICKETS.md`；改完再 `freeze` / `implement`。STATUS 里已经没了的旧票 id 会被丢掉，以这份文件为准。

### 2.5 冻结并建 worktree — `req freeze`

```bash
uv run dev-yard req freeze PG-13068
```

前提：`TICKETS.md` 里至少有一张带 `repo:` 的 `T<n>` 票。骨架示例票不能 freeze。

效果：

- 只给票里出现过的 alias 建需求 worktree：`reqs/PG-13068/worktrees/<alias>/`
- 分支名：`req/PG-13068`（各仓各一条）
- 起点：`origin/<default_base>`，没有 remote 则用本地 `default_base`
- `STATUS.yaml`：`phase=frozen`，无依赖的票变 `ready`

stdout 打印建好的 worktree 路径。需求级 worktree **一直留着**，方便回看和开 PR。

### 2.6 实现 — `implement`

```bash
uv run dev-yard implement PG-13068           # 所有 ready（含上次卡在 implementing 的）
uv run dev-yard implement PG-13068 T1 T3     # 指定票（会跳过 ready 检查，连 done 也能再跑，慎用）
uv run dev-yard implement PG-13068 --dry-run    # 列出将跑的票，不改 STATUS、不动 git
```

- 一次跑一张票：cwd 是该票 worktree，pi 加载 implement + tdd + codebase-design。
- 成功 → `implemented`，**不会**自动 review。失败 → `blocked`，不放行下游。
- `--dry-run` 只列出将跑的票，不改 `STATUS.yaml`、不建/删 worktree。
- 需要同仓并行时自动 `ticket start`（子分支 `req/<JIRA>/<票id>`，目录在 `.yard-worktrees/…`）。
- 进程被杀停在 `implementing`：再跑无参数 `implement` 会重入。
- 结束时打印 `ran: T1, T3` 或 `ran: (none)`（没有 ready 票）。

实现完成后自己跑 review，不要等 CLI 自动审。

### 2.7 审查 — `review`

```bash
uv run dev-yard review PG-13068              # 所有 implemented（含上次卡在 reviewing 的）
uv run dev-yard review PG-13068 T1
uv run dev-yard review PG-13068 --contract
```

票级：对照 `SPEC.md` + 该票，CLI 已把相对 `default_base` 的 `git log` / `git diff` 写进提示。成功 → `done`；有子 worktree 则合回需求分支，并删子 worktree **和子分支**。失败或摘要里出现 `REVIEW_FAILED` → `blocked`。

`--contract`：审所有需求 worktree 与 SPEC 的跨仓契约。必须已经 freeze（STATUS 里有 repos 且 worktree 在盘上），否则直接失败，不会标 passed。全部票 `done` 且契约通过时，`phase` 会变成 `done`。

### 2.8 看状态 — `status`

```bash
uv run dev-yard status
uv run dev-yard status PG-13068
```

示例：

```text
PG-13068  phase=frozen
  T1  ready  repo=research  child=-
  T2  pending  repo=research-front  child=-
  contract=passed
```

票状态机：`pending → ready → implementing → implemented → reviewing → done | blocked`。

`blocked` 处理：改代码或改票后，`implement <JIRA> T1` 指定票重跑（指定 id 不要求当前是 ready）。

### 2.9 开 PR（CLI 不会做）

每个仓 **一个 PR**：`req/PG-13068` → 该仓 `default_base`。

```bash
git -C reqs/PG-13068/worktrees/research status
git -C reqs/PG-13068/worktrees/research log --oneline origin/master..HEAD
# 按你们平时的方式 push / 开 MR
```

不要把子分支 `req/PG-13068/T3` 当主线留着。并行改同一文件的冲突在需求 worktree 里解。

---

## 3. 目录长什么样

```text
dev-yard/                          ← 你执行命令的地方
  AGENTS.md                        # pi 会从 cwd 读
  repos.yaml                       # 仓登记表（本机填，不要提交 path）
  repos.yaml.example
  .env / .env.example
  .pi/skills/                      # 已绑定的技能，不要另装
  .repos/<alias>/                  # 未指定 path 时的源 clone
  reqs/<JIRA>/                     # 本机产物
    REQUIREMENT.md
    GRILL.md
    SPEC.md
    TICKETS.md
    STATUS.yaml                    # CLI 维护，不要当常规流程手改
    assets/                        # req open 拉下来的图
    worktrees/<alias>/             # 需求级 worktree，分支 req/<JIRA>
  .yard-worktrees/<JIRA>/<alias>/<票id>/   # 并行子 worktree，done 时删除
  CONTEXT.md                       # 术语（grill 时可能写在仓库根）
  docs/adr/                        # ADR
```

`.repos/`、`.yard-worktrees/`、整个 `reqs/` 已 gitignore。

---

## 4. Git 拓扑

```text
源仓（path 或 .repos/<alias>）
  └── 需求分支 req/PROJ-123          ← worktree 常留（freeze 建）
        └── 并行票 req/PROJ-123/T3   ← 子 worktree，review/ticket done 后删除
```

手工对应命令（一般不必，implement 会在需要时 `ticket start`）：

```bash
uv run dev-yard ticket start PG-13068 T3
uv run dev-yard ticket done PG-13068 T3    # merge 进需求分支，删子 wt 和子分支
```

`ticket start` 要求已经 freeze（父 worktree 存在）。重开同一张并行票会把子分支重置到当前 `req/<JIRA>`，不会挂过期分支。

---

## 5. 和 AI 技能怎么配合

[mattpocock/skills](https://github.com/mattpocock/skills) 已经绑在 `.pi/skills/`。产物一律进 `reqs/<JIRA>/`。来源说明：`.pi/ORIGIN.md`。

| 步骤 | 命令 | 运行时 | 读 | 写 |
|------|------|--------|----|----|
| 抽需求 | `req open` | **Claude Code** + Atlassian MCP | Jira / 本票 Confluence | `REQUIREMENT.md`、`assets/` |
| 对齐 | `grill` | **pi**：grill-with-docs + grilling + domain-modeling | REQUIREMENT + 源仓 `default_base` | `GRILL.md`、`CONTEXT.md`、ADR |
| 写 spec | `spec` | pi：to-spec | GRILL | `SPEC.md` |
| 拆票 | `tickets` | pi：to-tickets | SPEC | `TICKETS.md` |
| 实现 | `implement` | pi：implement + tdd + codebase-design | SPEC + 票 | 该票 worktree 里的代码 |
| 审查 | `review` | pi：code-review | 内联的 git diff + SPEC | 报告；硬违规应非零退出并写 `REVIEW_FAILED` |

pi 启动形如：

```text
pi --approve --no-skills --tools <阶段工具> --skill .pi/skills/<绑定技能> … "<启动提示>"
```

pi 从 cwd 加载 `AGENTS.md`。文档阶段（grill/spec/tickets）没有 bash；review 没有 bash/edit（diff 由 CLI 写入提示）；implement 有 bash，以便跑测试和 git commit。

---

## 6. 命令与常用开关

| 命令 | 说明 |
|------|------|
| `dev-yard init [目录]` | 建 `repos.yaml`、`reqs/`、gitignore |
| `dev-yard repo add <alias> <url>` | `--default-base`（默认 `main`）`--role`（默认 `svc`）`--path` |
| `dev-yard repo list` | 列出已登记仓 |
| `dev-yard req open <JIRA>` | `--http` `--dry-run` `--force` |
| `dev-yard req freeze <JIRA>` | 按 TICKETS 建需求 worktree |
| `dev-yard ticket start <JIRA> <票id>` | 并行子 worktree |
| `dev-yard ticket done <JIRA> <票id>` | 合进需求分支并删子 wt / 子分支 |
| `dev-yard grill <JIRA>` | `--dry-run` `--print` |
| `dev-yard spec <JIRA>` | `--dry-run` `--print` |
| `dev-yard tickets <JIRA>` | `--dry-run` `--print` |
| `dev-yard implement <JIRA> [票id…]` | `--dry-run` `--print` |
| `dev-yard review <JIRA> [票id…]` | `--contract` `--dry-run` `--print` |
| `dev-yard status [JIRA]` | 阶段、票状态、子 worktree |

涉及哪些仓由 **人和 grill/spec/tickets** 根据文档和代码定，不是 Jira 自动推断。

---

## 7. 操作时常见失败

| 现象 | 原因 | 怎么办 |
|------|------|--------|
| `repos.yaml not found` | 不在 workspace 里 | 回到本仓库根再跑 |
| `(no repos)` / grill 提示里没有源码路径 | 还没 `repo add` | 第 1.3 节登记仓 |
| `is on <branch>, need master; … will not move a path-mapped clone` | `--path` 仓不在基线 | 自己 `git checkout` 到 `default_base`，有脏改动先处理 |
| `claude not found` | 没装 Claude Code | 装好或设 `YARD_CLAUDE` |
| `pi not found` | 没装 pi | 装好或设 `YARD_PI` |
| `already phase=frozen; pass --force` | 需求已往下走还 `req open` | 确认要重抽再用 `--force` |
| `TICKETS.md has no tickets with a repo` | 还没拆票，或标题不是 `T1` / 缺 `repo:` | 跑 `tickets` 或手改格式 |
| `unknown repo alias …` | 票上的名字和 `repo add` 不一致 | 改 TICKETS 或补登记 |
| `missing requirement worktree … freeze first` | 没 freeze 就 implement / ticket start / `--contract` | 先 `req freeze` |
| `ran: (none)` | 没有 ready 票 | `status`：看是 pending（等上游 done）还是 blocked（指定票重跑） |
| freeze 时报 git 错 | fetch 失败，或 ff 不了 | 检查网络 / 本地与 origin 是否分叉 |
| 路径已存在且不是 worktree | freeze 中途留下的脏目录 | 清掉该目录再 freeze；空目录 CLI 会自己删 |

---

## 8. 第一期明确不做

- Web 界面
- 自动 `gh pr create`、自动建 Jira 子票
- 同仓并行改同一文件的自动语义合并
- 清理需求级 worktree（子 worktree 会清）
- 把 grill/spec/tickets/implement/review 换成 Claude / Grok（固定 pi）

---

## 9. 开发本仓库

```bash
uv sync --group dev
uv run pytest
```
