---
name: implement
description: >
  按 yard 票在对应 worktree 里实现。Drive TDD then code-review.
  Use when the user runs /implement, yard implement, 写需求, 实现票.
---

# implement（dev-yard）

实现 `TICKETS.md` 里的一张票（或 CLI 指定的票）。**cwd 必须是该票 worktree**，不要改别的仓，不要改 `reqs/` 里的 spec。

## 必读

- `reqs/<JIRA>/SPEC.md`、`TICKETS.md`（只读）
- 本票条目：id、repo、验收
- `.pi/skills/tdd/SKILL.md` — 在 spec 已约定的缝上 red-green
- 该仓代码与测试习惯

## 做法

1. 确认当前目录是 `reqs/<JIRA>/worktrees/<alias>/` 或 `.yard-worktrees/<JIRA>/<alias>/<票id>/`，分支为 `req/<JIRA>` 或 `req/<JIRA>/<票id>`。
2. 按 TDD 做完本票范围，跑该仓类型检查与相关测试。
3. 提交到**当前分支**（不要推，不要开 PR）。
4. 不要自己宣布完成：yard CLI 会再跑 code-review。若你在交互会话里单独被叫 implement，做完后接着执行 `.pi/skills/code-review/SKILL.md`，fixed point 用该仓 `default_base`（三方 diff `default_base...HEAD`），spec 用 `SPEC.md`。
