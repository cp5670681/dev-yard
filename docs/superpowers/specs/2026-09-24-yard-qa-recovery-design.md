# yard-qa 可恢复执行环：单一状态机 + 故障自愈，人工只在审核与失败判定出现

- 日期：2026-09-24
- 状态：方案 v3（**P0 + P1/P2 已实现**，见 §13；仅 M3 池预检默认关、M1 门禁读取路径未改，均为有意取舍）
- 触发：
  - 复盘 [`docs/2026-09-23-yard-qa-retest-incident.md`](../../2026-09-23-yard-qa-retest-incident.md)：一次「用例自身错误」被平台放大成产品缺陷，且重测被整轮闸门锁死。
  - 使用反馈：自动测「流程不标准、容易卡住、走不通；中间因模型等问题中断后不好恢复」。
  - 基线变更：`a70e93a` 把 run 失败从「自动拆 B 票」改为「人工在失败/阻塞用例上下 bug」（`accept_test_report(spawn=False)` + `spawn_manual_fix_ticket`）。本方案据此把「失败判定」列为**唯一的设计后人工点**，并以自动分流把它压到最小。
- 前序：[`2026-09-16-yard-qa-workflow.md`](2026-09-16-yard-qa-workflow.md)（已落地：四态、池×DAG、证据、审核门）、[`2026-09-21-yard-qa-verified-design.md`](2026-09-21-yard-qa-verified-design.md)（设计期数据核实，本方案的 M5/M6 与其衔接）。
- 约束（冻结）：不改四态机 `passed|failed|blocked|skipped`；不取消人工 `--approve` 与人工下 bug；不引入交互式 design；不改 exec 路由、池×DAG 调度内核、票循环；测试 agent 只测不修。

---

## 1. 目标与非目标

### 目标

1. **流程标准**：一台显式、持久的状态机是唯一权威；CLI、Web、重启恢复都只驱动它，门禁不再各读各的分散文件。
2. **人工最小化、且集中在两处**：一次「设计→核实→修订」全自动；人只在 **(a) 审核门 approve 一次**、**(b) 对真正是产品缺陷的失败点一次「下 bug」**。其余（env/模型故障重试、`case-defect` 自动回流、B 票修完增量重跑、中断恢复）全自动；`case-defect` 由宿主自动分流，**不进入人工下 bug 列表**。
3. **可恢复**：任何中断（模型 `pi exit`、DB 抖动、web 重启、进程被杀、坏 pool）都能从状态机断点续跑：**不丢已过用例、不整轮全量重跑、不把环境故障当终态**。

### 非目标

- 不改四态语义与 `result.yaml` 契约（`passed|failed|blocked|skipped` 仍冻结）。
- 不做「同一 case × 每个模型」矩阵跑法。
- 不取消人工审核，不自动通过业务判断。
- 不让 run 改产品代码或用例预期（自动修复只改 setup/verify/依赖，见 M6）。
- 不新增除现有 exec 配方之外的执行器。

---

## 2. 根因（为什么现在必然卡）

| # | 环节 | 现状 | 后果 | 证据 |
|---|---|---|---|---|
| R1 | 状态权威 | 状态散在 `STATUS.yaml`(phase/票)、`qa/review.yaml`(审核)、`qa/design-verify/summary.yaml`(核实)、`qa/evidence/<run>/progress.yaml`(run)、**内存 `Job`** 五处 | 门禁各读各的，任一处过期/不一致就卡；web 重启丢 job | `qa.py:418`、`qa_review.py:57`、`qa_verify.py:509`、`web/jobs.py:86` |
| R2 | 恢复语义 | 终态含 `blocked`（`TERMINAL`）；`_run_incomplete` 只认 `pending/ready/running` | **模型/环境故障落成 `blocked`=终态 → 整轮判「已完成」→ 只能新开一轮全量重跑**；续跑只在进程被硬杀、case 停在 running 时生效 | `qa_schedule.py:13`、`qa.py:1109` |
| R3 | design 幂等 | `need_design = ... (redesign or not cases)` | 只要目录里有**任意** `case-*.md` 就当设计完成；design pi 中途崩溃留下 3/5 条用例 → 半成品直接进 verify/run，缺的覆盖只靠 `uncovered_changes` 日志提示 | `qa.py:1605`、`qa.py:1713` |
| R4 | 故障分类 | `verify_case` 把 `run_sql_count` 的 `TestRejected`（usql 缺失、DB 连不上、`db.url` 未配）一律记 `failed` | 一次 DB 抖动被当数据缺口，回灌 design 重做 3 次，最后 `design-blocked` 交人工 | `qa_verify.py:280-286`、`qa.py:842-910` |
| R5 | 人工点 | `_verify_loop` 每轮 `reject_cases`；run 期 `case-defect` 无自动回流 | 自动修复也置 `rejected`，人必须再 approve；run 期漂移只能人 `--redesign` | `qa.py:877/909`、`qa_review.py:194` |
| R6 | 并发/锁 | `env_lock` 同 env 跨需求**直接拒绝**（非排队）；web job 层对不同 jira 放行 | 两个需求同时点执行，第二个 job 必 `error`；用户看到「走不通」 | `qa_verify.py:421`、`web/jobs.py:1072`、`qa.py:2153` |
| R7 | 重启恢复 | `resume_pending_grills` 只恢复 grill | web 重启后 QA run 无入口，用户不知「跑到哪、能否继续」 | `web/jobs.py:958` |
| R8 | 契约脆弱 | result.yaml 引号、db 断言 `expected` 标量、`repo`/`account` 必填，任一违反整条 case 变 `blocked` | 失败点全在 run 时才炸，代价是一整轮；只能靠 prompt 约束 | `qa.py:820-828`、`qa_exec.py:591-633` |
| R9 | 预检缺失 | `executor.ping()` 只验脚本通道，`qa check-env` 不验模型池 | 坏 pool 要跑到 `pi exit 1` 才暴露，诊断事后 | `qa.py:1946`、`qa_exec.py:424` |
| R10 | 失败分流 | `a70e93a` 后 run 失败只落报告，人工逐条「下 bug」（`spawn=False` + `spawn_manual_fix_ticket`）；`defect_class` 已算出但只进报告，不驱动任何自动动作 | 每个失败/阻塞点都要人判断一次；`case-defect` 也混在待判定列表里，人工点无法随失败数下降 | `test_report.py:306`、`qa_report.py:116`、`service.py:1516`、`bug_tickets.py:424` |

> 已修/已变更（不再重复设计）：incident P0-1（单条重测绕票门 `qa.py:1561`）、P0-2（前端等 job 终态 `RequirementView.vue:1456`）、P1-4（per-pool 熔断 `qa_schedule.py:339-419`）、P1-6（prose 复核不降级 `qa_exec.py:483`）。**P0-3 已由 `a70e93a` 以另一条路线解决**：失败不再自动拆票，闸门死锁（R5 的一部分）随之消失；代价是新增了 R10 的人工判定点，由本方案 M6b 压小。本方案处理 R1–R10。

---

## 3. 设计原则

1. **单一真相**：`qa/state.yaml` 是唯一权威；`review.yaml` / `design-verify/` / `evidence/` 降级为「证据」，门禁只通过状态机读取它们，不再各自判定。
2. **终态二分**：真终态 = `passed` / `failed` / `blocked(case-defect)`；可重试 = `blocked(env|auth|undeployed|worker-exit)`。可重试项在轮内回队列，不在轮间被当终态。`defect_class` 只用于**分流辅助**，不再决定是否自动建票（建票已是人工动作）。
3. **自愈优先、人审兜底**：能用自动重试/回流解决的，不产生人工待办。人工只剩两处：业务判断（approve）与产品缺陷判定（下 bug）。`case-defect` / 环境类失败必须被**自动分流**掉，不混进人工下 bug 列表。
4. **幂等 + 断点**：每个阶段可重复执行、可从中断处继续；阶段产物带 `generation`/`fingerprint` 标记，**无标记或指纹不符 = 未完成**。
5. **审计不变**：run 期不改产品代码/预期；一切自动修复留痕，可在审核门与报告复查。

---

## 4. 状态机（M1 的骨架）

### 4.1 状态与转移

```text
idle ──design ok──► designed ──verify──► verifying
                                          │  ├─ all pass ─────────────► awaiting_review
                                          │  ├─ case gaps (<N) ──► verifying（自动回流 design）
                                          │  └─ env error ────────► verifying（退避重试，不计 case 缺口）
awaiting_review ──approve(人)──► approved ──run──► running
awaiting_review ──feedback(人)──► designed
running ──┬─ all passed ──────────► ingested(passed) ──► closed (phase=done)
          ├─ failed/blocked ──────► ingested(failed) ──► awaiting_triage
          ├─ env blocked ─────────► running（轮内重试 ≤N；坏 pool 隔离后改派）
          ├─ case-defect ─────────► designed（自动回流，护栏见 M6）
          └─ 中断 ────────────────► running（从 progress.yaml 续跑）
awaiting_triage ─┬─ 人工下 bug ────► recycled（建 1 张 source=test 票）
                 ├─ 自动分流 case ─► designed（M6b，不建票）
                 └─ 误报/暂不建票 ─► closed（保持 awaiting，可随时补下 bug）
recycled ── B 票 done + submit-test ─► running（增量重跑，M10）
any ──需求文档变更──► awaiting_review（mark_stale）
```

| from | 事件 | to | 动作 / 守卫 |
|---|---|---|---|
| idle | design pi ok | designed | 宿主写 `design.yaml` 标记（M4） |
| designed | 进入核实 | verifying | |
| verifying | 全部 pass | awaiting_review | `verify_required` 生效 |
| verifying | case 缺口 < `verify_attempts` | verifying | 自动回流 design（machine-fixed，M6） |
| verifying | env 错误 | verifying | 退避重试，**不计入 design 回流次数**（M5） |
| verifying | case 缺口用尽 | awaiting_review | 失败项列 `design-blocked`，人可见 |
| awaiting_review | approve（人） | approved | 绑 `cases_fingerprint` |
| approved | run | running | 获取 env 锁（M8） |
| running | 全 pass | ingested(passed) → closed | `accept_test_report(passed)` |
| running | failed/blocked | ingested(failed) → awaiting_triage | 只落报告，**不自动建票**（`spawn=False`）；自动分流见 M6b |
| running | env blocked | running | 回 `ready` + attempt++，≤`run.retry_attempts`；池隔离改派（M3） |
| running | case-defect blocked | designed | 自动回流（只改 setup/verify，M6） |
| running | 进程/web 重启 | running | `resume_pending_qa` 或页面「继续」（M9） |
| awaiting_triage | 人工下 bug | recycled | `spawn_manual_fix_ticket`（`service.py:1516`）；幂等、只拆该条 |
| awaiting_triage | 自动分流 case-defect | designed | M6b：`defect_class=case` 直接回流，不进人工列表 |
| awaiting_triage | 误报 / 暂不建票 | closed | 不建票，`state.yaml` 保持 `awaiting_triage` 供随时补建 |
| recycled | B 票 done + submit-test | running | 默认增量重跑 failed/blocked（M10） |

### 4.2 `qa/state.yaml`（新，权威）

```yaml
version: 1
jira: PG-13218
env: test
generation: 2026-09-24T10:00:00Z#d3f2     # design 代次，M4
cases_fingerprint: 9a1c…                  # 复用 cases_fingerprint（qa_review.py:57）
phase: awaiting_triage                    # 见 §4.1
review:
  status: approved                        # awaiting|approved|rejected
  approved_fingerprint: 9a1c…             # 人 approve 时的指纹（须等于 cases_fingerprint）
  machine_fixed: false                    # 自动修复产生的新代次（M6）
last_run_id: 2026-09-24-100000
last_verdict: failed
triage:                                   # 失败判定（M6b）
  pending: [case-01, case-08]             # 待人工判断（failed 且非 case-defect）
  auto_recycled: [case-03]                # 已自动回流 design 的 case-defect
  filed: { case-01: B1 }                  # 已下 bug 的 case → 票号（幂等）
attempts: { design: 1, verify: 2, run: 1 }
retry: { run: { count: 0, next_at: null } }
pools:                                    # M3
  MiniMax-M3: { state: healthy }
  deepseek-flash: { state: quarantined, reason: "pi exit 1; 疑似额度", at: "…" }
updated_at: 2026-09-24T10:05:00Z
```

**兼容**：状态机是**索引与权威**，不替换证据文件。`STATUS.yaml.phase` 仍由 `submit_test`/`accept_test_report` 维护（不变量）；`review.yaml`、`design-verify/summary.yaml`、`progress.yaml` 继续写，但门禁改为「先读 state.yaml，再校验对应证据指纹」。

---

## 5. 机制

### M1 单一状态机（`src/dev_yard/qa_state.py`，新）

- 提供 `load/save/transition(root, jira, event, **data)`，所有转移写 `state.yaml` 并返回新状态；非法转移抛 `TestRejected`。
- `_req_test` 改为「解析意图 → 推进状态机」：`design_only` 推进到 `awaiting_review` 即返回；`run_only` 要求 `approved`；`rerun_cases` 是 `running` 内的诊断转移，不受 `recycled` 的票门约束。
- `failed/blocked` run 落 `awaiting_triage`：这是**设计后唯一需要人的状态**，但只有 `defect_class != case` 的项进入 `triage.pending`；`case-defect` 由 M6b 自动转 `designed`。人工「下 bug」是 `awaiting_triage → recycled` 的显式转移（幂等，`filed` 记票号）。
- CLI `dev-yard qa status <JIRA>` 与 web 需求页读同一份 `state.yaml`，输出「当前态 + 下一步可执行动作 + 待判定失败项 + 阻塞原因」，作为统一观测入口。
- 保留现有 `_run_lock`（进程内）+ `env_lock`（跨进程）作为**执行互斥**，状态机负责**语义推进**，两者职责分离。

### M2 可重试终态与轮内自动重试

- 调度层把 `blocked` 再分：`blocked_kind(reason, blocked_class)` 已区分 `env|case-defect|cancelled|other`（`qa_schedule.py:189`）。
  - `env`（含 `auth`/`undeployed`/`worker-exit`）→ **回队列**：`state=ready`、`attempt++`、写 `retry.next_at`（指数退避）；达 `run.retry_attempts`（`qa.yaml`，默认 2）才落终态 `blocked`。
  - `case-defect` → 触发 M6 自动回流（run 期走 M6b），不改产品、不建票。
  - `other`/`cancelled` → 终态，交人。
- `progress.yaml` 仍只暴露四态给看板；重试计数放 `progress.cases[].attempts`，不污染 `result.yaml`。
- **中断恢复**因此自然成立：进程被杀时重试项停在 `ready`，`_run_incomplete`（`qa.py:1109`）判定未完成 → 自动 resume。
- 熔断（per-pool `tripped`）改为「**隔离池**」：坏池退出派发，其 `ready` case 改派健康池；**只有所有池都被隔离**才把剩余标 `blocked`，且理由明确写「全部模型池不可用」。

### M3 池健康预检与隔离

- 跑前对每个 pool 做一次轻量冒烟：`pi -p` 跑一句固定 prompt（或 provider 的 model-list 探测），失败即 `pools[id].state=quarantined`，日志给出 `diagnose_pi_exit` 的可行动结论（鉴权/额度/上下文）。
- 冒烟成本可控：`qa.yaml run.pool_preflight: true`（默认 true，可关）；失败不阻塞整轮，只把该池从本次可用集移除。
- 运行中首条 `worker exit` 也立即隔离该池（不等连续 2 次），健康池继续领队列。

### M4 design 原子完成标记

- **宿主写标记，不信任 agent**：design pi `result.ok` 后，宿主 `discover_cases()` 计算指纹，写 `qa/design.yaml`（`generation`、`cases` 计数与 id 列表、`fingerprint`、`completed_at`）。
- `need_design` 改为：无 `design.yaml`、或标记指纹 ≠ 当前 `cases_fingerprint`、或 `cases` 为空、或 `--redesign`。
- design pi 失败（`result.ok=False`）→ 不写标记 → 下次必重跑 design；半成品永不被当成成品。
- 与 `_verify_loop` 的回流衔接：回流成功后再写一次 `design.yaml`（新 `generation`），`cases_fingerprint` 随之一致。

### M5 故障分类前置（核实期 env ≠ case-defect）

- `VerifyResult.status` 增加 `blocked`；`verify_case` 捕获执行层 `ExecUnreachable` / `ExecErrorClass`（`unreachable|auth|pod_not_found|timeout|runner|config`）与「`db.url`/usql 缺失」→ `status=blocked`、`blocked_class=env`，**不**计入 design 回流。
- `_verify_loop` 只把 `failed`（真数据缺口）回灌 design；`blocked` 触发一次带退避的重试（`verify.retry_attempts`），仍失败则整轮停到 `awaiting_review` 并显式提示「环境不可用，非用例缺陷」，不再烧 3 轮 pi。
- `verify_gate` / `failed_cases` / `describe` / `render_feedback` 同步区分 `blocked` 与 `failed`（`blocked` 不阻止 `--approve` 的业务判断，但阻止进 run，可 `--allow-unverified` 越权跳过）。

### M6 自动修复不触发人工复审（护栏）

- 新增 `qa_review.machine_fixed(qa, feedback)`：记录自动修复反馈，`status=awaiting`、`machine_fixed=true`，**不写 `rejected`**；`review_payload` 的 `approved` 判定在「仅 setup/verify/依赖变化、正文与断言未变」时沿用原 `approved_fingerprint`，即免人工复审直接续跑。
- 自动修复的**范围护栏**（沿用 09-21 方案 M6）：回流前后各算一次 `cases_fingerprint`，只允许 `setup.*`/`verify.*`/`data.writes`/`data.identity`/`depends_on` 变化；**case 正文、`## 预期`、断言变化 → 中止自动修复，落 `awaiting_review` 交人**。
- 两处接入：`_verify_loop`（设计期，`qa.py:842`）与 run 期 `case-defect` 回流（M6b，`default_case_runner` 判定后触发，有界 `M=2`）。
- 效果：设计→核实→修订全自动；run 期漂移自动修种子；**设计期不再需要额外 approve**，除非被测点本身要改。

### M6b 失败自动分流，把「下 bug」压到最小

`a70e93a` 后失败不再自动建票，人要在失败/阻塞用例上「下 bug」。若不分流，`case-defect`、环境类、模型类失败都会混进待判定列表，人工点随失败数线性增长——与「后期基本不用人工」直接冲突。故在 `awaiting_triage` 前先做宿主自动分流（复用已有的 `classify_defect` / `blocked_kind`，`qa_report.py:14`、`qa_schedule.py:189`）：

| 用例状态 / 分类 | 宿主动作 | 是否进人工下 bug 列表 |
|---|---|---|
| `blocked` 且 `blocked_class ∈ {env, auth, undeployed}` 或 `worker-exit` | 已是可重试终态（M2/M3）；重试仍败则标「环境不可用」 | 否 |
| `blocked` 且 `reason` 以 `case-defect:` 开头 | 自动回流 design（M6 护栏），转 `designed` | 否 |
| `failed` 且 `defect_class = case` | 同上，自动回流 design | 否 |
| `failed` 且 `defect_class = product` | 进 `triage.pending`，附上已有证据（断言、截图、`reason`） | **是**（一键下 bug） |
| `failed` 且 `defect_class` 缺省 / `unclassified` | 进 `triage.pending`，标「待判定」 | **是**（但明确标注需人判断） |

- 目的：**人工下 bug 列表里只剩「可能是产品缺陷」的项**；`case-defect` 与基础设施失败全部自动消化。
- 列表级操作：web 支持批量「下 bug 全部 product 项」（逐条仍走幂等的 `spawn_manual_fix_ticket`），CLI 提供 `dev-yard req triage <JIRA> --product` 一次性为 `defect_class=product` 的项建票。
- 与 `_gate` 的关系：建票后 `all_done` 才会因 B 票未完成而挡重测；不建票的失败项**不锁**重测/redesign——incident 的死锁结构上不再存在。
- 兜底：`defect_class` 误判（把产品缺陷标成 `case`）由人工在报告/看板覆盖为 `product` 后下 bug；误判方向偏向「少自动回流」而非「多自动建票」。

### M7 用例静态契约校验（design 后、verify 前）

- 新增 `lint_cases(root, jira, cfg) -> list[Problem]`，在 design 后立即跑，问题回灌 design（与 verify 失败同通路）：
  - frontmatter 必填（`id`/`title`/`repo`）；`repo` ∈ `repos.yaml` 别名；`account` ∈ 本次配置账号（复用 `_check_case_repos`/`_check_case_accounts` 的规则，前移）。
  - `data.verify` 存在性规则复用 `needs_verify`（`qa_verify.py:170`）。
  - `data.writes` 必须配套 `data.identity`（`_bind_identity` 的规则前移）。
  - `covers` 的 D 必须都在 `meta.yaml changes`（把 `uncovered_changes` 从「日志提示」升为「approve 门禁项」）。
- **运行期契约容错**（降低 R8）：`_read_case_result`（`qa.py:952`）解析失败时，改用 `bug_tickets._load_yaml_loose` 同款「逐行回退解析」，并做标量引号修复（把裸 `: ` 的自由文本字段重新加引号）；仅当关键字段（`status`/`assertions`）仍缺才判 `blocked`。目标：一个裸冒号不再让整份 result.yaml 作废。

### M8 同 env 排队而非拒绝

- `env_lock`（`qa_verify.py:385`）改为**带超时的等待获取**：拿不到锁时按间隔轮询持有者 PID，直到释放或超时（`qa.yaml run.env_wait_timeout`，默认 1800s）；期间回调 `on_log`/`on_progress` 输出「等待其它需求释放环境 <env>」。
- web job 层（`web/jobs.py`）对同 env 的 `qa-run`/`qa-design` 允许排队：job 状态置 `waiting` 并带提示，而不是提交即 `error`。`_jobs_conflict`（`jobs.py:1065`）增加 env 维度判断。
- CLI 保留快速失败：默认等待，`--no-wait` 时立即拒绝（脚本化场景）。

### M9 web 重启恢复 QA run

- `JobRunner.__init__` 增加 `resume_pending_qa()`（对齐 `resume_pending_grills`，`jobs.py:958`）：扫 `reqs/*/qa/state.yaml`，对 `phase=running` 且 `progress.yaml` 仍有非终态 case 的 run：
  - `qa.yaml run.resume_on_restart`（默认 true）→ 自动重建 `qa-run` resume job；
  - 否则在需求页/看板列出「可继续的 run」卡片，一键继续。
- 自动 resume 只针对**同一 workspace、未被其它 job 占用**的 run；启动日志明确写出恢复了哪些 run。
- 与 `_run_lock` 配合：重启后旧 PID 已死，锁自动回收（`qa.py:510`），恢复不会被旧锁挡住。

### M10 增量重跑（B 票修完默认不全量）

- 状态机 `recycled` → `running` 时：读上一 run 的 `progress.yaml`，默认只 `reset_cases_in_run`（`qa.py:1403`）重置 `failed` + `blocked` + 其 `depends_on` 后继，以及 `covers` 命中本次 diff 的 case；其余 `passed` 保持，续用同一 `run_id`。
- 触发条件：`cases_fingerprint` 未变、`env` 未变、契约未变；否则退回全量（安全）。
- CLI：默认增量；`--fresh` 强制新 run 全量（已有语义），新增 `--full` 显式全量。
- 这是「B 票修完重测」的主路径，把整轮全量重跑降为只跑受影响用例。

---

## 6. 流程对比

| 环节 | 现状 | 本方案 |
|---|---|---|
| 状态权威 | 5 处分散，门禁各读各的 | `qa/state.yaml` 唯一权威 + 证据文件 |
| 模型/环境故障 | 落 `blocked`=终态，整轮判完成 | 回队列重试 ≤N，坏池隔离改派，中断可续跑 |
| design 半成品 | 有任意 case 就当完成 | 无 `design.yaml` 标记不得进 verify/run |
| 核实期 DB 抖动 | 当数据缺口，烧 3 轮 pi + 人工 | 判 `blocked(env)`，退避重试，不烧 pi |
| 自动修复 | 写 `rejected`，人必须再 approve | 仅 setup/verify 变化免复审；正文变化才交人 |
| 同 env 跨需求 | 第二个必 `error` | 排队等待，状态 `waiting` |
| web 重启 | QA run 丢失 | 自动/一键恢复未完成 run |
| B 票修完重测 | 整轮全量重跑 | 只重跑 failed/blocked + 受影响 case |
| 失败判定 | 每条 failed/blocked 都混在待下 bug 列表，人逐条判断 | `case-defect`/环境类自动分流；人工列表只剩 `product`/`unclassified`，可批量下 bug |
| 人工点 | approve + 反复复审 + 手动重测 + 手动诊断 + 逐条判失败 | **审核 approve 一次 + 失败判定一次（可批量，且只对疑似产品缺陷）** |

---

## 7. 落点

| 文件 | 改动 |
|---|---|
| `src/dev_yard/qa_state.py`（新） | 状态机 load/save/transition、`awaiting_triage`/`recycled` 转移、`qa status` 数据源 |
| `src/dev_yard/qa.py` | `_req_test` 改为推进状态机；`need_design` 认 `design.yaml`（M4）；`_verify_loop` 接 M5/M6；`_read_case_result` 容错（M7）；run 后 M6b 分流并写 `triage`（`spawn=False` 不变）；增量重跑入口（M10） |
| `src/dev_yard/qa_report.py` | 新增 `triage_buckets(run, cases)`（复用 `classify_defect`）；`finding_from_case`（`a70e93a` 已有）供人工下 bug |
| `src/dev_yard/qa_schedule.py` | 可重试终态回队列（M2）；池隔离改派（M3） |
| `src/dev_yard/qa_verify.py` | `VerifyResult.status += blocked`；`verify_case` 故障分类（M5）；`env_lock` 排队获取（M8） |
| `src/dev_yard/qa_review.py` | `machine_fixed`；`review_payload.approved` 支持「仅 setup/verify 变化」沿用指纹（M6） |
| `src/dev_yard/qa_config.py` | `run: {retry_attempts, env_wait_timeout, resume_on_restart, pool_preflight}`；`verify.retry_attempts` |
| `src/dev_yard/qa_exec.py` | `diagnose_pi_exit` 供预检复用（M3） |
| `src/dev_yard/service.py` | `ticket_from_qa_case`（`a70e93a` 已有）接 `awaiting_triage → recycled` 状态转移 |
| `src/dev_yard/test_report.py` | `accept_test_report(spawn=False)` 已落地（`a70e93a`）；状态机读其 verdict 决定 `closed`/`awaiting_triage` |
| `src/dev_yard/bug_tickets.py` | `spawn_manual_fix_ticket`（`a70e93a` 已有）；批量下 bug 复用 `only_ids` |
| `src/dev_yard/web/jobs.py` | 同 env 排队（M8）；`resume_pending_qa`（M9） |
| `src/dev_yard/cli.py` | `qa status`；`req triage <JIRA> --product`；`--no-wait`/`--full`；`req test` 输出状态机当前态与下一步 |
| `src/dev_yard/qa_board.py` / `web/src/views/QaView.vue` | 展示状态机态、待判定失败项与批量下 bug、重试/隔离池、可恢复 run、下一步动作 |
| `.pi/skills/qa-design/SKILL.md` | 说明 `design.yaml` 由宿主写；正文/断言改动会要求人工复审（M6 护栏） |
| `.pi/skills/qa-run/SKILL.md` | `case-defect` 说明改为「交宿主自动回流，不改预期」；result.yaml 容错不再鼓励裸冒号 |

---

## 8. 验收（用 incident 的 PG-13218 场景回归）

- **中断恢复**：跑 run 时 `kill -9` web 进程 → 重启 → 自动 resume，`passed` 用例不重跑，仅重跑未完成/可重试项；`state.yaml.phase=running` 可读。
- **坏池隔离**：配一个必失败的 pool + 一个健康 pool → 坏池被隔离并给出可行动原因，健康池跑完全部 ready case；不再「整轮剩 9 条 blocked」。
- **DB 抖动**：verify 期断开 `db.url` → 判 `blocked(env)`，不产生 design 回流、不写 `design-blocked`、不拆票。
- **半成品设计**：design pi 中途杀 → 无 `design.yaml` → 下次必重跑 design，不进入 run。
- **自动修复免复审**：run 期 `case-defect`（仅 setup 改动）→ 自动回流后免人工续跑；若改动触及 case 正文 → 落 `awaiting_review` 交人。
- **失败自动分流（M6b）**：造一轮同时含 `case-defect` + env blocked + product failed → `case-defect` 自动回流、不进 `triage.pending`；env 类自动重试；待下 bug 列表**只剩** product/unclassified 项；批量下 bug 只为 product 项建票；未建票的失败项不锁重测/redesign。
- **人工下 bug 幂等**：同一 case 重复「下 bug」→ 只建一张票、返回同票号（`spawn_manual_fix_ticket` + `state.triage.filed`），且只拆点击那条（不带走报告里其它 finding）。
- **同 env 排队**：两个需求同时执行 → 第二个 `waiting` 后成功，不再 `error`。
- **增量重跑**：B 票修完 `submit-test` 后重测 → 只跑 failed/blocked + 受影响 case，`passed` 保持。
- **契约容错**：故意在 `reason` 写裸 `case-defect: x` → result.yaml 仍可解析，按 `case-defect` 分类，不再整条 blocked。
- **回归**：现有 `tests/test_qa.py` / `test_qa_flow_fixes.py` / `test_qa_verify.py` / `test_qa_web.py` / `test_hardening.py` 全绿（含 `a70e93a` 新增的下 bug 端点用例）；新增状态机转移、可重试终态、`machine_fixed`、`triage_buckets`、批量下 bug、`lint_cases`、env 排队、`resume_pending_qa` 的单测。

---

## 9. 分期

| 阶段 | 内容 | 价值 | 挂账 |
|---|---|---|---|
| P0 | M1 + M2 + M4 + M5 | 流程标准化 + 中断可恢复 + 不再把环境故障当用例缺陷 | 池预检/隔离（M3）暂靠首失败隔离；失败分流（M6b）暂全进人工列表 |
| P1 | M3 + M6 + M6b + M8 + M9 | 去人工（自动修复免复审 + 失败自动分流，下 bug 只对疑似产品缺陷）、坏池自愈、同 env 排队、重启恢复 | 增量重跑（M10）暂全量 |
| P2 | M7 + M10 + 看板/`qa status` 完整化 | 契约前移、重测提速、观测统一 | — |

建议按此顺序，每期独立可回归；P0 先解「卡死与不可恢复」，P1 解「多余人工」，P2 解「效率与体验」。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 状态机与现有五处状态不同步 | 状态机只做**索引/权威判定**，证据文件继续写；每次转移校验证据指纹，不一致即报 `TestRejected` 而非静默 |
| 自动重试掩盖真环境故障 | 退避 + 次数上限；`blocked_class=env` 终态明确写「环境不可用」；报告单列重试次数 |
| `machine_fixed` 免复审被滥用（偷换被测点） | 范围护栏：正文/预期/断言变化一律落 `awaiting_review`；前后指纹比对，越界即中止 |
| 自动 resume 在启动时意外拉起浏览器 | `resume_on_restart` 可关；默认只恢复「进程被中断」的 run，不恢复已终态 run；启动日志明示 |
| env 排队导致长等待 | 超时（默认 30min）+ 状态可见 + `--no-wait` 快速失败 |
| 池预检增加启动成本 | 轻量冒烟 + 可关；只在池首次使用时探测并缓存结果 |
| 增量重跑漏跑受影响用例 | 触发条件严格（指纹/env/契约未变）；命中 `covers`/依赖的 case 必重跑；`--full` 兜底 |
| M6b 把真产品缺陷误判为 `case-defect` 而自动回流，漏建产品票 | 误判方向偏「少自动回流」：仅 `defect_class=case` 或 `reason` 以 `case-defect:` 开头才回流；`unclassified` 一律进人工列表；人工可在报告/看板把 `case` 覆盖为 `product` 后补下 bug |
| 人工「下 bug」列表仍偏长 | 批量下 bug + `req triage --product`；`case-defect`/env 类不进列表；列表项附断言与截图，降低单条判断成本 |

---

## 11. 非目标（重申）

- 不改四态语义、`result.yaml` 契约、票循环、exec 路由、池×DAG 调度内核。
- 不自动通过业务审核；不改产品代码或用例预期。
- 不引入交互式 design。
- 不做矩阵跑法、不新增执行器。

---

## 12. 已决与待定

已定：

1. 状态权威落 `qa/state.yaml`，证据文件保留为「证据」而非门禁来源。
2. 可重试终态在**轮内回队列**（`ready`+attempt），不新增第五种对外状态。
3. `design.yaml` 由**宿主**在 design pi 成功后写，不信任 agent 自报。
4. 自动修复免复审以「只改 setup/verify/依赖」为硬护栏，正文变化必交人。
5. 同 env 默认排队，CLI `--no-wait` 可选快速失败。
6. 重启恢复默认开启自动 resume，可用 `run.resume_on_restart: false` 关闭。
7. B 票修完默认增量重跑，`--full` 强制全量。
8. 保留 `a70e93a` 的人工下 bug 语义：**建票必须由人点**；本方案只在它前面加 M6b 自动分流，不改「人工建票」这一决定。
9. `awaiting_triage` 是设计后唯一人工状态；**未建票的失败项不锁重测/redesign**（闸门只认已建的 B 票）。

待定：

1. `run.retry_attempts` 默认值（建议 2）与退避曲线（建议 5s/30s）。
2. 池预检是「每次运行都探」还是「按 pool 缓存 N 分钟」——取决于 provider 探测成本。
3. run 期 `case-defect` 自动回流是否需要在下一次审核门**免复核**（默认建议免复核 + 报告单列「漂移-回流」变更供抽查，与 09-21 方案一致）。
4. 是否提供 `run.auto_file_product`（默认关）：对 `defect_class=product` 的高置信失败自动建票，进一步去掉「下 bug」这一步。与当前「建票须人工」的决策冲突，故默认关、需显式开启；若团队接受，可把人工点收敛到只剩 approve。

---

## 13. 实现状态（2026-09-24）

已实现并有单测覆盖：

| 机制 | 实现 | 测试 |
|---|---|---|
| M1 状态机 | `src/dev_yard/qa_state.py`（`load/save/record/record_triage/derive_phase/status_payload`）；`EVENTS` 事件表 + `can()/transition()`；`qa/state.yaml` 记录 phase/triage/pools；`dev-yard qa status <JIRA>` 输出记录态与证据漂移提示；`_req_test` 的 approve/verify/run-start/run-end、`ticket_from_qa_case` 的 `file_bug` 走 `transition()`，非法转移抛 `TestRejected`。**取舍**：`derive_phase` 仍从 `STATUS.yaml`+证据**推导**当前态（证据是校验指纹），`transition` 写入的 `phase` 作为记录态，`status_payload` 暴露 `recorded_phase`/`phase_drift`；门禁判定仍以证据推导结果为准（见 §14） | `test_transition_rejects_illegal_move_and_records_legal`、`test_run_end_records_transition`、`test_status_payload_flags_phase_drift`、`test_ticket_from_qa_case_updates_triage_state`、`test_qa_state_records_and_derives_phase`、`test_qa_status_cli` |
| M2 可重试终态 | `qa_schedule.run_schedule(retry_attempts, retry_backoff)`；env 类 blocked 回 `ready`+attempt（无 setup/cleanup 的用例），熔断不计入；`qa.yaml run.retry_attempts/retry_backoff` | `test_run_schedule_retries_env_block_then_passes` 等 4 条 |
| M4 design 原子标记 | 宿主写 `qa/.design.pending`（开始）/`qa/design.yaml`（成功）；`need_design` 认 pending。**偏离原案**：不用「指纹 ≠ 当前」硬门，避免人工改用例后被强制重设计；pending 哨兵已能挡住半成品 | `test_design_pending_sentinel_forces_redesign` |
| M5 故障分类前置 | `VerifyResult.status += blocked`；`verify_env_error()`；`_verify_loop` 不把 blocked 回灌 design，改为**带退避重试**（`design.verify_retry_attempts`/`_backoff`）；`verify_gate/view/describe` 区分 blocked | `test_verify_env_error_blocks_not_fails`、`test_verify_env_error_does_not_block_design_loop`、`test_verify_env_block_is_retried` |
| M6 自动修复免复审 | `cases_fingerprint(qa, scope=)`（bodies/seeds）；`qa_review.machine_fixed()` 沿用审批；仅在正文未变时生效 | `test_machine_fixed_requires_unchanged_body`、`test_auto_recycle_triggers_redesign_and_keeps_approval` |
| M3 池预检与隔离 | `run_schedule(on_pool_trip=)` 把隔离写进 `state.yaml.pools`；`req_test(pool_probe=)` 可注入预检，`run.pool_preflight` 开启时用 `_real_pool_probe`（轻量 pi 调用，60s）逐个探测，失败的池隔离、全失败即拒；**默认关**（见 §13 待做说明） | `test_run_schedule_reports_pool_trip`、`test_record_pools_merges`、`test_pool_preflight_drops_bad_pool`、`..._all_failed_rejects` |
| M6b 失败自动分流 | `qa_report.triage_buckets()`；run 后写 `state.triage`；下次 design invocation 自动回流 `auto_recycled`（case-defect，消费后清空）；CLI `dev-yard req triage <JIRA> --product/--all`、`POST /api/requirements/{jira}/qa/triage`、`QaView` 批量下 bug 按钮 | `test_triage_buckets_splits_case_defect_from_product`、`test_triage_qa_cases_files_only_product`、`test_qa_triage_endpoint_files_pending` |
| M7 契约校验+容错 | `qa.lint_cases()`（repo/account/writes/identity/`needs_verify` 缺 verify.sql/covers）在 approve 前拦下（`--allow-unverified` 可越权），并在 verify 前**有界回灌 design 修一次**；`qa.load_yaml_tolerant()`（标量补引号 + 逐行回退）供 `_read_case_result`/`_file_verdict` | `test_lint_cases_flags_contract_problems`、`test_lint_cases_flags_missing_verify`、`test_approve_refused_on_lint_problem`、`test_lint_problem_triggers_one_design_fix`、`test_result_yaml_with_bare_colon_is_salvaged` |
| M8 同 env 排队 | `env_lock(..., wait_timeout=, on_wait=)`；run 路径传 `run.env_wait_timeout`（默认 1800s）；CLI `--no-wait` 立即拒绝；`on_wait(bool)` 透出「等待环境」态到 web job（`Job.env_waiting` + `JobPanel` 徽标） | `test_env_lock_waits_for_holder_when_asked`、`test_env_lock_reports_wait_state`、`test_job_env_waiting_flag_round_trips` |
| Web UI | `/qa` 与需求详情 payload 带 `phase`/`next`/`triage`；`QaView.vue` 顶部「失败分流」卡片展示待判定 / 自动回流用例；SPA 已 `pnpm build` 重打（`src/dev_yard/web/spa/` 已 gitignore） | `test_qa_page_payload_carries_phase_and_triage` |
| M9 重启恢复 | `JobRunner.resume_pending_qa()`；`create_app` 启动时调用；受 `run.resume_on_restart` 控制 | `test_resume_pending_qa_restores_interrupted_run`、`..._skips_opt_out` |
| M10 增量重跑 | `latest_retryable()`（含 `depends_on` 传递闭包）；`affected_covers()` 按 `meta.yaml changes[].ref` × `repo-baseline/<alias>.head` 反查本次 diff 命中的 `covers`，并入增量集；`_req_test` 在 `resume is None/非 redesign/非 run_only` 时 amend 上一轮 failed/blocked；amend 时刷新 mutation baseline（B 票修复合法前移 HEAD，不再误报 worker 变更）；`run.incremental` 可关；`--full` 强制全量 | `test_incremental_rerun_amends_failed_run`、`test_incremental_can_be_disabled`、`test_latest_retryable_includes_dependents`、`test_affected_covers_maps_diff_to_cases`、`test_incremental_reruns_affected_covers` |

`qa.yaml run` 新键（均可不写）：`retry_attempts`(2)、`retry_backoff`(0)、`env_wait_timeout`(1800)、`resume_on_restart`(true)、`incremental`(true)、`pool_preflight`(false)。`qa.yaml design` 新键：`verify_retry_attempts`(1)、`verify_retry_backoff`(0)。

待做 / 偏离：

- **M3 主动预检默认关**：`run.pool_preflight: true` 才启用。偏离原案「默认 true」——每次运行多一次 pi 往返，而 per-pool 熔断 + M2 轮内重试已能兜住坏池；需要时按需打开。
- **M7 lint 回灌只做一次**：有界 1 次；仍不通过则交人工（approve 门禁拦下）。
- **M1 门禁仍读证据推导**：`transition()` 与事件表已落地并成为唯一的 phase 写入者；但门禁判定仍以 `derive_phase`（`STATUS.yaml`+证据）为准，`state.yaml.phase` 为记录态，`status_payload`/需求详情页/`QaView` 均暴露漂移。原案的「门禁改读 state.yaml」未做（重写读取路径风险大）。
- **已完成（原列于此）**：M8 `--no-wait` + `on_wait` 等待态；M7 `needs_verify` lint；M10 `covers` 命中 diff；M1 `transition()`。
- **既有失败已清零**（2026-09-24 第三轮）：`test_web_app.py::test_rerun_waits_for_the_job_before_claiming_success`（`cf97ad8` 批量重测把 `settleJob`/`showCaseBanner` 重构为 `submitRerun`/`showRerunBanner`，测试未同步）、`test_contract_diff_base.py::test_contract_diff_ignores_upstream_drift`（`e201173` 把 prompt 的 `files: [` 改成 `files (paths …`，测试未同步）——两处均为**过时断言**，行为本身正确，已更新断言对齐现状。

---

## 14. CR 修订记录（2026-09-24）

对实现做了一次两轴 CR（Standards / Spec）。以下为确认的问题与处置：

已修：

| 问题 | 处置 |
|---|---|
| M2 重试被架空：`_retryable_block` 遇 setup/cleanup 就拒绝，真实用例基本都带 setup → 环境故障永不重试 | 改为「env 类一律重试；仅宿主脚本失败（`setup failed:`/`env fault:`/`cleanup failed:`）不重试」 |
| M2 计数不持久：`progress.yaml` 无 `attempts`，续跑后预算重置 | `progress_payload` 写入 `attempts`，`_apply_resume` 恢复（含非终态） |
| M6b 永久回流：`auto_recycled` 从不清理，之后每次 design 都强制 redesign | 自动回流消费后清空 `state.triage.auto_recycled`（保留 `pending`） |
| `qa_state.save` 并发：固定临时名 + 无锁读改写，会丢写/撕裂 | 改 `tempfile.mkstemp` 唯一临时名 + 原子 rename；`record`/`record_pools`/`record_triage` 持 `status.jira_lock` |
| `derive_phase` 冗余分支（`stale` 与下一行同返回） | 合并为单条返回 |
| 命名：函数 `machine_fixed()` 与字段 `machine_fixed` 混淆 | 函数改名 `mark_machine_fixed` |
| M9 缺占用检查 | `resume_pending_qa` 跳过已有活动 job 的需求 |
| §5 提到 `--full` | CLI 增加 `--full`（等价强制新 run 全量） |

确认但**未做**的收敛（2026-09-24 第二轮补做后，仅剩 M3 默认关与 M1 门禁读取路径）：

- ~~**M1 非权威**~~ **部分补做**：已加 `EVENTS` + `can()/transition()`，approve/verify/run/file_bug 走状态机，非法转移抛 `TestRejected`；`status_payload` 暴露 `recorded_phase`/`phase_drift`，`_try_transition` 被拒时写日志（不静默，也不阻断已成立的动作）；门禁读取路径仍以证据推导为准（未改）。
- ~~**M8 web 层 `waiting` 态**~~ **已补做**：`env_lock(on_wait=)` 在等待/取得锁时回调，`Job.env_waiting` 透出，`JobPanel` 显示「等待环境」徽标（SPA 已重打）。`_jobs_conflict` 未加 env 维度（跨需求同 env 由 `env_lock` 排队，无需 job 层拦截）。
- ~~**M10 的「`covers` 命中本次 diff」**~~ **已补做**：`affected_covers()` 用 `meta.yaml changes[].ref` × `repo-baseline/<alias>.head` 反查，并入增量集；amend 时刷新 mutation baseline。
- ~~**M7 `needs_verify` 独立 lint 规则**~~ **已补做**：并入 `lint_cases`，approve 门禁拦下。
- **M3 默认关**（原案默认 true）：保留。

后续 CR 轮已补齐（原列于此）：M5 核实期 blocked 退避重试、M6b CLI `req triage`/批量下 bug、M10 `depends_on` 后继。

判断项（接受，不改）：`_parse_run` 返回 6 元组、`load_yaml_tolerant` 也用于 `_file_verdict`、增量 amend 触发面比 §5 宽（按「有 finished failed/blocked run 的普通 `req test`」触发）。
