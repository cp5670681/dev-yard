# yard-qa 收口（一）：把「宿主替 agent 判断」换成「agent 自己查库自证」

- 日期：2026-11-13
- 状态：**P0 已落地**（P1 见 §6）
- 关联前作：[`2026-09-23-yard-qa-retest-incident.md`](2026-09-23-yard-qa-retest-incident.md)、[`2026-09-29-yard-qa-design-verify-blind-loop.md`](2026-09-29-yard-qa-design-verify-blind-loop.md)、[`2026-09-30-yard-qa-design-fact-grounding.md`](2026-09-30-yard-qa-design-fact-grounding.md)
- 一句话：**worker 不能查库 → 只能猜 → 猜错 → 宿主预证发现 → 回灌重做 → 又猜错。** 解开这个环的办法不是再加规则，而是把「查库」这个能力还给 worker，并把宿主的判断权收掉。

## 1. 为什么改（对照 qa-powers）

| | qa-powers | yard-qa（改前） |
|---|---|---|
| 宿主代码 | 0（5 个 skill，1063 行 md） | 14,683 行 Python + 10,157 行测试 |
| worker 能查库吗 | 能，`usql` 直接跑 | **不能**，只能写 `sql` 字段等宿主重跑 |
| worker 能跑脚本吗 | 能，`ssh + kubectl exec -i … runner -` | **不能**，宿主 design 期代跑 |
| 数据就绪 | run 期 agent 自己做 | design 期宿主预证（`verify.sql` + setup → `seeds.yaml`） |
| 判定 | agent 三层断言后自己写 result.yaml | worker 写 expected，**宿主独立重跑并可降级 failed** |
| 门禁 | 无，最多问一句 | **164 处 `raise TestRejected`**、20+ CLI flag、相位机 |
| 上下文注入 | `config.yaml`（几十行）+ 自己查 | `context.md` **85KB**（全量 `information_schema`） |

`git log`：qa 相关提交 68 / 239（28%），`qa.py` 被改 53 次。三次复盘指向同一个结构问题：**预证把一条链拆成两段（两段环境不同），宿主替代 agent 判断（判断错了就自动拆票、拆票又挡住重测）**。2026-09-30 那篇的原话是「上一轮拍板的 3 条静态规则 + 硬门禁今天命中 **0/17**」——加规则不是解法。

## 2. 本次改动（P0）

### 2.1 给 worker 三条宿主通道（新增）

```
dev-yard qa sql  <JIRA> -c "SELECT ..."   # 或 -f x.sql；--json 取第一行；--db <catalog>
dev-yard qa exec <JIRA> <script> [--case <id>] [--kind setup|cleanup|verify|other]
dev-yard qa logs <JIRA> --request-id <id>  # 既有，5xx 诊断
```

- `qa sql` 只允许**单条只读**语句（`assert_readonly_sql`），走 `db.verify_url`（配了就只用只读账号）；**DSN 与 exec 配方都不进 prompt**——既 self-serve 又不泄密，这是与 qa-powers「明文 DSN 直接给 agent」的刻意差别。
- `qa exec` 与宿主跑 setup/cleanup 走**同一条** `run_script_path`（同样的 exec 配方、同样的 worktree 回滚），区别只是由 worker 决定何时跑；每次写操作在 `qa/exec-log.md` 留一行（时间/env/case/kind/脚本/结果），可追责。
- 两个 skill（`qa-design`、`qa-run`）与 `qa/context.md` 都改成「你可以自己查、自己造」，删掉「不许自己连库 / 不许跑 data.setup」的旧口径。

### 2.2 删掉设计期预证的回灌（`_verify_loop`）

> 注：§2.2 描述的是 P0 的中间态（“设计期仍跑一遍”）。同批 P1（§6）把 `verify_cases`
> 整个删掉了，所以现在设计期**不跑**任何预证；`data.verify` 只剩下面列出的两个用途。

`data.verify` 从**强制前置证明**降级为**可选自检**：

- 设计期仍跑一遍（一次，不循环），结果落 `qa/design-verify/` 当参考；
- **未通过不回灌 design、不挡 approve、不挡 run**（不再 `reject_cases`、不再 `design-blocked` 封顶、不再写 `BLOCKED.md` 挡人）；
- 缺 `data.verify` 从 `failed` 改成 `skipped`，lint 不再报「必须有 data.verify」；
- 契约 lint（缺 covers / repo 不是别名 / 脚本事实）从「回灌一轮 pi 修」改成**日志提示**。

### 2.3 删掉宿主独立复核（`recheck_db_assertions`）

宿主不再重跑 worker 的 db 断言、不再按「expected vs 单元格」降级 failed（这条曾把整句 expected 的 case-08 从 passed 打成 failed，进而拆出假 B 票）。`sql` 字段保留为**审计痕迹**：报告原样展示 SQL 与 worker 读到的值，人可 `dev-yard qa sql <JIRA> -c "<那条 sql>"` 复跑（复跑按钮是 P1 的 UI 收口项）。db 断言里自报 passed 而值对不上，报告里一眼能看出。

### 2.4 闸门 → 提示

| 原来 | 现在 |
|---|---|
| 还有未完成的产品票 → `TestRejected` | 日志提示，照跑（曾造成 redesign 与单条重测的死锁） |
| 已有一轮通过的测试报告 → `TestRejected` | 提示「本次是追加一轮」 |
| 用例未审核 → `awaiting_review`，`--run-only` 要加 `--unsafe-skip-review` | 提示一句，照跑；`--approve` 只记一笔 |
| 数据自查未通过 → `--allow-unverified` 才放行 | 无作用，自查不挡任何东西 |
| `--approve` 被任何 lint 问题挡住 | 只有**账号未配置**（真的跑不了）才挡 |
| Web 看板「执行用例」未审核时置灰 | 可点，reason 提示先看一眼用例 |

`--unsafe-skip-review` / `--allow-unverified` 保留为兼容参数（帮助文本已写明「已无作用」），P1 再删。

## 3. 行为对照（使用者视角）

| 场景 | 改前 | 改后 |
|---|---|---|
| `req test <JIRA>` | 设计 → 停在待审核 | 设计 → 直接跑（`--design-only` 只设计） |
| 用例缺种子数据 | design 期回灌 3 轮后 `design-blocked` | worker 执行时 `qa sql` 查 → `qa exec` 造 → 复测 |
| worker 猜错列名/常量 | 预证报错，回灌盲改 | 直接 `qa sql` 查 `information_schema`，不猜 |
| db 断言与页面不一致 | 宿主复核降级 + 可能拆票 | 报告里并列展示 expected/actual/sql，人工判 |

## 4. 改了哪些文件

- 新增：`dev-yard qa sql` / `qa exec`（`cli.py`）、`qa_exec.run_script_path`、`tests/test_qa_agent_cli.py`
- 删除：`qa_design._verify_loop`、`qa_design._mark_design_blocked`、`qa_exec.recheck_db_assertions`（含 `_db_recheck_*` / `_is_prose_expected` / `_scalar_eq`）、`qa.py` 的契约回灌与 `verify_gate`
- 改语义：`qa_runloop._gate`（告警化）、`qa.py` 审核/审批/自查路径、`qa_verify.verify_case`（缺 verify.sql → skipped）、`reqboard`（未审核不再置灰、未完成票不再挡 QA）、`web/jobs.py`（design-blocked 不再判 job 失败）
- 文档：`AGENTS.md`、`README.md`、`web/src/views/QaView.vue`、`.pi/skills/qa-{design,run}/SKILL.md`

净减约 950 行（其中 qa 主链路 −470，测试 −500 是被删功能的用例）。

## 5. 顺带修的一个坑

`yard-doc-align.ts` 只该在 doc-align 阶段生效（`stages.py` 用显式 `--extension` 加载、并 `--no-extensions` 关掉目录发现）。但**交互式 `pi` 会话会自动发现 `.pi/extensions`**，于是任何人在本仓手工开 pi 都会被「只能写 `.doc-align/*.json`」的写白名单挡住。

现在扩展自带 `shouldEnforce(process.env.YARD_STAGE)`：只有 `YARD_STAGE=doc-align` 才拦；runner 在两种 spawn 路径上都注入该变量。`tests/test_yard_guard.py` 增加了 node 侧用例（顺带修好了两个一直 skip 的 node 测试——jiti 是 hoisted 安装，旧路径找不到）。

## 6. P1（同批已落地）：把剩下的重机器也拆掉

| 删掉的东西 | 行数 | 为什么 |
|---|---|---|
| `qa_seeds.py` + `<seed.x>` 占位符替换 + `seeds.yaml` | 217 | worker 自己 `qa sql` 查到真值，不需要宿主替它填占位符 |
| `qa_facts.py`（facts.yaml）+ `qa_probe.py`（探针轮）+ `facts_prompt_block` | 825 | 85KB 的 schema/常量转储喂给模型，命中率不如它自己查一次 `information_schema` |
| `qa_verify.py` 的设计期核实（`verify_cases`/`verify.sql` lint/宿主复跑/`BLOCKED.md`） | 1879 → 139 | 只剩 `env_lock` 与错误分类 |
| `qa_design._verify_loop` 及其回灌、`_repair_keeps_assertions`、body 快照 | ~400 | 回流整套没了 |
| context.md 的 `## Database columns`（全量转储）、`## 现场脚本契约`、未部署列清单 | — | 85KB → **<4KB**，改成「自己查」的一句话 |
| 池预检（`_real_pool_probe`）+ 整轮熔断（`_mark_env_fault`） | ~80 | 一个坏 provider 只挡它自己的用例（2026-09-23 P1-5 的根因） |
| 相位机的来源态守卫（`EVENTS` frozenset + `transition` 拒绝） | ~40 | 证据说了算，非法事件只提示 |
| CLI `--verify-only/--no-verify/--allow-unverified/--unsafe-skip-review` | — | 连同语义一起删，不再留空壳 |
| `qa_deploy.frozen_models/undeployed_columns` | ~90 | 只喂给已删的 facts 与 context 小节 |

qa 代码 **14,683 → 10,569 行（−28%）**，qa 测试 **10,157 → 7,466 行**。

还留着（有意）：`qa_config`（= qa-powers 的 config.yaml）、`qa_schedule`（并发调度与重试）、`qa_runloop` 的 resume/incremental、web 控制台、账号体系、`qa logs`。再往下砍就要动并发与 UI 契约，收益不划算。

## 6b. 还想做但没做

1. **web 收口**：`QaView`(1378) + `QaConfigView`(1444) 里仍有 `design-verify` 相关卡片与「模型池」措辞要清。
2. **「重跑这条 SQL」按钮**（CR F1）：现在靠 `dev-yard qa sql -c "<那条 sql>"` 人工复跑。

## 7. 验收指标（连续 3 个需求）

- design-verify 回流次数 = 0
- host-recheck 降级次数 = 0
- 每个需求 ≤ 1 轮 run 跑完
- 单条用例收敛次数 ≤ 1.3（靠 `.replay.sh`）

## 8. 回退

改动都在单一提交里，按文件回退即可；无配置迁移、无数据格式变更（`qa/design-verify/*.yaml`、`seeds.yaml`、`evidence/` 布局不变，旧 run 仍可读）。

## 9. 复查修复（同一批，代码走查后）

对外行为不变，修的是「删东西留下的洞」：

| 问题 | 修法 |
|---|---|
| `qa.py` 仍调已删的 `prior_defects`/`render_prior_defects`，`NameError` 被 `except Exception` 吞掉 → 设计提示词里的「上一轮缺口」永远为空（ruff F821 也挡 CI） | 删掉这段死调用与 `_design_prompt(history=)`；跨轮缺口不再注入，数据由 worker 用 `qa sql` 自己查 |
| `qa_state.EVENTS` 漏 `run_recycled`，而 run 结束仍会发它 → 每次 recycled run 都走异常兜底并记一条「状态机未记录」 | 补 `"run_recycled": "recycled"` |
| `dev-yard qa sql` 无输出上限：`usql` stdout 全量进内存 + 进 worker 的 prompt（与图片 base64 炸窗口同一类事故） | 新增 `qa_exec.run_sql_rows`：DB 端 `LIMIT`（Oracle 用 `ROWNUM`）、行数切片、单行 2000 字符截断 |
| `dev-yard qa exec` 写库不持 env 锁（原来设计期核实是持 `.verify.lock` 的） | `qa exec` 默认取 `env_lock`（`run.env_wait_timeout` 排队）；run 派生的 worker 通过 `YARD_QA_ENV_LOCK` 认出自己，不走锁（否则父子进程互等） |
| M6b 删除后 `qa_review.mark_machine_fixed` / `machine_fixed` 字段无人生成；`qa_config` 的 `design.verify_*`、`design.probe_attempts`、`run.pool_preflight` 仍被解析却全无作用 | 一起删；旧 `qa.yaml` 里留着这些键不再报错、也不再有任何效果 |
| `cli.py` / `web/jobs.py` 里 `awaiting_review` 分支已不可能命中，提示语还写着「通过后执行：--run-only」 | 删分支，提示改成「审核只作记录，执行不需要它」 |
| `qa_design._catalogs_for_requirement`、`write_context_md` 的 `others`、`qa_state.can`、`qa_exec.run_sql_value`、`qa_design` 的未用 import | 删 |
| `README.md` / `AGENTS.md` / 本文 §2.2 的口径过期 | 更新：`--design-only` 不再「停下等审核」；`qa status` 不再提「隔离模型池」；§2.2 注明设计期已不跑预证 |

`tests/test_qa_agent_cli.py` 新增两条用例钉住新行为：SQL 的 `LIMIT` 在 DB 端生效、单行被截断；`qa exec` 在别人持 env 锁时拒绝写、在 `YARD_QA_ENV_LOCK` 匹配时放行。
`tests/test_qa.py` 的 `test_machine_fixed_requires_unchanged_body` 改成 `test_case_fingerprint_body_scope_ignores_setup`（保留指纹作用域的断言，去掉已删的 `mark_machine_fixed`）。

## 10. 复查修复（二）：删完之后剩下的「空承诺」

同一批，代码走查后发现「功能删了、外表面还留着承诺」。对外行为按第 9 节不变，这里只把承诺也删掉：

| 问题 | 修法 |
|---|---|
| 自动回流（M6b）已删，但看板仍写「将自动回流用例缺陷：…（下次设计时只改种子，不占人工判定）」、`qa status` 仍打印「自动回流用例缺陷」 | `triage_buckets` 只留 `pending`（case-defect 也是人工判定项，靠 `classify_defect` 挡住「下 bug」）；`record_triage` / `triage()` 去掉 `auto_recycled` 字段；看板卡片去掉那一段；`EVENTS` 去掉 `run_recycled` |
| `EVENTS["run_recycled"]` 不再有触发点后，纯 case-defect 的一轮会写 `phase=recycled` 而 `derive_phase` 只认 `filed` → 每次一条假漂移 | 同上：没有 `auto_recycled` 分桶就没有这条记录路径 |
| `qa_report.has_design_blocked_skip` / `DESIGN_BLOCKED_PREFIX` / ingest 的 `design-blocked` 分支 | 唯一产出 `design-blocked:` 的 `_mark_design_blocked` 已删 → 三处一并删（含那条测试） |
| `_lint_block_message` 的兜底分支写着「或加 `--allow-unverified` 越权」，且只被账号缺口调用 | 只留账号缺口一条消息 |
| 删掉契约回灌后，`_req_test` 里 `lint_cases` 与 `_try_autofill_accounts` 各跑两遍、同一批问题打两行日志 | 收成一次：修发现查询 → autofill → lint 一次 → 打一行 |
| `qa_review._case_assets(scope=)` / `cases_fingerprint(scope=)` 的 bodies/seeds 拆分只剩测试在用 | 去掉 `scope`，测试改测「setup 改动与正文改动都会移动指纹」 |
| `script_exec.FUSE_CLASSES` 无人引用；`EVENTS["design_start"]` 无触发点 | 删 |
| `.pi/skills/qa-design/SKILL.md` 让 agent 认「只修这些」，但没有任何提示词含这句话 | 改成实际口径：「人工审核意见（必须据此修订用例）」/「发现查询修补。这不是重新设计。」 |
| `qa_design._duties` 仍允许写 `qa/probes/`；`qa_config` 仍称 `verify_url` 是 design-time verify.sql | 改成现状（`qa sql` / `data.verify`） |

测试 1175 通过；`ruff` 对改动文件全绿（仓库既存 21 条告警不在本次范围）。
