# AGENTS

CLI 入口是 `dev-yard`（或 `devyard`），不要用 `yard`（会撞上 Ruby YARD）。

## 运行时

全程 **pi** + `.pi/skills/`。`dev-yard req open` 走本机 `mcp-atlassian-pro`（Jira + Confluence），只抽**当前这条 Jira** 的产品说明，不要整本历史 Confluence。

`dev-yard web` 是本机控制台（FastAPI），套同一套 `service`；agent 阶段走 `pi -p`。

## 命令安全

pi 的 `bash` 工具 `timeout` 是可选、**无默认值**，内置 `find`/`grep` 工具**根本没有 timeout**；一条挂死的命令会拖死整个阶段（web 里表现为 job 永远 `running`）。所有阶段遵守：

- 一切搜索（`bash` 里的 `find`/`grep`，以及内置 `find`/`grep` 工具）都限定在当前 worktree / 仓库内，**绝不传 `/`**；优先 `rg`（自动跳过 `.gitignore`）。
- `bash` 调用带 `timeout`（秒）。跑测试 / 构建给足下限（如 ≥300s，且小于 `YARD_PI_TIMEOUT`，默认 3600s）；慢套件不是跳过理由，别用短 timeout 误杀。
- 确需跨盘扫描时（仅 `find`）用 `-xdev`，或 `-prune` 掉挂载点。WSL 下 `/mnt/*`、`/usr/lib/wsl/*` 是 9p，遍历会阻塞在 `p9_client_rpc`，连 kill 都不一定收得掉。
- 起服务 / 连 DB / 装依赖等可能阻塞的命令，先想好超时与失败退出，不要裸跑。

上面不只是约定：每个 pi run 都自动加载 `.pi/extensions/yard-guard.ts`（打包在 `dev_yard/extensions/`，见 `resolve_extension_path`）。它在 `tool_call` 层拦下扫 `/`、`/mnt/*`、`/usr/lib/wsl/*`、`/proc`、`/sys`、`/dev`、`/run` 的递归搜索：`find`/`fd`/`du`/`tree`/`rg`/`ag`/`ack`，以及带 `-r` 的 `grep`、含 `R` 的 `ls -lR`。引号内的 `&&` 不会误拆（`rg 'foo && bar' /` 照样拦），`bash -c "..."`、`sudo` 前缀、`cd / && find .`、`pushd / && find .` 都会拆开解析；`find /tmp -o -path /mnt -prune` 这种 `-prune` 写法不拦，`cd` 只在 `&&`/`;` 链内传播（`||`、`)` 后复位）。纯搜索类 `bash` 没带 `timeout` 时补 300s（`rg foo && bundle exec rspec` 这类混跑不补，免得压掉慢套件的下限）。prompt 里的规则模型可能不听，这道是机械的；改拦截规则改那个 TS 文件即可（`tests/test_yard_guard.py` 有正反用例）。

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
| `dev-yard req accounts` | 配本需求要用的账号，写 `.yard-qa/requirements/<JIRA>/accounts.yaml`（明文、随需求变、已 gitignore）；不跑 agent。`--auto` 按 `qa/accounts-discover.sql`（`username \| account_key` 两列）一键发现并写入（复用全局默认账号密码、不动全局账号，权限漂移重跑即改绑）；`--refresh` 清本需求账号缓存登录态强制重登；web 需求页「测试账号」卡片同款按钮 |
| `dev-yard req attach <JIRA> <file>...` | 人工补附件（HTML 原型、文档等）到 `reqs/<JIRA>/uploads/`，并在 REQUIREMENT.md 维护「补充附件」小节；`req detach <JIRA> <name>...` 反向移除。不跑 agent；`uploads/` 不会被 `req open` 清空。web 需求页也可上传/删除 |
| `dev-yard req test` | qa-design + qa-run（提测后设计用例，**暂停等人工审核**；`--approve` 通过后执行，`--redesign` 配合 `--feedback`/`--feedback-file` 带意见重做；设计期宿主跑 `data.verify` 核实数据前置，失败自动回灌重做，`--verify-only` 只核实、`--no-verify` 跳过、`--allow-unverified` 越权放行；`--run-only` 未审核时需再加 `--unsafe-skip-review`；宿主会独立重跑 db 断言的 `sql`，不一致即降级 failed；产物在 `reqs/<JIRA>/qa/`） |
| `dev-yard qa check-env` | 不跑 agent；解析 qa.yaml exec 配方 → ping → hello 回显 |
| `dev-yard req change <JIRA> --note ... --repo ...` | 轻量变更：追加变更记录到 REQUIREMENT.md →（可选 grill）→ 更新 SPEC → 追加一张 `source: light` 票；不重跑 tickets、不改 phase、不动契约。仅 frozen/testing，不涉及契约 |
| `dev-yard req reset-grill <JIRA>` | 不跑 agent；丢弃待答的对齐轮次（`.grill-round.json`）+ 把 GRILL.md 清回空白 + 清 `stage_runs.grill`，下次「对齐」从头生成；不改 phase/票/契约。web 需求页有对应「重置对齐」按钮 |
| `dev-yard implement --from-test` | 修就绪的测试 bug 票（B 票） |
| `dev-yard tdd [on\|off\|status]` | 开/关 TDD（写 `repos.yaml` 的 `dev.tdd`，默认 on）；不跑 agent。关掉后 implement/review 不再要求写跑测试 |

## 产物

- 术语：`reqs/CONTEXT.md`（多票共用）；ADR：`reqs/docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 测试：`reqs/<JIRA>/qa/`（用例、证据、截图）；不进业务仓
- 代码：仅 `dev-yard req freeze` 之后的 worktree。术语/ADR 留在 `reqs/`，不进业务仓。
