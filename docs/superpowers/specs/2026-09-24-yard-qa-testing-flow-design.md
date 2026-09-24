# yard-qa 测试流程收敛设计：三层状态模型 + run 生命周期

- 日期：2026-09-24
- 状态：方案 v1（最终稿，待评审）
- 触发：整体 review 测试流程。使用反馈「一轮有很多用例，测完一部分、一部分在跑、一部分没开始；想先停止，过一会再恢复」暴露的暂停缺口，不是孤立 bug，而是**状态模型把三层不同粒度的事实揉在一起、且靠推断而非记录**的结构性结果。
- 前序：[`2026-09-16-yard-qa-workflow.md`](2026-09-16-yard-qa-workflow.md)（四态/证据/审核门/池调度，已落地）、[`2026-09-24-yard-qa-recovery-design.md`](2026-09-24-yard-qa-recovery-design.md)（可恢复执行环 M1–M10，已落地）。
- 约束（沿用 recovery）：不改四态语义 `passed|failed|blocked|skipped`；不改 `result.yaml` 的详细证据契约；不改 exec 路由、池×DAG 调度内核、票循环；测试 agent 只测不修。

---

## 1. 背景：一次暂停暴露的结构问题

一轮 run 里三种用例状态并存：`passed`（已跑完）、`running`（在跑）、`pending/ready`（没开始）。用户点「停止」，期望的是**暂停**：已跑完的保留，未开始的留待下次继续。实际行为：

1. web「停止」→ `Job.cancel()` 置 `cancel_requested` 并杀掉所有 pi 子进程（`web/jobs.py:243`）。
2. 调度器下一轮 `cancel_check()` 为真 → `cancelled=True`，不再派发（`qa_schedule.py:379-389`）。
3. 等在跑的收尾后，**把仍是 `pending/ready` 的用例批量改成 `blocked/cancelled`（终态）**（`qa_schedule.py:473-481`）。
4. run 在写 `result.yaml` 前 `_raise_if_cancelled` 直接 bail（`qa.py:2693-2695`），所以这轮「未完成」。
5. 下次「继续未完成的 run」→ `_apply_resume` 读到 `blocked` 是终态，于是**把它们当已完成跳过**（`qa.py:1688-1706`），调度器无活可干，这轮被「收尾」成一批 cancelled。

净效果：**停止 → 继续，未开始的用例不会被重跑**；看板「继续未完成的 run（剩 N 条）」的 N 甚至会是 0（`_pending_in` 只数 `pending/ready/running`，`qa.py:1331`）。

> 对照：CLI `Ctrl-C`（无 `cancel_check`）和 `kill -9` / web 重启，都不会触发第 3 步，`progress.yaml` 保留 `pending/ready/running`，续跑按设计工作。问题只出在**主动、优雅地取消**这条路径上——因为**没有地方能记「这轮是暂停，不是结论」**。

---

## 2. 现状

```text
需求生命周期  STATUS.yaml.phase     open → frozen → testing → done               (4 态)
   ↑ 由 submit-test / accept-test 维护（不变量）

测试相位      qa/state.yaml.phase   designing / verifying / awaiting_review /
   ↑ derive_phase() 每次从证据重算   approved / running / awaiting_triage / recycled / closed  (11 态)

单次 run      qa/evidence/<run_id>/ progress.yaml + result.yaml + <case>/result.yaml
   ↑ 是否存在 / 是否完成，靠推断

用例结论      progress.cases[].state  pending / ready / running / passed / failed / blocked / skipped
```

「当前处于哪一步」不是被**记录**的，而是每次从证据（`review.yaml`、`design-verify/`、`progress.yaml`、`result.yaml`）**推断**出来的：`qa_state.derive_phase`（`qa_state.py:222`）不信任 `state.yaml`，因此才有 `phase_drift` 与「记录态 vs 推导态」两套口径。recovery 方案 M1 本想「门禁读 `state.yaml`」，实际只做到「`transition()` 写 `state.yaml` 作为记录」，门禁仍读推导（该文档 §13/§14 自认）。

描述同一批事实的文件有 6 个：`STATUS.yaml`、`qa/state.yaml`、`qa/review.yaml`、`qa/design-verify/summary.yaml`、`evidence/<run>/progress.yaml`、`evidence/<run>/result.yaml`（+ 每 case 的 `result.yaml`）。

---

## 3. 结构性问题

### 3.1 结构层（按影响排序）

| # | 问题 | 现状 | 证据 |
|---|---|---|---|
| S1 | **run 没有生命周期** | 「这轮跑完没有 / 停没停 / 能不能续」全靠推断：有没有 `pending/ready/running`、`result.yaml` 在不在 | `qa.py:1298`、`qa.py:1252` |
| S2 | **五套 run 语义互相打架** | 暂停、取消、续跑、单条重跑、增量重跑各自推断同一件事；调度器取消时把未开始用例判成终态，`_apply_resume` 又把它当已完成跳过 | `qa_schedule.py:473`、`qa.py:1664` |
| S3 | **相位靠推导，记录态冗余** | `state.yaml.phase` 只是记录，门禁读 `derive_phase`；不一致靠 `phase_drift` 暴露 | `qa_state.py:279` |
| S4 | **god function + flag 组合爆炸** | `_req_test` ~1050 行，揉进门禁/design/lint/verify/approve/调度/变异/ingest/triage；CLI `req test` 23 个 option 互相约束 | `qa.py:1871`、`cli.py:933` |
| S5 | **影子分类法跨层耦合** | 四态之外，真正驱动分流的是 `blocked_class`/`defect_class`/`blocked_kind`，取值须跨调度/报告/状态机/web 一致 | `qa_schedule.py:159/193`、`qa_report.py` |
| S6 | **配置面过大** | `qa.yaml` 十几个开关，多数团队不调却要理解与测试 | `qa_config.py:130-147` |

设计判断：**S1 是根**。一旦 run 有显式生命周期，S2 的推断链全部收敛到一个字段；S3 的相位推导可以顺势降级为一致性校验。

### 3.2 具体缺口（暂停路径）

| # | 环节 | 现状 | 证据 |
|---|---|---|---|
| P1 | 「取消」把未开始用例判成终态 | 调度器取消分支把 `pending/ready` 批量写 `blocked` + `blocked_class=cancelled` | `qa_schedule.py:473-481` |
| P2 | `blocked(cancelled)` 被当作真 verdict | `TERMINAL` 含 `blocked`，`_run_incomplete`/`_apply_resume` 不区分「取消」与「真阻塞」 | `qa_schedule.py:14`、`qa.py:1298`、`qa.py:1664` |
| P3 | 「未完成」判据依赖 `result.yaml` 缺失 | 取消恰在写 `result.yaml` 前 bail，才侥幸仍判「未完成」；但续跑又跳过全部 cancelled，语义打架 | `qa.py:1308`、`qa.py:2695` |
| P4 | 主动暂停与崩溃不可区分 | 都留下「未完成 run」，`resume_pending_qa` 无差别拉起 | `web/jobs.py:1003` |
| P5 | 没有「放弃本轮」的正规出口 | 想彻底丢弃只能新开 run 或手改文件 | — |

设计原则：**`cancelled` 不是 verdict，是「被中断」——它必须可恢复。** 真 verdict 只有 `passed/failed/blocked(case-defect|env|...)`；`cancelled` 属于「还没结论」。

---

## 4. 目标模型：三层分离

| 层 | 粒度 | 状态 | 谁写 | 用途 |
|---|---|---|---|---|
| **L1 需求相位** | 需求 | `open / frozen / testing / done` | `submit-test` / `accept-test`（不变） | 提测槽门禁 |
| **L2 run 生命周期** | 单次 run | `running / paused / concluded(outcome=passed\|failed\|blocked) / abandoned` | 宿主，每次转移原子写 | **续跑 / 暂停 / 取消 / 重跑 / ingest 的唯一依据** |
| **L3 用例结论** | 单条 case | `passed / failed / blocked / skipped`（内部 `pending/ready/running`） | worker 写、宿主校验 | 证据，不再作为门禁推断源 |

### 4.1 run 记录（权威）

新增 `qa/evidence/<run_id>/run.yaml`，作为该轮 run 的**唯一权威**：

```yaml
run_id: 2026-09-24-153000
env: test
status: running          # running | paused | concluded | abandoned
outcome: null            # passed | failed | blocked，仅 status=concluded 时有值
started_at: 2026-09-24T15:30:00Z
paused_at: null
ended_at: null
amend: 0                 # 同 run 内 amend（重跑/增量）次数
cases:
  - {id: case-01, verdict: passed}
  - {id: case-02, verdict: pending}
```

- `progress.yaml` 降级为**高频 live 视图**（池占用、正在跑哪条），由宿主从内存写，不作为门禁。
- `result.yaml` / `<case>/result.yaml` 降级为**详细证据**（断言、截图、failure），不作为门禁。

### 4.2 run 状态机

```text
(none) ──start──► running ──pause──► paused ──resume──► running
                    │                   │
                    ├──conclude─────────┤──► concluded(outcome)
                    │                   │
                    └──abandon──► abandoned ◄──abandon──┘
        running/concluded ──rerun cases──► running
        running ──crash（进程死，无写入）──► running   # 仍可自动恢复
```

规则：

- **只有 `concluded` 的 run 才 ingest**，才可能产出 `awaiting_triage`/`closed`（L1）。
- `running` / `paused` 都**可续**；`abandoned` 是「用户明确放弃」，不 ingest、不再被自动拉起。
- **暂停不写用例终态**：未开始/被中断的 case 保持 `pending/ready/running`，或记为可恢复的 `blocked(cancelled)`（见 M2）。
- 崩溃（`kill -9` / web 重启）**不写任何东西**，`status` 停在 `running` → 可自动恢复；主动暂停写 `paused` → 只手动续。二者天然可分。

### 4.3 三层如何咬合

```text
derive_phase(需求) = f(最新 run.status, run.outcome, review.yaml, triage)
  - 无 run 或 status∈{running,paused}      → running（可续）
  - 未审核                                  → awaiting_review
  - concluded(outcome=passed)               → closed
  - concluded(outcome=failed/blocked)       → awaiting_triage / recycled（按 triage）
```

`derive_phase` 从「门禁真相」降级为「记录态一致性校验」；`state.yaml` 成为唯一写入者（M3）。

---

## 5. 机制

### M1 run 生命周期字段（解 S1/S2）

- 宿主在 4 个转移点写 `run.yaml.status`：`start`（取到 env 锁后）、`pause`（调度器取消）、`conclude`（写 outcome + ingest）、`abandon`。
- 续跑/重跑/暂停/放弃全部读同一个字段，删除下列推断：
  - `_run_incomplete`（`qa.py:1298`）→ `run.status in {running, paused}`；
  - `_pending_in`（`qa.py:1331`）→ 数「无结论或可恢复」的 case；
  - `find_incomplete_run`（`qa.py:1252`）→ 找最新 `status∈{running,paused}` 的 run；
  - `_latest_retryable_run`（`qa.py:1504`）→ 读最新 `concluded` 的 `outcome`；
  - `derive_phase`（`qa_state.py:222`）→ 读 `run.status/outcome`，不再看 `result.yaml` 是否存在。

### M2 暂停 / 续跑 / 重跑 / 放弃（解 S2 与 §3.2）

这是本次暂停缺口的正式落点。

**M2.1 单一谓词 `resumable`**（`qa_schedule.py`）：

```python
def resumable(state: str, blocked_class: str = "", reason: str = "") -> bool:
    """True = 该行没有最终结论，续跑应重跑它。"""
    if state in {"pending", "ready", "running"}:
        return True
    # 主动/被动取消不是 verdict：可恢复
    return blocked_kind(reason, blocked_class) == "cancelled"
```

复用已有 `blocked_kind`（`qa_schedule.py:193`，认 `blocked_class=cancelled` 与 reason 里的 `cancelled:`），不新增对外状态。

**M2.2 调度层不再判死**：`run_schedule` 的取消分支（`qa_schedule.py:473-481`）从「批量标 `blocked`」改为**不动** `pending/ready`（保留原状态），并在返回时告诉调用方「本轮被取消」：

```python
def run_schedule(...) -> bool:      # 返回值新增：是否被取消
    ...
    elif cancelled:
        # 暂停：未开始的用例保持 pending/ready，留给下次续跑。
        # 在跑的用例已被杀，_safe_run 记为 blocked/cancelled（M2.1 视作可恢复）。
        ping()
    ...
    return cancelled
```

- 不改 `_safe_run`（`qa_schedule.py:484`）：被杀的 in-flight 仍记 `blocked/cancelled`，保留「本轮被打断」的可观测性。
- 池熔断分支（`if not pools_open() and tripped:`）保持原样，优先级高于取消。

**M2.3 续跑三处改用 `resumable`**：

| 函数 | 现状 | 改为 |
|---|---|---|
| `_run_incomplete`（`qa.py:1298`） | 只看 `pending/ready/running`，否则看 `result.yaml` 是否存在 | `run.status` + `resumable(...)`；`blocked/cancelled` 也算未完成 |
| `_pending_in`（`qa.py:1331`） | 只数 `pending/ready/running` | 数 `resumable(...)`，让「剩 N 条」把被中断的也计入 |
| `_apply_resume`（`qa.py:1664`） | 终态行一律采纳（跳过） | 终态行若 `resumable`（即 `blocked/cancelled`）→ 重置为 `ready`、清 `reason/blocked_class`，**重跑**；其余终态照旧跳过 |

`_apply_resume` 关键改动：

```python
for job in cases:
    prev = prev_by_id.get(job.id) or {}
    state = str(prev.get("state") or "")
    if isinstance(prev.get("attempts"), int):
        job.attempts = prev["attempts"]
    if prev_by_id and state not in TERMINAL:
        continue                       # 已是 pending/ready/running：留待重跑
    if resumable(state, str(prev.get("blocked_class") or ""), str(prev.get("reason") or "")):
        job.state = "ready"            # cancelled 不是结论 → 重跑
        job.reason = ""
        job.blocked_class = ""
        continue
    ... 原逻辑：读 result.yaml、reconcile、采纳终态、skipped += 1 ...
```

这与 M10 增量重跑的既有语义一致（`_latest_retryable_run` 本就把 `blocked` 计入重跑集，`qa.py:1525-1537`），只是把「续跑」补齐到同一标准。

**M2.4 暂停 vs 崩溃：`run.yaml.status`**：

- 主动暂停：`run_schedule` 返回 `cancelled=True` 时，宿主把本轮 `run.yaml.status` 置 `paused`（`_req_test` 在 `_raise_if_cancelled` 之前，读改写、原子落盘）。
- 手动续跑：进入 resume 分支后把 `status` 置回 `running`。
- 崩溃：进程死、无写入，`status` 停在 `running`。
- `resume_pending_qa`（`web/jobs.py:1003`）：`status=running`（崩溃）按 `run.resume_on_restart` 自动恢复；`status=paused` **跳过**（页面仍显示「继续未完成的 run」按钮，等用户点）。
- CLI `Ctrl-C`：走异常路径、不写 `paused`，`status` 停在 `running`，与既有「崩溃可自动恢复」一致；用户重跑 `dev-yard req test` 时本就会自动续。

**M2.5 动作语义总表**：

| 动作 | 语义 | 实现 |
|---|---|---|
| 暂停 | 停止派发、等在跑收尾，未开始用例**保持未终态** | 调度器取消分支不判死；写 `status=paused` |
| 续跑 | 继续**同一** run，已 `passed` 不重跑 | `status=running`；跳过已 `passed`，重跑未终态 + `blocked(cancelled)` |
| 重跑单条/增量 | 在**同一** run 内重置指定用例 | `amend++`，`reset_cases_in_run` |
| 放弃 | 判 `abandoned`，不再拉起 | 新动作；等价旧「取消收尾」 |
| 新开 | 新 run dir，旧 run 不再是 latest | `--fresh` / 不勾「继续」 |

放弃的首选出口是「新开一轮」：CLI `--fresh` / web 执行用例时不勾「继续未完成的 run」→ `resume=False` → `_claim_run_dir` 新建 run（`qa.py:2322-2324`）。旧 run 不再是 latest，`derive_phase`/`find_incomplete_run` 自然忽略它，`resume_pending_qa` 也不会再拉起。可选（P2）：web 加「放弃本轮」动作，把最新未完成 run 的 `status` 置 `abandoned`。

### M3 `state.yaml` 转权威（解 S3，渐进）

- 每次转移原子写 `qa/state.yaml`（`transition()` 已有），门禁改为「读 `state.yaml` + 校验证据指纹」。
- 证据文件继续写，但**只作为附件与校验**，不再是门禁的真相来源。
- 迁移期保留 `derive_phase` 作一致性校验（`phase_drift` 从「常态」降为「异常告警」）。
- 风险最高，单独回归（见 §11 P1）。

### M4 service 拆分（解 S4）

`_req_test` 拆为 4 个职责单一的函数 + 一个薄编排：

```text
qa_design.design(root, jira, cfg, *, feedback=None) -> cases      # 含 lint 回灌
qa_verify.verify(root, jira, cfg, cases) -> verify_results         # 含回流
qa_review.approve(root, jira) / reject(...)
qa_run.run(root, jira, cfg, cases, *, intent) -> RunResult         # 含调度/变异/ingest
orchestrator.req_test(root, jira, intent)                          # 解析 intent，按相位编排
```

- CLI/Web 只构造 `intent`（`design|verify|review|run|resume|rerun|fresh`），flag 互斥校验留在 CLI 层。
- `_req_test` 的 `if design_only / if verify_only / if approve / elif not rerun_ids` 分支树（`qa.py:2171-2249`）消失。

### M5 分类法收敛（解 S5）

- 新建一处 `qa_taxonomy`：定义 `blocked_class` 合法取值与语义（`case-defect|env|auth|undeployed|worker-exit|cancelled|other`）、`defect_class`（`case|product|unclassified`）、以及 `blocked_kind`/`env_block_class`/`classify_defect` 的派生规则。
- 调度、报告、状态机、web 全部从它派生，禁止各自硬编码取值集合。

### M6 配置瘦身（解 S6）

- 保留团队真会调的：`active_env`、`envs.*`（`base_url`/`db`/`auth`/`script`/`exec`）、`workers`、`browser`、`design` 模型、`accounts`。
- 其余调优项（`run_retry_*`、`env_wait_timeout`、`pool_preflight`、`verify_retry_*`…）收成代码常量 + 环境变量覆盖，默认值即最佳实践；需要时再暴露到 `qa.yaml`。
- `resume_on_restart`、`incremental` 保留为 `run` 下的两个开关（语义高频）。

---

## 6. 行为对比

| 场景 | 现状 | 本方案 |
|---|---|---|
| 主动暂停 → 续跑 | 未开始用例被判死，续跑跳过 | 同一 run 续跑，只重跑未完成 + 被中断 |
| 「剩 N 条」 | 常为 0（漏计 cancelled） | 含未开始与被中断的，数目正确 |
| 「跑到哪了 / 能否继续」 | 从证据推断 | 读 `run.yaml.status` |
| 崩溃恢复 vs 主动暂停 | 不可区分 | `running` vs `paused` 显式区分 |
| 相位一致性 | 推导态 + 记录态两套 | `state.yaml` 权威，推导仅校验 |
| `req test` 复杂度 | 1050 行 + 23 flag | 4 个纯函数 + intent 编排 |
| 分流分类 | 三处各自维护取值 | 单处 `qa_taxonomy` |
| 配置面 | 十几开关 | 高频项 + 常量默认 |
| 想彻底丢弃 | 只能手改/新开 | `--fresh` / 不勾继续即新开；可选「放弃本轮」 |
| 报告 `blocked_kind=cancelled` | 取消即终态 | 仅保留给「被中断」的可观测计数，不再是最终结论 |

---

## 7. 落点

| 文件 | 改动 |
|---|---|
| `src/dev_yard/qa_schedule.py` | 新增 `resumable()`；取消分支不判死；`run_schedule` 返回 `cancelled`；分类法迁 `qa_taxonomy` |
| `src/dev_yard/qa_run.py`（新） | run 生命周期（`run.yaml` 读写、状态转移、暂停/续跑/放弃、ingest、变异闸门、triage 记录） |
| `src/dev_yard/qa_taxonomy.py`（新） | `blocked_class`/`defect_class` 取值与派生规则 |
| `src/dev_yard/qa.py` | `_req_test` 拆薄；`_run_incomplete`/`_pending_in`/`_apply_resume`/`find_incomplete_run` 改读 `run.status` 与 `resumable` |
| `src/dev_yard/qa_state.py` | `derive_phase` 改读 `run.status/outcome`；`state.yaml` 转权威 |
| `src/dev_yard/qa_config.py` | 调优项收常量 + env 覆盖 |
| `src/dev_yard/web/jobs.py` | `resume_pending_qa` 按 `status`（`running` 自动 / `paused` 手动） |
| `web/src/views/*.vue` / `components/JobPanel.vue` | 「停止」= 暂停（可续）；新增「放弃本轮」 |
| `.pi/skills/qa-run/SKILL.md` | `cancelled` = 被中断、可恢复 |

不改：`_safe_run`、四态枚举、`result.yaml` 详细证据契约、`env_lock`、池调度内核。

---

## 8. 测试

新增/调整（`tests/test_qa.py`、`tests/test_qa_flow_fixes.py`、`tests/test_qa_web.py`）：

- `test_run_status_transitions`：`start/pause/resume/conclude/abandon` 合法转移；非法转移被拒。
- `test_run_schedule_cancel_leaves_pending_resumable`：`cancel_check` 触发后，未开始用例仍为 `pending/ready`，不是 `blocked`。
- `test_resumable_predicate`：`pending/ready/running` 与 `blocked(cancelled)` 为真；`passed/failed/blocked(env|case-defect)` 为假。
- `test_run_incomplete_counts_cancelled` / `test_pending_in_counts_cancelled`。
- `test_apply_resume_resets_cancelled_to_ready`：progress 里 `passed + blocked(cancelled)` → resume 后只重跑 cancelled 那条，`passed` 不动。
- `test_req_test_resume_after_cancel_reruns_unstarted`：端到端——取消一轮（passed + 未开始 + 在跑），`--resume` 后跑的是后两类。
- `test_crash_run_auto_resumes_paused_does_not`：`status=running` 自动重建 job；`status=paused` 不重建，手动 `resume=True` 可续且续后 `status` 回 `running`。
- `test_abandoned_run_not_resumed`。
- `test_derive_phase_reads_run_status`；`test_gate_reads_state_yaml_with_evidence_fingerprint`。
- `test_orchestrator_intent_dispatch`；`test_taxonomy_is_single_source`；`test_advanced_knobs_env_override`。
- 回归：`test_qa.py` / `test_qa_flow_fixes.py` / `test_qa_web.py` / `test_qa_verify.py` / `test_hardening.py` 全绿；`test_render_qa_report_uses_progress_for_cancelled_run` 仍成立（report 仍认 `cancelled` 计数）；`test_resume_pending_qa_restores_interrupted_run` 保持绿。

---

## 9. 验收

- **停止 → 继续**：一轮 5 条（1 passed / 1 在跑 / 3 未开始）→ 暂停 → 续跑 → 只重跑 4 条，`passed` 不重跑；看板「继续未完成的 run（剩 4 条）」。
- **重启不误拉**：暂停后重启 web → 不自动起 job；页面仍可一键继续。`kill -9` 后重启 → 自动续跑。
- **CLI**：`Ctrl-C` 后 `dev-yard req test <JIRA>` → 自动续跑未完成项。
- **放弃**：暂停后 `--fresh` / 不勾继续 → 新开一轮，旧 run 不再被 `qa status`/看板当作待续。
- **观测统一**：`qa status` 直接读 `run.status`，与看板、CLI 一致，无 `phase_drift`。
- **复杂度**：`req test` flag 组合校验集中在 CLI，service 层不再出现「互斥 flag」判断；新增一个 `blocked_class` 取值只改一处。

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `cancelled` 改为可恢复后，暂停的 run 永远悬着 | `status=paused` 不自动拉起；`--fresh`/「放弃本轮」出口；`qa status` 明示「可继续」 |
| 崩溃型 run 被误标 `paused` 而不自动恢复 | 只有调度器取消分支（用户主动、进程存活）才置 `paused`；`kill -9`/崩溃无机会写，`status` 停在 `running`，仍自动恢复 |
| 续跑把「真 blocked」也重跑，浪费额度 | `resumable` 只认 `blocked_kind == cancelled`；`env/case-defect/other` 仍按 M2/M6b 既有分流处理 |
| `run.yaml` 与既有 `result.yaml`/`progress.yaml` 消费者不一致 | 迁移期 `run.yaml` 为权威，`result.yaml`/`progress.yaml` 继续写；逐消费者切换 |
| `state.yaml` 转权威改动门禁读取路径，回归面大 | 单独分期（P1）；迁移期 `derive_phase` 作校验，`phase_drift` 告警不阻断 |
| 拆分 `_req_test` 引入行为回归 | 先补编排级集成测试再拆；保持对外 `req_test()` 签名不变 |
| 配置瘦身让高级用户失去控制 | 环境变量覆盖兜底；被移出 `qa.yaml` 的键保留解析兼容（读到即生效，只是不主动暴露） |
| 报告口径变化让旧断言失败 | 仅取消路径不再产生终态 `cancelled`；report 对 `cancelled` 的解析保留 |
| 取消时 in-flight 用例的中间数据残留 | 沿用现有语义：续跑重跑整条；`setup/cleanup` 由用例自身保证幂等（既有约束） |

---

## 11. 分期

| 期 | 内容 | 价值 | 风险 |
|---|---|---|---|
| **P0** | M1 + M2（run 生命周期 + 暂停/续跑/放弃） | 直接解暂停缺口；S1/S2 收敛 | 低（新增字段 + 改推断读点） |
| **P1** | M4 + M3（service 拆分 + state.yaml 权威） | god function 与相位漂移消失 | 中高（动门禁读取路径，单独回归） |
| **P2** | M5 + M6（分类法收敛 + 配置瘦身） | 长期可维护性 | 低 |

建议按序；P0 独立可回归。

---

## 12. 已决与待定

已决：

1. 三层分离：L1 需求相位（不变）/ L2 run 生命周期（新增，权威）/ L3 用例结论（证据）。
2. `run.yaml.status` 是续跑/暂停/取消/重跑/ingest 的唯一依据，删除相关推断。
3. `cancelled` 定义为「被中断、可恢复」，不是 verdict；用 `resumable()` 单一谓词落地。
4. 暂停不写用例终态；未开始用例保持 `pending/ready`。
5. 崩溃（`running`）自动恢复，主动暂停（`paused`）只手动续。
6. 续跑语义不变：重跑整条被中断用例，已 `passed` 的不重跑。
7. `state.yaml` 最终转权威，证据降级为附件；分 P1 做。
8. `_req_test` 拆 4 函数 + intent 编排。
9. 非 `qa-run` 动作（`grill`/`qa-design`/`qa-review`）的取消语义不动。

待定：

1. `run.yaml` 是否合并进 `result.yaml`（少一个文件）还是独立——倾向前者若消费者能平滑迁移，否则独立更清晰。
2. `abandoned` 是否需要单独 UI 动作，还是靠 `--fresh` 隐式放弃。
3. M3 是否一步到位，还是先只让 `derive_phase` 读 `run.status`、门禁读取路径留到后续。
4. M6 移出的键是否保留 `qa.yaml` 解析兼容（建议保留，静默生效）。
5. qa-run 的按钮是否从「停止」改名「暂停」，或保留「停止」+ 文案「可继续」——取决于团队对「停止=终止」的既有直觉。

---

## 13. 实现状态（2026-09-24）

**P0（M1 + M2）已实现**，全量测试 919 passed、ruff 通过：

| 机制 | 实现 | 测试 |
|---|---|---|
| M1 run 生命周期 | `src/dev_yard/qa_run.py`（新）：`run.yaml` 的 `load/status/save/set_status/is_resumable`；`status` 对无 `run.yaml` 的旧 run 回退「有 `result.yaml` = concluded，否则 running」 | `test_run_status_lifecycle_round_trips` |
| M1 推断改读 status | `_run_incomplete` 先看 `run.status`；`find_incomplete_run`/`derive_phase` 随之生效 | `test_run_incomplete_and_pending_count_cancelled` |
| M2.1 可恢复谓词 | `qa_schedule.resumable()`（`pending/ready/running` 或 `blocked(cancelled)`） | `test_resumable_predicate` |
| M2.2 调度不判死 | `run_schedule` 取消分支不再批量标 `blocked`，返回 `cancelled: bool` | `test_run_schedule_cancel_leaves_remaining_resumable` |
| M2.3 续跑 | `_pending_in`/`_apply_resume` 用 `resumable`；cancelled 重置为 `ready` 重跑 | `test_apply_resume_resets_cancelled_to_ready`、`test_req_test_pause_then_resume_reruns_unstarted` |
| M2.4 暂停 vs 崩溃 | `_req_test` 取消时写 `status=paused`、续跑写 `running`、结束写 `concluded(outcome)`；`resume_pending_qa` 跳过 `paused` | `test_resume_pending_qa_skips_paused`（既有 `..._restores_interrupted_run` 仍绿） |

**P1 / P2 未做**（backlog，见 §12 待定）：它们不修缺陷、不阻塞功能，触发条件见本设计讨论——M3 需要真实 `phase_drift` 事故、M4 需要继续往 `req_test` 加功能、M5 需要新增分类取值、M6 需要用户被开关搞混。建议 P1 内先 M4 后 M3（M3 风险最高）。

**P0 内的取舍**：`abandoned` 状态未产出（放弃走 `--fresh`/不勾继续，见 §12 待定 #2），代码只写 `running/paused/concluded`；`set_status` 不做转移合法性校验（它是低层 setter，不是状态机）。`running` 在取到 `env_lock` 后才写（与既有「排队不报 running」一致）；`concluded(outcome)` 在写 `result.yaml` 后、ingest 前落盘，避免崩溃窗口把已 ingest 的 run 留成可续。

已随 P0 调整的既有测试：`test_run_schedule_cancel_marks_remaining_cancelled` → `..._leaves_remaining_resumable`（语义变更）；`test_apply_resume_keeps_later_verdict_over_pass_file`（case-01 改用 `worker exit` 而非 `cancelled`，因 cancelled 已不再终态）；`test_qa_web.py::test_qa_api_has_cases_and_run`（带 `result.yaml` 的 run 视为 concluded，`incomplete_run` 为 None）。
