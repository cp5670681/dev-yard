---
name: code-review
description: >
  Two-axis review (Standards + Spec) of a yard ticket worktree.
  Use when the user runs /code-review, dev-yard review, 审查代码.
---

# code-review（dev-yard）

沿用 mattpocock 双轴审查：**Standards** 与 **Spec** 分开写，不要合成一个排名。pi 没有子 agent，同一会话顺序做完两轴。

**不要**去读 `docs/agents/issue-tracker.md`，**不要**让用户跑 `/setup-matt-pocock-skills`。

## 1. Fixed point

- 票级：启动提示已内联**本票**相对上一张同仓票（或 `default_base`）的 `git log` / `git diff`，含工作区未提交改动。不要自己跑 git（本阶段没有 bash）。
- 用户若指定 commit/branch，用用户的。
- 仅当提示里是 `(no changes vs …)` 或 `(could not diff …)` 才停。同仓上一张票的改动不在本票 diff 里是正常的；要用 `read` 打开本票涉及的文件核对是否已实现，不要把「本票窗口里没有某文件」直接写成整仓未实现。

契约审查（`dev-yard review --contract`）：cwd 是 yard 根。启动提示已内联每个需求 worktree 相对 `default_base` 的 diff。对照 `SPEC.md` 跨仓契约。不跑 Fowler 气味轴也可以，但必须列出契约缺口。不要在 yard 仓库根上 `git diff`。失败时在报告末尾输出 YAML `findings:` 列表（每条独立缺口一条，含 `id` / `title` / `repo` / `detail` / 可选 `depends_on`），yard 会按条拆成 B 票。

pi 没有子 agent。两轴都自己做，先 Spec 再 Standards（契约模式可只做 Spec）。工具：`read` 参数是 `path`。

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

## 4. 两轴（同一会话，顺序做）

**Spec**：diff + `SPEC.md`（契约模式）或该票正文（票级）。报告 (a) spec 有但缺/残 (b) 没要的 scope creep (c) 看起来做了但做错。每条引用 spec。少于 400 字。

**Standards**（票级；契约模式可省略）：完整 diff、commit 列表、标准文件。报告每处 (a) 违反成文标准（引用文件+规则）(b) 气味（点名+摘 hunk）。硬违规 vs 判断题分开。少于 400 字。

## 5. 汇总

`## Standards` 与 `## Spec` 分开贴。末行：每轴发现数 + 该轴最严重问题。

- 有硬违规或 Spec 缺需求 → 审查失败：报告里写 `REVIEW_FAILED`，并以非零退出（yard 据此把票标 `blocked`）
- 契约模式失败：在 `REVIEW_FAILED` 之后附 YAML `findings:`（`repo` 必须是 `repos.yaml` alias；同仓互不依赖的缺口分开写，有先后的用 `depends_on` 指向其它 finding id）
- 仅判断题气味 → 通过，但写在报告里
