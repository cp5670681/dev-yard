# AGENTS

本仓库运行时只有 **pi**（`yard` 启动 `pi`，技能在 `.pi/skills/`）。不要用 Claude / Grok / Codex 当执行 agent，也不要去装 mattpocock 插件或跑 `/setup-matt-pocock-skills`。

## 路由（yard 命令 → pi --skill）

| 命令 | 技能 |
|------|------|
| `yard grill` | grill-with-docs + grilling + domain-modeling |
| `yard spec` | to-spec |
| `yard tickets` | to-tickets |
| `yard implement` | implement + tdd + codebase-design |
| `yard review` | code-review |

## 产物

- 术语：`CONTEXT.md`；ADR：`docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 代码：仅 `yard req freeze` 之后的 worktree
