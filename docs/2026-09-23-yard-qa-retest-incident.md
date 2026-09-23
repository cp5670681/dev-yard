# yard-qa 重测事件复盘：一次「用例自身错误」被平台放大成产品缺陷

- 日期：2026-09-23
- 状态：问题分析（待转设计）
- 触发需求：`reqs/PG-13218/`（show 页「预处理」Checkbox）
- 触发操作：web 需求页点 case-03「重测」，job `731f671f9b`
- 关联 run：`qa/evidence/2026-09-23-172644`，报告 `test-reports/2026-09-23-175212-574469.md`

## 0. 现象

用户在需求页点 case-03「重测」，结果：

1. case-03 仍是 `blocked`。页面顶部横幅仍是「本轮 0 通过 / 3 失败 / 15 阻塞 → 已拆 3 张 B 票」。这些数字是 `2026-09-23-172644` 的真实汇总：重测没改任何 case。
2. 重测 job 在 worker 里被闸门拒绝，用例没有重跑。提交接口先返回成功，页面立刻绿字 toast「已重测 case-03」。job id 会进地址栏，`JobPanel` 随后变成 `error` 并打出下面这句日志。页面级红色 alert 只接 HTTP 抛错，不接 job 终态，成功 toast 也不会被撤掉。

```
GET /api/jobs/731f671f9b
state: error
log: "PG-13218 still has open tickets; finish the test bug tickets before re-testing"
```

## 1. 事件经过

| 时间 | 事件 |
|---|---|
| 17:24 | 提交 `3ca77e2`（宿主复核对整句 expected 的补丁） |
| 17:25 | 启动 `dev-yard web` |
| 17:26 | 启动执行用例 run `2026-09-23-172644`（两个 pool：MiniMax-M3 / opencode-go-deepseek） |
| 17:29–17:31 | deepseek pool 连续 `pi exit 1`；case-04 `missing case result.yaml` |
| 17:31 | 连续两次 env 类 block 触发整轮熔断。已经在跑的 MiniMax case 会跑完，队列里剩余 9 条不再派发 |
| 17:34 | `qa.yaml` 被改动（移除 deepseek worker）。运行按启动时读入的 pool 配置跑完，这是预期行为，界面没有说明 |
| 17:52 | run 结束：3 failed（case-01/02/08）+ 15 blocked（1 case-defect + 14 env）；生成报告并拆出 B1/B2/B3 |
| 17:5x | 用户点 case-03 重测 → job `error`。绿 toast 报已重测，横幅仍是整轮汇总 |

## 2. 关键事实：三张 B 票是用例问题，不是产品功能坏了

| 票 | 对应 | 真实原因 | 结论 |
|---|---|---|---|
| B1 | case-01 | Worker 当时 UI、接口、自己记下的 SQL `actual` 都是 `0`（项目 `10009268`）。落盘后这条 db 断言的 `status` 被宿主改成 `failed`，`actual` 字段仍是 `0`。宿主稍后重跑同一条 SQL 得到 `1`，打成 `host-recheck-mismatch`。case-02 把同一项目改成 `1`。**基线串扰，复核把时间差当成产品失败** | 用例缺陷 |
| B2 | case-02 | 提交、关闭、toast、DB=1 均命中。失败点是 `navigator.language=en-US` 下按钮成了「OK/Cancel」，以及 HTTP `201` 对用例写死的 `200`。Worker 写了实现未传 `okText`/`cancelText`。若规格要求按钮文案不随浏览器语言变化，缺 `okText` 仍可能是产品缺口；HTTP `201` 是断言过严。**两条都不该无条件拆成必须做完才能再测的 B 票** | 用例断言过严；文案是否算产品缺口取决于规格 |
| B3 | case-08 | Worker 整案曾是 `passed`（宿主只对 `passed` 做复核降级）。`expected` 是整句「刷新前后…始终保持 1」，worker 记的 `actual` 也是整句。宿主读到单元格 `1`，对不上，降级 failed；文件里 db 断言的 `status: failed` 是宿主覆写。**expected 非标量，双侧都是句子时复核假失败** | 用例缺陷 |

case-03 本身 `blocked_class: case-defect`，同样是用例/种子缺口。它的 reason 写共用最高 id `10009269`，且把 case-01/02 也算进这一行；case-01/02 的证据实际用的是 `10009268`。两行都发生了「多个 case 改同一行」：`10009268` 被 case-02 改成 1，`10009269` 被后续 setup 覆写回 0，并且该候选项目 `can_edit=false`。这不是产品 bug。

`blocked` 且全轮没有 `failed` 时，报告不入库、不拆票。`case-defect` 因此挡得住纯阻塞轮，挡不住上面这三条 `failed`。

## 3. 平台问题清单

严重度：P0 = 直接造成本次误导/阻塞；P1 = 系统性缺陷；P2 = 体验/次要。

| # | 严重度 | 问题 | 证据 |
|---|---|---|---|
| 1 | P0 | 单条重测被整轮闸门硬拒；提交成功的绿 toast 盖过 job `error` | `qa.py` `_gate` / `_req_test`；`RequirementView.vue` `rerunCase`；`JobPanel` |
| 2 | P0 | 横幅按整轮 `run_id` 去重，重测不新建 run 就不刷新，文案「本轮」把旧汇总说成这次操作的结果 | `RequirementView.vue` `showRunEndBanner` |
| 3 | P0 | `failed` 无条件拆成产品 B 票；`case-defect` 只覆盖 `blocked` | `qa_report.py` `map_qa_result`；`test_report.py` `spawn_fix_tickets` |
| 4 | P0 | 闸门死锁：假失败票挡住 redesign，redesign 也被同一闸门拦 | `qa.py` `_gate` 在 `_req_test` 开头无条件执行 |
| 5 | P1 | 一个坏模型池的连续 env block 熔断整轮派发，未按 pool 隔离 | `qa_schedule.py` `run_schedule` |
| 6 | P1 | 数据核验逐条证明前置，不查「本批次会改同一行/同一字段」 | `qa_verify.py` `verify_cases` |
| 7 | P1 | 宿主复核对「整句 expected + 整句 actual」仍降级 failed | `case-08/result.yaml`；`qa_exec.py` `_db_recheck_ok` |
| 8 | P2 | 运行使用启动快照（合理），配置中途改动无提示 | `qa.yaml` mtime 17:34 vs run 17:26 |

### 3.1 单条重测被整轮闸门硬拒，成功 toast 盖过 job 错误（P0）

- `_gate` 要求 `st.all_done(data)`（所有票 `done`），否则 `TestRejected`。
- 该闸门在 `_req_test` 里位于 rerun 处理之前且无条件执行，`rerun_cases` 不豁免。拒绝发生在 job worker 内，所以 HTTP 提交仍然成功，job 随后变成 `error`。
- `rerunCase` 只看提交返回：`state === "queued"` 时 toast「已提交重测，将排队执行」，否则 toast「已重测」。两种都是 success。页面级 `error` 只在请求抛错时设置。
- 需求页会把 job id 放进 query，`JobPanel` 会显示 `error` 和闸门日志。`QaView` 的重测不写 query。错误没有升到页面级 alert，成功 toast 也不撤回。

设计缺口：重测是诊断动作，被「还有未完成票」这条整轮门禁拦截在语义上是错的。提交成功不等于执行成功，终态要盖过那条绿 toast。

### 3.2 重测后横幅仍是整轮汇总，且不再刷新（P0）

- `showRunEndBanner` 读 `qa.latest_run.summary`，是整轮聚合。这次重测没跑成，横幅上的「0 通过 / 3 失败 / 15 阻塞」仍是那一轮的真实数字，不是算错了。
- 横幅按 `run_id` 去重（`run.run_id === lastBannerRunId` 即 return）。重测改同一轮、不新建 run，后续重测不再弹新横幅。`onJobDone` 对 `error` 的 `qa-run` 也会再调一次，同样被去重吃掉。
- 文案「本轮」让用户把这块旧汇总当成刚刚那次重测的结果。

### 3.3 failed 无条件变 B 票，缺用例缺陷分流（P0）

- `map_qa_result` 把每条 `failed` 收成 finding。`verdict == failed` 时 `spawn_fix_tickets(..., "test")` 生成产品 bug 票。
- `failed == 0` 且仍有 `blocked` 时直接 `return None`，不入库、不拆票。`case-defect` 因此只挡住纯阻塞轮。
- 本轮 3 条 failed 拆出 B1/B2/B3。B1、B3 是用例/复核问题。B2 的 HTTP `201` 是断言过严；按钮文案是否算产品缺口取决于规格是否要求不随 `navigator.language` 变化。三条都被收成必须做完才能再测的票。

### 3.4 闸门死锁（P0）

修用例走 `--redesign`，它和普通 test、单条重测一样先过 `_gate`。`all_done` 只认 `state == done`。于是「用例错了 → 产生票 → 票让你改不了用例，也重测不了」。实现 B 票的命令不走这道门，但正确动作是改测试资产，不是改产品。redesign / 单条重测不应受「票做完」约束。

### 3.5 一个坏模型池熔断整轮派发（P1）

- 同一 env 类 block 连续两次即 `breaker = True`，随后停止派发。还在 `pending`/`ready` 的 case 被批量标 `blocked`，reason 抄成触发熔断的那条（本次是 `worker exit: pi exit 1`）。
- 熔断是整轮一个布尔值，不看 pool。已经 in-flight 的健康池会跑完，队列不再派给它。本次坏池是 `opencode-go/deepseek-v4.1-flash`，MiniMax-M3 当时仍在跑 case-01/02/08，跑完后剩余 case 不再派发。
- 健康池若在两次失败之间返回非 block，`last_block_class` 会清掉，连续计数被打断。本次是 deepseek 的完成挤在 MiniMax 返回之前。
- `pi exit 1` 不做自诊断（鉴权 / 额度 / 上下文超限），用户没有可行动信息。

设计缺口：坏 pool 单独下线，健康 pool 继续领队列。

### 3.6 数据核验逐条做，不查会改同一行的串扰（P1）

design 期 `verify_cases` 逐条 setup → verify → cleanup。注释已说明种子共用一个库，所以串行，避免验证彼此竞赛。每条单独都能通过。执行期多 case 改同一行时，这道验证发现不了。

本次两行都撞了：case-01/02 共用 `10009268`，case-02 把它改成 1；case-03 的 reason 写 `10009269` 被后续 setup 覆写回 0。平台有 `_check_case_repos` / `_check_case_accounts`，没有「本批次里会写同一行、同一字段」的检查。

只读夹具可以共用一行。要查的是变更撞车，不是禁止任何共用。

### 3.7 宿主复核对整句 expected + 整句 actual 仍假失败（P1）

`3ca77e2` 的 `_db_recheck_ok`：能当单元格的 expected 必须等于 SQL 单元格；明显是句子时（含 `=`，或多于两个词），改拿 worker 记的 `actual` 去对单元格。

case-08 的 expected 与 worker 的 actual 都是整句。句子对不上单元格 `1`，`_scalar_eq(recorded, got)` 失败，整案从 `passed` 降级为 `failed`。reason 写成 `expected='刷新前后…' actual='1'`，这里的 `1` 是宿主读到的单元格，不是 worker 的 actual 字段。

本次单元格已经是 `1`，和句子意图一致，不该打成产品失败。双侧都是句子时宿主无法独立核对：不要降级成 failed，也不要静默保留 `passed` 当成宿主已确认。保留 worker 结论，并把该断言标成未复核。

### 3.8 运行使用启动快照（P2）

`qa.yaml` 在 run 进行中被改（17:34，run 17:26 起）。`_req_test` 开头 `load_qa_config`，pool 由此定死，中途不重读。用启动快照是对的。缺的是界面说明「本轮使用启动时的 pool 配置」。

## 4. 建议修复优先级

1. **P0-1 重测语义解耦**：单条 `rerun_cases` 绕过「全部票 done」闸门；`--redesign` 同样豁免。
2. **P0-2 前端反映真实 job 终态**：`rerunCase` 等到 job 终态再下结论。`error` 升到页面级 alert，并撤掉提交时的成功 toast。重测后的横幅只讲本条用例，同一 `run_id` 的重测也要刷新。
3. **P0-3 failed 分流**：报告里的 `failed` 要带「产品缺陷 / 用例缺陷」判定和证据。用例缺陷不拆产品 B 票，转成 design 修复项。纯 `blocked` 轮已经不拆票，这条补的是 failed。按钮文案这类「可能是规格问题」的条目不要自动收成必须做完才能再测的票。
4. **P1-4 pool 级熔断**：坏 pool 单独下线，健康 pool 继续领队列。`pi exit 1` 给出可行动诊断。
5. **P1-5 批次内写冲突**：design 期增加「本批次会改同一行、同一字段」的校验。只读共用一行放行。
6. **P1-6 宿主复核 prose 兜底**：expected 与 recorded actual 都是句子时不降级。保留 worker 结论，断言标未复核，不当成宿主已确认。
7. **P2-7 配置一致性提示**：run 期配置变更时说明「本轮使用启动快照」。快照本身不改。

## 5. 一句话结论

平台把「用例失败」直接收成产品 B 票，再用这张票锁死重测和 redesign。前端在 job 还没跑成时先报「已重测」，横幅又把整轮旧汇总留在屏幕上。本次「重测没跑成 + 3 张不该锁流程的 B 票 + case-03 一直 blocked」都是这条链的产物。

## 附：原始证据

- job：`curl -s localhost:8765/api/jobs/731f671f9b` → `state=error`
- run 汇总：`progress.yaml` / `result.yaml`（`0 passed / 3 failed / 15 blocked`，`case-defect=1 env=14`）
- B 票：`reqs/PG-13218/STATUS.yaml:356-379`（B1/B2/B3 均 `state: ready`）
- 报告：`reqs/PG-13218/test-reports/2026-09-23-175212-574469.md`
- case-01 / case-02 / case-03 / case-08：`qa/evidence/2026-09-23-172644/case-0{1,2,3,8}/result.yaml`
