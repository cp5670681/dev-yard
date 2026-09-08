# AGENTS

CLI 入口是 `dev-yard`（或 `devyard`），不要用 `yard`（会撞上 Ruby YARD）。

## 运行时

| 命令 | 运行时 |
|------|--------|
| `dev-yard req open` | **Claude Code**（本机已配的 Atlassian MCP：Jira + Confluence） |
| `dev-yard grill/spec/tickets/implement/review` | **pi** + `.pi/skills/` |

`req open` 只抽**当前这条 Jira 的产品说明**，不要整本历史 Confluence。其它阶段不要用 Claude 当默认 agent。

## 路由（pi）

| 命令 | 技能 |
|------|------|
| `dev-yard grill` | grill-with-docs + grilling + domain-modeling |
| `dev-yard spec` | to-spec |
| `dev-yard tickets` | to-tickets |
| `dev-yard implement` | implement + tdd + codebase-design |
| `dev-yard review` | code-review |

## 产物

- 术语：`CONTEXT.md`；ADR：`docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 代码：仅 `dev-yard req freeze` 之后的 worktree
