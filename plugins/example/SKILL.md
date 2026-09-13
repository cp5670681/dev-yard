---
name: example
description: >
  产物自检示例阶段。Use when the user runs dev-yard run example <JIRA>.
---

# example（产物自检）

读取 `reqs/<JIRA>/` 下的 REQUIREMENT.md / GRILL.md / SPEC.md / TICKETS.md，
按启动提示 guidance 里的清单逐项给出 ✅/❌ 与一句理由，最后输出
"READY" 或 "NOT READY: <缺口列表>"。只读，不写任何文件。
