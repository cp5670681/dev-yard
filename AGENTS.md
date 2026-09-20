# AGENTS

CLI 入口是 `dev-yard`（或 `devyard`），不要用 `yard`（会撞上 Ruby YARD）。

## 运行时

全程 **pi** + `.pi/skills/`。`dev-yard req open` 走本机 `mcp-atlassian-pro`（Jira + Confluence），只抽**当前这条 Jira** 的产品说明，不要整本历史 Confluence。

`dev-yard web` 是本机控制台（FastAPI），套同一套 `service`；agent 阶段走 `pi -p`。

## 路由（pi）

| 命令 | 技能 |
|------|------|
| `dev-yard req open` | fetch-requirement |
| `dev-yard grill` | grill-with-docs + grilling + domain-modeling |
| `dev-yard spec` | to-spec |
| `dev-yard tickets` | to-tickets |
| `dev-yard implement` | implement + tdd + codebase-design |
| `dev-yard review` | code-review |
| `dev-yard run <stage>` | 插件阶段，以及 grill/spec/tickets（`dev-yard stages` 可查）。`open` / `implement` / `review` / `contract` / `qa-design` / `qa-run` / `test` 走专用命令 |
| `dev-yard req submit-test` | 提测：把冻结分支 push 到远端，再 merge 进各仓 `test_branch` 并 push（缺 `test_branch` 的仓跳过）；幂等可重入，冲突时默认用 implement 模型消解（`--no-resolve` 关闭，`--all` 全量重推） |
| `dev-yard req accounts` | 配本需求要用的账号，写 `.yard-qa/requirements/<JIRA>/accounts.yaml`（明文、随需求变、已 gitignore）；不跑 agent |
| `dev-yard req test` | qa-design + qa-run（提测后设计用例，**暂停等人工审核**；`--approve` 通过后执行，`--redesign` 配合 `--feedback`/`--feedback-file` 带意见重做；产物在 `reqs/<JIRA>/qa/`） |
| `dev-yard qa check-env` | 不跑 agent；解析 qa.yaml exec 配方 → ping → hello 回显 |
| `dev-yard implement --from-test` | 修就绪的测试 bug 票（B 票） |
| `dev-yard tdd [on\|off\|status]` | 开/关 TDD（写 `repos.yaml` 的 `dev.tdd`，默认 on）；不跑 agent。关掉后 implement/review 不再要求写跑测试 |

## 产物

- 术语：`reqs/CONTEXT.md`（多票共用）；ADR：`reqs/docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 测试：`reqs/<JIRA>/qa/`（用例、证据、截图）；不进业务仓
- 代码：仅 `dev-yard req freeze` 之后的 worktree。术语/ADR 留在 `reqs/`，不进业务仓。
