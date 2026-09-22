---
name: qa-run
description: >
  执行一条 yard-qa UI 用例并写证据。只测不修。
  Use when the user runs dev-yard req test, qa-run, 跑测试用例.
---

# qa-run（dev-yard）

**一次调用只跑 prompt 指定的那一条 case。** 不要再测其它 case。不要改产品代码、不要改用例预期。

## 必读

- prompt 里的那条 case 全文
- `reqs/<JIRA>/qa/context.md`（本次 run 固定的 `env`、base_url、worktree、state_file）
- `reqs/<JIRA>/qa/meta.yaml` 的 `routes`（宿主也拼进 context.md 的 `## Routes`）
- 若 prompt 给出 `.replay.sh`：先回放，失败再对该步探索

页面 URL = `context.md` 的 `base_url` + 已知路由。不要猜 host。hash 路由必须带 `#/`（如 `http://host/#/works/...`）。

造数 / 清理由**宿主**按本次 env 的 exec 配方执行。不要再跑 `data.setup` / `data.cleanup`，不要自己 ssh/kubectl，不要对 freeze worktree 跑 `bin/rails runner`。5xx 诊断走宿主只读命令 `dev-yard qa logs <JIRA> --request-id <id>`（见「四态」§7）。

若 `context.md` 的 `exec.site` 是 remote：浏览器和脚本都打**已部署**现场，不是 freeze worktree。DB 断言以部署版模型为准。

## 禁止

- 改任何 worktree 文件（含测试、配置、源码）
- git checkout / commit / push / switch；部署
- 为变绿改 case 预期或 setup
- 动别人的 playwright 会话
- 写 `reqs/` 下 `qa/` 以外的路径

可写路径：`reqs/<JIRA>/qa/**` 以及同目录 `.replay.sh`。

## 做法

1. 登录态：
   - 若配置了 `state_file` 且文件存在，先在会话 `-s=qap-<case-id>` 执行 `playwright-cli state-load <state_file>`。
   - 账号凭据**不在 prompt 里**，从环境变量取：`$YARD_QA_USERNAME` / `$YARD_QA_PASSWORD`（`state_file` / `auth_replay` 亦见 `$YARD_QA_STATE_FILE` / `$YARD_QA_AUTH_REPLAY`）。不要把值打印或写进任何文件。
   - 打开页面后，若未登录或被重定向至登录页（或会话失效）：
     - **不要硬编码或假设固定表单结构**。先执行 `snapshot` 查看当前页面的真实输入框与按钮（支持各种自定义表单、SSO、OAuth 等）。
     - 按页面语义定位并填入用户名（`username`）与密码（`password`），点击登录/提交按钮。
     - 确认登录成功进入目标系统后，可执行 `playwright-cli state-save <state_file>` 保存登录态供后续复用。
     - 只有在无可用账号凭据、或登录明确报账号密码错误/封禁且无法进入系统时，才将 case 标记为 `blocked`。
2. 无头默认；prompt 的 `headed` 为准。截图一律用**绝对路径**写到 prompt 给出的 `screenshots/`。
3. **回放**：有 `.replay.sh` 则逐条执行（命令间不主动 snapshot）。某步元素找不到 → 对该步重新 snapshot 按语义定位，并用 `playwright-cli --raw generate-locator` 修好脚本里那一行。goto 的 host 换成本次 `base_url`。
4. **探索**（无回放）：每步 snapshot → 按语义操作 → 失败重试 1 次 → 把稳定的 getByRole/getByLabel 命令追加进 `.replay.sh`。文案与用例不一致仍完成操作，但 ui 断言判 failed（文案漂移）。
5. 三层断言：UI → 网络（有提交时）→ DB（预期含 DB 时）。下层为准。HTTP 2xx ≠ 成功，必看 response-body。
6. 瞬态 toast：操作后立即 snapshot；抓不到则断言「关键请求未发出 + 页面未变」。
7. 四态：`passed | failed | blocked | skipped`。
   - `failed`：**被测实现**与预期不符（UI 实际值 ≠ 预期，或落库值不符）。
   - `blocked`：登录失败 / 5xx / DB 连不上；或**用例自身的种子数据/前置不满足**（如期望出现在关联表/展开行/子表格里的记录，setup 没造出对应关联或字段）。
   - **数据缺口判 blocked，不判 failed**：先查 DB 定位。DB 里**根本没有**该数据（记录/关联/字段缺失）→ 用例缺陷，`status: blocked`、`blocked_class: case-defect`，`reason` 也以 `case-defect:` 开头并写明缺哪个实体/关联/字段 + 建议补什么（**必须写成 YAML 引号标量：`reason: "case-defect: 缺 xxx"`**，否则裸 `: ` 会让整份 result.yaml 解析失败、本 case 被判 blocked）；DB 里**有**该数据但页面或接口没透出 → `failed`（实现问题）。
     - 注意：设计期已跑过 `data.verify`（宿主执行，见 `qa/design-verify/`）。核实已过仍在 run 期缺数据，多半是**漂移**（被前序用例改脏或库变动）；照常按上面取证并写 `reason`（以 `case-defect:` 开头，加引号），宿主会据此自动回流设计期修种子后重排，不需要你在 run 期改 setup/预期。
   - **5xx 判据（诊断优先，禁止默认豁免）**：响应体常是兜底文案（如 `{"error":"Invalid response"}`），不代表真实原因。先取响应头 `x-request-id`，用宿主只读命令查日志：`dev-yard qa logs <JIRA> --request-id <id>`（不要自己 ssh/kubectl）。再判：未部署/环境 → `status: blocked`、`blocked_class: undeployed`（`reason` 写明依据）；实现缺陷 → `failed`。不要只看响应体，也不要先入为主写成"环境问题"；日志命令不可达时，在 `reason` 记 `undeployed:` + `x-request-id`，交人工排查。
   - **取消**：`reason` 以 `cancelled:` 开头，与 `case-defect:`/环境故障分开（由宿主统一打，不用你写）。
   - **校验类用例「意外成功」**：预期被拦截却通过了 → 先查 DB 确认是否真的写入。未写入按断言正常判；已写入即误创建：按记录 ID 清理，在 result.yaml 的 `cleanup` 段注明「执行中误创建并已清理」，然后复测该用例。
   - 数据缺口只取证、不改 setup/预期；在 reason 里点明需回设计阶段补种子数据后重跑。
8. 失败只取证。写该 case 的 `result.yaml`。不要写 run 级 `evidence/<run_id>/result.yaml`。
9. 命令、日志、result 里的 DSN 与密码脱敏为 `***`。

## result.yaml（字段必须齐全）

```yaml
case: case-01
title: ...
repo: <alias>
covers: [D1]
model: <prompt 里的池模型>
provider: <池 provider>
status: passed            # passed | failed | blocked | skipped
reason: ""                # 自由文本，必须加引号；含 : / # 或换行时用 "..." 或 |- 块标量
blocked_class: ""         # 仅 blocked 时写：case-defect | env | undeployed | auth | other
assertions:
  - type: ui            # ui | net | db
    expected: "..."
    actual: "..."
    status: passed
  - type: db
    expected: "3"       # 期望的标量值
    actual: "3"
    status: passed
    sql: "SELECT count(*) FROM projects WHERE status=1"  # 单条只读 SQL；宿主会独立重跑它比对 expected
failure:
  step: 3
  step_desc: ...
  evidence: screenshots/step-03.png
```

`failure` 仅 `failed` 时写。每条 assertion 必须有 `type`、`expected`、`actual`、`status`。缺字段宿主会把本 case 标 blocked，不当 passed。

- **YAML 必须能解析（硬要求）**：宿主用 `yaml.safe_load` 读，解析失败整份作废 → 本 case 判 `blocked`（`reason: worker exit: unreadable case result.yaml`）。写完后按下面规则自检：
  - 任何字符串标量含 `: `（ASCII 冒号+空格）、`#`（前置空格后）、以 `-`/`?`/`:`/`{`/`[`/`&`/`*`/`!`/`|`/`>`/`%`/`@`/`` ` `` 加空格或行首开头，或含单/双引号时，**必须用双引号整体包裹**，如 `reason: "case-defect: 缺无权限账号"`。
  - `reason` / `expected` / `actual` / `step_desc` 这类自由文本**一律加双引号**；很长或含换行时用块标量：
    ```yaml
    reason: |-
      case-defect: 缺无权限账号。
      建议补一名非 pangu_news_list_view 集合里的在职员工。
    ```
  - 值里本身有 `"` 时用 `\"` 转义，或改用单引号包裹并把内部单引号写成 `''`。
  - 缩进只用空格，`-` 列表项与上一级对齐一致；不要用 Tab。

- **`blocked_class`（结构化，必填于 blocked）**：宿主优先按它分类，不再猜文本。`case-defect`（用例种子/前置缺口）、`undeployed`（现场未部署）、`auth`（登录失败）、`env`（环境故障）、`other`。缺字段时宿主按 `reason` 文本猜，容易误判并可能误触整轮中断。
- **db 断言务必带 `sql`**：宿主会独立重跑该只读 SQL 并把结果与 `expected` 比对；不一致会把本 case 降级为 `failed`（`host-recheck-mismatch`），不要自报 passed 蒙混。

## 陷阱

| 陷阱 | 对策 |
|---|---|
| HTTP 2xx ≠ 成功 | 看 response-body |
| 异步未返回就断言 | 先查 requests |
| hash 路由拼错 | 用 meta.yaml `routes:`；带 `#/` |
| hash 路由不重载 | goto 后快照确认，必要时 reload 再断言 |
| 截图落仓库根 | `--filename` 绝对路径 |
| ref 过期 | 重新 snapshot，不要写死 ref |
| 组件状态残留 | 每条 case 先导航到目标页 |
| 5xx/接口报错 | 响应体常是兜底文案；先按 `x-request-id` 查 pod 日志/Sentry 定位真实异常，再判环境/产品（见四态 §7） |
| 时间字段显示 UTC | 断言前先换算到页面/需求口径 |
| 只读库禁写 | DB 断言只用 `SELECT/SHOW/DESC`；写库交给宿主 setup/cleanup |
| 期望元素在关联行/展开行/子表格却不存在 | 先查 DB：该行对应实体/关联/字段是否存在；不存在 = 用例种子数据缺口，判 `blocked` + `reason` 以 `case-defect:` 开头（加引号），不是产品缺陷（设计期已核实过仍缺 = 漂移，交宿主回流） |
| reason 裸写 `case-defect: xxx` | 值含 `: ` 会让 YAML 解析失败 → 整份 result.yaml 作废、本 case 判 blocked。自由文本标量一律加双引号或用 `|-` 块标量 |
| 校验用例「意外成功」 | 先查 DB 确认是否真写入；已写入即误创建 → 按 ID 清理 → `cleanup` 段注明 → 复测 |
| 两次结果不一致 | 大概率前端异步竞态：换一次性完整输入替代逐键输入，区分竞态与后端行为 |
| local 环境服务没起 | 页面 5xx/连接拒绝 → `blocked`，提示起本地服务/看本地日志，不是用例失败 |
| 截图/落库与快照冲突 | 以下层（网络/DB）为准，不凭快照判 passed |
