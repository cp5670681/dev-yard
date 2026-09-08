---
name: fetch-requirement
description: >
  用 pi 的 mcp-atlassian-pro 抽取当前 Jira 的产品说明到 reqs/<JIRA>/REQUIREMENT.md。
  Use when the user runs `dev-yard req open` or says 打开需求, 抽取需求.
---

# fetch-requirement（dev-yard）

用 pi 的 `mcp` 网关、服务器 **`mcp-atlassian-pro`**，只抽 **这一张票** 的当前产品说明。写 `reqs/<JIRA>/REQUIREMENT.md`，本票截图放 `reqs/<JIRA>/assets/`。

调用形状：`mcp({ server: "mcp-atlassian-pro", tool: "<name>", args: { ... } })`。不要用 HTTP 爬整站，不要用其它 MCP。

## 步骤

1. `jira_get_issue`：`issue_key` 为当前 Jira，`fields` 为 `*all`（要产品文档自定义字段），带 comments。
2. 找本票产品文档：Product Document 字段、描述、remote links。标题常含 Jira key。
3. `confluence_get_page` 拉 **本票产品页**（`page_id` 或 title+space_key，`convert_to_markdown: true`）。上级模块总览、更新记录旧行：最多一行说明 + URL。
4. 本票截图：`confluence_get_page_images`（`content_id`）和/或 `jira_get_issue_images`。写到 `assets/`，在 REQUIREMENT.md 里链接。单文件用 `confluence_download_attachment`。
5. 落盘后停。不要写 `GRILL.md` / `SPEC.md` / `TICKETS.md`。

## REQUIREMENT.md

```markdown
# <JIRA>
<title>

## Jira
summary / type / status / description / 关键自定义字段

## Product document (this ticket only)

## Out of scope / not fetched
跳过的历史页 URL
```
