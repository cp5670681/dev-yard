# dev-yard

> **面向多仓协作的 AI 研发工作区**  
> 单需求驱动多仓库：以文档对齐方案与跨仓契约，以独立 Git Worktree 隔离分支并调度 Agent 实现与审查。

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Package Manager](https://img.shields.io/badge/uv-managed-blueviolet.svg)](https://docs.astral.sh/uv/)
[![Testing](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](#开发与测试)

CLI 入口为 **`dev-yard`**（或简写 **`devyard`**）。

---

## 核心流程

```text
req open ──► grill ──► spec ──► tickets ──► req freeze ──► implement ──► review ──► submit-test ──► req test ──► [用例人工审核] ──► 执行
(拉需求)     (对齐)    (契约)    (拆票)      (建Worktree)   (编码实现)   (契约审查)  (提测)        (设计用例)      (通过/打回重做)   (跑用例)
```

---

## 插件：加一条 agent 阶段

一个目录 = `plugin.yaml` + `SKILL.md`。给某条需求多跑一段 pi（只读自检、额外审查、或换掉内置阶段的 skill）。不是通用插件平台，不能发明 phase、不能改主干步骤条、不能接管票循环。

```
my-yard/
├── yard.yaml                 # plugins: [plugins/example]
└── plugins/example/
    ├── plugin.yaml
    └── SKILL.md
```

启用 = 写进 `yard.yaml` 的 `plugins:` 列表（相对 yard root 的目录）。不扫盘。

`plugin.yaml` 只允许这些键（多一个键或写 `sets_phase` 都会加载失败）：

```yaml
name: example                 # 必填；^[a-z][a-z0-9-]{0,31}$
title: 产物自检               # 可选；看板按钮、`dev-yard stages`
description: 需求文档就绪度检查   # 可选；仅展示
skill: example                # 可选；默认 = name；目录相对插件根，内必须有 SKILL.md
bundles: []                   # 可选；按名走 workspace .pi/skills → 包内 skills
tools: [read, grep, find, ls] # 必填；非空；∈ {read,bash,grep,find,ls,edit,write,mcp}
protects: [REQUIREMENT.md, GRILL.md, SPEC.md, TICKETS.md]  # 可选；默认 []
requires_phase: frozen        # 可选；缺省 = 不检查；若写则必须 ∈ {open,frozen,testing,done}
lists_sources: false          # 可选；默认 false
order: 45                     # 可选；默认 50；只影响 stages 列表和额外按钮排序
guidance: 只读检查，不改文件。 # 可选；注入 prompt
```

使用：

```bash
dev-yard stages              # 内置标 [builtin]，插件标路径，有 title/description 则打印
dev-yard run example PROJ-101
```

覆盖内置：插件 `name` 等于 `grill`/`spec`/`tickets`/`review` 等即替换那条阶段的 skill/tools/guidance（换提示词）。票循环仍走 `dev-yard implement` / `dev-yard review`；`dev-yard run open|implement|review|contract|qa-design|qa-run|test` 会拒绝。插件不得占用 `qa-design` / `qa-run`。两个已启用插件不得同名。

信任模型：启用插件 = 信任作者。`tools` 含 `bash` 就能改工作区。宿主强制回滚 `STATUS.yaml`，再只写入 `stage_runs`，所以插件跑完 phase / 票状态 / 提测槽都不变。文档产物靠 `protects` 回滚；业务仓 / worktree 不在保护范围。

活文档：仓库内 [`plugins/example/`](plugins/example/) 是只读自检示例。

---

## 快速上手

### 1. 安装、升级与初始化

使用 [uv](https://docs.astral.sh/uv/) 安装或升级全局 CLI 工具（依赖 Python 3.12+、Git 与 [pi](https://pi.dev)）：

```bash
# 方式 A：从 GitHub Release 安装预构建 Wheel 包（推荐，开箱即用，无需 Node.js/pnpm）
uv tool install https://github.com/cp5670681/dev-yard/releases/latest/download/dev_yard-0.1.1-py3-none-any.whl

# 方式 B：从 GitHub 源码安装（本机若有 Node.js/pnpm 会自动编译前端）
uv tool install git+https://github.com/cp5670681/dev-yard.git

# 后续升级到最新版本
uv tool upgrade dev-yard
# 或强制拉取主干重装
uv tool install --force git+https://github.com/cp5670681/dev-yard.git
```

在任意目录创建并初始化工作区：

```bash
mkdir my-workspace && cd my-workspace
dev-yard init
```

> **关于 MCP 与需求链接访问**：  
> 若需求来源于 Jira / Confluence 或私有平台链接，`pi` 需借助对应的 MCP 或 Skill 访问数据。`pi` 加载 MCP 需要先安装适配器插件：  
> ```bash
> # 1. 安装 pi MCP 适配器插件
> pi install npm:pi-mcp-adapter
> # 2. 在 MCP 配置文件（如 ~/.config/mcp/mcp.json）中配置目标服务的 MCP（如 mcp-atlassian-pro）
> ```  
> 确保 `pi` 具备目标服务的读取权限，否则 `req open` 无法访问需求链接。本地 Markdown 文件或纯文本录入则无需任何 MCP。

### 2. 登记业务仓（写入 `repos.yaml`）

```bash
# 方式 A：托管 Clone（推荐，自动拉取到 .repos/）
dev-yard repo add core-api git@github.com:my-org/core-api.git --default-base main --role be
dev-yard repo add web-frontend git@github.com:my-org/web-frontend.git --default-base main --role fe

# 方式 B：复用本地已有目录
dev-yard repo add core-api git@github.com:my-org/core-api.git --path /path/to/core-api
```

### 3. 一个需求的完整周期（以 `PROJ-101` 为例）

```bash
# 1. 创建需求（支持 Jira Key / URL / 本地文件 / 纯文本）
dev-yard req open PROJ-101

# 2. 交互答辩与对齐（产出 GRILL.md、术语表 CONTEXT.md、架构决策 docs/adr/）
dev-yard grill PROJ-101

# 3. 编写技术方案与跨仓接口契约（产出 SPEC.md）
dev-yard spec PROJ-101

# 4. 拆解跨仓 DAG 任务（产出 TICKETS.md）
dev-yard tickets PROJ-101

# 5. 冻结方案并创建隔离 Worktree（默认分支: req/PROJ-101，可在配置页改模板）
dev-yard req freeze PROJ-101

# 6. Agent 编码实现（按依赖顺序自动运行 ready 任务）
dev-yard implement PROJ-101

# 7. 代码评审与跨仓契约检查
dev-yard review PROJ-101            # 单票代码评审
dev-yard review PROJ-101 --contract # 跨仓契约校验
# 若契约不符，拆成独立 B 票再修（无依赖的可并行）：dev-yard implement PROJ-101 --from-contract

# 8. 提测与修复闭环
dev-yard req submit-test PROJ-101
# 工作区根放 qa.yaml（envs.<环境>.base_url + auth.accounts 账号密码 + db.url，均明文直存、文件已 gitignore；db.verify_url 可选，给设计期只读核实用；测试模型也在这里：可选 design（qa-design）与 workers 模型池（qa-run））后：
dev-yard req accounts PROJ-101 --env test   # 本需求要多账号时先配：写 .yard-qa/requirements/<JIRA>/accounts.yaml（随需求变，已 gitignore）
dev-yard req test PROJ-101          # 设计用例后暂停，等人工审核；通过后执行，失败拆 B 票，通过则 phase=done
dev-yard req test PROJ-101 --env test   # 指定环境；缺省用 qa.yaml 的 active_env
dev-yard req test PROJ-101 --approve    # 人工审核通过当前用例并开始执行
dev-yard req test PROJ-101 --redesign --feedback "补齐权限拦截用例"   # 打回：带意见让 qa-design 重做用例
# 用例 frontmatter 的 account: 选需求账号；不写用需求 default（需求无则回退全局默认）
# 需求页「测试」Tab（/r/:key/qa）可看用例、改动点、run 与截图，并通过/打回用例
# --design-only 仍可只出用例；--run-only 在用例未审核时需显式加 --unsafe-skip-review（CI 或已审过时）
# 提 bug 后修就绪的 B 票：dev-yard implement PROJ-101 --from-test

# 9. 一键推送远端分支（提 PR）
dev-yard push PROJ-101              # 或 dev-yard req push PROJ-101
# 也支持只推送指定仓库：dev-yard push PROJ-101 core-api
# 或在 Web 看板直接点击「推送到远端」
```

> **说明**：Agent 阶段默认打开交互 TUI；加上 `--print` 可转为单次非交互运行（直接打印日志）。

---

## 核心机制

### 拆票格式（`TICKETS.md`）
```markdown
# Tickets — PROJ-101

## T1: 后端支付接口与回调
- repo: core-api
- depends_on:
- parallel: false

## T2: 前端收银台页面
- repo: web-frontend
- depends_on: T1
- parallel: false
```
- `repo`：必须与 `repo add` 登记的别名一致。
- `depends_on`：上游任务通过评审（`done`）后，下游任务才会变为 `ready`。
- 每张票都在自己的子分支/子 Worktree 里实现；父 Worktree 只做整合（合并通过的票），不直接写代码。
- `parallel: true`：同仓多票可并行实现；各自从同一冻结点派生。审查前会先把父分支并入子分支，冲突在实现阶段交给 Agent 就地解决。

### 仓库、模型与冻结分支（`repos.yaml`）
```yaml
git:
  freeze_branch: req/{jira}   # 缺省。也可 feature/{jira}、{jira} 等；必须含 {jira}
  ai_resolve_conflicts: true  # 提测 merge 冲突时让 implement 模型自动消解（默认 true）
pi:
  provider: anthropic
  model: claude-3-7-sonnet
  stages:
    grill:
      provider: openai
      model: gpt-4o

repos:
  core-api:
    url: git@github.com:my-org/core-api.git
    default_base: main
    role: be
    test_branch: PG-test      # 提测时把冻结分支 merge 到此共享测试分支；缺省=该仓不提测
    provider: anthropic
    model: claude-3-7-sonnet # 仓级别模型覆盖
```
- **模型回退机制**：`仓配置` → `pi.stages.<阶段>` → `全局 pi` → `环境变量(YARD_PI_*)` → `pi 默认`。
- **冻结分支**：Web「配置 → 分支」写入 `git.freeze_branch`。并行票子分支是 `{冻结名}-{票号}`（git 不允许 `冻结名/票号` 这种嵌套 ref）。改模板只影响之后新冻结的需求。
- **测试分支（`test_branch`）**：每仓可选。提测（`dev-yard req submit-test`）时，对配了该字段的仓，先把冻结分支 push 到远端，再把它 merge 进 `test_branch` 并 push；没配的仓跳过。测试分支是长命共享分支（同一仓对所有需求同一个），别并行提测多个需求，否则一个需求会把另一个需求的改动带进测试环境。另注意：若测试分支落后于 `default_base`，合并冻结分支会把它之后基线的大量无关提交一并带入，冲突面也会变大——提测前建议先把测试分支同步到基线。
- **提测可重入**：`submit-test` 幂等。修完测试 bug（冻结分支前进）后再跑一次，只重推 `freeze_sha` 变化或未推的仓；`--all` 强制全量，`--no-resolve` 关闭 AI 解冲突。

### 轻量变更（需求小改）

产品在实现/提测中改了一小段需求（补规则、改描述、加验收点）时用 `dev-yard req change`（Web 看板「轻量变更」）。只支持 `phase=frozen/testing`，只改描述/规则/验收等**非契约**内容；涉及接口契约请另开需求（方案级变更暂不支持）。

```bash
dev-yard req change PROJ-101 --note "外聘联系人可同时挂靠" --repo core-api
dev-yard req changes PROJ-101            # 查看变更记录（只读）
```

流程：追加变更记录到 `REQUIREMENT.md`（`## 变更记录`，不改原文）→（可选 `--grill`）→ 更新 `SPEC.md` → 追加**一张** `source: light` 的票。不重跑 `to-tickets`、不重排已有票、不改 `phase`、不动契约审查。新票按普通票「实现 → 审查 → 合并」；合并后 `submit-test` 幂等重提。若 SPEC 契约段被改动会告警，QA 用例会被标为待复核（需重新审核/`--redesign`）。

### 自动化测试（`req test`）

- **人工审核门**：qa-design 产出用例后暂停，需 `--approve` 才执行；审核绑定用例指纹，改动用例即失效。`--run-only` 跳过审核时必须显式 `--unsafe-skip-review`。
- **数据核实**：设计期由宿主真跑每条用例的 `data.verify`（单条只读 SQL，≥1 行通过），失败自动回灌 qa-design 重做，最多 `design.verify_attempts` 次；配 `db.verify_url` 可让核实走只读账号。
- **宿主独立复核**：run 期 worker 对带 `sql` 的 db 断言自报 passed，宿主会重跑该 SQL 比对 `expected`，不一致降级为 `failed`。
- **阻塞分类**：worker 在 result.yaml 写 `blocked_class`（`case-defect`/`env`/`undeployed`/`auth`/`other`），宿主据此决策；无法归类的原因不再触发整轮中断。
- **变更门**：run 前后比对 worktree 的 git 状态与 HEAD，commit 级改动也会被判为 worker 越权改动而中止。

### 目录与分支拓扑
```text
my-workspace/
├── repos.yaml                     # 仓库、模型、冻结分支（本机）
├── reqs/
│   ├── CONTEXT.md                 # 跨需求通用术语表
│   └── PROJ-101/                  # 需求产物 (REQUIREMENT/GRILL/SPEC/TICKETS.md)
│       └── worktrees/<alias>/     # 各仓整合 Worktree（默认分支: req/PROJ-101）
├── .repos/                        # 托管克隆母仓
└── .yard-worktrees/               # 每张票的子 Worktree（默认: req/PROJ-101-T1）
```

---

## 本地 Web 控制台

```bash
dev-yard web # 默认打开 http://127.0.0.1:8765（仅本机）

# 局域网其它设备访问：绑 0.0.0.0，用本机局域网 IP
dev-yard web --host 0.0.0.0 --allow-remote
# 例如 http://192.168.x.x:8765
```
提供可视化需求看板、在线文档读写、阶段一键触发与实时日志。

`--allow-remote` 无鉴权，且会暴露 `pi --approve`，只在可信内网使用。

---

## CLI 命令速查

| 命令 | 说明 | 常用选项 |
|:---|:---|:---|
| `dev-yard init [dir]` | 初始化工作区 | |
| `dev-yard repo add <alias> <url>` | 登记业务仓 | `--default-base`, `--role`, `--path` |
| `dev-yard repo list` | 列出已登记仓库 | |
| `dev-yard repo set-model <alias>` | 设置仓库的实现模型 | `--provider`, `--model` |
| `dev-yard req open <target>` | 创建/拉取需求 | `--key`, `--text`, `--file`, `--none`, `--force` |
| `dev-yard req freeze <key>` | 冻结方案并建 Worktree | `--force`（testing/done 回退） |
| `dev-yard req delete <key>` | 删除需求产物与 Worktree | |
| `dev-yard req push <key> [repos..]` | 推送各仓 Worktree 分支到远端 | `--remote`, `--force` |
| `dev-yard req submit-test <key>` | 标记提测 | |
| `dev-yard req test <key>` | 提测后设计用例（暂停等人工审核）+ 执行 | `--env`, `--print`, `--design-only`, `--run-only`, `--unsafe-skip-review`, `--redesign`, `--approve`, `--feedback`, `--feedback-file`, `--verify-only`, `--no-verify`, `--allow-unverified`, `--resume`, `--fresh`, `--rerun-case`, `--no-ingest` |
| `dev-yard req change <key>` | 轻量变更：追加变更记录 + 更新 SPEC + 建一张轻量票 | `--note`, `--repo`, `--grill`, `--run`, `--print` |
| `dev-yard req changes <key>` | 打印该需求的变更记录 | |
| `dev-yard qa check-env` | 解析 exec 配方、ping、hello 回显 | `--env`, `--jira` |
| `dev-yard req accept-test <key>` | 录入测试报告 | `--verdict`, `--body-file` |
| `dev-yard grill <key>` | 需求答辩与对齐 | `--print`, `--dry-run` |
| `dev-yard spec <key>` | 制定方案与契约 | `--print`, `--dry-run` |
| `dev-yard tickets <key>` | 跨仓拆票与 DAG | `--print`, `--dry-run` |
| `dev-yard implement <key> [T..]` | 编码实现 | `--print`, `--from-contract`, `--from-test`, `--force` |
| `dev-yard review <key> [T..]` | 代码评审 / 契约检查 | `--contract`, `--print` |
| `dev-yard stages` | 列出内置 + 已启用插件阶段 | |
| `dev-yard run <stage> <key>` | 跑插件阶段，或 grill/spec/tickets | `--print`；`open`/`implement`/`review`/`contract`/`qa-*`/`test` 请用专用命令 |
| `dev-yard push <key> [repos..]` | 推送各仓 Worktree 分支到远端 | `--remote`, `--force` |
| `dev-yard tdd [on\|off\|status]` | 开/关 TDD（写 `repos.yaml` 的 `dev.tdd`，默认 on） | |
| `dev-yard status [key]` | 查看需求与任务状态 | |
| `dev-yard web` | 启动 Web 看板 | `--port 8765`, `--host 0.0.0.0 --allow-remote` |

---

## 开发与测试

```bash
# 1. 克隆代码并安装 Python 依赖
git clone https://github.com/cp5670681/dev-yard.git && cd dev-yard
uv sync --group dev

# 2. 运行后端测试（若本地未编译前端会跳过 spa 相关测试）
uv run pytest

# 3. 前端独立开发（支持秒级热重载，自动反代后端 8765 端口）
cd web && pnpm install && pnpm dev

# 4. 构建与全包测试（wheel 需要 Node/pnpm，或已有 src/dev_yard/web/spa/index.html）
uv build
```
