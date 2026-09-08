---
name: code-review
description: >
  Two-axis review (Standards + Spec) of a yard ticket worktree.
  Use when the user runs /code-review, yard review, 审查代码.
---

# code-review（dev-yard）

沿用 mattpocock 双轴审查：**Standards** 与 **Spec** 分两个子 agent，互不污染，汇报时分开，不要合成一个排名。

**不要**去读 `docs/agents/issue-tracker.md`，**不要**让用户跑 `/setup-matt-pocock-skills`。

## 1. Fixed point

- 票级：该仓 `default_base`（见 `repos.yaml`），`git diff <default_base>...HEAD`，`git log <default_base>..HEAD --oneline`。
- 用户若指定 commit/branch，用用户的。
- `git rev-parse` 失败或 diff 为空则停，不要派子 agent。

契约审查（`yard review --contract`）：对每个需求 worktree 相对 `default_base` 做 diff，对照 `SPEC.md` 的跨仓契约，不跑 Fowler 气味轴也可以，但必须列出契约缺口。

## 2. Spec 来源（按序，找到就停）

1. `reqs/<JIRA>/SPEC.md` + 该票在 `TICKETS.md` 的条目
2. 用户给的路径
3. 没有 spec → Spec 轴记 "no spec available"，仍跑 Standards

## 3. Standards 来源

目标仓里的 `CODING_STANDARDS.md` / `CONTRIBUTING.md` / `AGENTS.md`。没有也要跑下面气味基线（Fowler）。仓内成文标准覆盖基线。气味一律判断题，不是硬违规。工具已强制的跳过。

- Mysterious Name → 改名
- Duplicated Code → 抽共享
- Feature Envy → 方法挪到数据上
- Data Clumps → 收成类型
- Primitive Obsession → 领域小类型
- Repeated Switches → 多态或共享 map
- Shotgun Surgery → 收到一个模块
- Divergent Change → 按变更原因拆
- Speculative Generality → 删到有真实需要
- Message Chains → 藏导航
- Middle Man → 去掉中间人
- Refused Bequest → 别继承，用组合

## 4. 并行子 agent

**Standards**：完整 diff 命令、commit 列表、标准文件列表、上面气味基线全文。报告每处 (a) 违反成文标准（引用文件+规则）(b) 气味（点名+摘 hunk）。硬违规 vs 判断题分开。少于 400 字。

**Spec**：diff + `SPEC.md` 与该票正文。报告 (a) spec 有但缺/残 (b) 没要的 scope creep (c) 看起来做了但做错。每条引用 spec。少于 400 字。

## 5. 汇总

`## Standards` 与 `## Spec` 分开贴。末行：每轴发现数 + 该轴最严重问题。

- 有硬违规或 Spec 缺需求 → 审查失败（yard 将票标 `blocked`）
- 仅判断题气味 → 通过，但写在报告里
