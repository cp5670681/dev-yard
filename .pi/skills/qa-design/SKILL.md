---
name: qa-design
description: >
  对着 freeze worktree 的 SPEC 与 diff 设计 yard-qa 用例。
  Use when the user runs dev-yard req test, qa-design, 设计测试用例.
---

# qa-design（dev-yard）

为当前需求写 UI 用例。**只写** `reqs/<JIRA>/qa/**`。测的是 freeze worktree，不是 `repos.yaml` 主克隆。

## 必读

- `reqs/<JIRA>/{REQUIREMENT,SPEC,TICKETS}.md`（只读）
- `reqs/<JIRA>/qa/context.md`（宿主写的 worktree 地图、本次 `env` 与 base_url）
- `reqs/CONTEXT.md`（若有：只读术语）
- 每个 worktree：`git -C <path> diff <default_base>...HEAD`（HEAD 即 `req/<JIRA>`）

不要调 MCP、不要重拉 Jira、不要交互式访谈。缺细节时：能按 SPEC + 常规默认决定的，写进用例并标注假设；**真正有歧义、答错会让用例判错的，逐条写进 `qa/OPEN-QUESTIONS.md`（见下节），不要卡住、也不要默认成常规值糊过去**。

`context.md` 的 `## Notes` 是本次环境的注意事项，**必读且逐条遵守**（见「Notes」节）。

`git` 只用 `diff` / `log`。不要 checkout、commit、push、switch。

## 澄清与开放问题（qa/OPEN-QUESTIONS.md）

非交互运行，不能当面提问。缺细节时：

- 能按 SPEC + 常规默认决定的：直接写进用例，在备注标注「假设」。
- 真正有歧义、答错会让用例判错的：逐条写进 `qa/OPEN-QUESTIONS.md`，格式
  `Q1: <问题> | 默认取值: <x> | 影响: case-02,case-04 | 答错后果: <y>`。
- 无歧义时也写一个**空文件**（保留文件，便于人审确认已想过）。

人审会看到 `OPEN-QUESTIONS` 计数，可 `dev-yard req test <JIRA> --redesign --feedback "Q1: …"` 回答后重做。不要为了省一轮往返而把真歧义默认掉。

## 权限账号与账号发现

从 diff/SPEC 判断是否存在权限控制点（角色 / 数据可见范围 / 操作拦截）。有则：

1. **先读后端代码确认权限的判定方式**（controller 的 `before_action`/策略类、模型上的角色谓词、权限表），不要猜。
2. **主动发现候选账号**（不要只把活推给人）：
   - 权限是简单表结构（user 表有 role 列 / 角色关联表）→ 写一条**只读**查询到 `qa/accounts-discover.sql`（单条 `SELECT`：第一列 `username`，第二列 `account_key`；用 `UNION ALL` 覆盖每个权限桶）。`account_key` 是语义名（如 `has_perm` / `no_perm`），会直接成为账号名。宿主跑
     `dev-yard req accounts <JIRA> --auto` 即按当前库一键发现并写入本需求 `accounts.yaml`（复用全局默认账号密码，不动全局账号）。
   - 权限会漂移 → 不要写死账号；`--auto` 可反复重跑刷新候选，`--auto --refresh` 还会清缓存登录态强制重登。
   - 权限是应用内逻辑（如 Rails 谓词 `user.research_director?`）→ SQL 查不出，在 `qa/OPEN-QUESTIONS.md` 写明**所需角色 + 判定代码位置**，请人工提供账号。
   - 表结构/列名从后端仓库 ORM/schema 定义读，禁止猜（踩坑：外键是 `right_role_id` 不是 `role_id`）。按「能覆盖全部权限差异的最少账号数」选号，账号名用语义化 key（如 admin/readonly）。候选查询要过滤脏数据（如 `username ~ '^[a-z]'`、排除 `system`），否则 `--auto` 可能选中 `12.21` 这种非账号。
3. 在 `qa/OPEN-QUESTIONS.md` 列出「需要哪些权限账号」。
4. 按 `context.md ## Accounts`（来自 `dev-yard req accounts` / `.yard-qa/.../accounts.yaml`）为每个权限各写正/反向用例：该权限可见/可操作 + 无权限不可见/被拦截。
5. 缺账号时**先跑 `--auto`**；仍缺（如权限源查不到人）才**显式写「未覆盖（缺账号 X）」**，禁止静默省略（不要只在 meta.yaml 备注里提一句）。

## 做法

1. 每个 worktree 做 `git diff <default_base>...HEAD`。空 diff：停止并说明，不要编改动。
2. 改动点 D1..Dn 写入 `qa/meta.yaml`。`repo` 必须是 `repos.yaml` 别名（context 地图里的 alias），不要写 frontend/backend 泛称。`role` 只作阅读提示。
3. **增量**更新 `meta.yaml`：改 `changes` / `base_branches` / `feature_branches` / `module` / `requirement`；保留已有 `routes:`，不要整文件覆盖。
4. 覆盖：每个 D 至少 1 条 + 1 条正常流 + UI **可达**的后端错误分支（无权限/重复/超限）。**每条 `meta.yaml` 的 `changes[].id` 都必须被至少一条 case 的 `covers` 引用**（宿主会把未覆盖的 D 单独提示，不能漏）。控件 `disabled`/`maxlength`/无清空导致点不到的拦截，不要写成用例。**每条含 UI 预期的步骤还必须过「预期可达性审查」（见下节）**。
5. 步骤用业务语言，不要写 selector、不要写 `bin/rails runner` / usql。按钮/文案必须来自 worktree 代码，不来自想象。
6. 预期写需求口径。实现与 SPEC 不符时仍写需求值，并备注「需求偏差」。
7. 跨仓改动拆成多条 case，或 `covers` 只含一个主仓。每条 frontmatter 必有 `repo:`（yard alias）。
8. `depends_on` 仅当共享可变数据或业务先后时写；无依赖省略，以便并发领取。
9. 需要非默认账号的用例，在 frontmatter 写 `account: <account_key>`；名字必须来自 `context.md` 的 Accounts 列表（宿主跑前校验，未配置会直接报错让你先跑 `dev-yard req accounts <JIRA> --auto`）。不写就用 `account.default`。若需新增账号，按「权限账号与账号发现」写 `qa/accounts-discover.sql`（只读单条 `SELECT`，`username | account_key` 两列）并在 OPEN-QUESTIONS 注明。
10. 造数优先 `.sql`（host usql 打 `qa.yaml` 的 db.url；`db.exec: inherit` 时走与脚本同一条 exec 管道）。非 SQL 脚本由宿主按本次 env 的 `exec` 配方执行（local = freeze worktree + stdin；remote = 已部署现场 + stdin）。脚本契约：
    - **单文件**，不要 `require` 邻居（多文件才用 payload bundle）。
    - 状态落 **DB**，禁止把 setup→cleanup 约定写到执行现场本地文件（pod 会换副本）。
    - 业务参数只读 `ENV['QA_ENV']` / `QA_JIRA` / `QA_CASE_ID` / `QA_SCRIPT_KIND`，**不要读 ARGV**（stdin 模式下 ARGV 是空的）。
    - stdout 是唯一回传通道（seed id 用 `puts`/`print`）。
    - 幂等，且不假设两次执行落在同一副本。
    - **seed 自证**：见「预期可达性审查 §4」，缺口要在造数阶段暴露，不留到 run。

## 预期可达性审查（强制）

UI 预期只有在「该区域的数据确实会被 setup 造出（或已被核实存在）」时才可判定。每条含 UI 预期的用例都要过下面三段。

### 1. 控件可达

对照改动组件确认 UI 上能触发：`disabled`/`readonly`/`maxlength`/无 `clearable`/默认值恒有 → 不可达的拦截分支**不要写成用例**，在 meta.yaml 或报告备注「防御性代码，UI 不可达」。

### 2. 数据可达

写用例时逐条自查：这个元素在哪个区域、由什么数据驱动、setup 是否覆盖那笔数据。**关联行 / 展开行 / 子表格往往是独立实体，不会从主记录继承字段**：

- 公司/项目的「联系人列表」与「联系人-项目表格」数据源不同：后者只列**参与过项目**的联系人。期望某联系人出现在该表，setup 必须建好 项目↔联系人 关联（如 `pj_contacts`/`firmtender`），只 `INSERT contacts` 不够。
- 查重页/重复电话子表格里的「重复联系人」是**同号码的其它 contacts 记录**（各自的字段独立）。期望子行展示某字段，setup 必须逐条设置这些子记录，不能只改主联系人并指望继承。
- **编辑/保存路径的必填字段**：种子记录必须带上保存时前端/后端会校验的字段（yard 实例：`l_salutation/province_id/city_id/l_address`），否则「更新」被校验拦住，断言根本执行不到。
- 每个断言对象都要能追到 setup：前置里逐条列出 setup 会创建/修改的实体及关联、键值。

**只读自检 → 写成可执行的 `verify.sql`（强制）**：依赖「线上已有数据」或自带种子的用例，都要在 frontmatter 声明 `data.verify: verify.sql`，内容是**单条只读查询**（`SELECT`/`SHOW`/`DESC`/`EXPLAIN`，连接串取 `qa.yaml` 的 `db.url`，配了只读的 `db.verify_url` 则用它），语义为**返回 ≥1 行即通过**（写成 `SELECT ... WHERE <前置条件>`，0 行即失败）。宿主在设计期真跑它，失败会带着结果回灌给你重做。

- 每个断言对象都要能追到 `verify.sql`：**FROM/JOIN 里的每张表名**和**至少一个列名**都必须出现在用例正文里（宿主会 lint，缺表或缺列直接判失败；连表都不提的 `SELECT id FROM 别的表` 不能蒙混）。
- 含 `setup`/`cleanup` 或 `## 预期` 里有 `- DB:` 的用例**必须**有 `data.verify`，否则判失败。
- 纯 UI 用例无数据可断言时写 `SELECT 1`，会被标为「空转豁免」供人抽查；不要用它掩盖真断言。
- 依赖线上既有数据的步骤（如"某项目已有重复电话数据"）不要只写"假设"：要么用 `verify.sql` 核实，要么改成自带 setup 造数。查不了（无 usql/无权限）就在前置里显式标注「未验证假设」。

### 2b. 必填字段反查（保存路径）

含提交/保存的用例，必须从**目标表单组件**反查校验规则（`:rules` / `required` / 自定义 validator），把**全部**必填字段写进种子的 `verify.sql` 断言（`... IS NOT NULL`），不要只补报错时冒出来的那一个。动态/条件必填读代码覆盖不到，照实标注「留给提交预检」。

### 3. 文案溯源 + 量化口径

- 按钮/提示语取自 worktree diff 原文，不来自需求文档想象。
- 排序/Top-N/计数先写判定口径（按什么字段、什么顺序、取前几条），真值由 run 查库比对，不在设计期硬编码。

### 4. seed 自证（硬护栏）

只"逐条列必填字段"仍会漏。三条硬要求：

1. 走**应用内保存路径**（如 `Contacts::SaveCommand`）或显式带齐「目标表单保存时会校验的字段」，前端专属必填字段必须显式设置；
2. seed 末尾**自检并硬失败**：断言本 case 每条 UI 预期引用的实体/关联/字段确实就位，**不满足就 `exit(1)`**（不是只 `puts` 打印后继续）；
3. 反查必填：从提交被拦的错误文案定位 validator，一次枚举**全部**必填字段（含尚未触发的），写进种子与 `verify.sql`。

`verify.sql` 是宿主执行的权威判据，seed 自证是 setup 内的快速失败，二者互补：seed 自证让缺口在造数阶段就炸，`verify.sql` 让宿主在 design 期替你把关。

## meta.yaml

```yaml
module: <JIRA>-<短名>
requirement: <JIRA>
base_branches: { <alias>: <default_base> }
feature_branches: { <alias>: req/<JIRA> }
routes: {}
changes:
  - id: D1
    repo: <alias>
    ref: src/foo.vue
    desc: ...
```

## 用例

```markdown
---
id: case-01
title: ...
priority: P0
requirement: <JIRA>
repo: <alias>
covers: [D1]
depends_on: []
account: <可选；context.md Accounts 里的账号名，缺省=default>
data: { setup: setup.sql, cleanup: cleanup.sql, verify: verify.sql }
---

## 前置
- 已登录
- <业务前置；逐条列出 setup 会创建/修改的实体及关联与键值，以及本 case 依赖的既有数据（由 verify.sql 核实，或标注「未验证假设」）>

## 步骤
1. <可在 UI 上执行的业务步骤>

## 预期
- UI: <可判定>
- 网络: <有提交时>
- DB: <预期含 DB 时>
```

无 DB 则去掉 `data` 与 DB 预期。造数脚本与 case 同目录，幂等。**setup 必须覆盖该 case 每条 UI 预期引用的实体，含关联行/展开行/子表格里的独立实体与编辑/保存路径的必填字段；cleanup 对称恢复；seed 要自证（见 §4）。** 有 `setup`/`cleanup` 或 `- DB:` 预期的用例必须有 `data.verify`（见 §2）。不要在步骤里写执行器命令。

写完后停。不要跑浏览器、不要改 STATUS.yaml。
