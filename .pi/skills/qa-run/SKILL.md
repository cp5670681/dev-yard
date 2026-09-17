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

造数 / 清理由**宿主**执行。不要再跑 `data.setup` / `data.cleanup`，不要对 freeze worktree 跑 `bin/rails runner`。

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
7. 四态：`passed | failed | blocked | skipped`。登录失败 / 5xx / DB 连不上 = **blocked**。
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
status: passed
reason: ""
assertions:
  - type: ui            # ui | net | db
    expected: "..."
    actual: "..."
    status: passed
failure:
  step: 3
  step_desc: ...
  evidence: screenshots/step-03.png
```

`failure` 仅 `failed` 时写。每条 assertion 必须有 `type`、`expected`、`actual`、`status`。缺字段宿主会把本 case 标 blocked，不当 passed。

## 陷阱

| 陷阱 | 对策 |
|---|---|
| HTTP 2xx ≠ 成功 | 看 response-body |
| 异步未返回就断言 | 先查 requests |
| hash 路由拼错 | 用 meta.yaml `routes:`；带 `#/` |
| 截图落仓库根 | `--filename` 绝对路径 |
| ref 过期 | 重新 snapshot，不要写死 ref |
| 组件状态残留 | 每条 case 先导航到目标页 |
