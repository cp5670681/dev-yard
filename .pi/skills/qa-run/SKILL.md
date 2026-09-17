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
- `reqs/<JIRA>/qa/context.md`（本次 run 固定的 `env`、base_url、worktree、state_file、db 是否已配）
- 工作区根 `qa.yaml` 的 `envs.<ENV>`（登录账号密码、`db.url`、`script.runner`；明文存放，文件已 gitignore）——按 context.md 的 `env` 取，密码不要写进 result/证据
- `reqs/<JIRA>/qa/meta.yaml` 的 `routes`（宿主也把它们拼进 context.md 的 `## Routes`，优先用那份）
- 前端路由代码（需要时）

页面 URL = `context.md` 的 `base_url` + 已知路由。不要猜 host。

`context.md` 的 `env` 是本次 run 唯一允许的环境；即使文件里列了其它 `available envs`，也不要切换或用它们的地址。

## 禁止

- 改任何 worktree 文件（含测试、配置、源码）
- git checkout / commit / push / switch；部署
- 为变绿改 case 预期或 setup
- 动别人的 playwright 会话；并发时 `state-save`
- 写 `reqs/` 下 `qa/` 以外的路径

可写路径：`reqs/<JIRA>/qa/**`。

## 做法

1. 登录态：用 prompt 的 `Account`（无则用 context.md 的 `account.default`）在 context.md 的 Accounts 列表里找它的 `state_file`，先 `playwright-cli state-load <state_file>`；文件不存在或加载后仍是登录页 → 读该账号 username/password（优先 `.yard-qa/requirements/<JIRA>/accounts.yaml` 的 `envs.<ENV>.auth.accounts.<name>`，没有则 `qa.yaml` 同路径），在 base_url 登录页填表提交（SSO 跳转则填完回跳；非必填的多因子字段留空），成功后 `state-save` 到该 state_file（仅当 context.md 的 `concurrency` 为 1）。并发（>1）且 session 缺失时宿主已在跑前报错；若运行中仍失败 → 该 case `blocked`，reason 写清账号与原因，不要 state-save。
2. 按 case `data.setup` 造数：`.sql` 走 `usql "<qa.yaml envs.<ENV>.db.url>"`（仅当 context 标明 db 已配）；其它脚本走 `script.runner`。未配则不要跑 usql。
3. playwright 会话 `-s=qap-<case-id>`。无头默认；prompt 的 `headed` 为准。
4. 按步骤操作。selector 可按语义重定位一次；业务路径不可改。
5. 三层断言：UI → 网络（有提交时）→ DB（预期含 DB 时）。下层为准。
6. 四态：`passed | failed | blocked | skipped`。登录失败 / 5xx / DB 连不上 = **blocked**，不是产品失败。
7. 失败只取证。截图写到 prompt 给出的 `screenshots/`（绝对路径记进 result）。
8. 写该 case 的 `result.yaml`。不要写 run 级 `evidence/<run_id>/result.yaml`（宿主汇总）。
9. 按 `data.cleanup` 清理。
10. 命令、日志、result、证据里出现的 DSN 与密码一律脱敏为 `***`，不要原样回显。

## result.yaml

```yaml
case: case-01
title: ...
repo: <alias>
covers: [D1]
model: <prompt 里的池模型>
provider: <池 provider>
status: passed
reason: ""
assertions:
  - type: ui
    expected: ...
    actual: ...
    status: passed
failure:
  step: 3
  step_desc: ...
  evidence: screenshots/step-03.png
```

`failure` 仅 `failed` 时写。`blocked`/`skipped` 把原因写在 `reason`。
