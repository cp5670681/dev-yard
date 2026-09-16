---
name: qa-design
description: >
  对着 freeze worktree 的 SPEC 与 diff 设计 yard-qa 用例。
  Use when the user runs dev-yard req test, qa-design, 设计测试用例.
---

# qa-design（dev-yard）

为当前需求写 UI 用例。**只写** `reqs/<JIRA>/qa/**`。测的是 freeze worktree，不是 `repos.yaml` 主克隆。

## 必读

- `reqs/<JIRA>/{REQUIREMENT,SPEC,TICKETS}.md`（只读）
- `reqs/<JIRA>/qa/context.md`（宿主写的 worktree 地图与 base_url）
- `reqs/CONTEXT.md`（若有：只读术语）
- 每个 worktree：`git -C <path> diff <default_base>...HEAD`（HEAD 即 `req/<JIRA>`）

不要调 MCP、不要重拉 Jira、不要访谈。缺细节用 SPEC 与常规默认，在用例里标注假设。

`git` 只用 `diff` / `log`。不要 checkout、commit、push、switch。

## 做法

1. 每个 worktree 做 `git diff <default_base>...HEAD`。空 diff：停止并说明，不要编改动。
2. 改动点 D1..Dn 写入 `qa/meta.yaml`。`repo` 必须是 `repos.yaml` 别名（context 地图里的 alias），不要写 frontend/backend 泛称。`role` 只作阅读提示。
3. **增量**更新 `meta.yaml`：改 `changes` / `base_branches` / `feature_branches` / `module` / `requirement`；保留已有 `routes:`，不要整文件覆盖。
4. 每个 D 至少 1 条用例；另加 1 条正常流。写到 `qa/cases/<module>/case-NN.md`。
5. 步骤用业务语言。按钮/文案必须来自 worktree 代码，不来自想象。
6. 预期写需求口径。实现与 SPEC 不符时仍写需求值，并备注「需求偏差」。
7. 跨仓改动拆成多条 case，或 `covers` 只含一个主仓。每条 frontmatter 必有 `repo:`（yard alias）。
8. `depends_on` 仅当共享可变数据或业务先后时写；无依赖省略，以便并发领取。

## meta.yaml

```yaml
module: <JIRA>-<短名>
requirement: <JIRA>
base_branches: { <alias>: <default_base> }
feature_branches: { <alias>: req/<JIRA> }
routes: {}
changes:
  - id: D1
    repo: <alias>
    ref: src/foo.vue
    desc: ...
```

## 用例

```markdown
---
id: case-01
title: ...
priority: P0
requirement: <JIRA>
repo: <alias>
covers: [D1]
depends_on: []
data: { setup: setup.sql, cleanup: cleanup.sql }
---

## 前置
- 已登录
- <业务前置>

## 步骤
1. <可在 UI 上执行的业务步骤>

## 预期
- UI: <可判定>
- 网络: <有提交时>
- DB: <预期含 DB 时>
```

无 DB 则去掉 `data` 与 DB 预期。造数脚本与 case 同目录，幂等。

写完后停。不要跑浏览器、不要改 STATUS.yaml。
