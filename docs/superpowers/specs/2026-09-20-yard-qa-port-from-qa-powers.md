# 把 qa-powers 的防错机制移植进 yard-qa

- 日期：2026-09-20
- 状态：已实现（P0 + P1；P2/M5c 取消）
- 落点：`.pi/skills/qa-design`、`.pi/skills/qa-run`、`src/dev_yard/qa.py`（`_duties` / design-only 返回 / run summary / blocked 分类）、`src/dev_yard/qa_schedule.py`、`cli.py`、`web/jobs.py`、`qa_board.py`、`web/src/views/QaView.vue`、`qa.yaml`
- 对照：qa-powers（`/home/chengpeng/rcc/qa-powers`，`plugin_version 0.3.8`）为参考实现，不作为运行时依赖
- 触发：PG-13175（联系人外聘，`reqs/PG-13175/`）本轮 0 pass / 2 条实质 blocked（case-01、case-02）/ 13 条因 run 取消连带记 blocked，其中多数是**用例数据缺口**而非产品缺陷；同项目 qa-powers 曾出现一轮 44/44 全绿（PG-13054，2026-09-15，env=test，见 `/home/chengpeng/rcc/research/.qa-powers/evidence/`）
- 约束：[2026-09-16-yard-qa-workflow.md](2026-09-16-yard-qa-workflow.md) 的非目标仍然有效（不把 qa-powers 收成插件；第一刀只做 yard 内部）

## 1. 背景与问题

yard-qa 的 `qa-design` / `qa-run` 是从 qa-powers 的 `design` / `run` **精简移植**（98/91 行 vs 156/281 行）。精简时丢掉了 qa-powers 用来在**设计期**拦截错误前置的机制，于是同一批"想当然"直接暴露成失败/阻塞：

| 观测 | 性质 | 本可被哪一环拦住 |
|---|---|---|
| case-03/04：期望记录出现在「联系人-项目表格」/「查重展开子行」，但它们是独立实体，setup 没造 | 用例数据缺口 | 数据可达性审查（M2） |
| case-02：编辑页种子联系人缺 `l_salutation/province_id/city_id/l_address`，被前端必填校验拦住 | 用例数据缺口 | 必填字段审查（M2）／澄清（M1） |
| 权限分支直接不测（meta 写「无权限分支不可测」） | 覆盖缺口 | 权限账号（M3） |
| case-01：`POST /contacts/save` 500，响应体 `{"error":"Invalid response"}`（Grape 兜底文案，真实异常被 production 的 `rescue_from StandardError` 吞掉，见 `app/api/application_base_api.rb:42`） | **根因未证实**：case-02 的 setup 能 `Contact.create!(hire_type:)` 成功，说明部署端模型认该列，"常驻进程 schema cache 未加载"只是可能之一，也可能是产品缺陷 | 5xx 诊断协议（M4/M5b）：按 `x-request-id` 查 pod 日志 / Sentry 后再归类，禁止先入为主地豁免 |

结论：不是 dev-yard 的测试能力弱，而是**设计期缺少防错**。本设计只做"移植"，不追求把 qa-powers 全盘抄进来。

## 2. 差异根因

1. **设计不做澄清**。qa-powers `design` §3 强制交互式澄清（业务规则/验收/边界/权限）；yard-qa 的 `_duties("design")` 与 `qa-design` 都明确 `Do not interview.`，缺细节用"常规默认+标假设"。
2. **少了强制的预期可达性审查**。qa-powers `design` §4 有「预期可达性审查（强制）／文案溯源／量化预期先取真值口径」；yard-qa 原版没有。
3. **run 不能自愈**。qa-powers `run` §1a 在造数报约束错时"先查全表约束再改脚本"；yard-qa 的 setup 由**宿主在 agent 之前**执行，`qa-run` 明令禁止改 setup/预期，缺口只能变失败。
4. **状态/报告口径**。yard-qa 起初把"用例自身数据缺口"也判 `failed` 并进报告（`qa_report.map_qa_result`），显得问题多；qa-powers 从设计上区分 `blocked`（环境/外部依赖）与 `skipped`（前置不适用）。
5. **配置偏薄**。qa-powers 的 `init` 把多账号、环境/系统注意点（notes）、k8s 明细收进 config；yard 的 `qa.yaml` 只配了 default 账号、`notes: []`。
6. **取消语义污染口径**。run 被取消时，13 条 pending/在跑用例统一记 `blocked`（`reason: worker exit: qa-run case-05 cancelled`），与"环境故障""用例缺陷"混在一起，稀释 §7 的统计。

## 3. 设计约束（决定映射方式，不能照搬）

| qa-powers 做法 | yard-qa 结构 | 映射 |
|---|---|---|
| design 交互式澄清（一次一问） | `qa-design` 是 pi `-p` 一次性、非交互，工具无 question | **异步澄清**：设计产出问题清单 → 人在审核门回答 → `--redesign --feedback` |
| run 遇造数报错可改 setup | 宿主先跑 setup，agent 不可改 setup/预期 | **不让 run 自愈**：设计期做对 + run 把缺口判 `case-defect` 回流 |
| init 收多账号 / notes | `qa.yaml` 支持 `envs.*.notes` 与 `.yard-qa/requirements/<JIRA>/accounts.yaml`；`context.md` 已渲染 `## Notes`/`## Accounts` | **配置 + 设计**问题，宿主基本不改 |
| run 陷阱表 / 自愈 | `qa-run` 已有简化版 | 扩表 + 「case-defect 回流协议」 |
| design 仅靠 prompt 列必填字段 | setup 由宿主在 run 前执行，缺口 run 才暴露 | **seed 自证**：setup 走应用内保存路径/模型校验，或 `puts` 自检断言字段（M2.4） |
| 5xx 直接判环境 | `rescue_from StandardError` 吞掉真实异常，响应体只剩兜底文案 | **诊断协议**：按 `x-request-id` 查 pod 日志/Sentry 后再判（M4/M5b） |

**关键**：设计规则同时注入在 `.pi/skills/qa-design/SKILL.md` 与 `src/dev_yard/qa.py:_duties()`。`_duties("design")` 现在写着 `Do not interview.`，只改 skill 会被它压住，**两处必须同步**（建议在 `_duties` 加注释"与 SKILL.md 同步"，并可加一条测试断言关键词）。

## 4. 机制清单

### M1 异步澄清（qa-powers design §3）

- `qa-design` 新增产物 `qa/OPEN-QUESTIONS.md`：逐条列不确定项，格式
  `Q1: <问题> | 默认取值: <x> | 影响: case-02,case-04 | 答错后果: <y>`；无歧义时写空文件（保留文件，便于人审确认已想过）。
- `qa.py:_duties("design")`：`Do not interview.` → `Do not interview interactively; record uncertainties in qa/OPEN-QUESTIONS.md instead.`
- 人审通路复用现有：测试页/看板填「打回意见」→ `dev-yard req test <JIRA> --redesign --feedback "Q1: …"`（`qa.py:1091-1150`）。零新代码。
- 出口（**P0，必须**，否则 OPEN-QUESTIONS 无人可见）：`qa.py` 的 `--design-only` 返回（`qa.py:1131`）与 `awaiting_review` 返回（`qa.py:1145`）带 `questions` 计数，CLI 打印 `OPEN-QUESTIONS: N`；web 端的提示（P1）。

### M2 预期可达性审查（qa-powers design §4）

把现有「数据可达性」节扩成三段，全部落 `SKILL.md`：

1. **控件可达**：`disabled`/`readonly`/`maxlength`/无 `clearable`/默认值恒有 → 不可达的拦截分支不写成用例，备注"防御性代码，UI 不可达"。
2. **数据可达**：关联行/展开行/子表格是**独立实体**；种子必须覆盖断言引用的每个对象，**含编辑/保存路径的必填字段**（yard 实例：`l_salutation/province_id/city_id/l_address`）。前置里逐条列 setup 创建/修改的实体+关联+键值。
3. **文案溯源 + 量化口径**：按钮/提示语取自 worktree diff 原文；排序/Top-N/计数先写判定口径，真值由 run 查库比对（`qa-run` 已有此项，design 补前向约束）。
4. **seed 自证（M2.4，硬护栏）**：只让 agent"逐条列必填字段"仍会漏（case-02 就是范例）。要求 setup 造数尽量走**应用内保存路径**（如 `Contacts::SaveCommand`）或让模型校验生效，使"缺字段"在造数阶段就炸；走不通时，seed 末尾 `puts` 一段自检（断言本 case 每条 UI 预期引用的实体/字段/关联确实就位），stdout 即证据。`qa-design/SKILL.md` 的造数契约补这一条。

### M3 权限账号（qa-powers design §3 账号发现）

- `qa-design`：从 diff/SPEC 判断是否存在权限控制点；有则
  a. 在 `OPEN-QUESTIONS.md` 列"需要哪些权限账号"；
  b. 按 `context.md ## Accounts`（来自 `dev-yard req accounts` / `.yard-qa/.../accounts.yaml`）为每个权限各写正/反向用例；
  c. 缺账号时**显式写"未覆盖（缺账号 X）"**，禁止静默省略（现状 meta.yaml 只在备注里提一句）。
- P1（可选宿主）：design 前若 diff 命中权限关键字且 accounts 只有 default，CLI 打 warning（不改 gate，不阻塞）。

### M4 notes（qa-powers init + design §2）

- 机制已在：`qa.yaml` `envs.<env>.notes` → `context.md ## Notes`（`qa.py:170-176`）。
- `qa-design` 把 notes 提为**必读且逐条遵守**；与用例冲突时以 notes 为准并写进用例备注。
- 把本轮坑沉淀进 `qa.yaml`（**待你提供/确认文案**），候选：
  - `5xx/接口报错：先按响应头 x-request-id 查 pod 日志 / Sentry 定位真实异常，再判「未部署（环境）」还是「产品缺陷」；不要只看响应体的兜底文案（如 {"error":"Invalid response"}），也不要默认豁免为环境问题`
  - `列表类断言先加筛选，避免全量分页噪声`
  - `时间字段若显示 UTC，断言前先换算`

### M5 run 侧（qa-powers run）

- **M5a 扩陷阱表**（`SKILL.md`）：HTTP 2xx≠成功、异步未返回就断言、组件状态残留、hash 路由不重载/拼错、ref 过期、截图落仓库根、时区/UTC、只读库禁写、5xx 先判环境。
- **M5b 回流与分类协议**：
  - run 遇数据缺口 → `status: blocked`，`reason` 以 `case-defect:` 开头，结构化写明**缺哪个实体/关联/字段 + 建议补什么**；只取证、不改 setup（上一轮已落地一半，本设计补齐"建议补什么"与"仅取证不改 setup"的显式措辞）。
  - 5xx → 先按 `x-request-id` 查 pod 日志/Sentry 定位真实异常，再判 `blocked: undeployed`（环境）或 `failed`（产品缺陷）；**禁止凭响应体兜底文案直接豁免**。
  - 取消 → `reason` 以 `cancelled:` 开头，与 `case-defect:`/环境故障分开；run summary 的 `blocked` 必须拆 `case-defect / env / cancelled / other` 四类计数（**P0**，§7 指标依赖它）。
  - `qa.py:_duties("run")` 加一句同义说明（case-defect / 5xx / cancelled）。
  - P1：宿主结束提示"需 `--redesign` 补种子"。
- **M5c（不做）**：原设想让 run 只读核对后产出 `case-NN.setup.patch.sql` 建议文件。现不采纳：已有 `--redesign --feedback` 通路，宿主可直接把结构化 `case-defect:` reason 喂回，无需新增产物与指纹边界（见 §9.4）。

## 5. 落点汇总与分期

| 机制 | 落点 | 阶段 |
|---|---|---|
| M1 异步澄清（产物 + `_duties` 改写） | `qa-design/SKILL.md`、`qa.py:_duties` | P0 |
| M1 出口：`questions` 计数（CLI 打印） | `qa.py`（design_only / awaiting_review 返回） | P0 |
| M2 三段式可达性审查 | `qa-design/SKILL.md`（`_duties` 加一句） | P0 |
| M2.4 seed 自证 | `qa-design/SKILL.md`（造数契约） | P0 |
| M3 权限账号（prompt 部分） | `qa-design/SKILL.md` | P0 |
| M4 notes 强制遵守（诊断性文案） | `qa-design/SKILL.md` ＋ `qa.yaml` 内容 | P0 |
| M5a 陷阱表、M5b 回流/分类协议 | `qa-run/SKILL.md`、`qa.py:_duties` | P0 |
| run summary `blocked` 四分类 + `cancelled:` reason | `qa.py`（progress / result 汇总） | P0 |
| 结束提示"需 `--redesign` 补种子" | `qa.py`、`web` | P1 |
| `web QaView`/看板提示 `questions`、渲染 `OPEN-QUESTIONS.md` | `web/src/views/QaView.vue`（新增只读块） | P1 |
| 权限缺口 warning | `qa.py` design 前置检查 | P1 |

P0 = prompt/文档 + `_duties` 两句 + `qa.py` 两处返回/汇总字段（`questions`、`blocked` 四分类）。**不改执行语义、不碰指纹与调度**，风险仍低。

## 6. 非目标

- 不引入交互式 design（`-p` 不变）；澄清一律异步。
- 不让 `qa-run` 直接改 setup/预期（硬约束保留）；M5c 已取消（见 §4 / §9.4）。
- 不自动补齐 `qa.yaml` 的账号密码/notes 内容（涉密，需人填）。
- 不重建 qa-powers 的 `init`/`report`/`k8s` skill；yard 只吸收与本环相关的部分。
- 不改现有四态机、依赖 DAG、模型 worker 池、证据结构。

## 7. 验收与指标

- **回归（先 `--design-only`，低成本直击 M1/M2）**：`dev-yard req test PG-13175 --design-only`，验两件事——① case-02 的 `setup_seed.rb` 补齐 `l_salutation/province_id/city_id/l_address`（或 seed 自证能过）；② 产出 `qa/OPEN-QUESTIONS.md` 且 CLI 打印 `OPEN-QUESTIONS: N`。通过后再整跑。
- **分类正确**：run 撞上数据缺口稳定判 `blocked/case-defect:`；5xx 经 pod 日志/Sentry 核实后才归类；被取消的用例 `reason: cancelled:`，不与前两者混计。
- **指标**（注意：case-defect 判 `blocked` 而非 `failed`，故不能用"`failed` 中 case-defect 比例"——该值恒为 0）：
  - `blocked` 拆四类后，`case-defect` 与 `other` 目标 **0**；`env` / `cancelled` 允许非 0 但须可解释。
  - **覆盖率**：未覆盖的 D 数 = 0，且"未覆盖点清单"为空才算达标（防止靠"显式写未覆盖"把指标做好看）。
  - OPEN-QUESTIONS 只在真歧义时非空（不增加无关往返）。
- **不回归**：一轮正常需求（如 PG-13054 类型）仍可无人干预跑通；P0 改动不改变用例正文之外的执行结果。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 异步澄清多一轮人审往返 | OPEN-QUESTIONS 允许"默认值可直跑、不强制阻塞"；只在真歧义时产出 |
| `_duties` 与 `SKILL.md` 双写漂移 | `_duties` 只留硬约束、细则全在 `SKILL.md`；加测试断言"两处不再出现旧句 `Do not interview`，且 `case-defect` / `OPEN-QUESTIONS` 关键词齐备" |
| 设计期只读 SQL 自检被误用为写库 | `qa-design` 明确只允许 `SELECT/SHOW/DESC`；写库留给 setup（宿主执行） |
| 产品缺陷被误判为环境问题（或反之） | M4 改为诊断性 note：5xx 先按 `x-request-id` 查 pod 日志/Sentry 再归类；case-defect 用"DB 有/无数据"判据；**不写"不直接判产品 bug"这类豁免** |
| `blocked` 四分类改动影响既有消费方 | 只加字段、不改既有 `status` 取值；`qa_report.map_qa_result` 仍只按 `failed` 出 finding，不受影响 |

## 9. 开放问题

1. `OPEN-QUESTIONS.md` 保留独立文件（结构清晰、可 diff）；`meta.yaml` 不重复存，`questions` 计数由宿主解析该文件得出。
2. 已定：CLI 打印（P0）；web banner / 看板理由（P1）。
3. `qa.yaml` notes 的最终文案由谁定（本设计只给候选）。
4. 已定：M5c 不做（见 §4）——`--redesign --feedback` 已能闭环。
