---
name: to-tickets
description: >
  把 SPEC 拆成 reqs/<JIRA>/TICKETS.md，yard CLI 可解析的 DAG。
  Use when the user runs /to-tickets or `dev-yard tickets`. Do not run during grill.
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
- covers: FR-001 US1 US1/AC1

## T2: 短标题
- repo: frontend
- depends_on: T1
- parallel: false
- covers: US1/AC2 C-001
```

二级标题必须是 `## T<n>: <title>`（如 `T1`、`T2`）。契约/提测缺陷由 CLI 追加 `## B<n>` 票，不要手写去覆盖。其它二级标题（「验收」「Further Notes」）CLI 会忽略。字段名必须是 `repo` / `depends_on` / `parallel` / `covers`（bug 票还可有 `source` / `finding`）。没有 `- repo:` 的票不会入列。

`covers` 用空格分开，每项必须是 SPEC 里的 `FR-`、`US`、`US/AC` 或 `C-` 编号。一张票可以覆盖多条。规格里的每条 `FR-`、`C-`、验收场景都要至少被一张票覆盖；写了 `US1` 就覆盖这条故事下的全部场景。规格还没有这些编号时不要发明 `covers`。

在标题下可用普通段落写「做什么 / 验收」，并点名本票的编号。`parallel: true` 仅当同仓两张无依赖票要同时开子 worktree。

启动提示若含 `TDD mode is OFF`：验收只写可观察的业务行为。不要写「验收：spec」「补单测」，也不要要求新增测试文件。

用户批准后写入 `TICKETS.md`，提示 `dev-yard req freeze <JIRA>`，然后 `dev-yard implement`。
