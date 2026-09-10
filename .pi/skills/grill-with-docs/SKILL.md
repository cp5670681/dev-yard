---
name: grill-with-docs
description: >
  Yard 绑定的需求对齐：对 reqs/<JIRA>/ 做 grilling + domain-modeling。
  Use when the user runs /grill-with-docs, `dev-yard grill`, or says grill a requirement,
  对齐需求, 分析需求文档.
---

# grill-with-docs（dev-yard）

本仓库已绑定 [mattpocock/skills](https://github.com/mattpocock/skills)。**不要**让用户去插件里另跑一遍；本技能就是那一套，输出落到 yard 目录。

先读并执行：

1. `.pi/skills/grilling/SKILL.md`
2. `.pi/skills/domain-modeling/SKILL.md`

## 定位需求

- 参数或 cwd 若含 `reqs/<JIRA>/`，就用该 JIRA。
- 否则对最新的 `reqs/<JIRA>/`（含 `STATUS.yaml` 或 `REQUIREMENT.md`）或问用户 JIRA key。跳过 `reqs/docs/` 和 `reqs/CONTEXT.md`，那不是需求。
- 没有目录则先 `dev-yard req open <JIRA>`（或让用户跑）。

工作目录：workspace 根（含 `repos.yaml`）。只读 CLI 列出的源仓目录（`repos.yaml` 的 `path` 或 `.repos/<alias>`）。托管 clone（`.repos/`）CLI 会切到 `default_base`；`path` 映射的日常副本 CLI **不会**代为 checkout，不在 `default_base` 时命令失败。源仓只用来对照代码和之后 `git worktree add`，不要在源仓里改业务、不要再切分支。需求分支在 `freeze` 之后的 `reqs/*/worktrees`。

## 读写

| 文件 | 用途 |
|------|------|
| `reqs/<JIRA>/REQUIREMENT.md` | 只读：产品原文 |
| `reqs/<JIRA>/GRILL.md` | **唯一要写的需求产物**：每轮 Q/A、已拍板决策 |
| `reqs/<JIRA>/.grill-round.json` | 仅 WEB_GRILL_ROUND：本轮表单契约 |
| `reqs/CONTEXT.md` | 术语表（多票共用，domain-modeling） |
| `reqs/docs/adr/` | 难逆、意外、有取舍的决策（多票共用） |
| `repos.yaml` | 只读 |

**禁止**写 workspace 根的 `CONTEXT.md` / `docs/adr/`，禁止写进源仓或 freeze worktree。术语和 ADR 一直留在 `reqs/`（gitignore），不进业务仓。

**禁止**写 `SPEC.md`、`TICKETS.md`、业务代码。SPEC 留给 `dev-yard spec`，票留给 `dev-yard tickets`。

当 session prompt 含 `WEB_GRILL_ROUND` 时，读并执行 [`WEB-ROUND.md`](WEB-ROUND.md)（写本轮 `.grill-round.json` 后停）。

## 结束

Grill 阶段的目标是**高效收敛并对齐核心业务与关键架构**，坚决避免无休止追问：
- **死守总轮次（Max 1~3 轮）**：小需求 1 轮（1~3 题），中需求 1~2 轮（3~5 题），大需求 2~3 轮（5~8+ 题）。
- **按规模弹性展开 + 模块分组**：大需求可以在单轮内一次性提出较多核心问题，但多题时（≥ 4）必须按模块/领域分组并给出推荐选项（`➡️`），方便批量审批。
- **合理默认假设（Sensible Defaults）**：次要实现细节与非关键边缘分支直接采用业内最佳实践，记录在 `GRILL.md` 的「默认假设与实现细节」中，无需占用提问轮次。
- **收敛与停机**：主链路闭环且无阻塞性分歧时立即停止，提示用户进入 `dev-yard spec <JIRA>`。不要自己写 spec、不要 freeze、不要写实现。
