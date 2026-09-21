# yard-qa 数据前置核实：设计期跑通，run 期零人工

- 日期：2026-09-21
- 状态：方案 v2（待实现，据评审修订：核实循环走内部通路、行数宿主计数、豁免 lint、M6 范围护栏、跨轮去重、并发锁、开放问题落案）
- 触发：[PG-13175](../../../reqs/PG-13175/) 本轮 3 passed / 13 blocked，其中 2 条 `case-defect` 都是**数据缺口**（case-02 种子缺前端必填字段、case-06 引用了不存在的项目 id），且 case-02 与上一轮一字不差地重复。
- 对照：qa-powers 的 design 会查库/问人，run 由同一 agent 自己跑 setup 且「造数报错先查约束再改脚本」（`design` §3/§4、`run` §1a），所以数据问题在 run 期自愈；yard 的 run 被禁止改 setup，只能 blocked。
- 约束：不改四态机、不碰 exec 路由与调度；不自动通过业务审核。

## 1. 目标

1. **人只在前期出现**：design 阶段回答问题、审核业务；**进入 run 后不再需要人**。
2. **数据可达性从「prose 声明」变成「可执行、有产物、有门」**：design 说的「已核实」必须是宿主真跑过的查询。
3. **缺口在 design 阶段暴露并自动修复**，而不是留到 run 变 `blocked`。
4. 保留审计性：run 期仍不改产品代码、不改例子预期；数据修正只发生在 design 期（或 run 期的自动回流，见 M6；P2 的可选补填见 M7）。

## 2. 根因（为什么现在必然踩）

| 环节 | 现状 | 后果 |
|---|---|---|
| 作者/执行者分离 | design 只写 setup，宿主在 run 前跑一次，run agent 禁止改 | design 的任何未核实假设只能变 `case-defect` |
| 「只读核实」是 prose | 无产物、无人审可见项、无校验 | case-06 能写「已核实」而项目不存在 |
| seed 自证选错载体 | 只用 `Contact.create!`（模型校验），不覆盖前端专属必填 | case-02 四字段 `l_salutation/province_id/city_id/l_address` 造数期不报 |
| 外部实体不校验 | `UPDATES` 只 `abort if 联系人不存在` | 项目/关联缺失不报 |
| 回流是建议 | run 结束只打印「需 --redesign」，未自动执行 | 缺口跨轮重复（已发生） |

## 3. 设计原则

1. **核实即执行**：任何「数据存在/字段齐全」的断言，都写成宿主可执行的只读查询（`verify.sql`），跑过才算数。
2. **失败前置**：核实失败在设计阶段自动回灌给 design agent 修复；修不好就不让它进 run。
3. **run 只跑已核实的用例**：run 期不再处理「数据本身不对」的问题；只处理产品缺陷与环境故障（P2 的 M7 补填是唯一例外，默认关且带护栏）。
4. **前期尽量参考真实数据**：design 可通过宿主跑只读 `probe.sql` 拿到真实 id/样本，再写用例，杜绝硬编码臆想数据。
5. **零人工不等于零审计**：所有核实查询、结果、seed 自证输出都落盘。

## 4. 机制

### M1 可执行数据核实（`data.verify`）

- 用例 frontmatter：`data: { setup: setup.rb, cleanup: cleanup.rb, verify: verify.sql }`。
- `verify.sql`：**单条只读 SELECT**（`SELECT/SHOW/DESC/EXPLAIN`，宿主用 `usql` 打 `qa.yaml` 的 `db.url`），语义固定为 **返回 ≥1 行即通过**（把前置写成 `SELECT ... WHERE <条件>`，0 行即失败）。
- **行数由宿主取，不解析 stdout**：`rows` 必须在执行层拿到稳定计数，而不是从 `usql -f` 的表格文本里数行。做法二选一：给 `_run_sql` 增加 `count_only` 变体，用 `usql -t -c` 执行包了一层计数的 SQL（`SELECT count(*) FROM (<verify.sql 去分号>) t`），或让 `verify.sql` 本身就以 `SELECT count(*) ...` 收尾、由宿主取首行整数。二者都必须与「≥1 行即通过」等价。
- 每条含 UI 预期、且预期引用数据的用例都必须有 `verify.sql`。
- **豁免必须过机检，不能靠自觉**：确无数据可断言的用例可写 `SELECT 1`，但宿主在核实前先做 lint——`verify.sql` 里 `FROM`/`JOIN` 的表名必须出现在该 case 正文中（列名只作提示，不强制，否则 `deleted_at IS NULL` 之类正确写法会被误杀）；lint 不过即判 `failed`（`reason: verify-lint: ...`），写 `SELECT 1` 也不能蒙混。lint 判为空转的 case 在审核门标为「空转豁免」，供人抽查。
- **必填字段来自表单代码，不是想象**：含提交的用例必须从目标表单组件反查校验规则（`:rules` / `required` / 自定义 validator），把**全部**必填字段列入 `verify.sql` 断言（`... IS NOT NULL`）；静态必填由本机制覆盖，动态/条件/跨字段必填在 P0 覆盖不到，由 M4b 兜（见 M4b 依赖关系）。
- 宿主产物：`reqs/<JIRA>/qa/design-verify/<case-id>.yaml`：
  ```yaml
  case: case-06
  status: passed | failed
  setup: { code: 0, stdout: "..." }
  verify_sql: "SELECT id FROM projects WHERE id=669215"
  rows: 0                # 宿主计数（count(*) 包裹或输出行数），非 stdout 解析
  lint: { ok: true, matched: [projects] }
  error: "0 rows"
  ```
- 远程 env 的 `.sql` 一律宿主执行（沿用现有「.sql 在宿主用 usql 跑」），因此核实结果可信、可重现。
- **同 env 串行化**：核实循环要真跑 setup（会写共享测试库），必须与在跑的 run / schedule 互斥。宿主在**每次核实执行周期**持 env 级锁 `.yard-qa/locks/<env>.verify.lock`（沿用 `_run_lock` 的 token+硬链接写法；放 `.yard-qa/locks/` 而非 `qa/design-verify/`，前者是按 env 复用的既有锁目录），拿不到锁则拒绝，杜绝「design 期种子」与「run 期快照」互脏。（不跨 agent 重做期持锁——每次核实前后 seeds 已 cleanup，锁只为串行化写库。）
- 落盘：每次核实写逐 case `qa/design-verify/<case-id>.yaml` + 汇总 `qa/design-verify/summary.yaml`（含 cases 指纹，指纹不符即视为过期）；仍有失败时另写 `qa/design-verify/BLOCKED.md`（design-blocked 的可读清单，替代会污染 `Qn:` 计数的 OPEN-QUESTIONS.md）。

### M2 seed 自证（硬失败）

- setup 契约补三条硬要求：
  1. 走**应用内保存路径**（`SaveCommand`）或显式带齐「目标表单保存时会校验的字段」——前端专属必填字段（如本次 4 个）必须显式设置；
  2. setup 末尾自检：断言本 case 每条 UI 预期引用的实体/关联/字段就位，**失败即 `exit(1)`**（不是只 `puts`）；
  3. **反查必填**：从提交被拦的错误文案（如「中文称呼不能为空！」）定位 validator，一次枚举**全部**必填字段（含尚未触发的），写进种子与 `verify.sql`；不要只补报错时冒出来的那一个。
- `verify.sql` 是权威判据，seed 自证是 setup 内的快速失败，二者互补。

### M3 设计期核实循环（宿主编排，自动回灌）

`dev-yard req test <JIRA>`（design 路径）改为：

```
design(agent) → 宿主 verify 全量用例 → 全绿？→ 进审核门
                     ↑                          │
                     └── 失败清单回灌 design ←──┘   （最多 N 次，N=qa.yaml design.verify_attempts，默认 3）
```

- 失败清单回灌 = 调用**内部函数**（`_design_prompt(verify_feedback=...)` + 重做后 `reject_cases`），不是重拼 `--redesign --feedback` 命令行：`_req_test` 里 `--approve` 与 `--redesign/--feedback` 互斥（`qa.py:1160`、`qa.py:1190`），重入 CLI 参数直接会被拒。verify 的失败走独立 channel，避免与人工审核意见（`feedback=`）混为一谈；`reject_cases` 把宿主发现记进 review.yaml，人审可见「这一轮改了什么」。feedback 内容为宿主生成的结构化失败（用例、verify.sql、期望/实际行数、setup stdout 关键段、lint 结果）。
- **跨轮缺口去重**：循环开始前，宿主读该 JIRA 上一轮同 case 的缺口——run 的 `reason: case-defect:`（`evidence/*/result.yaml`）与上一轮 `qa/design-verify/summary.yaml` 里失败的 case（即上轮 `design-blocked`）——作为「历史缺口」一并注入 design prompt。case-02 正是上一轮一字不差重复的缺口，这一步成本最低、直接治复发。
- 达到上限仍失败：该 case 标 `design-blocked`，写入 `qa/design-verify/BLOCKED.md`（可读清单，见 M1 落盘）；正常路径由审核门拦下不进 run，`--allow-unverified` 越权时这些 case 被标 `skipped`（reason `design-blocked:`）。
- **循环结束后的落点统一为「停在审核门等待人审」**：核实的失败不回滚审阅状态。内部调用 `reject_cases` 后 review.yaml 为 `REJECTED`，`review_gate` 返回「等待复审」（`qa_review.py:180`、`qa_review.py:169`），这正是期望态——`design-blocked` 用例照常出现在审核门（见 M5），只是 `--approve` 默认拒绝。即「不进入 run」≠「不展示给人」。
- 设计 agent 在循环内可修改 setup/verify/case 正文；宿主的 verify 是唯一裁判。

### M4 前期参考真实数据（`probe`）

- 约定：design 写 `qa/probe/*.sql`（只读），宿主在 M3 循环内自动执行并把结果回灌给 design，**不新增独立 CLI 命令**（见 §11 决策 4）。
- 更省事的做法：宿主在 M3 第一次 verify 失败后，把该 case 的**相关只读候选查询结果**（如「projects 表里真实存在、且含可测联系人的前几个 id」）随失败清单一并回灌，让 agent 直接选真数据。
- 目的：把 qa-powers「run 期自己找数据」前移到 design 期由宿主代查。

### M4b 提交预检（UI preflight，兜底）

- 对含提交的用例，在审核门之前跑一次无头预检：打开目标页 → 触发提交 → 抓**全部**校验拦截文案（不是逐个试）。
- 有拦截 → 结构化成失败清单回灌 design（同 M3）；无拦截 → 记 `preflight: passed`。
- 用于读代码覆盖不到的情形：动态必填、条件必填、跨字段校验、后端业务校验文案。
- 默认对 `priority: P0`、且 `repo` 为前端仓的提交类用例开启；成本是每条一次浏览器操作，远低于 run 期翻车。
- **依赖关系（必须写清）**：M1/M2 只能从代码反查出**静态必填**；动态必填、条件必填、跨字段校验依赖页面运行时状态，读代码覆盖不到，**P0 阶段存在这块残余缺口**，只有 M4b 能兜。因此 §7 里「case-02 反查出全部必填」在 P0 只承诺静态字段（报错文案点出的 4 个字段即静态），条件/动态部分要等 M4b（P1）。若要把这条缺口也压到 P0，只能把 M4b 提前——但那要求宿主在设计期新增一套 playwright 编排，代价明显高于收益，故维持 P1。
- 预检证据落 `qa/design-verify/<case-id>.preflight.md`（页面快照/截图 + 拦截文案）。

### M5 审核门只审业务

- 审核门（`review_gate`）在展示前先跑 M3；展示信息含：
  - 每条用例 `verified ✓ / design-blocked ✗`；
  - `OPEN-QUESTIONS` 计数（业务歧义）；
  - 未通过核实的结构原因。
- 人在这一步只看业务正确性 + 答歧义；数据已由宿主证明。默认 `design.verify_required: true`：核实未过、或**根本没做核实**（无 summary）都拒绝 `--approve`（可 `--approve --allow-unverified` 显式越权，用于纯 UI 用例；`--no-verify` 等价于放弃该项要求）。「没做核实」不得与「核实通过」同义——这是 gate 严格性的关键。
- 审核门展示 `verify` 摘要（passed/failed/skipped、失败清单、空转豁免清单、是否过期），CLI 与 web 控制台同源。
- **`--approve` 不得批准本次 invocation 里刚被核实循环重做的用例**：若 verify 循环改动了用例指纹，本次 `--approve` 改为返回待审核（理由「数据核实失败已自动重做用例，请复核后重新 --approve」）。人审对象必须是人看过的版本。

### M6 run 期零人工

- run 只调度 `verified` 用例；`design-blocked` 用例直接 skip（reason 指向设计核实失败）。
- run 中若某已核实用例仍出现 `case-defect`（环境漂移：数据被前序用例改脏、或核实后库变动）：
  1. 宿主自动对该 case 触发 M3（redesign+verify，有界 M=2）；
  2. 通过则重排该 case 进当前 run；
  3. 仍失败才标 `blocked`，并在报告里点名「核实-漂移」，交人（这是兜底，不应常见）。
- **范围护栏（审计前提）**：run 期回流**只允许改 `setup` / `verify.sql` / 依赖声明**，不得改 case 正文、预期与断言——否则等于在批准后偷换被测点，打穿 `cases_fingerprint` 与审批语义（`qa_review.py`）。宿主在回流前后各算一次 fingerprint，正文变化即中止回流并标 `blocked: redesign-out-of-scope`。此约束在 M7 的 `run_autofill` 之上，优先级更高。
- 该自动回流默认开启，可用 `--no-auto-redesign` 关闭以做纯取证回归。

### M7 run 期受限自动补填（P2，可选，默认关）

- 场景：已核实用例在 run 期仍被前端校验拦住（漏读/环境漂移）。agent 可定位输入框 → 读组件确认该字段确属必填 → 填安全测试值 → 继续。
- 护栏（必须全部满足才允许补）：
  1. 只补**代码确认必填**、且与本用例被测点无关的字段；
  2. 补之前查 DB/seed，证明确实是「种子没给」而非「产品没预填/没默认值」；
  3. 每处补填在 `result.yaml` 记 `adaptation: filled <field>`，报告单列「用例自适应」。
- 默认关闭；`qa.yaml design.run_autofill: true` 才启用。
- 注意：这会牺牲「种子缺口 vs 产品缺陷」的区分度，故只作最后一道，且必须留痕。

## 5. 流程对比

| | yard 现状 | 本方案 | qa-powers |
|---|---|---|---|
| 数据是否可达 | design 声明，无验证 | 宿主执行 `verify.sql`，有产物 | design 只读查 + run 自愈 |
| 缺口暴露时机 | run 期 → blocked | design 期 → 自动修 | run 期 → 自愈 |
| run 期人参与 | 需人 `--redesign` | 无 | 无 |
| run 期能否改数据 | 否（禁止） | 否（自动回流设计期；P2 可选受限补填） | 是（改 setup 重跑） |
| 审计性 | 强（代码不可变） | 强 + 数据核实留痕 | 弱（当场改脚本） |

## 6. 落点

- `qa.yaml`：`design: { verify_attempts: 3, verify_required: true }`（P0）；`auto_redesign: true` 随 M6 进 P1。
- 用例 frontmatter：`data.verify: <file.sql>`（可选，见 M1 豁免规则）。
- 新增宿主模块 `src/dev_yard/qa_verify.py`：`verify_cases(root, jira, cfg, cases, on_log) -> dict[case, VerifyResult]`，跑 setup/verify/cleanup，产物写 `qa/design-verify/`（逐 case + `summary.yaml` + 失败时 `BLOCKED.md`）；`lint_verify` 校验表名命中；`env_lock` 做 env 级互斥；`write_summary`/`verify_gate`/`verify_view`/`prior_defects`/`render_feedback` 供 `qa.py` 与 `qa_review.py` 复用。M4b 的 `preflight_case(...)` 属 P1。
- `src/dev_yard/qa_exec.py`：新增 `case_script_path`（setup/cleanup/verify 共用的安全路径解析）、`assert_readonly_sql`（单条只读校验，忽略字符串字面量里的写关键字）、`run_sql_count`（`usql -t -A -c` 包 `SELECT count(*)`，非 SELECT 数输出行数）；verify 的 `rows` 由此而来，不解析 stdout。
- `src/dev_yard/qa.py`：`_req_test` 增加 M3 核实循环 `_verify_loop`（内部 `_design_prompt(verify_feedback=...)` + `reject_cases`，非 CLI 参数）；design prompt 注入上一轮 `case-defect:`/`design-blocked` 与 lint 规则；每轮 `write_summary`，失败余留 `write_blocked`；`_mark_design_blocked` 在 `--allow-unverified` 时把失败 case 标 `skipped`；`review_gate`/`verify_gate` 按 `design.verify_required and verify_enabled` 从严。
- `src/dev_yard/qa_review.py`：`review_payload` 增加 `verify` 视图；`review_gate` 透传 `require_verify`/`allow_unverified`。
- `src/dev_yard/qa_schedule.py` / `_req_test`：run 后对 `case-defect` 触发自动回流（M6，P1）；回流前后比对 `cases_fingerprint`，正文变化即中止并标 `blocked: redesign-out-of-scope`。
- `cli.py`：`req test` 打印核实摘要（`--verify-only` 专打印）；新增 `--no-verify`、`--verify-only`、`--allow-unverified`（`--probe`/`--no-auto-redesign` 见 §11 与 P1）。
- 数据核实的可见性由 `review_payload.verify`（CLI + web 控制台共用）承载；`qa_doc.py` 的 run 报告「数据核实」段属 P1（设计期无 run_id，硬并进 run 报告会搅乱两套生命周期）。
- `.pi/skills/qa-design/SKILL.md`：把「只读自检」改成「写 `verify.sql` + seed 自证必须硬失败」；新增「从目标表单组件反查 validator，枚举**全部**静态必填字段（动态/条件必填如实标注、留给预检）」，并写明「`SELECT 1` 需过 lint，不得空转」。
- `.pi/skills/qa-run/SKILL.md`：`case-defect` 说明改为「数据核实已过仍失败=漂移，交宿主回流」；写明 M6 回流只改 setup/verify、不改正文；写明 M7 补填护栏（P2）。

## 7. 验收（直接用 PG-13175 的两个缺口）

- **case-02**：design 从编辑页 validator 反查出**全部静态**必填字段（不止报错冒出的 4 个），写入种子；`verify.sql` 断言 `l_salutation/province_id/city_id/l_address IS NOT NULL`（P0）→ 设计期绿 → run 不再 blocked。动态/条件必填的「无拦截」以 M4b 预检结论为准（P1）。
- **case-06**：`verify.sql` 断言 `projects` 存在该 id 且 `project_firms` 关联到对应 firm（或经 probe 选真项目）→ 设计期失败 → agent 修 → 绿。
- **复发**：跨轮去重生效——case-02 的历史 `case-defect:` 被注入本轮 design prompt，缺口不再原样重复。
- **回归**：`case-0*.md` 不得再出现无 verify.sql 的数据断言；`qa/design-verify/*.yaml` 全覆盖；`verify.sql` 用 `SELECT 1` 的 case 全部过 lint 并标「空转豁免」；P0 提交类用例的 preflight 结论属 P1 验收项。
- **指标**：run 轮 `case-defect` 目标 0（漂移除外）；`blocked` 只剩 env/环境类。

## 8. 分期

| 阶段 | 内容 | 价值 | 本阶段挂账 |
|---|---|---|---|
| P0 | M1 + M2 + M3 + M5（`verify.sql`、seed 硬自证、设计期核实循环、审核门拦未核实） | 直接消灭这两类缺口 | 动态/条件必填未覆盖（等 M4b）；run 期漂移仍会 blocked（等 M6） |
| P1 | M4（probe 查真数据回灌）+ M4b（提交预检兜底）+ M6（run 期 case-defect 自动回流） | 进一步去人工、抗漂移、抗漏读 | 需在设计期新增宿主 playwright 编排（M4b 的前置成本） |
| P2 | M7 run 期「受限自动补填」（默认关，见 M7 护栏） | 兜漏读/漂移，贴近 qa-powers；牺牲部分审计性 | 无 |

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| design 期跑 setup 会写测试库（多一遍） | seed 已要求幂等 + cleanup 对称；核实后 cleanup，run 再幂等重建；核实循环持 `.yard-qa/locks/<env>.verify.lock` 与 run/schedule 同 env 互斥 |
| `verify.sql` 写空转（`SELECT 1`） | 宿主 lint：`FROM`/`JOIN` 表名须命中 case 正文，不过即判 failed；空转 case 在审核门标「空转豁免」供抽查 |
| 远程 env 核实慢（逐 case JMS 执行） | 批量 `.sql` 可合并；setup 已并行调度；必要时 `verify` 与 `setup` 复用一次执行 |
| 核实通过后数据被前序用例改脏 | M6 漂移自动回流 + `depends_on` 串行化共享数据 |
| M6 回流偷换被测点 | 回流只许改 setup/verify/依赖，正文改动即中止并标 `blocked: redesign-out-of-scope`（前后各算 fingerprint） |
| agent 改 verify.sql 去「通过」 | verify 由宿主执行且只读；run 的断言独立于 verify（verify 只证明前置，不替代断言） |
| M7 自动补填把「产品没预填」洗白成 passed | 补前查 DB/seed 证明属种子缺口；只补与本用例被测点无关字段；每处留痕、报告单列；默认关 |
| M4b 预检过重拖慢设计 | 仅 P0 前端提交类开启；复用会话批量跑；可 `--no-preflight` 关 |

## 10. 非目标

- 不自动通过业务审核，不取消 `--approve`。
- 不让 run 改产品代码/预期。
- 不引入交互式 design（仍是 `-p`，澄清走 OPEN-QUESTIONS + 自动回灌）。
- 不改变四态机、exec 路由、模型池调度。

## 11. 已决与待定

已定（本版落案）：

1. `verify.sql` 语义止步于「≥1 行即通过」；精确计数/字段值不作扩展——它只证明前置存在，不替代 run 的断言。确需精确断言时走 M4b/run 层，不在 verify 层加 `expect:` DSL（省一套解析器与一类新错法）。
2. 核实产物落 `qa/design-verify/`。`evidence/` 以 run_id 为轴，设计期尚无 run_id，硬塞会把两套生命周期搅在一起。
3. M7 默认关。消灭 run 期数据类 blocked 的目标由 M6（自动回流，带范围护栏）承担，不必以审计性为代价。
4. `--probe` 走设计循环内 `qa/probe/*.sql` 自动执行回灌，不新增独立命令——独立命令等于把「宿主代查」又变回人驱动的往返。
5. 核实循环与 run 的锁粒度：用 env 级锁 `.yard-qa/locks/<env>.verify.lock`（与 `_run_lock` 同款实现）。同 env 一把锁即可——核实要写共享库，串行是必要的，不同 repo 的并行 case 本就不能与核实同时跑。
6. `verify_required` 与「未核实」：gate 从严，无 summary 也拒绝 `--approve`（`--no-verify` 才放弃要求），避免「没做」被当成「通过」。回滚旧流程用 `--no-verify`。

待定：

1. M6 回流产生的新种子/verify 是否需要人在下一次审核门复核（默认建议：免复核，但报告单列「漂移-回流」变更，供事后抽查）。
