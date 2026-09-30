# yard-qa 设计回路修复（二）：从「症状黑名单」到「可查询事实 + 谓词级归因」

- 日期：2026-09-30
- 状态：**提案，待评审**
- 触发需求：`reqs/PG-13227/`（firm_task 详情页「已有联系人」勾选 / 完成度报表）
- 触发操作：web 需求页「设计用例」，job `df62d62fbc`（`action=qa-design`）
- 关联产物：`reqs/PG-13227/qa/design-verify/*.yaml`、`BLOCKED.md`、`design.yaml`、`state.yaml`
- 关联前作：`docs/2026-09-29-yard-qa-design-verify-blind-loop.md`（其 §5 已全部落地，本次是其**第二次发作**）
- 一句话：**上一轮拍板的 3 条静态规则 + 硬门禁今天命中 0/17；而更糟的是，盲修把「猜错的环境」固化成了用例语义——9 个 setup 被统一改成「回退到任意在职员工」，其中一个还写下了假结论的注释。**

> 前作的硬门禁**今天生效了**：前置检查 `deploy=ok`，当天没有出现「需求列未部署」那一类失败。本文不推翻前作，只处理它没覆盖的部分。

## 1. 现象与经过

| 时间（本地） | 事件 |
|---|---|
| 10:15 | `qa/context.md` 生成（85KB） |
| ~10:29 | `qa/accounts-discover.sql` 落盘 |
| 10:48–10:57 | `meta.yaml`、`cases/{detail,overseas,satisfy_report,task_list}/`、`OPEN-QUESTIONS.md` 落盘 |
| 10:57–11:00 | 第 1 轮现场核实：**21 条中 17 条 failed**（`design-verify/case-*.yaml`） |
| — | 回灌第 1 次：`数据核实 17 条未通过，只修补这些用例（第 1/2 次）` |
| — | 第 2 轮核实：**4 条 failed** |
| — | 回灌第 2 次：`数据核实 4 条未通过，只修补这些用例（第 2/2 次）` |
| — | 第 3 轮核实：**仍 4 条 failed** → 封顶 |
| 终态 | `state=ok`；`verify passed=17 failed=4 blocked=0 skipped=0 total=21`；4 条标 `design-blocked`；`review=rejected`；`OPEN-QUESTIONS=9`；**未进入 run** |

成本（取自 job log，可复现）：**3 次 pi 设计轮**（原始 + 2 次修补）、**3 轮现场核实**、**104 次 ssh/kubectl 执行**。

关键观察：**第 2 次修补轮把 4 条修成 4 条，收益为零**，白烧 1 次 pi + 约一轮远程执行。

## 2. 证据链

### 2.1 第 1 轮 17 条的四簇（逐条取自 `design-verify/case-*.yaml`）

| 簇 | 条数 | 表面错误 | 真实根因 |
|---|---|---|---|
| A | **9** | `找不到销售/客服口径下的在职员工` → `exit 1` | `PointSalesSetting::CLIENT_DEPARTMENT_ID = 22` 被当成**部门 id** 用 |
| B | **3** | `freeze_model_concern.rb:59 不支持修改FirmTask！` | 用例路由到 research（带 `FreezeModelConcern`），却用 AR 写 `FirmTask` |
| C | **2** | `ORA-00907: missing right parenthesis (position 310)` | verify.sql 用了 Oracle 12c+ 的 `FETCH FIRST`，现场是 11.2 |
| D | **3** | 各一条 | D1 引用不存在的常量；D2 verify 与 setup 互斥；D3 Ruby 字符串比数值列 |

> 取数说明：本节四簇来自第 1 轮 `design-verify/case-*.yaml` 的 `error` 字段。**这些文件随后被修补轮覆盖**（见 §3 P2-2），所以 §2.1 的表只在本会话的记录里可复现；§2.2/§2.3/§2.5/§2.6 的判据都改用了盘上仍在或可直接问库的证据。

### 2.2 A 类：22 不是部门（实测）

9 个 setup 共用**同一行**（复制粘贴，如 `cases/detail/setup_detail_display.rb:36-43`）：

```ruby
creator = Employee.first(
  :conditions => [
    "department_id in (select id from departments where id = :dep or parent_id = :dep) " \
    "and active = 1 and allow_login = 1 and username is not null and REGEXP_LIKE(username, '^[a-z][a-z0-9._-]*$')",
    {:dep => PointSalesSetting::CLIENT_DEPARTMENT_ID}
  ],
  :order => "id"
)
```

`PointSalesSetting::CLIENT_DEPARTMENT_ID = 22`（`.repos/reach/app/models/point_sales_setting.rb:35`），其上方注释是「**查询部门数据ID**」。实测：

```sql
-- departments 表里没有 22
SELECT id, name, parent_id FROM departments WHERE id IN (22,3,4,10162,10164,10002,10022);
--  → 3 工程信息销售部 / 4 工程信息客服部 / 10002 瑞达恒研究院 / 10022 销售二部 / 10162 慧讯网销售部 / 10164 慧讯网客服部
--  没有 22，也没有 parent_id = 22 的行

-- 22 是 point_sales_settings 的键
SELECT count(*) FROM point_sales_settings WHERE department_id = 22;   -- → 7746
```

子查询恒为空集 → `Employee.first` 恒 nil → 9 条 setup 全部 `exit 1`。**这不是数据偶发缺失，是结构性恒失败。**

对照 4 条**通过**的用例，它们用的是 `Department::SALES/CCE/PRICE_SALES/PRICE_CCE/RESEARCH_INSTITUTE`（`department.rb:80/84/125/123/97`，即 3/4/10162/10164/10002），各口径可用员工数：**213 / 92 / 57 / 21 / 26**；而 `10022 销售二部` 是 0、`22` 无行。

### 2.3 修补轮把 9 条的语义改掉了（本次最关键的新证据）

第 1 次修补后，9 个 setup **全部**把部门判据删了，改成「任一非 w.deng 的在职员工」回退：

```ruby
# cases/task_list/setup_tasklist_no_filter.rb:13-22（9 个文件的共同形状）
creator = Employee.first(
  :conditions => ["active = 1 and allow_login = 1 and id <> ? "
                  "and username is not null and REGEXP_LIKE(username, '^[a-z][a-z0-9._-]*$')", default_user.id],
  :order => "id"
)
warn "... 找不到非 w.deng 的在职员工作 created_by 回退"
```

`cases/detail/setup_detail_display.rb` 还把这个**错误结论**写成了注释（该文件是 9 个里唯一保留 `CLIENT_DEPARTMENT_ID` 字样的）：

```ruby
# 3) 找 created_by 候选人：任一非 w.deng 的在职员工（测试环境无销售/客服口径下员工，回退到任意在职员工）
#    verify.sql 仅校验种子任务存在与状态，不依赖 created_by 部门归属
```

「测试环境无销售/客服口径下员工」**与实测相反**（五部各有 213/92/57/21/26 名可用员工）。agent 只是查错了地方（22），就断言环境里没有这类人。

**这是盲回路真正的代价**：它不只烧轮次，它让 agent 在缺少事实时**发明一个解释并固化进用例**。前作 §2.3 记的「教唆删断言」是同一模式的上一代形态。

### 2.4 现有静态规则命中 0/17

`_lint_script_facts`（`qa.py:922`）现有三条模式规则，每条都是**上一次事故的具体症状**：

| 规则 | 位置 | 上一轮的病因 | 今天 17 条 |
|---|---|---|---|
| `_WHERE_AR` AR `.where` | `qa.py:897,939` | reach 是 AR 2.1.1 | 命中 0 |
| `_DELETE_ALL` 冻结模型 | `qa.py:898,947` | 上一轮某条用了 `delete_all` | **漏**：今天的调用点是 `.new(...).save` |
| `_EQ_STRING` 数值列 = 字面量 | `qa.py:894,960` | 上一轮 `id = 'qa-…'` 报 ORA-01722 | **漏**：今天的类型错在 Ruby 里（`[...].join == NUMBER`） |

即：**同一根因（freeze 模型 / 类型错配）换了调用点就漏。** 这不是规则写坏了，是「症状黑名单」范式的固有属性——它只能拦已知的坏形状。

### 2.5 残留 4 条：全是「世界状态」假设

| 用例 | 错误 | 性质 |
|---|---|---|
| `case-overseas-personal-excludes-marked` | `期望 n_personal=1 n_baseline=2，实际 n_personal=4 n_baseline=7` | setup 假设基线计数为 2，实际 7 |
| `case-overseas-dept-no-filter` | `部门口径过滤后 n_dept_filtered=30, 期望 n_dept-1=33` | 同上，基线漂移 |
| `case-overseas-team-no-filter` | `团队口径过滤后 n_team_filtered=31, 期望 n_team-1=34` | 同上 |
| `case-satisfy-inquiry-staff-view-only-self` | `from (eval):19`（已从 `EmployeesGroup` 常量改为裸 SQL，仍报错） | 待查 |

三条 overseas 的形状一致：setup 先算基线、再造种子、然后断言「造种子使计数恰好 +1」。**基线计数是个可由一次 SELECT 直接读到的真实值**（`ClientRequestStatistics.count_completed_firm_task`），agent 却只能猜。

对照：同文件里 `skip_freeze = true` 的三处修复是**正确且外科式**的（`cases/overseas/*.rb` 各一处，带注释）。差别在于——freeze 的报错文案直接说了该怎么做（「不支持修改FirmTask！请修改Oracle数据表数据」），而 A 类的报错只有「找不到…员工」，于是 agent 只能编。**归因质量决定修补质量。**

### 2.6 C 类的精确复现

`verify_satisfy_save_snapshot.sql` 与 `verify_satisfy_del_bonus.sql` **字节相同**，故报错位置都是 310。`FETCH FIRST 1 ROW ONLY` 落在去注释后的第 310 字符。实测：

```bash
usql <reach/oracle> -c "SELECT 1 FROM dual ORDER BY 1 FETCH FIRST 1 ROW ONLY"   # → ORA-00933
usql <reach/oracle> -c "SELECT 1 FROM dual WHERE ROWNUM = 1"                    # → 1 ✓
```

现场 Oracle 版本实测 `11.2.0.1.0`（`product_component_version`）——`FETCH FIRST` 是 12c+ 语法。

## 3. 平台问题清单

严重度：P0 = 直接造成本次大部分失败；P1 = 系统性缺陷；P2 = 次要。**「修法」列指向 §5。**

| # | 问题 | 位置 | 级别 | 修法 |
|---|---|---|---|---|
| P0-1 | 静态规则是「症状黑名单」，命中 0/17；同根因换调用点即漏 | `qa.py:894-980` | P0 | §5.2 |
| P0-2 | 设计 agent **无任何取事实通道**：方言、取值域、模型清单、基线计数只能猜 | `.pi/skills/qa-design/SKILL.md:92`、缺 | P0 | §5.1 |
| P0-3 | 盲修把「猜错的环境」固化成用例语义（9 条降级 + 1 条假注释） | `qa.py:1302-1348`（回灌） | P0 | §5.1 + §5.4 |
| P1-1 | verify.sql 的语法/列错要等远程核实才发现 | `qa.py:836`（`lint_cases` 不跑 SQL） | P1 | §5.3 |
| P1-2 | verify 返回 0 行时无谓词级归因，只能整轮回灌 | `qa_verify.py`（`VerifyResult`） | P1 | §5.4 |
| P1-3 | 回灌按用例平铺，不聚类 → 同一根因的 N 条各写一遍 | `qa_verify.py:1177`（`render_feedback`） | P1 | §5.5 |
| P1-4 | 修补轮无收益判定：第 2 次修补 4→4，白烧 1 pi + ~1 轮远程 | `qa.py:1302-1348` | P1 | §5.5 |
| P2-1 | catalog 缺方言事实（runner 有 `rails` 版本，库没有对应物） | `exec_cfg.py`、`qa.yaml` | P2 | §5.2 |
| P2-2 | **每轮核实覆盖上一轮的失败原文**：`design-verify/case-*.yaml`、`summary.yaml`、`review.yaml:feedback` 都是 last-round-only；job log 只记 exec 命令不记脚本输出；`reqs/` 又在 `.gitignore` 里 → 一轮走完后无法复盘「第 1 轮为什么挂 17 条」 | `qa_verify.py`（写 summary/blocked）、`.gitignore:25` | P2 | 本次不做（待办：按轮次归档，或让回灌文本追加而非覆盖） |

## 4. 决策

### 4.1 推翻前作 §4.3「不做 design 期只读探针」

前作的理由是「静态规则以远低代价覆盖同一批失败」。今天的账：**静态规则覆盖 0/17，探针能覆盖 A（9）+ C 的发现 + §2.5 的三条基线漂移（共 13/17）**。

代价侧也要认：探针是**宿主中介**的（agent 不碰 ssh/kubectl/DSN）、**只读**、**单语句**、**有配额**——这与前作担心的「放 agent 自由探库」不是一回事。且宿主**已经有这个原语**：`qa/accounts-discover.sql`（agent 出只读 SQL，宿主代跑，见 `qa_accounts.py`）。本次只是把它从「找账号」泛化出去。

保留前作 §4.3 的另一半：**仍不做 `VerifyResult.blame` 归因分流**。§5.4 的谓词级归因是机械的（delta-debug），不是「分类器」。

### 4.2 白名单可以倒，但要说清它治不了 A 类

§5.2 的 `facts.yaml` 能让「引用不存在的东西」变成静态错误（D1 必然被抓）。但 A 类的常量**存在**——它只是**语义不是部门 id**。存在性白名单治不了语义错配。

**A 类的解药只有 §5.1（探针，取到真值）**，别指望规则化。这条要写进实施者的预期里。

### 4.3 不做

- **不提高 `design_verify_attempts`**：本次第 2 次修补 0 收益，是「无事实可依时重试无用」的直接证据。
- **不做通用断言强度守卫**（前作 P1-3 的待办）：本次 9 条是**删掉前置判据**而非放宽断言，守卫的形状不同，单独立项。
- **不放开 agent 直连 DSN / ssh**：探针必须走宿主。
- **不自动部署分支 / 不改环境**：沿用前作。

## 5. 实施方案

> **实施者先读 §5.7**（顺序、落点、四个会踩的坑），再回来读 5.1–5.6。

### 5.1 新增 `qa/probes/*.sql`：把 `accounts-discover` 泛化成通用探针

- **产物**：`qa/probes/<name>.sql`，单条只读 `SELECT`/`WITH`，允许开头注释（复用 `qa_accounts.py` 已有的单语句校验）。
- **执行**：新增 `src/dev_yard/qa_probe.py`，宿主代跑，复用 `run_sql_lines`：
  - 目标 catalog：文件首行 `-- probe: <catalog>` 指定，缺省该需求主 catalog；
  - 行数上限（如 50）与大结果截断；单次运行探针条数上限（如 12）；
  - 失败带真实错误（沿用 `9fbcf26` 的方言探测口径）。
- **回路**：在 `context.md` 生成与第 1 次 design 之间插一个**有界探针轮**（预算 1 次，镜像 `design_verify_attempts` 的写法）：
  1. design pass 1 只做两件事：读需求/worktree，写 `qa/probes/*.sql`，并**停下**；
  2. 宿主跑探针 → 结果写 `qa/probe-results.md`；
  3. design pass 2 带着 `probe-results.md` 正式写用例。
- **提示词**：`SKILL.md:92` 那句「设计阶段不要自己跑 usql 探库」改为「不许自己连库；需要真实取值/方言确认时，写 `qa/probes/*.sql` 交给宿主跑」。`_duties("design")` 同步。
- **覆盖**：A（22 是否是部门、五部有多少可用员工）、C（该构造在本库能否跑）、§2.5（基线计数实际是多少）。

### 5.2 新增 `qa/facts.yaml`：把 lint 从黑名单倒成白名单

- **产物**：与 `context.md` 同处生成（扩展 `write_context_md`，`qa.py:106`），机器可读：

```yaml
catalogs:
  reach: {dialect: oracle, version: "11.2.0.1.0",
          tables: {firm_tasks: {id: NUMBER, task_name: VARCHAR2, ...}}}
  main:  {dialect: postgres, version: "16", tables: {...}}
worktrees:
  reach:  {constants: [Department::SALES, PointSalesSetting::CLIENT_DEPARTMENT_ID, ...],
           frozen_models: [FirmTask]}
sites:
  reach: {runner: "ruby script/runner /dev/stdin", rails: "2.1.1"}
```

- **新规则**（加进 `lint_cases`）：
  1. 脚本引用的**表/列**必须在该 case catalog 的 `tables` 里，否则拒（表/列名错、方言外构造）；
  2. 脚本引用的 **`Const::NAME`** 必须在 `constants` 里，否则拒（**这条抓 D1 的 `EmployeesGroup`**）；
  3. 写 `frozen_models` 里的模型 → 要求同文件出现 `skip_freeze = true`。**从「拦 `.delete_all`」改成「正面要求逃生口」**——本次 B 类就是换调用点漏的，正面要求对调用点不敏感。
- **频率**：`version` 探测失败即跳过该 catalog 的方言类判据（沿用前作「事实缺失不触发」的原则）。
- **注意**：`constants` 用 worktree 静态扫描（`app/models/**/*.rb` 的 `class X` / `X = <字面量>`）。这是**存在性**事实，治不了 A 类语义错配（见 §4.2）。

### 5.3 `lint_cases` 期把 verify.sql 跑一次（只判语法/列）

在 M7（`lint_cases` 的调用点 `qa.py:2494` 一次有界修补 pass / `qa.py:2594` 契约检查）对每条 `verify.sql` 走**宿主自己的包装器**（`run_sql_count`）在目标 catalog 上跑一次，加行数上限：

- **只解析语法与列/表存在性**；返回 0 行**不算失败**（此刻 seed 还没造出来，0 行是正常的）。
- 语法错（**C 类 ORA-00907 在此被拦下**）、列不存在、表名错 → 归因到 SQL 本身，在**任何远程执行之前**修掉。
- 这条与 §5.2 规则 1 有重叠，但 §5.3 能覆盖 §5.2 静态判不出的一切（函数不存在、保留字、构造不可用）——**跑一次比列清单更权威**。

### 5.4 核实期的谓词级归因（delta-debug）

`verify_cases` 返回 0 行时，**趁 seed 还在**（setup 刚跑过），做机械归因：逐个去掉 WHERE 里的合取项重跑，找出让结果从 0 变非 0 的那一项。

- 输出：「断言 `e.id = ft.created_by` 与 `e.username = 'w.deng'` 互斥：去掉前者即 1 行」——**D2 的精确解**。
- 替代现在的 `0 rows` + 泛泛 hint（`qa_verify.py` 的 `enrich_verify_hint`）。
- 只对 0 行的 case 做，最多 N 次探测，失败即回退到现有文案。

### 5.5 回灌：按根因聚类 + 零收益即停

`render_feedback`（`qa_verify.py:1177`）：

- **聚类**：按「归一化错误签名」分组（同一 setup 函数名的公共行 / 同一 offending SQL 片段 / §5.4 的归因结论），输出先给一行「**这 9 条同一根因：`department_id in (select … id = :dep …)`**」，再列用例 id。
- **零收益即停**：`_verify_loop`（`qa.py:1302`）记上一轮失败集合；若本轮失败集合与上轮**相同**，直接封顶、不再烧一次 pi + 一轮远程。本次第 2 次修补 4→4，这条能省下整整一轮。

### 5.6 测试

- `qa_probe.py`：单语句校验、只读校验（含 `WITH`）、配额、结果截断、catalog 选择；打桩 `run_sql_lines`。
- `facts.yaml` 生成：常量扫描（含不存在反例）、freeze 集合、方言探测失败降级。
- 三条新 lint 规则：每条一正一反（事实缺失时**不**报）。
- delta-debug：0 行 case 归因到正确合取项；非 0 行不动。
- 聚类 + 零收益即停：同一失败集合连续两轮 → 只跑一轮。

### 5.7 实施者须知：顺序、落点、四个坑

**建议顺序**（每一步都可独立合入、独立验证；越靠前越便宜）：

1. **M5 零收益即停 + M4 回灌聚类** —— 都在 `_verify_loop` / `render_feedback` 内，不引入新概念，立刻降本。本次第 2 次修补 4→4 就是 M5 的目标场景。
2. **M3 lint 期跑 verify.sql** —— 自包含，用现成的 `run_sql_count`，不需要新配置。
3. **M2 `facts.yaml`** —— 需要新增生成 + 规则。
4. **M1 探针轮** —— 唯一改动 agent 指令（`SKILL.md` + 提示词 + 配置 + 流程）的一步，评审负担最重，放最后。

**一处可以砍掉的重叠**：M2 的规则 1（表/列必须在 `facts.yaml` 里）**被 M3 完全覆盖**——把 SQL 真跑一次比查列清单更权威，还能覆盖函数不存在/保留字/构造不可用。实施时只做 M2 的规则 2（常量存在性）与规则 3（frozen → 必须 `skip_freeze`）；`facts.yaml` 仍要生成（供提示词注入方言与 freeze 事实），但**不要再写一套表/列白名单校验**。

**落点（本节数字均已核准）**：

| 要改的东西 | 位置 |
|---|---|
| `write_context_md`（`facts.yaml` 与它同处生成） | 定义 `qa.py:106`，调用 `qa.py:2326` |
| 第 1 次 design 的调用点（**M1 探针轮插在 `2326` 与 `2445` 之间**） | `qa.py:2443-2445` |
| M7 契约 lint 与其一次有界修补 pass（**M3 落这里**） | `qa.py:2494` / `qa.py:2508` |
| `_verify_loop`（**M5 落这里**） | `qa.py:1302-1348` |
| 回灌 prompt 构造（M4 的聚类文本从这里进） | `_design_prompt` 定义 `qa.py:1164`，回灌调用 `qa.py:1357` |
| design pass 的启动形状（**M1 的探针轮照抄**） | `qa.py:1361-1370`：`design_runner().start(prompt, root, attachments.with_images(root, jira, [qa, paths.req_dir(root, jira)]))` |
| `render_feedback`（**M4 落这里**） | `qa_verify.py:1177` |
| **只读单语句校验（M1 直接复用，别另写）** | `qa_accounts.assert_readonly_sql`（`qa_accounts.py:55-75`） |
| 跑 SQL 的两个原语 | `run_sql_lines`（`qa_exec.py:672`）、`run_sql_count`（`qa_exec.py:703`），签名 `(cfg, sql, on_log=None, catalog=None, *, verify=False)` |
| 列类型探测（`facts.yaml` 的表列部分可复用它） | `dump_live_column_types(cfg, tables, catalog=None)`（`qa_verify.py:469`） |
| freeze 模型集合（M2 规则 3 的判据） | `qa_deploy.frozen_models`（`qa_deploy.py:156`） |
| 配置字段四步（**M1 的探针预算照这个走**） | 字段 `qa_config.py:182-185` → 解析 `_parse_design_verify` `qa_config.py:482` → 接线 `qa_config.py:613,631-634` |
| `accounts-discover` 的完整先例（M1 照它抄） | `qa_accounts.py` 整文件 + `paths.qa_accounts_discover_sql`（`paths.py:98`） |
| 复现 §9 用的 DSN | 仓库根 `qa.yaml` 的 `envs.test.db.catalogs.{main,reach}` |

**三个坑**：

1. **同步测试会拦你。** `tests/test_qa.py:1143 test_qa_duties_and_skills_stay_in_sync` 断言 `_duties("design")` 与 `qa-design/SKILL.md` 都必须含 `information_schema`、`accounts-discover.sql`、`OPEN-QUESTIONS`（`schema.rb` 仅 duties、`Database columns` 仅 SKILL），且都**不得**含 `Do not interview.`。M1 改 `SKILL.md:92` 与 `_duties` 时保留这些 token；M2 若要收回 context.md 列清单的「唯一权威」地位，**不要**删掉 `information_schema` / `schema.rb` 这两个词。
2. **M1 改的是 agent 指令，安全边界不能松。** `SKILL.md:92` 现文是「设计阶段不要自己跑 usql 探库（DSN 在 qa.yaml，由宿主查）」，改为「不许自己连库；需要真实取值/方言确认时写 `qa/probes/*.sql` 交宿主跑」——保留「不要自己跑」的语义，只是给了正规出口。
3. **探针的只读校验别自己写。** `assert_readonly_sql` 已处理单语句、`;`、`WITH` 开头、写关键字黑名单（`qa_accounts.py:55-75`）。
4. **M4/M5 会动到被断言的输出，先跑一遍基线测试。** `_verify_loop` 与 `render_feedback` 的测试都在 `tests/test_qa_verify.py`：`test_render_feedback_lists_only_failures`（`:431`）、`test_render_feedback_omits_name_gaps_and_keeps_real_failures`（`:365`），以及同文件里涉及轮次控制的用例。聚类会改 `render_feedback` 的版式、零收益即停会改轮数——**这些测试要同步改，不是回归**。动手前先 `uv run pytest tests/test_qa_verify.py` 拿一份绿基线。

**本仓库的运行约定**：这个 repo 的 agent runtime 是 **pi** 不是 Claude（见 `CLAUDE.md`），用 `.pi/skills/`。门禁命令见 §7。

## 6. 验收判据（机器可测）

1. **§5.1**：在 PG-13227 场景下，design pass 1 产出 `qa/probes/*.sql` 且**未**写任何 case；宿主跑完探针后 `probe-results.md` 含「22 不在 departments」这类事实；pass 2 的 prompt 含该文件。
2. **§5.2**：引用 `EmployeesGroup` 的 fixture → `lint_cases` 报「常量不存在」；写 `FirmTask.new(...).save` 无 `skip_freeze` → 报「需显式 skip_freeze」（**当前规则对此漏报，必须能被抓**）。
3. **§5.3**：含 `FETCH FIRST 1 ROW ONLY` 的 `verify.sql` 在 lint 期即报错，**0 次远程执行**。
4. **§5.4**：`e.id = ft.created_by` + `e.username='w.deng'` 互斥的 fixture → 归因指出该合取项。
5. **§5.5**：9 条同根因 → 回灌文本出现 1 个根因块而非 9 个用例块；连续两轮同一失败集合 → 只跑一轮。
6. **§5.7 的取舍已落实**：M2 **没有**新增表/列白名单校验（该职责归 M3）；`facts.yaml` 仍生成。
7. **不回归**：`design_verify_attempts` 保持 3；`deploy` 硬门禁行为不变（前作 §6.2）；纯 UI `SELECT 1` 豁免不变；`tests/test_qa.py:1143 test_qa_duties_and_skills_stay_in_sync` 通过。

## 7. 验证方式

复用 `tests/test_qa_verify.py` 的既有风格（`_write_qa_yaml` / `_testing_req` / `_CaseWriter` / `_DesignRunner`、`monkeypatch.setattr` 打桩 `run_sql_count` / `run_sql_lines`）；无 fixture 目录，全部在 `tmp_path` 现场造。

**动手前先取基线**（M4/M5 要改的正是被这些测试断言的输出，见 §5.7 坑 4）：

```bash
uv run pytest tests/test_qa_verify.py tests/test_qa.py -q
# 2026-09-30 实测：220 passed, 2 failed —— 这 2 个是既有破损，与本 RFC 无关
#   tests/test_qa.py::test_preload_auth_runs_when_account_configured
#   tests/test_qa.py::test_run_prompt_hides_password
#   TypeError: _case_auth_env() missing 1 required positional argument: 'job'
# 根因：`_case_auth_env` 在 qa.py:767 已是 (root, cfg, job) 三参，这两个测试仍在用旧签名。
# 是提交 5d8a687「执行现场带 origin，登录态按 origin 拆」漏改的测试。
# → 实施者不要把它们的红当成本次改动引入的回归；顺手修掉即可（把 job 传进去）。
```

门禁命令（CI 同款，**以修掉上面 2 个后为全绿为准**）：

```bash
uv run pytest
uvx ruff check src tests
```

真机回归（§5.3/§5.4 可直接在本次的残留上验）。**前置**：仓库根 `qa.yaml` 已按本机配置（catalog DSN、exec site），且 JumpServer 可达——`req test` 会走 ssh+kubectl：

```bash
# 现状：4 条 design-blocked，其中 3 条是基线漂移、1 条 setup 报错
sed -n '1,40p' reqs/PG-13227/qa/design-verify/BLOCKED.md

# §5.4 目标：3 条 overseas 的 setup 自证给出「哪个合取项/哪个基线值」而不是裸数字
dev-yard req test PG-13227 --design-only
```

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 探针轮让单次 design 成本 +1 pi | 有界（1 轮）。**估算**：按本次 104 次远程执行 / 3 轮 ≈ 35 次/轮，若探针把第 1 轮写成，可省 2 次修补 pi 轮 + 约 70 次远程执行 |
| agent 滥发探针 / 写重查询 | 单次条数上限 + 行数上限 + 只读单语句校验；探针由宿主执行，不加超时即视为失败 |
| `facts.yaml` 白名单误伤（worktree 扫描漏常量） | 常量清单缺失时**不**触发该规则（沿用「事实缺失不触发」）；只报「引用了清单外常量」这一种 |
| `constants` 治不了 A 类语义错配 | 已在 §4.2 明说：A 类靠 §5.1 探针，不靠规则 |
| delta-debug 在无 seed 时不成立 | 只在核实期（setup 已跑）做；lint 期只判语法（§5.3 明确排除 0 行） |
| 零收益即停误判（失败集合相同但内容变好） | 只在**集合完全相同**时停；集合变了就继续 |

## 9. 关联证据（可复现）

```bash
# 任务终态与成本
curl -s "http://localhost:8765/api/jobs/df62d62fbc" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['state']);[print(l) for l in d['log'].splitlines() if '数据核实' in l or 'verify passed' in l]"
# → 数据核实 17 条未通过（第 1/2 次） / 4 条（第 2/2 次） / 4 条封顶 / verify passed=17 failed=4 total=21
curl -s "http://localhost:8765/api/jobs/df62d62fbc" | python3 -c "import json,sys;print('ssh 次数',(json.load(sys.stdin)['log'] or '').count('\$ ssh'))"   # → 104

# A 类：22 不是部门（常量侧与库侧可复现；用例侧第 1 轮的原文已被修补轮覆盖 → 见 §3 P2-2）
grep -rn "CLIENT_DEPARTMENT_ID = 22" .repos/reach/app/models/point_sales_setting.rb   # → point_sales_setting.rb:35
grep -rc "where id = :dep or parent_id = :dep" reqs/PG-13227/qa/cases/ | grep -v ':0'  # → 空（第 1 轮是 9 处）
usql <main> -c "SELECT id FROM departments WHERE id = 22;"                            # → 0 行
usql <main> -c "SELECT count(*) FROM point_sales_settings WHERE department_id = 22;"   # → 7746

# A 类的「修正」：9 个 setup 的降级回退（现在可复现）
grep -rn "回退" reqs/PG-13227/qa/cases/*/setup_*.rb                                    # → 10 处 / 9 文件

# 残留 4 条（最后一轮，仍在盘上）
python3 -c "
import glob,yaml
skip=('warning:','command terminated','already initialized')
for p in sorted(glob.glob('reqs/PG-13227/qa/design-verify/case-*.yaml')):
    d=yaml.safe_load(open(p,encoding='utf-8')) or {}
    if d.get('status')!='failed': continue
    keep=[l.strip() for l in (d.get('error') or '').splitlines() if l.strip() and not any(s in l for s in skip)]
    print(d['case'], '|', (keep[-1] if keep else '')[:120])
"
# → case-overseas-dept-no-filter       | setup_overseas_dept_no_filter: 部门口径过滤后 n_dept_filtered=30, 期望 n_dept-1=33
# → case-overseas-personal-excludes-marked | setup_overseas_personal_exclude: 期望 n_personal=1 n_baseline=2，实际 n_personal=4 n_baseline=7
# → case-overseas-team-no-filter       | setup_overseas_team_no_filter: 团队口径过滤后 n_team_filtered=31, 期望 n_team-1=34
# → case-satisfy-inquiry-staff-view-only-self | from script/runner:3
#   （最后一条的尾行是栈帧；有效信息在其上方几行：一段内联 SQL + 打印出的 103621）

# 现有规则命中 0 的判据
sed -n '894,980p' src/dev_yard/qa.py
```

相关代码：`qa.py:106`（`write_context_md`）、`qa.py:836-891`（`lint_cases`）、`qa.py:894-980`（`_lint_script_facts`：三条症状规则）、`qa.py:1011-1060`（`_duties`）、`qa.py:1302-1348`（`_verify_loop`）、`qa.py:2494` / `qa.py:2594`（M7 契约 lint 与其调用点）、`qa_verify.py:1177`（`render_feedback`）、`qa_verify.py:447-488`（`enrich_verify_hint`）、`qa_accounts.py`（**探针原语先例**）、`qa_deploy.py`（`frozen_models` / `undeployed_columns`）、`.pi/skills/qa-design/SKILL.md:92`（禁止探库那条）。
