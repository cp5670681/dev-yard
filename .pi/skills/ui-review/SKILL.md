---
name: ui-review
description: >
  Review implemented code against requirement docs, HTML prototypes, and UI screenshots.
  Use when the user runs /ui-review, dev-yard review --ui, 原型核对, UI核对.
---

# ui-review（dev-yard 原型与UI核对）

对照需求文档、`uploads/` 目录下的 HTML 原型、以及原型图截图，审查前端 worktree 中已经实现的代码，核对功能、字段、交互与排布是否完整还原原型。

## 1. 原型与规范来源

1. **原型图截图**：已作为图片附件（`@file`）挂在用户输入消息中（包括 `assets/` 中的截屏和 `uploads/` 中的设计截图）。
2. **HTML 原型**：`reqs/<JIRA>/uploads/*.html`。HTML 原型包含最真实的表单联动、状态机、字段校验与交互规则，必须用 `read` 工具仔细阅读。
3. **补充附件与文档**：`reqs/<JIRA>/uploads/` 中的其他文本/文档。
4. **产品需求与规约**：`reqs/<JIRA>/REQUIREMENT.md`（产品意图）与 `reqs/<JIRA>/SPEC.md`（实现规约）。

## 2. 审查范围与工作区

- 启动提示中已内联各需求 worktree 相对冻结点的 `git diff`，并列出了各仓库的本地路径。
- **重点审查前端仓库**（包含 Vue/React 组件、路由、模板、样式、状态管理等）。
- 使用 `read` 工具打开前端相关文件（如页面组件、表单组件、对话框、数据字典），不要只看 diff 片段，要核对整体渲染模板和脚本逻辑。
- 本阶段为只读审查阶段，**不要修改任何代码文件**，不要跑 git 命令（无 bash 工具）。

## 3. 核对维度

### 维度 A：页面字段与元素完整性（Missing / Extra）
- 原型上的所有表单输入项、下拉选择、单选/复选框、标签、日期选择器，代码模板里是否全部存在？
- 表格的列定义、列名、操作列按钮（如“编辑”、“查看”、“导出”、“删除”）是否遗漏？
- 页面关键区域（面包屑、标题、筛选区、统计卡片、空状态 Empty State）是否完整？

### 维度 B：交互与状态机（Behavior / States）
- 表单联动逻辑（如选中 A 后 B 禁用/隐藏、切换类型变更校验规则）是否与 HTML 原型一致？
- 弹窗、抽屉（Dialog / Drawer）的触发条件与关闭/提交逻辑是否符合原型？
- 默认选中项、占位符（Placeholder）、默认排序、分页配置是否符合原型？

### 维度 C：文案与提示语（Copy / Labels）
- 页面标题、按钮文字、弹窗确认提示语、二次确认文案是否忠实于原型和需求？
- 校验失败的错误提示语（ErrorMessage / Tooltip）是否一致？

## 4. 报告结构与结论提交

审查报告按上述维度清晰列出对比结论：
- 符合原型的部分简要说明。
- 存在出入的部分，明确指出：【原型要求】、【当前代码】、【建议修改文件与方案】。

**报告写完后，必须且仅能调用一次 `submit_review` 工具**：
- `verdict`：
  - 如果发现实质性的字段遗漏、交互缺失、组件错误或严重文案偏差，设为 `"failed"`。
  - 如果与原型完全一致，或仅存在合理的前端工程微调（不影响产品交互），设为 `"passed"`。
- `findings`：
  - 当 `verdict` 为 `"failed"` 时，将每个独立的缺口作为一条 finding 填入数组：
    - `id`：简短编号，如 `UI-001`, `UI-002`
    - `title`：缺陷标题（如 "缺少审核状态筛选下拉框"）
    - `repo`：存在缺陷的前端仓库别名（`repos.yaml` 中的 alias）
    - `detail`：详细对比说明（原型如何要求，代码缺少什么，如何补齐）
    - `files`：涉及的前端文件路径列表
  - 当 `verdict` 为 `"passed"` 时，`findings` 传空数组 `[]`。
