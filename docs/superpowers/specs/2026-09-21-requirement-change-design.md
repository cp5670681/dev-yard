# 需求文档轻量变更 设计草案

- 日期：2026-09-21
- 状态：草案（决策见 §13，待实现）
- 范围：**只做「轻量变更」入口**；方案级（重开已完成的票、拆票、级联失效契约与提测）本次不做，见 §14
- 影响命令：`dev-yard req change`、`dev-yard req changes`、Web 看板「轻量变更」
- 关联：`src/dev_yard/service.py`、`reqboard.py`、`actions.py`、`status.py`、`tickets.py`、`stages.py`、`qa_review.py`、`qa.py`、`test_report.py`、`web/jobs.py`、`web/routes/api_reqs.py`

## 1. 背景与问题

需求进入实现或提测后，产品仍会小改需求（补一条规则、改一段描述、加一个验收点）。当前平台没有对应入口，只有两个极端：

- 手改文档 + 手点「对齐/写规约/拆票」：`grill`/`spec`/`tickets` 动作始终可跑（`reqboard.py:388-390`），且各阶段 `protects` 互不回抹（`stages.py:126-164`）。但**票不会自动跟上**——重跑 `to-tickets` 是从 SPEC 重新生成整份 DAG，会重排/改名已有票；`sync_tickets` 对消失的票只丢状态、**不拆 worktree/分支**（`status.py:108-122`），留下孤儿。已在实现的票也不会被重开（`refresh_ready` 对 `done` 直接跳过，`status.py:130`）。
- `dev-yard req reset-phase`（`service.py:483`）：删 worktree + `git branch -D` 冻结/票分支、清空票/契约/提测，`phase -> open`。把已实现代码一起炸掉，代价过大。

本设计补一个**轻量变更**入口，把「文档改了」收敛成固定动作链，产出**恰好一张**实现票，不重排已有票、不改 phase、不动契约。

### 关键约束（已核对现有实现）

- `grill-with-docs` 把 `REQUIREMENT.md` 声明为**只读**（`.pi/skills/grill-with-docs/SKILL.md` 读写表），只写 `GRILL.md`。改 requirement 必须由宿主单独完成，不能指望 grill。
- `to-spec` 只写 `SPEC.md`（`.pi/skills/to-spec/SKILL.md:10`）；`to-tickets` 会重写整份 TICKETS.md，而 CLI 追加 `## B<n>` 票是既有惯例，skill 明文「不要手写去覆盖」（`.pi/skills/to-tickets/SKILL.md:36`）。
- 一张票只绑**一个** repo alias（`tickets.py`；`to-tickets` 规则）。
- 票的 `source` 只被 `{"contract","test"}` 特判（`reqboard.py:295,361,586,588`；`service.py:1050,1212`）；新增 `source: light` 会被当作普通票，无需额外分支。
- QA 侧**没有**「用例相对 SPEC/REQUIREMENT 过期」的概念：`cases_fingerprint` 只哈希 `qa/cases/**`（`qa_review.py:33-60`），`review.yaml` 固定 4 键（`qa_review.py:85-98`），`meta.yaml` 的 `requirement`/`base_branches` 字段**从未被代码读取**。要新增需求级 stale 标记属于**净新代码**。
- 本仓库运行时的 QA 是 `.pi/skills/qa-design` + `.pi/skills/qa-run` 与 `src/dev_yard/qa*.py`；`qa-powers` 是外部独立工具（参考实现，非运行时依赖，见 §7）。

## 2. 目标与非目标

### 目标

1. 新增「轻量变更」入口：一句变更说明 + 选一个仓，一键完成文档更新与建票。
2. 变更记录**追加**到 `REQUIREMENT.md`（保留原文），不覆盖产品原文。
3. 可选跑一轮 `grill`（只写 `GRILL.md`），然后跑 `spec`（增量改 `SPEC.md`）。
4. 宿主**追加一张** `source: light` 的票到 `TICKETS.md`，不重跑 `to-tickets`、不重排已有票。
5. 新票复用现有票流程：实现 → 审查 → 合并进冻结分支 → 幂等重提测。
6. 不改 `phase`、不清契约、不拆任何已有 worktree。
7. 写结构化变更记录（`STATUS.yaml changes[]`），看板可见时间线与「下一步」。

### 非目标

- **不做方案级**：不重开已完成的票、不删除票、不做票级 diff/影响报告、不级联失效契约与提测（§14）。
- 不重跑 `to-tickets`，不自动重排/改名已有票（含 `B` 票）。
- 不回滚已合并进冻结分支的代码，不 force-push。
- 不改 `phase` 枚举，不引入需求版本分支。
- 不在变更里修改 SPEC 的跨仓契约段（§6）。
- 不重构 `open`/`reset-phase` 的既有语义。

## 3. 入口与动作

注册进 `actions.py:22 BOARD_ACTIONS`：

```python
ActionSpec("change", "轻量变更")   # 唯一新增动作
```

看板交互（`RequirementView.vue`）：顶栏动作区新增「轻量变更」，开对话框：

- 变更说明 textarea（必填）。
- **仓下拉**（必填，单选）：选项**只列该需求已冻结的仓**，即 `reqs/<JIRA>/worktrees/<alias>` 存在的 alias ——不是 `repos.yaml` 全部登记仓（原因见 §8）。
- 「需要澄清（跑一轮 grill）」勾选（默认否）。
- 「创建后立即实现」勾选（默认否）。

CLI：

```
dev-yard req change <key> --note "..." --repo <alias> [--grill] [--run]
dev-yard req changes <key>            # 打印变更记录（只读）
```

门控（`reqboard.available_actions`）：

- `change`：`has_worktrees and phase in {"frozen","testing"}`。
  - **不含 `done`**：`done` 意味着测试已通过（`pipeline_complete` 要求 `phase==done` 且 `test_passed`，`status.py:154-157`）。此时追加票会让 `tickets_done=false`，但 `can_fill` 因 `not test_passed` 为假、`can_submit` 也因 `passed` 为假（`reqboard.py:283-293`），变更将卡死无出口。`done` 后要改属于方案级/另开需求。
- reason：无 worktree → 「需要先 freeze 创建 worktree」；`phase=open` → 「直接改文档后重跑对齐/写规约/拆票即可」；`done` → 「已完成的需求改动属于方案级，本次不支持」。

## 4. 变更记录模型（`STATUS.yaml`）

顶层 `changes` 列表，追加式：

```yaml
changes:
  - id: c1
    at: "2026-09-21T10:00:00+08:00"
    actor: web | cli
    note: "补一条规则：外聘联系人可同时挂靠"
    repo: research-front
    ticket: T8                     # 本次追加的轻量票
    grilled: false
    docs_before:                   # 变更前文档指纹（§9）
      requirement: sha256:ab12...
      spec: sha256:cd34...
      tickets: sha256:ef56...
    contract_touched: false        # SPEC 契约段是否被改动（§6）
    stale:
      qa: true                     # 文档变了，用例需复核（§5.6）
```

- 写入时机：整个 `change` job 成功结束时一次写全。
- `dev-yard req changes <key>` 与看板「变更记录」时间线读它（渲染 `at/note/repo/ticket`）。
- `status.py` 目前没有 `changes` 辅助，需新增（读写与 `tickets_map` 同风格）。

## 5. 流程

新增 `service.req_change(root, jira, note, *, repo, grill=False, run=False, on_progress=None, runner_factory=None) -> dict`。

```
1. 校验（短持 jira_lock）
   - 需求存在；phase in {frozen, testing}
   - note 非空且消毒后非空（§5.1）
   - repo 是该需求已冻结的仓：paths.req_worktree(root, jira, repo) 是目录（paths.py:68）
   - old_fp = doc_fingerprint(req)（§9）
2. 追加变更记录到 REQUIREMENT.md（宿主，§5.1）
3. 可选 grill（§5.2）
4. run_stage("spec", prompt_extra=<变更约束>)（§5.3）
5. 追加一张轻量票到 TICKETS.md（宿主，§5.4）
6. st.sync_tickets + refresh_ready（新票落 ready）
7. 标记 QA 用例待复核（§5.6）
8. 写 changes[]，st.save
9. 可选 run：立刻 service.implement 实现新票
```

### 5.1 追加变更记录（宿主）

在 `REQUIREMENT.md` 末尾追加（不存在则先建 `## 变更记录` 段，永不改原文其它内容）：

```markdown
## 变更记录

### c1 · 2026-09-21 10:00 (web)
补一条规则：外聘联系人可同时挂靠

- 仓：research-front
- 票：T8
```

- **note 消毒**（关键）：`note` 会同时进入 REQUIREMENT.md 与 TICKETS.md，必须清洗——去掉/转义换行（压成空格）、去掉行首的 `#`、`-`、`## T`/`## B`（防止伪造票标题被 `parse_tickets` 的 `HEADING` 正则命中，`tickets.py:21`）、限制长度。仓库现有 `web/sanitize.py` 面向 HTML 渲染，不适用于这里，需新写一个纯文本消毒函数。
- 写入用「读原文 → 追加 → 临时文件 + `os.replace`」原子写（`st.save` 的写-改-rename 只服务 STATUS.yaml）。
- 幂等：同一 note 不重复追加。

### 5.2 可选 grill

- `grill=True` 时跑 `grill` 阶段。
  - CLI：`run_stage("grill", prompt_extra=note)`，默认交互 TUI。
  - Web：复用 `web/jobs.py:_run_web_grill`（`web/jobs.py:666`）。**注意**：它内部的 `extra` 来自 `grill_round.web_grill_extra(jira)`（`grill_round.py:15-20`），当前**无法**注入变更说明；需扩展 `web_grill_extra(jira, note="")` 并让 `_run_web_grill` 接一个 `note` 参数，否则 Web grill 不会知道本次变更是什么。
- 顺序：**先写变更记录再 grill**。grill 开局读 `REQUIREMENT.md`，先 grill 再改会让本轮对齐基于旧文本。
- `grill` 会往 `GRILL.md` 追加 round；`MAX_ROUNDS=12`（`grill_round.py:12`）。

### 5.3 spec 阶段

- `run_stage("spec", prompt_extra=<变更约束>)`（`run_stage` 支持 `prompt_extra`，`service.py:787,810-811`）。约束文案要求 agent：只做本变更的增量更新、不重写全文、不动无关章节、不新增跨仓契约。
- `spec` 现有 `protects=("TICKETS.md",)`（`stages.py:147-155`）→ **扩为 `("REQUIREMENT.md","GRILL.md","TICKETS.md")`**，否则 spec agent 可能顺手改写需求原文，抹掉 §5.1 的变更记录。
- 同理 `grill` 现 `protects=("SPEC.md","TICKETS.md")`（`stages.py:138-146`）→ **补 `REQUIREMENT.md`**（技能虽声明只读，但保护要靠 `protects` 强制，`run_stage` 只回滚 `protects` 列出的文件，`service.py:831-836`）。
- `spec`/`grill` 的 `requires_phase` 均为 `None`（`stages.py:54`），在 `frozen`/`testing` 下都能跑。`spec` 的 `sets_phase` 为 `None`，不会改 phase。
- 不动 `contract_review`（`run_stage` 只写 `stage_runs` 与 `sets_phase`，`service.py:840-853`）。

### 5.4 追加轻量票（宿主）

宿主直接向 `TICKETS.md` **追加**一张票，不调 `to-tickets`：

1. 分配 id：`tickets.next_ticket_id(parsed)`（现有 max(`T\d+`)+1；`HEADING` 只认 `T\d+`/`B\d+`，`tickets.py:21`）。
2. 落盘前 `_snapshot(req, ("TICKETS.md",))` 备份，追加失败即 `_restore` 回滚。
3. 追加票块：

```markdown
## T8: 轻量变更：外聘联系人可同时挂靠
- repo: research-front
- depends_on:
- parallel: false
- source: light
- change: c1

来自轻量变更 c1。变更说明：<消毒后的 note>。
详见 REQUIREMENT.md「变更记录 c1」与 SPEC.md 对应段落。
验收：<可留空，由实现阶段补齐>
```

4. `Ticket` 新增 `change: str = ""`，`parse_tickets` 增加 `change` 分支（`tickets.py:10-18,41-50`；今天 `change:` 会被静默忽略）。
5. 追加是纯文本操作，不触碰任何已有 `## T*` / `## B*` 段。

### 5.5 实现与收尾

- 轻量票**无特殊实现分支**：普通票，`service.implement`（`service.py:1115`）按常规起 agent。
- 票级「实现/审查」只认票状态，不由 phase 门控：`claim_run` 只看 `slot["state"]`（`service.py:1076-1098`），`ctx.submit_action` 对 implement/review 无 phase 检查（`web/context.py:143-189`）。因此在 `testing` 下新票仍可正常实现与审查。
- 审查通过 → `_ticket_done_locked`（`service.py:569`）合并进冻结分支。
- **合并后的门控变化**（新票存在期间 `tickets_done=false`，`reqboard.py:265`）：
  - 看板 `submit-test`（`reqboard.py:283-288`）、`run-test`（`:334-340`）、`qa-review`（`:306-313`）都被禁用；这是**期望行为**——代码没实现就不该跑/审用例或提测。
  - `fill-test-report`（提 bug）板级**不**受 `tickets_done` 限制（`can_fill` 只看契约与 phase，`reqboard.py:289-293`）；但 service 侧 `accept_test_report(passed)` 会再校验 `all_done`（`test_report.py:262-265`），所以「通过」不会漏掉未完成的轻量票。`failed`/`blocked` 仍可提（用于提 bug）。
- 合并后冻结分支 head 变化 → `test_integrate.has_new_changes` 比较 `freeze` head 与 `integration[alias].freeze_sha`（`test_integrate.py:184-209`，比较在 `:200,207`）→ `submit-test` 可幂等重入。

### 5.6 文档变更后的 QA 处理

文档变了，已有用例可能过时。当前没有任何「用例相对 SPEC 过期」的机制，需要**新增**：

- `qa_review.py` 的 `ReviewState`（`:25-30`）、`save_review`（`:85-98`）、`load_review`（`:73-82`）、`review_payload`（`:101-132`）都要加可选 `stale_reason`（否则下一次 `approve`/`reject` 重建 4 键时会静默丢弃）。
- 语义：`stale_reason` 非空时 `review_payload.approved = False`（与 `stale` 同效），`review_gate` 返回「需求已轻量变更，请复核用例」（`qa_review.py:135-147`）；`approve_cases`/`reject_cases` 清空它。这样 `run-test` 会被 service 侧 `review_gate` 拦住（唯一调用点 `qa.py:1263`），直到人工重新审核或 `--redesign`。
- 仅**告警不拦**是不够的：`reqboard` 的 `run-test` 按钮并不调用 `review_gate`，真正拦截在 `qa.py`；若只写提示不改 `approved`，用例过期仍能跑。
- 变更记录 `changes[].stale.qa` 标 true；看板「测试」tab 提示「需求已轻量变更，请复核用例（`dev-yard req test --redesign`）」（命令与语义已确认：`cli.py:668-677`，`qa.py:1213-1248`）。
- 契约：默认不动。若 §6 检测到契约段被改，只置 `contract_touched=true` + 看板告警，不自动清契约（第一刀）。

## 6. 约束：轻量变更不得改契约

- 对话框与 CLI 帮助明确：**只改描述、规则、验收、文案等非契约内容**；涉及接口/字段/时序的新增或变更，应另开需求或走后续方案级。
- 宿主在 `spec` 后检测：比对变更前后 `SPEC.md` 中「Implementation Decisions」段落（含 API/事件/字段契约）是否改动。有则 `changes[].contract_touched=true` + 看板黄条告警「本次变更触及契约段，`contract_review` 可能失效，建议重跑契约审查」，**不自动清空**。

## 7. 与 QA / qa-powers 的关系

- 本设计只对接 **dev-yard 自带 QA 栈**：`.pi/skills/qa-design`、`.pi/skills/qa-run`（注册为专用阶段，`stages.py:26,187-196`）、`qa.py`/`qa_review.py`/`qa_board.py`/`qa_doc.py`。产物在 `reqs/<JIRA>/qa/`。
- `qa-powers`（`/home/chengpeng/rcc/qa-powers`）是**独立参考实现，不是运行时依赖**；`.qa-powers/` 目录在本仓库不存在，运行时也不会调用（port 文档：`docs/superpowers/specs/2026-09-20-yard-qa-port-from-qa-powers.md`）。因此本设计不引用、不修改 qa-powers，只用其对应概念（用例审核门、run/report）在 dev-yard 内的等价物。
- `qa-design` 只读 `REQUIREMENT.md/SPEC.md/TICKETS.md`（`.pi/skills/qa-design/SKILL.md:14`）并从 freeze worktree diff 设计用例；文档变更后应重新设计/复核，故 §5.6 的 stale 机制是必要的。

## 8. 失败语义与边界

| 场景 | 行为 |
|---|---|
| `phase=open` / 无冻结 worktree | 门控拒绝，提示直接改文档重跑阶段或先 freeze |
| `phase=done` | 门控拒绝（§3，会卡死无出口） |
| `repo` 不在该需求已冻结仓内 | 校验失败。**必须限制**：`ticket_start` 会因缺父 worktree 报错（`service.py:535-536`）；且 `sync_tickets` 会把它写进 `data["repos"]`（`status.py:121`），导致契约审查（`service.py:1403-1406`）与 `req test`（`qa.py:368-375`）因缺 worktree 直接失败 |
| note 为空 / 消毒后为空 | 参数校验失败，不落任何写操作 |
| note 含换行 / `## T*` / 行首 `-` | 消毒后再写入（§5.1），防止伪造票标题 |
| grill/spec 阶段失败 | 保留已完成的文档写入（变更记录/GRILL/SPEC），**不**追加票、不写 changes、不标 QA；可重跑 |
| 追加票写入失败 | `_restore` 回滚 TICKETS.md，不写 changes |
| 契约段被改 | 不阻断；`contract_touched=true` + 看板告警（§6） |
| QA 无用例 | 跳过 stale 标记，`stale.qa=false` |
| 变更与正在运行的 job 冲突 | `jobs.submit` 的 `_jobs_conflict`（`web/jobs.py:896-918,830-834`）拦截：`change` 非票级动作，作用域为整需求，任何同 jira 运行中 job 都会拒绝——安全但会串行化 |
| 之后有人跑 `dev-yard open --force` 重抽 | 会覆盖 `REQUIREMENT.md`（`service.py:298-301,307,313,317-320`），变更记录段丢失；本次不解决，README 注明 |
| 之后有人重跑 `dev-yard tickets` | `to-tickets` 可能重排已有票、留孤儿（`status.py:108-122`）；第一刀靠文档约定「轻量变更后不要重跑 tickets 阶段」，后续可考虑在 tickets 前加保护 |

## 9. 文档指纹

新增 `service.doc_fingerprint(req) -> dict[str, str]`：对 `REQUIREMENT.md / GRILL.md / SPEC.md / TICKETS.md` 计算 `sha256:...`（缺失为 `null`）。用于 `changes[].docs_before`。参考 `qa_review.cases_fingerprint`（`qa_review.py:33-60`）的「记录指纹」模式。看板「文档被外部手改」提示为 P2，不在第一刀。

## 10. CLI / Web / 看板改动

### CLI（`cli.py`）

- `req change <key> --note <text> --repo <alias> [--grill] [--run] [--print]`
- `req changes <key>`（只读）

结尾打印「已追加变更记录 c1、SPEC 已更新、新票 T8；下一步 `dev-yard implement <key> T8`」。

### Web（`web/jobs.py`）

- `change` job 分支：调 `service.req_change(...)`；`grill=True` 时复用 `_run_web_grill`（需按 §5.2 扩展以携带 note）。
- `ActionIn`（`web/schemas.py:55-67`）增加 `note: str`、`repo: str`、`grill: bool`、`run: bool`，并在 `web/routes/api_reqs.py:297-309` 的 `extra` 里透传。
- `change` 需在 `default_execute`（`web/jobs.py:421`）加显式分支（它会进 `_HOST_JOB_ACTIONS`，不会走通用 `bundle` 回退）。

### 看板（`reqboard.py` / `RequirementView.vue`）

- 新增 `change` 动作与门控（§3），仓下拉由「已冻结仓」填充。
- `ReqDetail` 增加 `changes: list[dict]`；渲染变更时间线 + 新票高亮。
- `contract_touched` 与 QA stale 复用现有告警横幅（`RequirementView.vue:153-230`）。

## 11. 待实现文件清单

| 文件 | 改动 |
|---|---|
| `actions.py` | `BOARD_ACTIONS` 增加 `change` |
| `service.py` | 新增 `req_change`、`doc_fingerprint`、`_append_change_note`、`_sanitize_note`；追加票辅助 |
| `status.py` | `changes` 读写辅助 |
| `tickets.py` | `Ticket.change` 字段 + 解析；`next_ticket_id`；`append_light_ticket` |
| `stages.py` | `spec.protects` 加 `REQUIREMENT.md`,`GRILL.md`；`grill.protects` 加 `REQUIREMENT.md` |
| `qa_review.py` | `stale_reason` 贯穿 `ReviewState`/`load`/`save`/`review_payload`，并让 `approved=False` |
| `grill_round.py` | `web_grill_extra(jira, note="")` |
| `cli.py` | `req change`、`req changes` |
| `web/jobs.py` | `change` 分支；`_run_web_grill` 接受 note |
| `web/routes/api_reqs.py` / `web/schemas.py` | `ActionIn` 新字段透传 |
| `web/src/views/RequirementView.vue` | 变更对话框 + 时间线 + 告警 |
| `web/src/api/types.ts` / `client.ts` | 新动作与 `changes` 类型 |
| `README.md` / `AGENTS.md` | 轻量变更语义、CLI 速查、`reset-phase` 分工、`to-tickets` 保护提示 |

## 12. 测试计划

- `tests/test_change.py`（新）：
  - 门控：`frozen`/`testing` 可建；`open`/`done` 拒绝。
  - 仓校验：未冻结的 alias 被拒；已冻结通过。
  - 变更记录：追加到 REQUIREMENT.md，原文不变；重复 note 幂等。
  - note 消毒：含 `\n`、`## T9: x`、行首 `- ` 不产生伪造票、不破坏 REQUIREMENT.md。
  - 建票：`source: light` + `change: c1`，id = max(T..)+1，已有 `T*`/`B*` 不变，新票 `ready`。
  - `--run`：复用 `service.implement`（桩 runner）→ `implemented`；桩审查通过 → 合并进冻结分支、票 `done`。
  - `testing` 下建票：`submit-test`/`run-test`/`qa-review` 禁用，票级 implement 仍可跑；合并后 `has_new_changes` 为真。
  - `accept_test_report(passed)` 在轻量票未完成时被拒（`test_report.py:262-265` 回归）。
  - spec/grill 失败 → 不建票、不写 changes、不标 QA。
  - 契约段被改 → `contract_touched=true`（不改契约状态）。
- `tests/test_qa_review.py`：新增 `stale_reason` 后 `approved=False`、`review_gate` 返回原因；`approve/reject` 清空 stale_reason。
- `tests/test_tickets.py`：`change` 字段解析、`next_ticket_id`、追加不破坏已有票。
- `tests/test_board.py`：`change` 门控与 reason（含 `done` 拒绝）。
- `tests/test_freeze.py` / `test_req_delete.py`：`protects` 扩展后既有行为不回归。
- 前端：变更对话框与时间线（若前端测试基建具备）。

## 13. 已定决策

| # | 决策 |
|---|---|
| 1 | 本次**只做轻量变更**；方案级不做（§14） |
| 2 | `REQUIREMENT.md` 只**追加「变更记录」段**，不改原文 |
| 3 | 顺序：**追加变更记录 → （可选）grill → spec → 追加轻量票**（先改 requirement 再 grill） |
| 4 | `grill` 可选，只写 `GRILL.md`；改 requirement 由宿主做；Web 需扩展 `web_grill_extra` 以携带 note |
| 5 | **不重跑 `to-tickets`**，宿主追加**一张** `source: light` 票 |
| 6 | 仓下拉**只列该需求已冻结的仓**（否则 `ticket_start`/契约审查/`req test` 都会因缺 worktree 失败） |
| 7 | `change` 门控为 `phase in {frozen, testing}`，**排除 `done`**（done 下会卡死无出口） |
| 8 | 不改 `phase`、不清契约、不拆 worktree；`testing` 下靠新票让 `tickets_done=false` 禁用 submit-test/run-test/qa-review，合并后幂等重提 |
| 9 | 轻量票是普通票，实现/审查/合并全复用，不加特殊分支 |
| 10 | 轻量变更不得改 SPEC 契约段；检测到改动只告警（`contract_touched`），不自动清契约 |
| 11 | QA 侧新增 `stale_reason` 并让 `approved=False`（**净新代码**），真正拦住 `run-test`，而非仅提示 |
| 12 | note 必须消毒后再写入 REQUIREMENT.md / TICKETS.md |
| 13 | `protects` 扩展：`spec` 加 `REQUIREMENT.md`/`GRILL.md`，`grill` 加 `REQUIREMENT.md` |
| 14 | 写 `STATUS.yaml changes[]` + 看板时间线；`docs_before` 用 `doc_fingerprint` |
| 15 | 只对接 dev-yard 自带 QA 栈，不涉及 qa-powers（外部参考实现） |
| 16 | **变更 id 由 `REQUIREMENT.md` 变更记录推导**（不是 STATUS），追加变更记录与最终建票+写 STATUS 各在一把 `st.jira_lock` 内完成；**同一 note 幂等**（重试复用同一 `cN` 与同一张轻量票，`changes[]` 按 id upsert），CLI/服务直调也不会串号 |
| 17 | `TICKETS.md`/`REQUIREMENT.md` 一律原子写（`fsutil.atomic_write_text`，tmp+rename），`_restore` 亦改为原子写 |
| 18 | 契约段识别同时匹配 `## Implementation Decisions`（to-spec 模板）与 `## Contracts`（内置 skeleton），行首精确匹配 |
| 19 | QA 标记在最终写 STATUS **之前**执行且失败即报错；`--redesign` 走 `clear_stale` 清掉 `stale_reason`（保留原指纹，必要时仍强制复审） |

## 14. 后续（本次不做）

方案级变更——在已完成票上做增量重规划：重跑 spec/tickets、票级 diff 与选择性重开、删除票的 worktree/分支清理、级联失效契约与提测、agent 影响报告。建议基于本设计的 `changes[]` 扩展 `kind=plan`，新增独立「变更方案」入口。另：`done` 后的需求变更、`open --force` 重抽保留变更记录、重跑 `to-tickets` 的保护，都在方案级一并处理。
