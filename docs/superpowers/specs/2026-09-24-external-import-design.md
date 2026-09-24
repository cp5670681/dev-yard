# 外部需求 / 外部代码分支直接进测试 设计草案

- 日期：2026-09-24
- 状态：草案（决策见 §7，待实现）
- 范围：新增「外部导入」入口——只给**需求文档 + 每仓一个代码分支**，直接在 freeze worktree 里落代码、直达 `testing`，后续走现成 QA / B 票流程
- 影响命令：新增 `dev-yard req import`；Web 需求列表「导入需求」
- 关联：`src/dev_yard/service.py`、`cli.py`、`gitops.py`、`status.py`、`tickets.py`、`test_report.py`、`qa.py`、`reqboard.py`、`actions.py`、`web/jobs.py`、`web/routes/api_reqs.py`、`web/schemas.py`、`.pi/skills/qa-design/SKILL.md`

## 1. 背景与问题

有人已经在平台之外写好了代码并推到远端，想拿 dev-yard 的 QA 能力（设计/执行用例、失败下 bug、`implement --from-test` 修票）跑测试、改 bug。现状只能靠手工拼命令：

```
req open → 手写 TICKETS.md → req freeze → 在 worktree 里手工 fetch/merge 外部分支
        → review-override --contract + 逐票 review-override → req submit-test → req test …
```

这条路每一步都能走通，但没有任何入口把它固化，容易漏、容易把分支合错（见 §3 的 `worktree_add` 重置陷阱）。

**关键洞察**：测试阶段跑的是 `qa.yaml` 里**已部署环境**（`qa.py:99` `write_context_md` 的 env/base_url），**代码来源从来不被校验**；卡人的只是 `req test` 入口的几道流程门禁。因此本设计不新建运行模式，而是把上面那串手工命令固化成一条 `dev-yard req import`，内部仍复用 `req_open` / `req_freeze` / `submit_test` / QA。

### 与「轻量变更」的区别

| | 轻量变更 `req change` | 外部导入 `req import` |
|---|---|---|
| 时机 | 已 frozen/testing 的需求再改 | 需求尚未在平台流转 |
| 代码来源 | 平台 `implement` | **外部已有分支** |
| 产出票 | 追加一张 `source: light` | 每仓一张 `source: import`（done） |
| 契约审查 | 保留，动契约只告警 | 无 SPEC，直接置 `passed` |
| phase | 不动 | `open → testing` 直达 |

## 2. 目标与非目标

### 目标

1. 一条命令完成：需求文档落 `REQUIREMENT.md`；每个仓建 freeze worktree 并检出**外部分支**；phase 直达 `testing`。
2. 跳过 `grill`/`spec`/`tickets`/`contract` 四个阶段，但**不**跳过 freeze worktree 与 QA 环。
3. `submit-test` / `push` / B 票 `implement --from-test` / QA 设计-审核-执行**全部零改动**可用。
4. 幂等可重入：外部分支前进后可更新 freeze，不动已有 QA 用例。
5. diff 基线正确：外部代码的改动口径以 merge-base（或 `--base`）为准，qa-design 不把无关上游提交当成本需求改动。

### 非目标

- 不产出 SPEC 正文（只留 skeleton，见 §7 D3）。
- 不引入新的运行模式 / 不改 `phase` 枚举 / 不改状态机。
- 不重跑 `to-tickets`；导入票由宿主直接写。
- 不做「同一需求分批导入不同仓」的复杂编排（第一刀要求一次给全；见 §11）。
- 不自动解决外部分支与 `test_branch` 的语义冲突（冲突仍交 `submit-test` 现有 AI 消解）。

## 3. 关键约束（已核对现有实现）

下游到底依赖什么，决定 import 必须造出哪些标记：

| 依赖 | 使用点 | import 必须满足 |
|---|---|---|
| `reqs/<JIRA>/` + `REQUIREMENT.md` | qa-design 必读、`_gate`（`qa.py:422`）、`review` | `req_open` 产出 |
| `phase == testing` | `_gate` | `submit-test` 后置 |
| `contract_review == passed` | `_gate`、`_assert_submittable`（`test_report.py:72`） | 需绕过（§6.3） |
| 每仓 ticket 且全 `done` | `st.all_done`（`status.py:147`）、`target_repos`（`test_integrate.py:151`）、`_involved_aliases`（`qa.py:308`） | **每仓造一张 done 票** |
| freeze worktree 存在 | `_gate`、qa-design、B 票 `ticket_start` | `req_freeze` 建 |
| `data["branch"]`=freeze 分支 | `submit_test`、`push`、`test_integrate._freeze_branch`（`test_integrate.py:174`） | 保持单一 `tom/{jira}` |

要点：`data["branch"]` 是所有仓**同名**的一个分支（`config.py:248` `render_freeze_branch`，当前 `repos.yaml` 为 `tom/{jira}`）。每仓是独立 git 仓，同名不冲突。只要把外部分支收进各仓的 `tom/{jira}`，`submit-test`/`push`/B 票**全部零改动**。

陷阱：`gitops.worktree_add`（`gitops.py:179`）在「路径不存在但同名本地分支已存在」时会 `git branch -f <branch> <start_point>` 把该分支**重置**到 `origin/default_base`（`gitops.py:205`）。所以外部分支若恰好叫 `tom/{jira}`，**不能**先手工建同名本地分支再 freeze；正确做法是让 import 在 fetch 后从外部 tip 建立 freeze 分支。

## 4. 入口与命令

### CLI

```
dev-yard req import <JIRA|URL|文件|文本> \
  --branch research:origin/feature/xxx \
  --branch research-front:origin/feature/yyy \
  [--base research:origin/master]        # 可选，显式 diff 基线（缺省 merge-base）
  [--key K] [--text T] [--file F]        # 透传给 req open 的来源参数
  [--no-submit]                          # 默认导入完自动提测
  [--update]                             # P1：已导入时只把外部分支 ff/merge 进 freeze
  [--force]                              # 重置已存在的导入（含 worktree）
  [--print]
```

- 需求文档来源复用 `req open` 的 `source`（`service.py:239`）：Jira/Confluence 走 open agent 抽产品说明；`--file`/`--text`/URL 直接写。
- `--branch alias:ref`：`ref` 可以是 `origin/xxx`（先 `git fetch` 后解析）或任意本地 ref。冒号按**第一个**切分，允许 ref 含 `/`。

### Web

需求列表页新增「导入需求」按钮，复用 `POST /api/open` 的表单（`web/routes/api_reqs.py:323`、`web/schemas.py:14` `OpenIn`）再加一组「仓 → 分支」输入，提交为新的 job action。

## 5. 流程

新增 `service.req_import(root, jira, *, source, target, payload, branches, bases, submit=True, update=False, force=False, on_progress=None, runner_factory=None) -> dict`。

```
1. 校验参数（短持 jira_lock）
   - branches 非空；每个 alias ∈ repos.yaml（load_repos）
   - ref 与 base 语法合法
2. req_open(...)（若 req 已存在且非 --force/--update 则拒绝）   ← 复用 service.py:239
   产出 REQUIREMENT.md / GRILL.md / SPEC.md(skeleton) / TICKETS.md(skeleton)
3. 宿主写 TICKETS.md：每仓一张票（§6.1）
     ## T1: 外部导入 — <alias>
     - repo: <alias>
     - depends_on:
     - parallel: false
     - source: import
4. req_freeze(start_refs={alias: ref})（§6.2）  ← service.py:422 的扩展
   fetch 母仓 → 校验 ref → freeze 分支 tom/{jira} 建在该 tip → 检出 worktree
   base_shas[alias] = --base 给定值 or merge-base(ref, origin/<default_base>)
5. 收尾打标（短持 jira_lock）
   - 每票 slot.state = "done"、slot.worktree = freeze worktree
   - contract_review = "passed"（复用 contract_review_override，service.py:2244）
     summary = "imported: 外部代码导入，跳过契约审查"
   - STATUS 顶层写 imported = {at, actor, branches, bases}（审计用）
   - phase = "frozen"
6. submit=True：submit_test(...)（test_report.py:93）→ phase=testing
7. 返回 {tickets, branch, phase, integration}
```

之后完全走现成流程：

```
dev-yard req test <JIRA> --design-only → 人工 --approve → --run-only
失败 → 失败卡片「下 bug」/ `req triage` → dev-yard implement <JIRA> --from-test
（--from-test 会把 phase 从 testing 翻回 frozen，修完 submit-test 幂等重提，天然闭环；
  B 票 worktree 由 ticket_start 从 freeze 分支派生，即外部分支内容）
```

## 6. 兼容点改造

### 6.1 导入票生成（宿主）

- 用 `tickets.py` 的解析/追加惯例（`next_ticket_id` `:69`、`append_light_ticket` `:95`；新增一个同风格的 `format_import_ticket`/`append_import_tickets`）。
- 每仓一张，`source: import`。`import` 不被任何 `implement`/`review` 过滤器特判（现有只特判 `{"contract","test","light"}`），所以导入票是「普通 done 票」，不会被误实现。
- 票块示例：

```markdown
## T1: 外部导入 — research
- repo: research
- depends_on:
- parallel: false
- source: import

来自外部导入：分支 origin/feature/xxx（diff 基线 origin/master）。
代码已存在于该分支，本票仅用于满足下游按仓/按票的流程门控，不触发实现。
```

- 写票前 `_snapshot(req, ("TICKETS.md",))` 备份，失败即回滚。

### 6.2 `req_freeze` 扩展 `start_refs`

只加一个可选入参，普通路径传空 dict 行为不变：

```python
def req_freeze(root, jira, force=False, *, start_refs: dict[str, str] | None = None, base_refs: dict[str, str] | None = None):
```

- `start_refs` 命中时：`start = gitops.start_point(source, repo.default_base)` 换成校验收到的外部 ref；`base_shas[alias]` 记 `--base` 或 `merge_base(source, start, origin/<default_base>)`，而非当前写死的 `origin/default_base` tip（`service.py:460-465`）。
- `gitops.freeze_base`（`gitops.py:300`）已优先用 `base_shas`，因此 `review --contract`（`service.py:1999` 附近已用 `freeze_base`）、`bug_tickets` 的 finding 归属过滤（`bug_tickets.py:199`）**自动**拿到正确基线。

### 6.3 契约门禁置 `passed`

复用 `contract_review_override`（`service.py:2244`）写 `contract_review="passed"` + `contract_summary="imported: …"`，零改动 `_gate` / `_assert_submittable`。看板「契约审查」会显示 passed，summary 注明来源，可审计。

### 6.4 qa-design 的 diff 基线口径（P0，必须改）

现状 `qa-design` SKILL 写死 `git diff <default_base>...HEAD`（`.pi/skills/qa-design/SKILL.md:17,55`），宿主 `context.md` 也只打印 `base <default_base>`（`qa.py:99`），**不读 `base_shas`**。这里有两个问题：

1. **本地 `default_base` 可能落后**：`gitops.fetch` 只做 `git fetch --all --prune`（`gitops.py:158`），**只更新 `origin/<base>`，不更新本地 `<base>`**；而 SKILL 里的 `<default_base>` 是配置字符串（如 `master`），git 解析的是**本地 `master`**。分支从较新的 `origin/master` 切出而本地 `master` 落后时，`master...HEAD` 的 merge-base 会落在落后的本地 master 上，把 `本地master..分叉点` 这段继承来的 master 提交也算成本次改动（多设计无关用例）。**这是既有隐患，普通 freeze 流程同样存在**，不是导入独有。
2. **`--base` 只生效一半**：给了 `--base` 后端会存进 `base_shas`，但 SKILL 不读，agent 仍用 `default_base`，两边口径不一致。

改法（小改）：
- `write_context_md` 每个 worktree 增打 `diff_base <sha|ref>`，来源 `gitops.freeze_base(wt, repo.default_base, data["base_shas"].get(alias))`（该函数已优先用 `base_shas`，回退 `origin/<base>` 的分叉点）。
- qa-design SKILL 改为「以 `context.md` 的 `diff_base` 为准做 `git diff <diff_base>...HEAD`；没有则回退 `default_base`」。

> 结论：即使所有分支都从主分支切，也不该省这一步——它同时消掉「本地 base 落后」与「`--base` 假生效」两个问题，且对普通需求是「更准」，不改变语义方向。因此 §4 的 `--base` 参数**只有在做本节时才有意义**；不做则不应暴露 `--base`。

## 7. 决策（已确认）

| # | 决策 | 选择 |
|---|---|---|
| D1 | 契约门禁 | **直接置 `passed`**（复用 override，summary 标注），不新增 `skipped` 状态 |
| D2 | 导入后是否自动提测 | **默认自动**（`submit_test`）；`--no-submit` 可停在 frozen |
| D3 | SPEC | **只写 skeleton**，不跑 agent；REQUIREMENT + 代码 diff 为准，歧义走 `qa/OPEN-QUESTIONS.md` |
| D4 | diff 基线 | **自动 `merge-base(外部分支, origin/<default_base>)`**，`--base` 可覆盖 |
| D5 | qa-design 基线口径 | **P0 顺手做**：`context.md` 增 `diff_base`（§6.4），SKILL 改用它；同时消掉「本地 base 落后」既有隐患与 `--base` 假生效 |

## 8. 失败语义与边界

| 场景 | 行为 |
|---|---|
| 已有 `reqs/<JIRA>/` 且非 `open` | 拒绝，提示 `--force` / `--update` / `req reset-phase` |
| alias 不在 `repos.yaml` | 参数校验失败，不落任何写 |
| ref 在母仓（本地+远端）都解析不到 | freeze 前失败，已建的需求目录保留，不写票/不置 testing |
| 外部分支 == 目标 `test_branch` | `submit_test.has_new_changes` 判无新改动 → 直接进 testing，不重复 merge/push |
| 仓无 `test_branch` | `eligible_repos` 自动跳过（`test_integrate.py:169`），该仓仅留在 freeze，不提测 |
| 重复 import（`--update`） | 只 `git fetch` + `git merge --ff-only`（或 `merge`）外部 ref 进 freeze 分支；**不动 QA 用例、不动 phase、不动票**；若 QA 已 `approved` 则按「轻量变更」思路标用例待复核（P2，见 §11） |
| 重复 import（`--force`） | 拆 worktree、重置票/契约（含 `contract_findings`）、重跑全流程；**不清** `qa/cases`，若有则告警提示 `req test --redesign` |
| 之后有人跑 `req tickets` | `to-tickets` 会重写整份 TICKETS.md、重排/留孤儿（`status.py:108-122`）；文档约定「导入后不要重跑 tickets」，P2 再考虑保护 |
| 之后有人跑 `req open --force` | 覆盖 REQUIREMENT.md（`service.py:298-320`）；与轻量变更同款已知限制，README 注明 |
| `req_freeze --force`（testing/done） | 会把 freeze 分支重置回基线、丢掉外部内容；文档提示导入需求不要用 `--force` 重新 freeze |
| 外部分支基于非 `default_base` 的发布分支 | 用 `--base alias:<release-branch>` 指定；否则 merge-base 可能过旧导致 diff 偏大 |

## 9. CLI / Web / 看板改动

### CLI（`cli.py`）
- `req import <target> --branch ... [--base ...] [--no-submit] [--update] [--force] [--print]`
- 结尾打印：「已导入 N 票（全部 done）、freeze=tom/<JIRA>、phase=testing；下一步 `dev-yard req test <JIRA> --design-only`」。

### actions（`actions.py`）
- `BOARD_ACTIONS` 增 `ActionSpec("import", "外部导入")`（`actions.py:22`）。
- `HOST_JOB_ACTIONS`/`JOB_ACTIONS` 自动包含（从 `BOARD_ACTION_IDS` 派生），在 `web/jobs.py:default_execute` 加 `if job.action == "import": …`（对照 `open` 分支 `jobs.py:498`、`freeze` 分支 `:557`、`submit-test` 分支 `:602`）。

### Web
- `schemas.py` 加 `ImportIn`（`OpenIn` + `branches: dict[str,str]` + `bases` + `submit` + `remote` + `force`）。
- `api_reqs.py` 加 `POST /api/requirements/import`，提交 `import` job（`known_action` 由 `BOARD_ACTIONS` 派生，加 `ActionSpec("import", "外部导入")` 即可）。
- 前端：`web/src/views/ImportView.vue` + 路由 `/import` + 侧栏「外部导入」入口；需求详情页 `/r/:jira` 复用现有跳转。
- 看板 `available_actions`（`reqboard.py:302`）**不加** per-req 按钮：导入是「造需求」入口，不是对已有需求的操作。

## 10. 测试计划

- `tests/test_service.py`
  - `req_import` 建票/建 worktree/base_shas/phase=testing 的 happy path（用临时 git 仓 fixture，构造 `origin/feature` 与 `origin/master` 分叉）。
  - `--no-submit` 停在 frozen；`--update` 只动 freeze 分支；`--force` 重建。
  - alias/ref 非法 → 拒绝且不部分落盘。
- `tests/test_tickets.py`：`source: import` 解析、`append_import_tickets` 不破坏已有 `## T*`/`## B*`。
- `tests/test_qa.py`：`write_context_md` 输出 `diff_base`，且等于 `freeze_base`。
- `tests/test_reqboard.py`（或现有板测）：`import` 按钮门控。
- `tests/test_jobs.py`：`import` job 分发。
- 端到端手工冒烟（不进 pytest）：真外部分支导入 → `submit-test` → `req test --design-only` 出用例、diff 不混入无关提交。

## 11. 分期

- **P0（本设计）**：`req import` 一条命令 + freeze `start_refs` + 导入票 + 契约置 passed + qa `context.md` 增 `diff_base` 并改 qa-design SKILL 口径（§6.4）；CLI 与 Web 入口。
- **P1**：`--update` 增量更新 freeze，并把已审核用例标待复核（复用轻量变更的 stale 思路）。
- **P2**：`to-tickets` / `req open --force` 对导入需求加保护；支持分批导入不同仓；可选 `--spec` 让 pi 依文档+diff 产最小 SPEC。
