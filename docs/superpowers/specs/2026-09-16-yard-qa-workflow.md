# 需求级自动测试环（yard-qa）

- 日期：2026-09-16
- 状态：已落地
- 实现：`dev-yard req test`、`.pi/skills/qa-design`、`.pi/skills/qa-run`、需求页「测试」Tab `/r/:jira/qa`
- 对照：qa-powers（`/home/chengpeng/rcc/qa-powers`）为参考实现，不作为运行时依赖
- 约束：[2026-09-15-plugin-stages-small-complete.md](2026-09-15-plugin-stages-small-complete.md) 的非目标仍然有效——**不把 qa-powers 收成插件**
- 决策：吸收 qa-powers 的覆盖矩阵 / 证据 / 四态，重写成 yard 专用 skill；编排走 **implement 同款专用 service**，接到现有 `submit-test` → ingest → `--from-test` 槽；**需求页只读展示** `qa/` 产物（用例 / 改动点 / run / 截图）；**按模型配置 worker 池**，宿主按 case `depends_on` 调度，在跑任务挂在看板上

## 1. 一句话

契约审查通过并提测之后，yard 用两条内置 skill 对着 **freeze worktree** 设计并执行 UI 用例，把 `result.yaml` 映射进现有 `accept_test_report`；失败拆 B 票，修复仍走 `implement --from-test`。测试 agent 只测不修。执行由宿主按 **模型 worker 池 + case 依赖 DAG** 调度；需求看板能看产物，也能看当前正在跑的 case。

## 2. 目标与非目标

### 目标

1. 在 `phase=testing` 下能跑通：设计用例 → 执行 → 证据 → ingest（通过则 `phase=done`，失败则拆测试 B 票）。
2. 用例覆盖来自 **SPEC + worktree 相对 `default_base` 的 diff**，不重拉 Jira、不问特性分支。
3. 产物只落在 `reqs/<JIRA>/qa/`，不进业务仓、不进插件 `stage_runs` 冒充提测。
4. 第一刀可测、可回归：单环境（local）；执行侧按 `qa.yaml` 的模型池并发，缺省池则 1 并发。
5. Web 控制台能只读看到这些产物：改动点 D、用例正文、各次 run 的四态、失败步骤与截图。没有跑过 run 时，design 产出的用例也要能看。
6. 可配置「哪些模型、每个模型几个并发、优先级」；总并发 = 各池之和。空槽时先填高优先级模型。case 按 `depends_on` 调度。看板展示排队 / 在跑 / 刚结束的测试任务（含模型名）。

### 非目标（冻结，有真实需求再开另一份设计）

- 把 qa-powers 当 `yard.yaml` 插件，或 `dev-yard run` 一条就当测完
- k8s / JumpServer / 远程 test 环境（第一刀只 local）
- 回放脚本（`.replay.sh`）、多账号自动发现
- 同一 case 在每个模型上各跑一遍（矩阵）。v1 是 **共享队列**：每条 case 只跑一次，空闲 slot（不论哪个模型）来领；不是「A、B 各测全集」
- 改 `PIPELINE` 步骤条（仍是九步；自动测是 testing 阶段上的动作，像 `contract` 之于 review）
- 测试 agent 改 worktree 业务代码、改用例预期以求变绿
- 把 cases 写进 `TICKETS.md`（票是实现 DAG，用例是验证覆盖）
- 在页面上编辑 / 删除用例或证据（测试页只读；改预期等于洗白失败）
- 把 `qa/` 塞进现有文档 Tab（`DocView` 是可编辑 Markdown，和结构化测试产物不是一类）
- 在测试页展示 `qa/context.md`、`.env`、登录态、`qa.yaml` 里的环境变量名以外的秘密

## 3. 内核不变量（由构造保证）

| 不变量 | 保证方式 |
|---|---|
| `phase ∈ {open, frozen, testing, done}` | 测试环不 `sets_phase`；只有现有 `submit_test` / `accept_test_report` 改 phase |
| 票循环 / 提测槽 | 测试 service **不经** `run_stage`（避免快照恢复 `STATUS.yaml`） |
| 主干步骤条 | `PIPELINE` 不变；新动作 `run-test` 进 `BUILTIN_ACTION_IDS`，不进 PIPELINE |
| 只测不修 | skill 禁止改 worktree 源码；宿主跑前/跑后对每个 worktree 做 porcelain 基线对比 |
| 测的是 freeze 树 | 启动 prompt 只注入 `reqs/<JIRA>/worktrees/<alias>`，禁止 `repos.yaml` 主克隆路径 |
| 术语/ADR 不进业务仓 | 与 implement 相同：只读 `reqs/CONTEXT.md` |
| 并发不由 skill 拉子 agent | 宿主线程池调度；`qa-run` 一次只跑 **一条** case |
| 看板能看见在跑的 case | 宿主写 `progress.yaml` + 父 job SSE；不把 case 做成 TICKETS |

插件契约不改：第三方仍不能发明 phase、不能接管 submit-test。

## 4. 流水线位置

```text
review --contract passed
  → req submit-test                         # 已有：phase=testing, test.status=awaiting
  → req test                                # 本设计：design（必要时）+ run + ingest
       失败 → findings → B 票 → implement --from-test
       通过 → accept_test_report(passed) → phase=done
  → 人工 fill-test-report 仍可用            # 自动测未跑、或要补人工结论时
```

`submit-test` 仍是门禁，自动测不替它改 phase。未提测（`phase=frozen` 且契约已过）时 `req test` 拒绝，提示先 `submit-test`。

人工 `fill-test-report` / `req accept-test` 保留：自动测是提测槽的一种 **source**，不是唯一入口。

## 5. 模块切分

与 grill / spec / implement 同构：

| 层 | 职责 | 不负责 |
|---|---|---|
| `.pi/skills/qa-design` | 读 SPEC/REQUIREMENT + worktree diff，写 `qa/meta.yaml` 与 `qa/cases/` | 拉 Jira、改 STATUS、跑浏览器 |
| `.pi/skills/qa-run` | **一条** case：造数 → 浏览器步骤 → 三层断言 → 写该 case 的 evidence | 调度其它 case、改产品代码、ingest |
| `service.req_test` | 门禁、design、**模型池调度**、拼单 case prompt、跑 pi、progress、变异闸门、ingest | 解析页面、写用例正文 |
| `qa_report.py`（新，薄） | `result.yaml` → `InboundReport` | 浏览器、pi |
| `board` + SPA `QaView` | 只读聚合 `qa/`：用例、D、run、截图 URL | 不写 `qa/`、不跑测、不 ingest |

报告 **不是** 第三条 skill。qa-powers 的 report 是给人看的 Markdown；yard 给人看的出口是 **需求页「测试」Tab**，机器出口仍是 `accept_test_report`（归档 `test-reports/<id>.md` + 提测槽 + B 票）。

## 6. 产物与配置

### 6.1 需求目录

```text
reqs/<JIRA>/qa/
  context.md          # 宿主每次 run 重写：worktree 地图、base_url、账号文件路径、注意事项
  meta.yaml           # design 写；run 可增量补 routes
  cases/<module>/
    case-NN.md
    setup.sql | setup.rb   # 可选
    cleanup.sql            # 可选
  evidence/<run_id>/
    progress.yaml          # 宿主维护：池占用与每条 case 状态（看板直播）
    result.yaml            # run 结束时写；run 级
    repo-baseline/<alias>.txt
    <case-id>/result.yaml
    <case-id>/screenshots/
```

`reqs/` 已被 `init_yard` gitignore，测试产物不进业务仓。禁止在 `repos.*.path` 或 worktree 下写 `.qa-powers/`。

### 6.2 工作区配置 `qa.yaml`

与 `yard.yaml` 分开（`yard.yaml` 继续只解释 `plugins:`）。工作区根：

```yaml
# qa.yaml
active_env: local
browser:
  channel: chrome          # playwright-cli --browser
  headed: false            # 总并发 > 1 时宿主强制无头（有头窗口会抢资源）
workers:                   # 模型池；省略则 1 并发，模型走 resolve_pi_choice(qa-run)
  - id: a                  # 看板展示名；缺省用 model
    provider: rcc          # 与 repos.yaml pi 相同：成对出现；可省略则回退工作区 pi
    model: grok-4
    concurrency: 2         # 该模型同时在跑的 case 数 ≥ 1
    priority: 1            # 越小越优先；空槽时先填本池。缺省 100
  - id: b
    provider: rcc
    model: MiniMax-M3
    concurrency: 2         # 上例总并发 = 4
    priority: 2            # grok-4 有空槽时不会派给 MiniMax
envs:
  local:
    base_url: http://127.0.0.1:8080
    auth:
      default: default
      accounts:
        default:
          username_env: YARD_QA_USER
          password_env: YARD_QA_PASSWORD
          state_file: .yard-qa/auth-local-default.json
    db:
      url_env: YARD_QA_DB_URL      # 可选；未配则禁止 usql
    script:
      runner: bin/rails runner     # 可选；未配则只允许 .sql
    notes: []                      # 自由文本，注入 context.md
```

凭据只通过 `.env` 环境变量名引用，不把密码写进 `qa.yaml`。`.yard-qa/` 加入工作区 `.gitignore`（登录态 cookie）。

第一刀只认 `envs.local`（或唯一的那个 `envs` 键）。出现 `test` / `k8s` 键不报错、不执行。

缺失 `qa.yaml` 或 `base_url`：`req test` 失败，提示补配置（不在 skill 里面试一整份 init）。

`workers` 校验：

- 至少 1 条（省略整段 = 一条默认池 `concurrency: 1`）
- `concurrency` 为正整数；单池 ≤ 8；**各池之和 ≤ 8**（再高拒配，避免本机浏览器/API 打满）
- `provider`/`model` 必须成对；都不写则该池用 `resolve_pi_choice(root, "qa-run")`，解析结果仍空则该池无法派发、命令失败
- `id` 在列表内唯一；缺省为 `model` 或 `w1`/`w2`
- `priority` 为整数，缺省 `100`；**数值越小越优先**（1 先于 2）。允许相同，同优先再按 `id` 字典序
- 语义是 **一个队列、多种 worker**：case 不绑模型。不是每个模型各跑一遍全集。优先级只决定「下一空槽用哪个池」，不把 case 钉死在某个模型上

### 6.3 `meta.yaml`

```yaml
module: <JIRA>-<短名>
requirement: <JIRA>
base_branches: { <alias>: <default_base> }      # 来自 repos.yaml，禁止写死 main
feature_branches: { <alias>: req/<JIRA> }       # freeze 分支约定
routes: {}                                      # run 首次推导后增量写；design 不整文件覆盖
changes:
  - id: D1
    repo: <repos.yaml 别名>                     # 必须是 yard alias，不是 frontend/backend 泛称
    ref: src/foo.vue
    desc: ...
```

`repo` 必须能在 `repos.yaml` 对上。design 用 `repos.yaml` 的 `role`（fe/be/…）只作阅读提示，写入时用 alias。

## 7. Skill 契约

打包方式与现有内置 skill 相同：源在仓库根 `.pi/skills/`，wheel `force-include` 进 `dev_yard/skills/`。

注册进 `BUILTIN_STAGES`，供 `get_runner` / `pi_argv` 解析 skill 与 tools。**禁止**经 `service.run_stage` 执行（与 implement 相同）。

```text
dev-yard run qa-design | qa-run | test
→ 退出码 2，提示改用 dev-yard req test
```

加入 `_RUN_VIA_DEDICATED`。`RESERVED_STAGE_NAMES` 里的 `run` / `init` 不占用；阶段名用 `qa-design` / `qa-run`。

### 7.1 `qa-design`

- **tools:** `read, bash, grep, find, ls, edit, write`（bash 仅 `git -C <worktree> diff` / `log`，禁止 checkout / commit / push）
- **bundles:** `("qa-design",)`
- **guidance（注入 prompt，硬约束）:**
  - 只写 `reqs/<JIRA>/qa/**`
  - 需求只读 `REQUIREMENT.md` / `SPEC.md` / `TICKETS.md`；不调 MCP、不重拉 Jira
  - diff：每个 worktree `git diff <default_base>...HEAD`（HEAD 即 `req/<JIRA>`）；空 diff 则停止并说明
  - 改动点 D1..Dn；每个 D 至少 1 条用例；正常流 1 条
  - 步骤用业务语言；按钮/文案必须来自 worktree 代码，不来自想象
  - 预期写需求口径；实现与 SPEC 不符时仍写需求值，并备注「需求偏差」
  - 不面试；缺细节用 SPEC 与常规默认，在用例里标注假设
  - 每条 case frontmatter 必有 `repo:`（yard alias，ingest 用）。跨仓改动拆成多条 case，或 `covers` 只含一个主仓
  - `depends_on`：共享可变数据或业务先后才写；无依赖省略（可被任意模型槽并发领取）
- **cwd:** yard root
- **protects:** 不经 `run_stage`，无快照。skill 不得改 `STATUS.yaml` 与四份需求 md

用例 frontmatter（相对 qa-powers 的改动加粗）：

```markdown
---
id: case-01
title: ...
priority: P0
requirement: <JIRA>
repo: <alias>              # 必填，yard 仓库别名
covers: [D1, D2]
depends_on: []
data: { setup: setup.sql, cleanup: cleanup.sql }
---
```

### 7.2 `qa-run`

- **tools:** `read, bash, grep, find, ls, edit, write`
- **bundles:** `("qa-run",)`
- **guidance:**
  - 只写 `reqs/<JIRA>/qa/**`；禁止改任何 worktree 文件（含测试、配置、源码）
  - 禁止 git checkout / commit / push / switch；禁止部署
  - 页面 URL 只来自 `context.md` 的 `base_url` + `meta.yaml` `routes` / 前端路由代码；禁止猜 host
  - 业务路径不可改；selector 可按语义重定位一次
  - 失败只取证，禁止为变绿改 case 预期或 setup
  - **一次调用只跑 prompt 指定的那一条 case**；禁止再 spawn 测其它 case；调度是宿主的事
  - playwright 会话 `-s=qap-<case-id>`，禁止动别人的会话；并发时禁止 `state-save`
  - 无头默认；`headed: true` 仅总并发为 1 时生效
  - 四态：`passed | failed | blocked | skipped`；登录失败 / 5xx / DB 连不上 = blocked，不算产品失败
  - 三层断言：UI → 网络（有提交时）→ DB（预期含 DB 时）；下层为准
  - 截图绝对路径写到该 case 的 `screenshots/`
- **cwd:** yard root

case 级 `result.yaml` 最小字段：

```yaml
case: case-01
title: ...
repo: <alias>
covers: [D1]
model: grok-4           # 实际跑这条的池
provider: rcc
status: passed          # passed | failed | blocked | skipped
reason: ""
assertions:
  - type: ui            # ui | net | db
    expected: ...
    actual: ...
    status: passed
failure:                # 仅 failed
  step: 3
  step_desc: ...
  evidence: screenshots/step-03.png
```

run 级 `evidence/<run_id>/result.yaml`：

```yaml
run_id: 2026-09-16-153000
env: local
workers:
  - id: a
    model: grok-4
    concurrency: 2
    priority: 1
  - id: b
    model: MiniMax-M3
    concurrency: 2
    priority: 2
cases:
  - case: case-01
    status: passed
    repo: frontend
    model: grok-4
    reason: ""
summary:
  total: 2
  passed: 1
  failed: 1
  blocked: 0
  skipped: 0
```

### 7.3 启动 prompt（宿主写，skill 当事实）

每次 design/run 前宿主重写 `qa/context.md`，并在 pi prompt 前置：

1. 职责块（全文，禁止只写「遵守 skill」）
2. `Req dir`、`qa dir`
3. Worktree 地图：`- <alias>: <abs path>  (branch req/<JIRA>, base <default_base>, role <fe|be|…>)`
4. `base_url`、browser channel/headed、state_file、db url 是否已配（**不**把密码写进 prompt；skill 从环境变量读）
5. `envs.local.notes`

`lists_sources` 保持 false，以免误注入 default_base 主克隆。

## 8. 宿主 service

### 8.1 `req_test(...)`

顺序：

1. 需求目录存在；`contract_review == passed`；`phase == testing`；尚未 `test_passed`。否则 `TestRejected`。
2. freeze worktree 都在：每个 `STATUS`/`TICKETS` 涉及的 alias 存在 `reqs/<JIRA>/worktrees/<alias>`。
3. 读 `qa.yaml`；解析 `active_env` 与 `workers`（见 §6.2）。
4. 写 `qa/context.md`。
5. 若非 `run_only`：无 `qa/cases/**/case-*.md` 则跑 `qa-design`（**单次** pi，不并发）；已有 cases 且非 `design_only` 则跳过 design（`--redesign` 强制）。
6. `design_only` 到此返回（不 ingest）。
7. 跑前：每个 worktree `git status --porcelain` → `evidence/<run_id>/repo-baseline/<alias>.txt`。`run_id` 由宿主生成。登录态：在派发前用默认账号 load 一次（并发期禁止 worker `state-save`）。
8. **宿主调度跑 case**（§8.5），不是一次 `qa-run` 跑完全集。Web 上这是父 job `run-test`；每条 case 的 pi 固定 `-p`。
9. 全部终态后变异闸门：porcelain 相对基线的**新增**行，若路径不属于 `reqs/<JIRA>/qa/` 且不是工作区根临时 png → `TestRejected("worker mutated worktree")`，**不 ingest、不 revert**。并行时禁止用「单次 porcelain 非空」给某一条 case 定罪。
10. 宿主汇总 `evidence/<run_id>/result.yaml`（不要等 skill 写 run 级文件）。
11. `ingest=True` 时 `map_qa_result` → `accept_test_report`。`ingest=False` 只留证据。

不经 `run_stage`。某条 case 的 pi 非 0 且无 case `result.yaml` → 该 case `blocked`（reason: worker exit），**不停整场**，除非触发 §8.5 的环境熔断。

### 8.2 CLI

```text
dev-yard req test <JIRA>
  --print
  --design-only
  --run-only
  --redesign
  --no-ingest
```

`--design-only` 与 `--run-only` 互斥。`--run-only` 时必须已有 cases。

`dev-yard req submit-test` / `accept-test` 不变。

### 8.3 Web / job

- 新动作 id：`run-test`，文案「自动测」
- `can_run_test` = `can_fill` 且存在 worktree 且 `qa.yaml` 可读（缺配置时按钮 disabled，reason 写明）
- `testing` 阶段 `next`：尚无自动测证据且无测试 B 票 → `run-test`；有就绪测试 B 票 → 仍 `fix-test`；否则保持 `fill-test-report`
- `jobs._HOST_JOB_ACTIONS`、`board.BUILTIN_ACTION_IDS`、SPA `ACTION_LABELS` 补上
- job 执行：`service.req_test(...)`；不要落到插件 `run_stage` 分支
- `PI_STAGES` 增加 `qa-design`、`qa-run`。**跑测时的模型以 `qa.yaml` `workers` 为准**，不走设置页里单一的 `qa-run` pair（那只给「workers 省略」时的默认池用）
- 父 job 带 `qa_progress`（形状同 `progress.yaml`），SSE `state` 事件要带上，供看板直播；**不要**为每条 case 再占一个 `JobRunner` 线程（父 job 等子 job 会死锁）。case 的 pi 由 `req_test` **自建线程池**跑，池大小 = 总并发

`PIPELINE` / 步骤条「提测」语义不变。

测试产物展示见 §8.4，与「自动测」按钮同一切片，不是后续加项。

### 8.4 Web：只读测试页（v1 必做）

人要看的是 `qa/` 目录，不是 ingest 后那一行 summary。现有「提测 / bug」面板只反映 `STATUS.yaml` 的槽，**不够**。

#### 入口

- 需求页文档 Tab 旁增加 **「测试」**，路由 `/r/:jira/qa`（SPA 壳与文档页相同）。无 `qa/` 时页仍可进，空状态文案：「还没有用例。提测后点自动测，或 `dev-yard req test --design-only`。」
- 看板保留并扩展「提测 / bug」折叠面板：除 status/verdict 外，若有 latest run 则显示 `passed/failed/blocked/skipped` 四数，并链到 `/r/:jira/qa`。B 票 `finding` 若等于 `case-id`，票卡片可链到 `/r/:jira/qa?case=<id>`。
- **正在跑的测试**（§8.6）画在票看板上方，与 Job 日志区分：卡片是 case，不是把 4 路 pi 日志全铺开。
- **不**把 cases 加进 `DOC_FILES`，**不**走 `save_doc`。

#### 页面结构（`QaView`，只读）

1. **改动点**：`meta.yaml` 的 `changes[]`（id / repo / ref / desc）。无 meta 则整段隐藏。
2. **用例列表**：每个 case 一行：id、title、priority、repo、covers、最近一次 run 的 status（无 run 则「未跑」）。点开看 Markdown 正文（步骤/预期，服务端 `render_markdown`，与文档相同消毒）。
3. **Run**：`evidence/` 下按 `run_id` 字典序倒序。默认展开最新一次。展示 env、summary 四态、每条 case 的 status/reason。`blocked` 的 run 即使未 ingest 也必须能看（这是环境故障的主展示面）。
4. **失败证据**：failed/blocked case 的 `failure.evidence` 及该 case 目录 `screenshots/` 全部图片。缩略图 + 点击大图，交互对齐需求页 `assets/`。
5. 历史 run 可切换；不提供删除。

不展示：`context.md`、repo-baseline 原文、setup.sql 文件内容（第一刀；需要造数排查时人去工作区看）。

#### API

需求详情可带摘要，避免看板为了四数拉全量：

```
GET /api/requirements/{jira}          # 现有 payload 增加
  qa: null | {
    has_cases: bool,
    has_meta: bool,
    latest_run: null | { run_id, env, summary },
    progress: null | <progress.yaml 对象>   # 有进行中的 run 才有
  }

GET /api/requirements/{jira}/qa       # 测试页主数据
  meta: object | null                 # meta.yaml；缺文件则 null
  cases: [
    { id, module, title, priority, repo, covers, depends_on,
      body, html, path }              # body=md 原文；html=消毒后
  ]
  runs: [                             # 新→旧
    { run_id, env, summary, workers, progress, cases: [
        { case, status, repo, model, reason, failure, screenshots: ["step-03.png", ...] }
    ] }
  ]

GET /r/{jira}/qa/evidence/{run_id}/{case_id}/screenshots/{name}
  # 静态图，Content-Type 按后缀；仅文件
```

路径闸门（与 `asset_file` 同构）：

- 根必须是 `reqs/<JIRA>/qa/evidence/` 的 resolve 结果
- `run_id` / `case_id` / `name` 各为单段，禁止 `/`、`..`、空
- 只允许后缀 `{png,jpg,jpeg,webp,gif}`
- 出界或非文件 → 404（不要 500）

`GET /api/requirements/{jira}/qa` 缺目录时返回空列表 + `meta: null`，HTTP 200，不 404。

**没有** POST/PATCH/DELETE。页面不能改 case、不能改 result、不能上传图。

#### 数据从哪来

| UI | 文件 |
|---|---|
| 改动点 | `qa/meta.yaml` |
| 用例 | `qa/cases/<module>/case-*.md`（frontmatter + 正文） |
| run / 四态 | `qa/evidence/<run_id>/result.yaml` 与各 `…/<case-id>/result.yaml` |
| 在跑 / 排队 | `qa/evidence/<run_id>/progress.yaml`（宿主写） |
| 截图 | 该 case 的 `screenshots/`；`failure.evidence` 相对该目录 |

畸形 YAML：该 case/run 标 `unreadable`，页面提示「结果文件无法解析」，不影响其它条。

#### 前端

- 新路由 `/r/:jira/qa` → `QaView.vue`
- 需求页 Tab 在文档之后加一项
- `ReqDetail.qa` 摘要驱动看板四数与「正在测」条；测试页自己拉 `/qa`
- 父 `JobPanel` 仍一条（编排日志）；不要为每个 worker 再挂一块全量日志
- 视口：桌面列表+详情；窄屏先列表后点开（与看板票卡片同一套密度，不必新设计系统）

插件禁止自定义视图的冻结项**不适用于**这条内置页。

### 8.5 调度（宿主，不在 skill 里）

`qa-run` 不懂全局队列。`req_test` 建线程池，**线程数 = `sum(workers.concurrency)`**。

**就绪：** case 的 `depends_on` 每条都已是 `passed`（或未声明依赖）。`depends_on` 指向不存在的 id、或成环 → 开跑前 `TestRejected`，不派发。

**领取：** 有就绪 case 且至少一池有空槽时：

1. 在 `inflight < concurrency` 的池里，取 **`priority` 最小** 的（同值按 `id`）
2. 从就绪集取一条：case 自己的 `priority` 降序，同值按 id
3. 把这条派给步骤 1 的池（带上该池 provider/model）

因此高优先级模型会先被填满，满了才用低优先级池。**不抢占**：已经在低优先级模型上跑的 case 不会因为高优先级槽空出来而杀掉重派。case 不预先绑模型。

（case frontmatter 的 `priority` 是用例紧急度，和池的 `priority` 不是同一个字段。）

**依赖失败：** 前置 `failed` / `blocked` / `skipped` → 本 case `skipped`（reason 写前置 id 与状态），不派发。

**熔断：** 在跑的 case 里连续 2 条因同类环境原因 `blocked` → 停止新派发，等在跑的收尾，剩余就绪/未就绪全部 `blocked`（reason 同）。已 `passed` 的不动。

**progress.yaml**（每次派发/终态都重写，供刷新和 SSE）：

```yaml
run_id: 2026-09-16-153000
env: local
pools:
  - id: a
    provider: rcc
    model: grok-4
    concurrency: 2
    priority: 1
    inflight: 2
  - id: b
    provider: rcc
    model: MiniMax-M3
    concurrency: 2
    priority: 2
    inflight: 1
cases:
  - id: case-01
    title: 正常流
    state: passed          # pending | ready | running | passed | failed | blocked | skipped
    repo: frontend
    depends_on: []
    pool: a
    model: grok-4
    started_at: 2026-09-16T15:31:02Z
    ended_at: 2026-09-16T15:33:10Z
  - id: case-03
    title: 权限拦截
    state: running
    repo: frontend
    depends_on: [case-01]
    pool: b
    model: MiniMax-M3
    started_at: 2026-09-16T15:33:11Z
```

父 job 的 `qa_progress` 与此文件同构。CLI 无 SSE 时 stdout 打一行：`running case-03 on MiniMax-M3 (b 1/2); queue 2`。

单条 case 的 pi argv：`--provider/--model` 用该池的 pair，`--skill qa-run`，prompt 只含这一条 case 全文 + context 职责块。tools 与 `qa-run` StageSpec 相同。

### 8.6 看板：正在测的任务

需求页（`RequirementView`）在步骤条 / 票看板之间加 **「自动测试」** 条，只要 `qa.progress` 存在且还有非终态 case：

1. 池占用（按 `priority` 排序）：`grok-4 2/2` · `MiniMax-M3 1/2`
2. **在跑**卡片：case id、title、模型、开始时间；点开可看父 job 日志里该 case 的切片，或链到 `/r/:jira/qa?case=`
3. **就绪未派发**小字列表（依赖已满足、等槽）
4. **被依赖挡住**不占主位，测试 Tab 里能看

票看板本身不改列。Case 不是 T/B 票，不要 `TicketBoard` 混装。

直播：父 job SSE 的 `qa_progress` 优先；刷新后读磁盘 `progress.yaml`（经需求详情 / `/qa`）。跑完条还在，直到用户离开或下一次 run 覆盖——终态后折叠进「提测 / bug」四数即可。

测试 Tab 对最新 run：在跑中的 case 显示 spinner + 模型，不要等 `result.yaml`。

## 9. result.yaml → ingest

现有 `SOURCES` 增加 `"yard"`。`parse_inbound` / `accept_test_report` 其余不变。

`map_qa_result(run: dict, cases: list[dict]) -> InboundReport | None`：

| 情况 | 动作 |
|---|---|
| `summary.failed > 0` | `verdict=failed`；每个 **failed** case 一条 finding：`id=case-id`，`title=case.title`，`detail=failure.step_desc + reason + evidence 相对路径`，`repo=case.repo`（缺省则拒） |
| `failed == 0` 且 `blocked > 0` | **不 ingest**（现网 `blocked` 也会拆 B 票，环境故障不当产品 bug）。命令成功，stdout/看板提示 blocked 原因；`test.status` 保持 `awaiting` |
| `failed == 0` 且 `blocked == 0`（全 passed，或 passed+skipped） | `verdict=passed`，`findings=[]` |
| 无 case / summary 全 0 | 视为畸形，不 ingest |

`body`：run 级 YAML 原文或精简 Markdown（断言失败列表）。`source=yard`。`summary`：`passed/failed/blocked/skipped` 一行。

finding 的 `repo` 必须是 `repos.yaml` 别名；`accept_test_report` 已要求 failed 时 findings 非空且每条有 repo。映射层在调用前再校验一遍。

不把 `TEST-REPORT.md` 当活文档（与现网 ingest 一致）；归档仍在 `test-reports/<id>.md`。

## 10. 变异闸门与「只测不修」

两层，缺一不可：

1. **Skill 文本：** 可写路径仅 `reqs/<JIRA>/qa/**`；三类禁止（产品源码 / 改历史或分支的 git / 部署）。
2. **宿主比较：** 相对 §8.1 步骤 7 的基线，worktree 新增脏文件 → 整次 run 作废（不 ingest）。忽略 `qa/` 与误落到 cwd 的 png（png 可删或挪到 evidence，不算变异）。

`qa-run` 的 tools 含 `write`/`bash` 是因为要写 evidence 和跑 playwright-cli/usql；闸门补工具白名单收不掉的洞。

## 11. 与 qa-powers 的对照（吸收清单）

| 吸收 | 改写 | 丢掉（第一刀） |
|---|---|---|
| diff → D → 每点 ≥1 条用例 | D.repo = yard alias；diff 对象 = worktree | 再拉 Jira / 问特性分支 |
| 业务步骤 + 三层断言 + 四态 | 产物在 `reqs/<JIRA>/qa/` | `.qa-powers/` 写进业务仓 |
| 只测不修 | 宿主 porcelain 闸门 | Claude hook / version-check / using-qa-powers |
| 证据目录 + case/run result.yaml | 映射进 `accept_test_report`；**给人看的是测试 Tab** | qa-powers 的 report skill 不当主出口 |
| 环境故障 ≠ 产品失败 | blocked 不 ingest | k8s 诊断 skill |
| notes / 登录态文件 | `qa.yaml` + `.env` + `.yard-qa/` | 明文密码进 yaml、init 长面试 |
| | 顺序单会话 | 并发、replay.sh、多账号发现、stdin 进 pod |

qa-powers 仓库保持独立产品。本设计不 git submodule、不 pip 依赖它。

## 12. 测试（宿主，pytest）

不在 CI 里起真浏览器。测 Python 缝：

1. `req_test` 在 `phase!=testing` / 契约未过 / 无 worktree / 缺 `qa.yaml` 时拒绝。
2. `dev-yard run qa-design`（及 `qa-run`/`test`）退出码 2。
3. FakeRunner：design 被调用当 cases 缺失；cases 已在且无 `--redesign` 时跳过 design。
3b. 调度单测（不跑 pi）：两池 2+2 时最多 4 条 running；`depends_on` 未完成的不会被派发；前置 failed → 后继 skipped；环依赖开跑即拒；`workers` 之和 > 8 拒配。`priority: 1` 的池有空槽时不会派给 `priority: 2`；高优先级满了才用低优先级；不抢占已在跑的 case。
4. `map_qa_result`：failed → findings+repo；全 passed → passed；仅 blocked → `None`（不 ingest）；缺 repo → 抛错。
5. 变异闸门：跑后 worktree 多一个非 qa 文件 → 不调用 `accept_test_report`。
6. 成功 passed 路径：ingest 后 `phase=done`（复用现有 `accept_test_report` 行为）。
7. 看板：`testing` 且无测试 B 票时 `run-test` enabled；插件阶段仍不进 PIPELINE。
9. `GET /api/requirements/{jira}/qa`：有 cases/run 时字段齐全；无 `qa/` 时 200 空列表。
10. 截图路由：合法 png 200；`../STATUS.yaml`、无后缀、出 `evidence/` → 404。
11. 测试页 API 为只读：不注册写方法。`DOC_FILES` 不含 qa。
12. 需求详情在写了 `progress.yaml`（含 running）时带上 `qa.progress`；看板组件能列出在跑 case 与各池 `inflight/concurrency`。
8. `run_stage` 回归：启用名为 `test-design` 的**插件**仍按插件规则回滚 STATUS（内置专用命令不走这条）。内置同名占用 registry——**两个已启用插件不得与内置同名**已有校验；内置先注册，插件同名会覆盖 skill。本设计规定：**禁止**用户插件命名 `test-design` / `test-run`（加入与 `open` 类似的保留，或文档约定 + 加载时若覆盖 builtin 测试阶段则失败）。选择：**覆盖内置测试阶段名视为加载错误**（与 `review` 可覆盖不同，因为测试环是专用 service）。实现：`BUILTIN_STAGES` 里给这两条加 `override=False` 或硬编码拒绝覆盖集合 `{test-design, test-run}`。更简单：名称加入「可覆盖白名单」之外。现网规则是任意同名即覆盖。为少改插件契约：**测试阶段名用 `qa-design` / `qa-run`**，不太可能被「换 review 提示词」类插件误伤。

**阶段命名最终决定：`qa-design` / `qa-run`。** CLI 入口仍是 `dev-yard req test`。动作 id `run-test`。Skill 目录 `.pi/skills/qa-design`、`qa-run`。`_RUN_VIA_DEDICATED` 含 `qa-design`、`qa-run`。插件仍可覆盖 `review`，不要覆盖这两条——若插件 `name` 等于二者，加载失败（与保留字同一类，扩 `RESERVED_STAGE_NAMES` 或单独 `DEDICATED_STAGE_NAMES`）。`run` 已在保留字里，故不能用 `run` 当阶段名。

## 13. 落地顺序

1. 空 `qa.yaml` schema + `paths.qa_dir` + 写 `context.md` + 拒绝门禁（无 pi）。
2. `map_qa_result` + `SOURCES+=yard` + 单测。
3. `qa-design` / `qa-run` skill 正文（按 §7 裁剪，不搬 qa-powers 全文）。
4. `BUILTIN_STAGES` + `req_test` + CLI + 禁止 `dev-yard run qa-*`。
5. 变异闸门。
6. Web：`run-test` 动作 / job / 文案 + §8.4 测试 Tab + §8.6 在跑任务条。用夹具 `progress.yaml` 即可测页面，不必真跑浏览器。
7. 用本仓 `reqs/PG-13054` 的 front/research worktree 做一次手工冒烟（不进 pytest）：local 需已起前端 + `qa.yaml`。冒烟失败不挡合并，但实现者须在 PR 注明是否跑过。

## 14. 以后可以开的设计（本文不实施）

- `envs.test` + 远程执行（才考虑是否调用独立 k8s skill）
- 同一 case × 每个模型的矩阵跑法（对比模型）
- replay.sh
- design 交互澄清（对齐 grill 的 web-round，而不是把 AskUserQuestion 塞进 `--print`）
- 测试页高亮 SPEC 对应章节（D ↔ 验收标准双向跳转）；第一刀只展示 `covers` id
- 页面上浏览 setup/cleanup 脚本、切换历史 run 对比 diff

## 15. 文档

落地时改：

- `AGENTS.md` 路由表加 `dev-yard req test` → `qa-design` + `qa-run`
- `README.md` 核心流程在 submit-test 下补一行自动测；说明需求页「测试」Tab 只读
- `.pi/ORIGIN.md` 注明这两条是 yard I/O 适配，不是 superpowers 原样拷贝
- 本文件状态改为「已落地」并链计划（若另写 `docs/superpowers/plans/`）
