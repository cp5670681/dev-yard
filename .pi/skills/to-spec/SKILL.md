---
name: to-spec
description: >
  把当前对齐写成 reqs/<JIRA>/SPEC.md。Turn conversation into a spec for yard.
  Use when the user runs /to-spec, says 写成spec, to-spec.
---

# to-spec（dev-yard）

**不要访谈。** 综合当前对话、`GRILL.md`、`reqs/CONTEXT.md`、`reqs/docs/adr/`。不要发到 GitHub/Jira，**只写** `reqs/<JIRA>/SPEC.md`。不要跑 `/setup-matt-pocock-skills`。

## 定位

与 grill-with-docs 相同：`reqs/<JIRA>/`。读 `GRILL.md`、`REQUIREMENT.md`、各仓 `default_base` 代码。

## 模块与测试缝

启动提示若含 `TDD mode is OFF`：跳过本节。不要列测试缝，也不要为测试缝向用户确认。

否则：列出会动的仓（`repos.yaml` alias）和模块。优先已有测试缝。向用户确认缝之后再落盘。

## 编号

行为用稳定编号，后面的票和核对靠它对齐：

- 用户故事 `US1`、`US2`。每条故事至少一个场景 `US1/AC1`，写成 Given / When / Then。
- 行为要求 `FR-001`。跨仓契约 `C-001`，仍写在 Implementation Decisions。
- `GRILL.md` 的 `### 规范陈述` 每条原文抄进 `## 规范陈述`，不改写。
- `GRILL.md` 的 `### 默认假设与实现细节` 抄进 `## 默认假设`。没有就写「无」。

## SPEC.md 模板

```markdown
# Spec — <JIRA>

## Problem Statement

## 规范陈述

- （从 GRILL.md「规范陈述」原样抄来；没有就写「无」）

## 默认假设

- （从 GRILL.md「默认假设与实现细节」原样抄来；没有就写「无」）

## User Stories

### US1 — 短标题

As an <actor>, I want a <feature>, so that <benefit>

- US1/AC1: Given <state>, When <action>, Then <outcome>

## Requirements

- FR-001: <可观察的行为>

## Per-repo

- <alias>: 做什么

## Implementation Decisions

- 模块与接口
- C-001: API / 事件 / 字段契约（跨仓必须写在这里）
- 架构与 schema

不要写会很快过期的具体文件路径或大段代码。原型里真正编码了决策的片段（状态机、类型形状）可以内联并注明来自 prototype。

## Testing Decisions

- 只测外部行为
- 各仓测哪些模块、已有测试先例

## Out of Scope

## Further Notes
```

启动提示若含 `TDD mode is OFF`：上面这条是指令，不要抄进 `SPEC.md`。Testing Decisions 只写一句「本需求不要求测试文件（dev.tdd=false）」。不要列 RSpec、Jest、单测用例，不要指定 spec 路径，也不要抄模板里的测试条目。

写完后告诉用户下一步跑 `dev-yard tickets <JIRA>`。
