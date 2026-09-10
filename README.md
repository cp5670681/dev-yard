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
req open ──► grill ──► spec ──► tickets ──► req freeze ──► implement ──► review ──► submit-test
(拉需求)     (对齐)    (契约)    (拆票)      (建Worktree)   (编码实现)   (契约审查)  (提测闭环)
```

---

## 快速上手

### 1. 安装、升级与初始化

使用 [uv](https://docs.astral.sh/uv/) 安装或升级全局 CLI 工具（依赖 Python 3.12+、Git 与 [pi](https://pi.dev)）：

```bash
# 全局安装
uv tool install git+https://github.com/cp5670681/dev-yard.git

# 后续升级到最新版本
uv tool upgrade dev-yard
# 或强制拉取最新主干重装
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

# 5. 冻结方案并创建隔离 Worktree（创建分支: req/PROJ-101）
dev-yard req freeze PROJ-101

# 6. Agent 编码实现（按依赖顺序自动运行 ready 任务）
dev-yard implement PROJ-101

# 7. 代码评审与跨仓契约检查
dev-yard review PROJ-101            # 单票代码评审
dev-yard review PROJ-101 --contract # 跨仓契约校验
# 若契约不符，一键按报告回溯修复：dev-yard implement PROJ-101 --from-contract

# 8. 提测与修复闭环
dev-yard req submit-test PROJ-101
# 收到测试缺陷报告后修复：dev-yard implement PROJ-101 --from-test

# 9. 提 PR（各仓 Worktree 内推送）
cd reqs/PROJ-101/worktrees/core-api && git push origin req/PROJ-101
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
- `parallel: true`：同仓并行开发时，会自动派生临时子分支与子 Worktree。

### 仓库与模型配置（`repos.yaml`）
```yaml
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
    provider: anthropic
    model: claude-3-7-sonnet # 仓级别模型覆盖
```
- **模型回退机制**：`仓配置` → `pi.stages.<阶段>` → `全局 pi` → `环境变量(YARD_PI_*)` → `pi 默认`。

### 目录与分支拓扑
```text
my-workspace/
├── repos.yaml                     # 仓库与模型配置（本机）
├── reqs/
│   ├── CONTEXT.md                 # 跨需求通用术语表
│   └── PROJ-101/                  # 需求产物 (REQUIREMENT/GRILL/SPEC/TICKETS.md)
│       └── worktrees/<alias>/     # 各仓独立 Worktree（分支: req/PROJ-101）
├── .repos/                        # 托管克隆母仓
└── .yard-worktrees/               # 并行子任务临时 Worktree (req/PROJ-101/T1)
```

---

## 本地 Web 控制台

```bash
dev-yard web # 默认打开 http://127.0.0.1:8765
```
提供可视化需求看板、在线文档读写、阶段一键触发与实时日志。

---

## CLI 命令速查

| 命令 | 说明 | 常用选项 |
|:---|:---|:---|
| `dev-yard init [dir]` | 初始化工作区 | |
| `dev-yard repo add <alias> <url>` | 登记业务仓 | `--default-base`, `--role`, `--path` |
| `dev-yard repo list` | 列出已登记仓库 | |
| `dev-yard repo set-model <alias>` | 设置仓库的实现模型 | `--provider`, `--model` |
| `dev-yard req open <target>` | 创建/拉取需求 | `--key`, `--text`, `--file`, `--none`, `--force` |
| `dev-yard req freeze <key>` | 冻结方案并建 Worktree | |
| `dev-yard req delete <key>` | 删除需求产物与 Worktree | |
| `dev-yard req submit-test <key>` | 标记提测 | |
| `dev-yard req accept-test <key>` | 录入测试报告 | `--verdict`, `--body-file` |
| `dev-yard grill <key>` | 需求答辩与对齐 | `--print`, `--dry-run` |
| `dev-yard spec <key>` | 制定方案与契约 | `--print`, `--dry-run` |
| `dev-yard tickets <key>` | 跨仓拆票与 DAG | `--print`, `--dry-run` |
| `dev-yard implement <key> [T..]` | 编码实现 | `--print`, `--from-contract`, `--from-test` |
| `dev-yard review <key> [T..]` | 代码评审 / 契约检查 | `--contract`, `--print` |
| `dev-yard status [key]` | 查看需求与任务状态 | |
| `dev-yard web` | 启动 Web 看板 | `--port 8765`, `--allow-remote` |

---

## 开发与测试

```bash
# 源码安装与运行测试
git clone https://github.com/cp5670681/dev-yard.git && cd dev-yard
uv sync --group dev
uv run pytest

# 构建前端 Web 资源
cd web && pnpm install && pnpm build
```
