# AGENTS

CLI 入口是 `dev-yard`（或 `devyard`），不要用 `yard`（会撞上 Ruby YARD）。

## 运行时

全程 **pi** + `.pi/skills/`。`dev-yard req open` 走本机 `mcp-atlassian-pro`（Jira + Confluence），只抽**当前这条 Jira** 的产品说明，不要整本历史 Confluence。

`dev-yard web` 是本机控制台（FastAPI），套同一套 `service`；agent 阶段走 `pi -p`。

## 路由（pi）

| 命令 | 技能 |
|------|------|
| `dev-yard req open` | fetch-requirement |
| `dev-yard grill` | grill-with-docs + grilling + domain-modeling |
| `dev-yard spec` | to-spec |
| `dev-yard tickets` | to-tickets |
| `dev-yard implement` | implement + tdd + codebase-design |
| `dev-yard review` | code-review |

## 产物

- 术语：`reqs/CONTEXT.md`（多票共用）；ADR：`reqs/docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 代码：仅 `dev-yard req freeze` 之后的 worktree。术语/ADR 留在 `reqs/`，不进业务仓。
