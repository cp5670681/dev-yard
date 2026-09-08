---
name: to-tickets
description: >
  把 SPEC 拆成 reqs/<JIRA>/TICKETS.md，yard CLI 可解析的 DAG。
  Use when the user runs /to-tickets, 拆票, to-tickets.
---

# to-tickets（dev-yard）

**不要**发到 GitHub/Linear，**不要**写成 `.scratch/.../issues/` 多文件。本仓库唯一票源是 `reqs/<JIRA>/TICKETS.md`。不要跑 `/setup-matt-pocock-skills`。

## 规则（mattpocock tracer bullet）

- 每张票是窄而完整的垂直切片，单独可验证
- 一张票只绑 **一个** `repos.yaml` alias（跨仓拆多张，用 `depends_on`）
- 先 prefactor 再功能
- 宽重构用 expand–contract，不要硬塞进一张垂直票
- 粒度与依赖问用户，批准后再写文件

## TICKETS.md 格式（CLI 解析这个，不要改形状）

```markdown
# Tickets — <JIRA>

## T1: 短标题
- repo: backend
- depends_on:
- parallel: false

## T2: 短标题
- repo: frontend
- depends_on: T1
- parallel: false
```

二级标题必须是 `## <id>: <title>`。字段名必须是 `repo` / `depends_on` / `parallel`。

在标题下可用普通段落写「做什么 / 验收」，CLI 会忽略。`parallel: true` 仅当同仓两张无依赖票要同时开子 worktree。

用户批准后写入 `TICKETS.md`，提示 `yard req freeze <JIRA>`，然后 `/implement`。
