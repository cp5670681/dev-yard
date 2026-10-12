---
name: analyze
description: >
  只读核对需求四份文档是否互相矛盾、含糊、编号对不上。
  Use when the user runs dev-yard analyze or says 核对文档.
---

# analyze（dev-yard）

**不要改** `REQUIREMENT.md`、`GRILL.md`、`SPEC.md`、`TICKETS.md`、`STATUS.yaml`。唯一可写的是 `reqs/<JIRA>/ANALYZE.md` 里宿主标记以外的定性部分。

## 必读

- `reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- `reqs/CONTEXT.md` 与 `reqs/docs/adr/`（术语和已记录的决定）

## 写什么

只写或替换 `## 定性`。不要删、不要改 `<!-- host-coverage -->` 到 `<!-- /host-coverage -->` 之间的内容。宿主会在这次运行之后重写那一块。

报告用表，一行一条，最多 50 条：

| ID | Category | Severity | Location | Summary |
| --- | --- | --- | --- | --- |
| Q1 | 不一致 | HIGH | SPEC.md US1 | 和 GRILL 的规范陈述相反 |

类别只使用这些：重复、含糊、缺编号、覆盖缺口、不一致、术语冲突。

- 含糊：没有判定标准的形容词（尽快、友好、合适、稳定）。
- 不一致：四份文档对同一行为有两种说法。`CONTEXT.md` 或 ADR 里已经定了的术语，冲突记为最高一级。
- 覆盖缺口：票的正文做了规格没写的事，或规格写了票完全没提。编号对账留给宿主，不要在这里假装已经数过。
- 没有问题时写「定性：没有发现」，不要编一条。

不要自动改文档。表后面用三五行写该回哪一份文档改。然后停。
