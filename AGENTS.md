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

上面不只是约定：每个 pi run 都自动加载 `.pi/extensions/yard-guard.ts`（打包在 `dev_yard/extensions/`，见 `resolve_extension_path`）。它在 `tool_call` 层拦下扫 `/`、`/mnt/*`、`/usr/lib/wsl/*`、`/proc`、`/sys`、`/dev`、`/run` 的递归搜索：`find`/`fd`/`du`/`tree`/`rg`/`ag`/`ack`，以及带 `-r` 的 `grep`、含 `R` 的 `ls -lR`。引号内的 `&&` 不会误拆（`rg 'foo && bar' /` 照样拦），`bash -c "..."`、`sudo` 前缀、`cd / && find .`、`pushd / && find .` 都会拆开解析；`find /tmp -o -path /mnt -prune` 这种 `-prune` 写法不拦，`cd` 只在 `&&`/`;` 链内传播（`||`、`)` 后复位）。纯搜索类 `bash` 没带 `timeout` 时补 300s（`rg foo && bundle exec rspec` 这类混跑不补，免得压掉慢套件的下限）。同一道还拦 `read` 打开图片（`png`/`jpg`/`jpeg`/`gif`/`webp`/`bmp`）：`read` 的图片进 `function_call_output`，grok-cli 网关把它当 base64 文本计 token，一张截图（~1MB）就 ~78 万 token，直接顶爆模型窗口报 `input_too_large` 让整轮失败。需求截图改为在 prompt 里以 `@` 附件挂进用户消息（`attachments.list_images` → 各阶段 `extra_read_paths` / `pi_argv(attach=…)`，同样的图只算 ~1.2k token）：grill/spec/tickets 走 `run_stage`，implement/review/contract 在 `service` 里挂，qa-design/qa-run 在 `qa` 里挂；UI 逻辑仍以 `uploads/` 的 HTML 原型与 `REQUIREMENT.md` 文本为准。prompt 里的规则模型可能不听，这道是机械的；改拦截规则改那个 TS 文件、改挂载范围改 `attachments.IMAGE_SUFFIXES`/`list_images`（两处后缀集需一致，`tests/test_yard_guard.py` 与 `tests/test_attachments.py` 有正反用例）。

## 路由（pi）

| 命令 | 技能 |
|------|------|
| `dev-yard req open` | fetch-requirement |
| `dev-yard req import <JIRA> --branch <alias>:<ref>...` | 外部导入：需求文档（复用 open 来源）+ 每仓一个已有分支，建 freeze worktree 直达 testing（默认提测，`--no-submit` 停在 frozen）；每仓自动一张 `source: import` 的 done 票，契约置 passed；后续走常规 `req test` / `implement --from-test` |
| `dev-yard grill` | grill-with-docs + grilling + domain-modeling |
| `dev-yard spec` | to-spec |
| `dev-yard tickets` | to-tickets |
| `dev-yard implement` | implement + tdd + codebase-design（`dev.tdd=false` 时不加载 tdd） |
| `dev-yard review` | code-review |
| `dev-yard run <stage>` | 插件阶段，以及 grill/spec/tickets（`dev-yard stages` 可查）。`open` / `implement` / `review` / `contract` / `qa-design` / `qa-run` / `test` 走专用命令 |
| `dev-yard req submit-test` | 提测：把冻结分支 push 到远端，再 merge 进各仓 `test_branch` 并 push（缺 `test_branch` 的仓跳过）；幂等可重入，冲突时默认用 implement 模型消解（`--no-resolve` 关闭，`--all` 全量重推） |
| `dev-yard req accounts` | 配本需求要用的账号，写 `.yard-qa/requirements/<JIRA>/accounts.yaml`（明文、随需求变、已 gitignore）；不跑 agent。`--auto` 按 `qa/accounts-discover.sql`（`username \| account_key` 两列）一键发现并写入（复用全局默认账号密码、不动全局账号，权限漂移重跑即改绑）；`--refresh` 清本需求账号缓存登录态强制重登；web 需求页「测试账号」卡片同款按钮 |
| `dev-yard req attach <JIRA> <file>...` | 人工补附件（HTML 原型、文档等）到 `reqs/<JIRA>/uploads/`，并在 REQUIREMENT.md 维护「补充附件」小节；`req detach <JIRA> <name>...` 反向移除。不跑 agent；`uploads/` 不会被 `req open` 清空。web 需求页也可上传/删除 |
| `dev-yard req test` | 拆成三段：设计用例（`--design-only`，或 web「设计用例」）→ **暂停等人工审核**（`--approve` 只标记通过，不再触发执行）→ 执行用例（`--run-only`，或 web「执行用例」）；`--redesign` 配合 `--feedback`/`--feedback-file` 带意见重做；设计期宿主跑 `data.verify` 核实数据前置，失败自动回灌重做，`--verify-only` 只核实、`--no-verify` 跳过、`--allow-unverified` 越权放行；未审核直接 `--run-only` 需再加 `--unsafe-skip-review`；宿主会独立重跑 db 断言的 `sql`，不一致即降级 failed；**失败不再自动拆 B 票**，在失败用例卡片/详情上「下 bug」人工建票（`POST /api/requirements/<JIRA>/qa/cases/<case>/bug`）；产物在 `reqs/<JIRA>/qa/` |
| `dev-yard qa check-env` | 不跑 agent；解析 qa.yaml exec 配方 → ping → hello 回显 |
| `dev-yard req triage <JIRA>` | 不跑 agent；给待判定失败用例批量下 bug（默认只对宿主判定为 `product` 的项，`--all` 含未分类）；web「批量下 bug」同款 |
| `dev-yard qa status <JIRA>` | 不跑 agent；读 `qa/state.yaml` + 证据推导当前态（designing/awaiting_review/running/awaiting_triage…）、待判定失败项、隔离模型池与下一步 |
| `dev-yard req change <JIRA> --note ... --repo ...` | 轻量变更：追加变更记录到 REQUIREMENT.md →（可选 grill）→ 更新 SPEC → 追加一张 `source: light` 票；不重跑 tickets、不改 phase、不动契约。仅 frozen/testing，不涉及契约 |
| `dev-yard req reset-grill <JIRA>` | 不跑 agent；丢弃待答的对齐轮次（`.grill-round.json`）+ 把 GRILL.md 清回空白 + 清 `stage_runs.grill`，下次「对齐」从头生成；不改 phase/票/契约。web 需求页有对应「重置对齐」按钮 |
| `dev-yard implement --from-test` | 修就绪的测试 bug 票（B 票） |
| `dev-yard tdd [on\|off\|status]` | 开/关 TDD（写 `repos.yaml` 的 `dev.tdd`，默认 on）；不跑 agent。关掉后 spec/tickets 不写测试验收，implement 不写不跑测试，review 不因测试文件缺失或跑不起来打回 |

## 产物

- 术语：`reqs/CONTEXT.md`（多票共用）；ADR：`reqs/docs/adr/`
- 需求：`reqs/<JIRA>/{REQUIREMENT,GRILL,SPEC,TICKETS}.md`
- 测试：`reqs/<JIRA>/qa/`（用例、证据、截图）；不进业务仓
- 代码：仅 `dev-yard req freeze` 之后的 worktree。术语/ADR 留在 `reqs/`，不进业务仓。
