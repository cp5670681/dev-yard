---
name: fetch-requirement
description: >
  Fetch and format requirement details into reqs/<KEY>/REQUIREMENT.md.
  Use when the user runs `dev-yard req open` or says 打开需求, 抽取需求.
---

# fetch-requirement（dev-yard）

根据给定的需求目标（可能是一个 URL、Issue 标识符、或简要描述），自主发现并调用可用工具，提取并整理完整的产品/功能需求说明。

写入目标：`reqs/<KEY>/REQUIREMENT.md`；相关截图或架构/原型图保存到 `reqs/<KEY>/assets/`。

## 目标与原则

1. **自主发现与工具调用**：
   - 检查当前环境中可用的工具（各类 MCP 工具、API、CLI 命令或 Web 提取工具）。
   - 根据目标特征自主选择最合适的工具进行数据获取（如 Jira、GitHub、GitLab、飞书、Notion、通用 Web 页面等）。
   - 提取需求标题、详细描述、关键业务字段（如 PRD 链接、验收标准）及重要讨论或评论。
2. **处理附件与图片**：
   - 将需求相关的原型图、UI 截图、架构流程图等下载保存到 `reqs/<KEY>/assets/`。
   - 在 `REQUIREMENT.md` 中使用相对路径（如 `![UI](assets/screenshot.png)`）进行引用。
3. **编写标准化 REQUIREMENT.md**：
   - 保持结构清晰、信息完整：包含需求背景、核心功能目标、详细业务规则、边界与异常流程、验收标准（Acceptance Criteria）。
   - 仅针对当前需求的范围，避免 dump 无关的历史页面或整站文档。
4. **落盘边界**：
   - 必须写入 `reqs/<KEY>/REQUIREMENT.md` 并正常结束退出。
   - **不要**在此阶段编写 `GRILL.md` / `SPEC.md` / `TICKETS.md`。

## REQUIREMENT.md 参考结构

```markdown
# <KEY>
<需求标题>

## 需求背景与目标
...

## 详细功能规格与业务规则
...

## 验收标准 (Acceptance Criteria)
...

## 相关链接与参考
...
```
