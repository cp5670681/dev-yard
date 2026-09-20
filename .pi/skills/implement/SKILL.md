---
name: implement
description: >
  按 yard 票在对应 worktree 里实现。Drive TDD; stop at implemented.
  Use when the user runs /implement, dev-yard implement, 写需求, 实现票.
---

# implement（dev-yard）

实现 `TICKETS.md` 里的一张票（或 CLI 指定的票）。**cwd 必须是该票 worktree**，不要改别的仓，不要改 `reqs/` 里的 spec。

## 必读

- `reqs/<JIRA>/SPEC.md`（技术方案与测试决策，必须遵守）、`TICKETS.md`（只读）
- `reqs/CONTEXT.md`（若有：只读术语；不要写进 worktree）
- 本票条目：id、repo、验收
- `.pi/skills/tdd/SKILL.md` — 未跳过跑测时，在 spec 已约定的缝上 red-green（跳过条件见做法第 2 步）
- 该仓代码与测试习惯
- 启动提示若含 `Previous review failed` 与审查报告：只修硬违规和 Spec 缺口，不要扩范围；气味可留
- 启动提示若含 `Previous contract review`：只修**本票**对应的契约缺口和硬违规，不要扩到其它 B 票；气味可留
- 启动提示若含测试 bug / finding：只修 **TICKETS.md 本票** 的缺陷，不要扩到其它 B 票；不要写 TEST-REPORT.md

## 做法

1. 确认当前目录是 `reqs/<JIRA>/worktrees/<alias>/` 或 `.yard-worktrees/<JIRA>/<alias>/<票id>/`，分支为 `req/<JIRA>` 或 `req/<JIRA>/<票id>`。
2. 默认 TDD：写测试并跑该仓类型检查与相关测试。仅在下列情况跳过**跑**测试（仍写测试，除非 SPEC/提示明确说本票不要求测试）：
   - 启动提示注明 TDD 已关闭（如 TDD mode is OFF）或无需写测试；或
   - SPEC.md 或启动提示明确说环境不可用 / 无需跑测试 / 免测；或
   - 做了一次环境探测失败（依赖连不上：DB/Solr/中间件、Connection refused 等），记下原因后不再重试。
   套件慢不算跳过。免测或跳过跑测时直接实现业务代码并做静态走查/语法检查。
3. 提交到**当前分支**（不要推，不要开 PR）。
4. 停在实现完成。不要自己跑 code-review：CLI 只把票标成 `implemented`。提示用户下一步 `dev-yard review <JIRA>`。
