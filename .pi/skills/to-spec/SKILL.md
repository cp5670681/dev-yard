---
name: to-spec
description: >
  把当前对齐写成 reqs/<JIRA>/SPEC.md。Turn conversation into a spec for yard.
  Use when the user runs /to-spec, says 写成spec, to-spec.
---

# to-spec（dev-yard）

**不要访谈。** 综合当前对话、`GRILL.md`、`CONTEXT.md`、ADR。不要发到 GitHub/Jira，**只写** `reqs/<JIRA>/SPEC.md`。不要跑 `/setup-matt-pocock-skills`。

## 定位

与 grill-with-docs 相同：`reqs/<JIRA>/`。读 `GRILL.md`、`REQUIREMENT.md`、各仓 `default_base` 代码。

## 模块与测试缝

列出会动的仓（`repos.yaml` alias）和模块。优先已有测试缝。向用户确认缝之后再落盘。

## SPEC.md 模板

```markdown
# Spec — <JIRA>

## Problem Statement

## Solution

## User Stories

1. As an <actor>, I want a <feature>, so that <benefit>

## Per-repo

- <alias>: 做什么

## Implementation Decisions

- 模块与接口
- API / 事件 / 字段契约（跨仓必须写在这里）
- 架构与 schema

不要写会很快过期的具体文件路径或大段代码。原型里真正编码了决策的片段（状态机、类型形状）可以内联并注明来自 prototype。

## Testing Decisions

- 只测外部行为
- 各仓测哪些模块、已有测试先例

## Out of Scope

## Further Notes
```

写完后告诉用户下一步跑 `dev-yard tickets <JIRA>`。
