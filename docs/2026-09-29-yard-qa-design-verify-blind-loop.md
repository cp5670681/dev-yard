# yard-qa 设计回路修复：从「11 条 design-blocked」到「1 分钟硬停 + 首轮写对」

- 日期：2026-09-29
- 状态：**已定稿，待实施**
- 触发需求：`reqs/PG-13227/`（firm_task 详情页「已有联系人」勾选）
- 触发操作：web 需求页「设计用例」，job `bb02c77aed`（`action=qa-design`）
- 关联产物：`reqs/PG-13227/qa/design-verify/BLOCKED.md`、`qa/design-verify/summary.yaml`、`qa/state.yaml`、`qa/design.yaml`、`qa/review.yaml`
- 一句话：**不是 agent 不会写用例，也不是重试次数不够。三类互不相干的失败（现场未部署 / 脚本语法事实没进 prompt / 宿主自己拼错 SQL）被同一个回路放大成 3 轮盲改封顶。**

> 本文档原先只做诊断与提案（§1–§3 为实测证据，保留）。§4 起是**拍板后的实施方案**：两个关键取舍已定（见 §4），原提案里的 D2（归因分流）与 D4（design 期探针）**不做**，理由见 §4.3。

## 1. 现象与经过

| 时间（本地） | 事件 |
|---|---|
| 17:44 | `qa/context.md` 生成（84KB，含 `## Database columns`） |
| 17:45 | job 启动；前置检查通过（`resolve/ping/hello/origin/db/accounts=ok`）；提示「改动疑似涉及权限控制，但只配了默认账号…**仅提示，不阻塞**」；第 1 次 design pi 启动 |
| 17:50–17:56 | `meta.yaml`、`accounts-discover.sql`、`cases/case-01..10.*`、`OPEN-QUESTIONS.md` 落盘 |
| 17:56–18:00 | **页面日志静默**（design 期 pi 输出不进 job log，见 P2-1）；design 返回 |
| 18:00:08 | `design.yaml`（11 条）；`state.yaml: phase=verifying`；`design-verify/*.yaml` 第 1 轮核实：**11 条全 failed** |
| 18:00:52 | 第 2 次 design pi 启动（核实失败回灌轮） |
| 18:19–18:20 | 第 3 轮核实：**仍 11 条全 failed**；写出 `BLOCKED.md`（23KB）与 `summary.yaml` |
| 结束 | job `state=ok`；`verify passed=0 failed=11 blocked=0 skipped=0 total=11`；标 `design-blocked`；`review=rejected`；`OPEN-QUESTIONS=7`；**未进入 run（0 条用例被执行）** |

job log 尾部：

```
数据核实仍有 11 条未通过，已达上限 3；标 design-blocked，交人工
verify passed=0 failed=11 blocked=0 skipped=0 total=11
数据核实未通过，design-blocked 清单：reqs/PG-13227/qa/design-verify/BLOCKED.md
PG-13227 design-only cases=11 review=rejected OPEN-QUESTIONS=7
```

整轮成本：**3 次 pi 全量设计轮 + 3 轮现场脚本执行**（每轮 11 条 setup 走 ssh + kubectl，共 33 次远程执行）。

## 2. 证据链

### 2.1 逐条失败原因（第 3 轮，取自 `qa/design-verify/case-*.yaml`）

```
case-01/05/06/07/11: undefined method `where' for #<Class:…>（AR 2.1.1；lib/sql_logic.rb:283 method_missing）
case-02/03/04/08  : OCIError: ORA-01722: 无效数字: DELETE FROM firm_tasks WHERE (id = 'qa-pg13227-cXX')
case-09           : ORA-00933: SQL command not properly ended（position 42）
case-10           : freeze_model_concern.rb:46 → 不支持修改！请修改Oracle数据表数据 (RuntimeError)
```

### 2.2 三类归因

| 类 | 命中用例 | 真实原因 | 该归谁 |
|---|---|---|---|
| A 现场未部署 | 7 条（01/02/05/06/07/08/10） | test 环境没跑本需求的迁移 `db/migrate/20260925120000_add_is_existing_contact_to_firm_tasks.rb` | 环境（部署）——**现在却在烧 design 轮** |
| B 脚本语法/契约 | 9 条（01/02/03/04/05/06/07/08/11、以及 10） | reach 是 Rails 2.1.1（AR 无 `.where`）；`firm_tasks.id` 是数值列而用例用字符串主键；research 侧模型 `freeze_model_concern` 禁 `delete_all` | 平台（事实没进 prompt、design 期无自测通道） |
| C 宿主自身 bug | 1 条（09） | 宿主拼的包装 SQL 在 Oracle 非法（见 2.4） | 宿主 |

（同一条用例可命中多类，故三类之和大于 11。）

### 2.3 A 类：列缺失 → 教唆删断言

1. **分支里有迁移，代码是齐的** —— `worktrees/reach/db/migrate/20260925120000_add_is_existing_contact_to_firm_tasks.rb` 存在（278B）。
2. **分支的 `schema.rb` 不含该列** —— `grep is_existing_contact worktrees/reach/db/schema.rb` → 零命中（迁移未在分支内 re-dump schema）。
3. **宿主给 design 的列清单，两路来源都不含该列**
   - `write_context_md` 的两个来源：`worktree_schema_files`（`qa_verify.py:436-444`）**只写文件路径、不读内容**，且 `_SCHEMA_FILES = ("db/schema.rb", "db/structure.sql", "prisma/schema.prisma")`（`qa_verify.py:380`）**不含 `db/migrate`**；`dump_live_schema`（`qa_verify.py:383`）查的是**现场库** `information_schema`，而 test 环境没跑这条迁移。
   - 现场真缺列（非 dump 失败）：`context.md:158`（`main`）与 `:410`（`reach`）都列出了 `firm_tasks` 全列，确实没有该列。
   - `grep is_existing_contact reqs/PG-13227/qa/context.md` → **零命中**（需求唯一的核心列，在 context.md 里完全不存在）。
4. **四处提示词把这个残缺清单钉成唯一权威**
   - `qa.py:191-195`（context.md 章节头）：`verify.sql 和 setup 的表名、列名必须来自本节…不要猜列名`。
   - `qa.py:913-920`（`_duties("design")`）：`Column names come from context.md Database columns … or worktree schema.rb`。
   - `qa.py:1063`（核实回灌轮的 repair 提示）：`列名以 context.md Database columns 为准；模型不认的字段从 setup 删掉。`
   - `qa_verify.py:1086`（`render_feedback` 前言）：`列不存在：用提示里的真实列名；应用常量不是表字段。`
   - `qa_verify.py:455-464`（`enrich_verify_hint` 失败提示）：`列 X 不存在。…列名从 worktree schema.rb / ORM 读。` —— **失败时又把它推回同一个残缺来源**。
5. **agent 的最优解只能是删断言** —— 于是 `case-01/02/05/06/07/08/10` 的 verify.sql 都写上了「is_existing_contact 列未部署，从断言中移除」或「本断言不读该列」。**这个需求唯一要验的行为（勾选「已有联系人」）无法被任何断言覆盖。**

### 2.4 C 类：宿主自己拼的 SQL 在 Oracle 非法

`src/dev_yard/qa_exec.py:726`：

```python
query = f"SELECT count(*) FROM ({body}) AS qa_verify" if head in {"select", "with"} else body
```

这行**没有方言分支**。Oracle 不接受派生表的 `AS` 别名，报 `ORA-00933`。

证据是自洽的：第 3 轮发出的语句是 `SELECT count(*) FROM (SELECT 1 FROM DUAL) AS qa_verify`（job log 可见），报错 `position 42` —— 按该语句逐字符数，第 42 个字符正是 `AS` 的 `A`。

这**不是**「宿主规则与现场判据不一致」（原稿 P2-2 的定性）：用例写的 `SELECT 1 FROM DUAL;` 本身在 Oracle 完全合法，是宿主把它包坏了的。后果也不止这一条用例：**任何走 usql 路径的 SELECT 断言在 Oracle catalog 上都会挂**，run 期的 DB 断言同样中招。

### 2.5 回灌轮封顶

`_verify_loop`（`qa.py:1172-1268`）：`case_attempts = cfg.design_verify_attempts`（`qa_config.py:182`，默认 3），每轮都是「实测 → `render_feedback` → agent **盲改** → 再实测」；`case_round >= case_attempts` 时 `reject_cases` + 标 `design-blocked` 退出（`qa.py:1212-1221`）。

**为什么是「盲改」**：design agent 在设计期没有任何执行通道 —— `.pi/skills/qa-design/SKILL.md:91` 明确「设计阶段不要自己跑 usql 探库（DSN 在 qa.yaml，由宿主查）」。它无法自证，只能每轮撞一个新的环境约束。

第 1 轮 → 第 3 轮的失败原因确实变了（`#` 注释、多语句 `;` 被改掉），说明回灌**有局部效果**；但最后这轮剩下的全是环境契约与 Ruby/AR 语法事实，**改成例措辞修不了**。

**不该做的修法**：把 `design_verify_attempts` 从 3 调大。失败原因不是「次数不够」，只会让它在盲改里烧更多算力。

## 3. 平台问题清单

严重度：P0 = 直接造成本次全部失败；P1 = 系统性缺陷；P2 = 体验/次要。**「修法」列指向 §5 的对应编号。**

| # | 问题 | 位置 | 级别 | 修法 |
|---|---|---|---|---|
| P0-1 | 列清单信息源缺「需求分支的 `db/migrate`」这一路，提示词又把它当唯一权威 → 用例被改写成验不到需求本身 | `qa.py:191-195`、`qa.py:913-920`、`qa.py:1063`、`qa_verify.py:380/436-444`、`qa_verify.py:455-464`、`qa_verify.py:1086` | P0 | §5.2 §5.3 §5.4 |
| P0-2 | 无「需求 ↔ 现场」一致性门禁：现场没有需求分支的迁移，等于这个环境没有要测的功能，却照常烧完 design→verify | 缺 | P0 | §5.7 |
| P0-3 | 宿主包装 SQL 在 Oracle 非法（`AS qa_verify`），凡是走 usql 的 SELECT 断言必挂 | `qa_exec.py:726` | P0 | §5.1 |
| P1-1 | 执行性判定被放在 design 之后：回路是「盲写 → 实测 → 盲改 → 封顶」，回灌只能撞环境 | `qa.py:1172-1268` | P1 | §5.7（硬停）+ §5.6（静态前置） |
| P1-2 | 失败无归因（`status/lint/error` 里没有 blame），环境问题被当成用例缺陷烧掉 design 轮 | `qa.py:1202-1207`、`qa_verify.py` `VerifyResult` | P1 | **不做**（见 §4.3） |
| P1-3 | 修补轮缺断言强度守卫：`covers` 可缩小、verify.sql 可退化，而 `bodies_before` 守卫只用在 feedback 轮 | `qa.py:2318-2325`（feedback 轮有）、`_verify_loop` 无 | P1 | §5.4（口径）+ 记待办 |
| P2-1 | design 期页面完全静默：`print_mode` 下 pi 输出走 `sys.stdout` 而非 `job.append`；且 `pi -p` 默认 `--mode text` **跑完才输出**，所以静默不是 bug 但会被读成「卡死」 | `runners/__init__.py:679-692`、`web/jobs.py:750` | P2 | 本次不动 |
| P2-2 | 前置检查输出未按现场归组（`resolve/ping/hello/origin` ×2、`db` ×3 平铺一行），读起来像重复执行；对应 2 个 origin × 3 个 catalog | 前置检查日志 | P2 | 本次不动 |

> 原稿的 P2-2「宿主与执行现场对『合法单语句』判据不一致」经复核**定性错误**，已升级为本文 P0-3。

## 4. 决策

### 4.1 现场缺需求分支迁移 → **design 前硬停**

跑 `req test`（含 `--design-only`）前置就比对「需求分支新增的迁移」与「现场库实际列」，缺了就拒绝，**不进 design**。目标态：

```
$ dev-yard req test PG-13227
[preflight] 现场缺需求分支迁移：
  db/migrate/20260925120000_add_is_existing_contact_to_firm_tasks.rb
  → 缺列 firm_tasks.is_existing_contact
-> 拒绝。不进入 design。
-> 0 pi run / 0 现场脚本执行
```

**不留 `allow_missing_branch_deploy` 之类的豁免开关**（原稿 D3 留了，本次明确不留）：真需要「故意测未部署分支」时，换 env 或临时改站点配置；少一个开关少一条静默放行的路。

### 4.2 脚本语法/契约类错误 → **静态 lint 前置 + 把事实注入 prompt**

这类事实**宿主本来就知道，或能静态推出来**，却要 agent 撞一次才学到。两件事同时做：

- **注入事实**：现场 Rails 版本升为配置字段，连同被 freeze 的模型，写进 `context.md`，随 design 提示词一起送到（`SKILL.md` 已要求 context.md 必读且逐条遵守）。
- **前置拦截**：`lint_cases`（M7）加三条静态规则，在**首次核实之前**拦下。命中时多花一次有界修补 pass（1 次 pi），换掉的是「1 次失败核实 + 1 次全量 pi 回灌」，净赚。

### 4.3 不做

- **不做 `VerifyResult.blame` 归因分流**（原稿 D2）：选了硬停之后，「现场未部署」根本到不了 design，归因机器的收益消失。等真出现硬停挡不住的环境类失败再说。
- **不做 design 期只读探针**（原稿 D4）：§5.6 的静态规则用远低的代价覆盖同一批失败；D4 单独立项、单独评审。
- **不提高 `design_verify_attempts`**；**不自动部署分支到测试环境**（沿用 `docs/script-exec-recipes.md` 非目标）。
- 不把平台细节（`src/`）暴露给 design agent；但**契约（列来源、语法事实）以机器可读形式进 prompt**，而不是靠打回让 agent 猜。

## 5. 实施方案

### 5.1 修宿主包装 SQL 的 Oracle 语法（P0-3，1 行）

`src/dev_yard/qa_exec.py:726` 去掉 `AS`：

```python
# Bare alias, never `AS`: Oracle rejects `FROM (...) AS x` with ORA-00933,
# while it accepts `FROM (...) x` — which Postgres and MySQL accept too.
query = f"SELECT count(*) FROM ({body}) qa_verify" if head in {"select", "with"} else body
```

不需要引方言判断。补一条 `SELECT 1 FROM DUAL` 形态的回归测试（`localhost` 上无 Oracle 时打桩 `_run`，断言发出的 SQL 不含 `AS`）。

### 5.2 新增 `src/dev_yard/qa_deploy.py`：从需求分支推出「现场应该有什么」

新模块，纯函数，便于脱离 preflight 直测（`qa_ready.ENABLED` 在单测里是关的）：

| 函数 | 职责 |
|---|---|
| `requirement_migrations(root, jira, alias) -> list[str] \| None` | 该需求**新增**的迁移：`git -C <worktree> diff --name-only --diff-filter=A <diff_base>...HEAD -- db/migrate`。`diff_base` 复用 `context.md`/meta 里已有的那个（`SKILL.md` 已在用）。worktree/git 不可用 → `None`（不可判定，不阻塞） |
| `branch_new_columns(migrations) -> dict[str, set[str]]` | 按文件名顺序重放 `add_column` / `create_table` 块内字段（跳过 `t.timestamps`）/ `remove_column`，得净新增列 `{table: {col}}` |
| `frozen_models(root, jira, alias) -> set[str]` | 扫 worktree 里 `include FreezeModelConcern`（含同名 concern）的模型，给出被冻结的模型名 |
| `missing_on_site(root, jira, cfg, alias) -> tuple[list[str], list[str]] \| None` | 对每个 (table, col) **定向**探测：只查该 alias 声明的 catalog（`repo.databases`，回退 `_catalogs_for_requirement`），任一命中即视为已部署。探测失败 → `None` |

**定向探测是硬要求，不能用整表 dump**：reach 的 `Live columns` 已被 `_SCHEMA_DUMP_MAX_LINES = 250` 截断（`context.md` 里有 `…(truncated)`），拿截断结果判「列不存在」会误报。

### 5.3 `src/dev_yard/qa_verify.py`：列类型探测 + 失败提示改口径

- 新增 `dump_live_column_types(cfg, tables, catalog) -> dict[str, dict[str, str]]`：**只查点名的表**，供 §5.6 的 lint 用；**不渲染进 `context.md`**（避免 84KB 的 context 再膨胀）。复用 `run_sql_lines`。
- `enrich_verify_hint`（`qa_verify.py:455-464`）：命中「列 X 不存在」时，先查该列是否属 `qa_deploy.branch_new_columns`。属需求新增且现场未部署 → 不再说「列名从 worktree schema.rb / ORM 读」，改说「该列属本需求新增、现场未部署，**保留断言**并在用例备注标注」。
- `render_feedback` 前言（`qa_verify.py:1086`）：`列不存在：用提示里的真实列名…` 之后追加同一条。

### 5.4 `src/dev_yard/qa.py`：context.md 补两路事实 + 提示词不再教唆删断言

`write_context_md`（`qa.py:105-261`）：

- `## Database columns` 下新增子节 **`### 需求新增·现场未部署（断言不得删除）`**，列出 `branch_new_columns` 减去现场列的结果，每条带迁移文件名。这是 A 类的直接解药；顺带也让 agent 知道「本需求新增了哪些列」——这本身就是设计用例的核心上下文。
- 新增段 **`## 现场脚本契约`**，按 exec site 列出：runner、Rails 版本（§5.5）、被 freeze 的模型。`SKILL.md` 已要求 context.md 必读且逐条遵守，所以这节天然进 prompt。

四处提示词改口径（**这是 A 类被系统化改写的真正来源**）：

| 位置 | 现状 | 改为 |
|---|---|---|
| `qa.py:191-195`（context.md 章节头） | 列名必须来自本节…不要猜列名 | 补：属「需求新增·现场未部署」子节的列**必须**写进断言 |
| `qa.py:913-920`（`_duties("design")`） | `…or worktree schema.rb` | 补同上（保留 `schema.rb` / `information_schema` 字样，避免动 `test_qa_duties_and_skills_stay_in_sync`） |
| `qa.py:1063`（repair 轮） | `列名以 context.md Database columns 为准；模型不认的字段从 setup 删掉` | `…以该节为准（含「需求新增·现场未部署」子节）；该子节的列不得删，保留断言并在用例备注标注待部署` |
| `qa_verify.py:1086` + `enrich_verify_hint` | 见 §5.3 | 见 §5.3 |

### 5.5 `exec_cfg.py` + `qa.yaml`：现场 Rails 版本升为配置字段

`QaExecSite`（`exec_cfg.py:154-164`）加 `rails: str = ""`，同步 `_SITE_KEYS`（`exec_cfg.py:33-44`）与 `_parse_exec_site`（`235-278`，照 `sql_runner` 的样子取值）。

**同时必须补 web 表单往返，否则 UI 一保存就把字段吃掉**：

- `exec_payload`（`exec_cfg.py:568-593`，两处 `sites_out` 构造）
- `exec_from_form`（`exec_cfg.py:706-735`，`sites_in` 循环）
- `web/src/api/types.ts:453` 附近的 site 类型
- `web/src/views/QaConfigView.vue:~970` 的站点默认值工厂（`le()`）

`qa.yaml` 给两处 `reach` 站声明 `rails: "2.1.1"`（local 与 test）—— 这正是 `qa.yaml` 行内注释里已经手写的事实，从注释升成字段后，既进 `## 现场脚本契约`，又当 lint 判据。

### 5.6 `qa.py`：`lint_cases` 新增三条静态规则（兜住 B 类）

`lint_cases`（`qa.py:801-855`）已有 7 条规则与「一次有界修补 pass」（M7，`qa.py:2337-2396`）。新增：

| 规则 | 判据 | 报错示例 | 覆盖 |
|---|---|---|---|
| 主键类型 | `<数值列> = '<非数字字面量>'`，且该列在 §5.3 的类型探测里是数值类型 | `firm_tasks.id 是 NUMBER，不能写 id = 'qa-pg13227-c02'` | 4/11 |
| legacy AR | 该 case 的 exec site 声明 `rails` < 3，且脚本命中 `\.where\s*\(` | `reach 站是 AR 2.1.1，无 Model.where，用 find(:all, :conditions => …)` | 5/11 |
| freeze 模型 | 脚本命中 `\.delete_all\b`，且模型在 `frozen_models()` 里 | `FirmTask 带 freeze_model_concern，不能 delete_all` | 1/11 |

三条规则**只在事实齐备时触发**（类型探测失败 / 站点未声明 `rails` / 未找到 freeze concern 即跳过），避免误伤；每条都要有反例测试。

### 5.7 `qa_ready.py`：新增 `deploy` 前置步骤 = 硬门禁（P0-2）

`assess_ready`（`qa_ready.py:136-174`）加一步 `{"step": "deploy", ...}`，用 §5.2 的 `missing_on_site`：

- 有缺失 → `status: "fail"`，detail 列出**迁移文件名 + 列**。`assert_ready` 据此 `raise TestRejected`。
- 不可判定（worktree/git/DSN/`diff_base` 取不到 → 迁移列表为空或探测失败）→ `status: "warn"`，**不阻塞**（`ok = env_ok and all(status != "fail")`，warn 不参与），并在 job log 说明原因，**不静默放行**。

落点已经现成：`req_test` 在 `qa.py:2217-2229` 调 `assert_ready`，位于 design（`2295`）与 M7（`2337`）之前，天然满足「0 pi run / 0 现场脚本执行」。`web/jobs.py:769` 已把 `TestRejected` 写进 job log；`reqboard` 的 `can_design_qa` 也会随之变灰。

### 5.8 `.pi/skills/qa-design/SKILL.md`

「表/列名不要猜」那条补上 `### 需求新增·现场未部署` 子节的用法（属该子节的列必须写进断言、不得删）；提一句 `## 现场脚本契约`。

### 5.9 文档与既有测试同步

- 同步既有测试：`test_repair_prompt_does_not_ask_for_a_redesign`（`tests/test_qa_verify.py:398-426`）、`test_qa_duties_and_skills_stay_in_sync`（`tests/test_qa.py:1074-1090`）、`tests/test_qa_ready.py`（新增 step）、`exec_cfg` 表单往返相关测试。
- 修本文档原稿的三处不实（已在本文中改掉）：`qa/summary.yaml` → `qa/design-verify/summary.yaml`；`design.yaml` 的 fingerprint 盘上是 `02c655409e2031f07d4668b89cf10f17270aae8b`（与 `review.yaml` 一致），`3e92daaf…` 全仓 grep 不到、不可复现，故不再引用；P2-2 定性纠正为 P0-3。

## 6. 验收判据（机器可测）

1. **§5.1**：`run_sql_count` 发出的 SQL 不含 `AS qa_verify`；`SELECT 1 FROM DUAL` 形态走通。
2. **§5.2/5.7**：构造「分支有迁移、现场无列」的 fixture → `assert_ready` 抛 `TestRejected`，且 design runner **一次都没启动**；探测失败/`diff_base` 缺失 → 只 warn、不阻塞。
3. **§5.4**：同 fixture 下 `context.md` 含「需求新增·现场未部署」且列出该列。
4. **§5.4**：repair prompt 含「不得删」，且**不再含**「模型不认的字段从 setup 删掉」。
5. **§5.6**：三条规则各一条正例 + 一条反例（事实缺失时**不**报）。
6. **不回归**：`design_verify_attempts` 保持 3；纯 UI 用例的 `SELECT 1` 豁免（`qa_verify.py:276-277`、`qa_ready`）行为不变。

## 7. 验证方式

复用 `tests/test_qa_verify.py` 的既有风格（`_write_qa_yaml` / `_testing_req` / `_CaseWriter` / `_DesignRunner` 假 Runner、`monkeypatch.setattr` 打桩 `run_sql_count` / `run_sql_lines`）；仓库无 fixture 目录，全部在 `tmp_path` 里现场造。

门禁命令（CI 同款，见 `.github/workflows/ci.yml`）：

```bash
uv run pytest
uvx ruff check src tests
```

真机回归：

```bash
# 期望：preflight 阶段非零退出，stderr/job log 含迁移文件名，pi 一次都没起
dev-yard req test PG-13227 --design-only

# 核对 pi 未启动：design.yaml 的 generation 与 job log 都不该变
curl -s "http://localhost:8765/api/jobs/<job>" | python3 -m json.tool | tail -5
```

待 test 环境部署该迁移后重跑，期望 11 条中不再有 A 类失败。

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 硬门禁误伤（现场其实已部署，但探不到） | 只做**定向**按列探测；探测失败一律 `None` → warn 不阻塞；任一 catalog 命中即放行 |
| `diff_base` 取不到 → 迁移列表为空 → 门禁静默失效 | 空列表降级为 warn，并在 job log 说明「未能判定需求迁移」 |
| web 表单保存吃掉 `rails` 字段 | §5.5 明确同时补 `exec_payload` / `exec_from_form` / TS 类型 / Vue 默认值工厂，并补往返测试 |
| 新增 lint 规则误报 → 多烧一次 M7 修补 | 规则只在事实齐备时触发；每条都有反例测试 |
| 硬门禁无豁免开关 | 已是明确决策（§4.1）；真要调试用 env 切换绕开 |

## 9. 关联证据（可复现）

```bash
# 任务终态与日志尾
curl -s "http://localhost:8765/api/jobs/bb02c77aed" | python3 -m json.tool | tail -5

# context.md 里没有需求的核心列（关键证据）
grep -n is_existing_contact reqs/PG-13227/qa/context.md                    # 零命中
grep -n is_existing_contact reqs/PG-13227/worktrees/reach/db/schema.rb     # 零命中
ls reqs/PG-13227/worktrees/reach/db/migrate/20260925120000_add_is_existing_contact_to_firm_tasks.rb

# 现场真缺列（不是 dump 失败）：两处 catalog 都列了 firm_tasks 全列
grep -n '^Live columns' reqs/PG-13227/qa/context.md        # main@38, reach@290
sed -n '158p;410p' reqs/PG-13227/qa/context.md

# 逐条失败原因（第 3 轮）
cd reqs/PG-13227/qa/design-verify && for f in case-*.yaml; do \
  python3 -c "import yaml;d=yaml.safe_load(open('$f'));print(d['case'],d['status'],(d.get('error') or '')[:80])"; done

# 人工交接清单
sed -n '1,20p' reqs/PG-13227/qa/design-verify/BLOCKED.md
```

相关代码：`qa_exec.py:703-748`（包装 SQL）、`qa.py:105-261`（context.md 生成）、`qa.py:801-855`（`lint_cases`）、`qa.py:1036-1087`（design 提示词）、`qa.py:1172-1268`（`_verify_loop`）、`qa.py:2217-2229`（`assert_ready` 落点）、`qa.py:2337-2396`（M7 静态契约 lint，正确形状的先例）、`qa_verify.py:265-314`（`lint_verify`）、`qa_verify.py:371-444`（schema 两路来源）、`qa_verify.py:447-488`（`enrich_verify_hint`）、`qa_verify.py:1078-1106`（`render_feedback`）、`qa_ready.py:136-196`（`assess_ready` / `assert_ready`）、`exec_cfg.py:33-44/154-164/235-278/554-636/639-735`（站点配置与表单往返）、`web/jobs.py:723-818`（qa-* job 分支）。
