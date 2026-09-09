---
name: implement
description: >
  按 yard 票在对应 worktree 里实现。Drive TDD; stop at implemented.
  Use when the user runs /implement, dev-yard implement, 写需求, 实现票.
---

# implement（dev-yard）

实现 `TICKETS.md` 里的一张票（或 CLI 指定的票）。**cwd 必须是该票 worktree**，不要改别的仓，不要改 `reqs/` 里的 spec。

## 必读

- `reqs/<JIRA>/SPEC.md`、`TICKETS.md`（只读）
- `reqs/CONTEXT.md`（若有：只读术语；不要写进 worktree）
- 本票条目：id、repo、验收
- `.pi/skills/tdd/SKILL.md` — 在 spec 已约定的缝上 red-green
- 该仓代码与测试习惯
- 启动提示若含 `Previous review failed` 与审查报告：只修硬违规和 Spec 缺口，不要扩范围；气味可留
- 启动提示若含 `Previous contract review`：只修本仓相关的契约缺口和硬违规，不要扩范围；气味可留
- 启动提示若含 `Previous test report`：只修本仓相关的测试失败项，不要扩范围；不要改测试报告文件

## 做法

1. 确认当前目录是 `reqs/<JIRA>/worktrees/<alias>/` 或 `.yard-worktrees/<JIRA>/<alias>/<票id>/`，分支为 `req/<JIRA>` 或 `req/<JIRA>/<票id>`。
2. 按 TDD 做完本票范围，跑该仓类型检查与相关测试。
3. 提交到**当前分支**（不要推，不要开 PR）。
4. 停在实现完成。不要自己跑 code-review：CLI 只把票标成 `implemented`。提示用户下一步 `dev-yard review <JIRA>`。
