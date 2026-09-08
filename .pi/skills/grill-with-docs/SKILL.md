---
name: grill-with-docs
description: >
  Yard 绑定的需求对齐：对 reqs/<JIRA>/ 做 grilling + domain-modeling。
  Use when the user runs /grill-with-docs, /yard-grill, or says grill a requirement,
  对齐需求, 分析需求文档.
---

# grill-with-docs（dev-yard）

本仓库已绑定 [mattpocock/skills](https://github.com/mattpocock/skills)。**不要**让用户去插件里另跑一遍；本技能就是那一套，输出落到 yard 目录。

先读并执行：

1. `.pi/skills/grilling/SKILL.md`
2. `.pi/skills/domain-modeling/SKILL.md`

## 定位需求

- 参数或 cwd 若含 `reqs/<JIRA>/`，就用该 JIRA。
- 否则对最新的 `reqs/*` 或问用户 JIRA key。
- 没有目录则先 `yard req open <JIRA>`（或让用户跑）。

工作目录：workspace 根（含 `repos.yaml`）。只读各仓 **源 clone** 的 `default_base`（`.repos/<alias>` 或 `path`），**不要**在 `reqs/*/worktrees` 里改业务代码。

## 读写

| 文件 | 用途 |
|------|------|
| `reqs/<JIRA>/REQUIREMENT.md` | 产品原文 |
| `reqs/<JIRA>/GRILL.md` | 每轮 Q/A、已拍板决策；边问边记 |
| `CONTEXT.md`（workspace 根） | 术语表（domain-modeling） |
| `docs/adr/` | 难逆、意外、有取舍的决策 |
| `repos.yaml` | 有哪些仓 |

## 结束

Frontier 清空且用户确认理解一致后停。下一步是 `/to-spec`，不要 freeze、不要写实现。
