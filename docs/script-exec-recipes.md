# 脚本执行配方（Script Exec Recipes）

状态：implemented
日期：2026-09-18
关联事故：PG-13054 自动测试 4 轮共 ~50 条 case 因 setup 连不上测试库而 blocked

三层都做。本期要把「选环境 = 选执行现场」做成宿主能力，而不是一家公司的 k8s 管道。下面改的是契约，不是砍层。

## 1. 背景与问题

### 事故复盘

PG-13054 提测后自动测试，选了 `env: test`，但 `.rb` 造数脚本仍在**本机 worktree** 里跑
`bin/rails runner`。worktree 的 `config/external.yml` 是 example 复制拼出来的（真配置被
gitignore，`git worktree add` 不携带），解析出内网 PolarDB（172.16.195.187），本机无路由
→ TCP 超时 → 所有需要造数的用例 blocked。测试环境本身健康：不依赖造数的纯前端用例全部
通过，且其网络断言打到 `research.dev1.rccchina.com` 正常返回 200。

### 三个根因

| # | 根因 | 位置 |
|---|---|---|
| a | 脚本执行不随 env 切换场地：`run_case_script` 只有两条路（`.sql`→usql、其他→本地 runner），选 test 只换 base_url/登录态/db.url | `qa_exec.py:200` |
| b | 本地 runner 依赖 worktree 自带 gitignored 本地配置，创建 worktree 不同步、无真配置时 example 静默顶替 | `service.py` worktree_add（另案） |
| c | 无连通性预检与熔断：同一环境故障逐条撞墙，4 轮 × 最多 14 条，每条烧一次 rails 启动 + 超时 | `qa.py` run loop |

根因 a 的本质不是「少写了一条 ssh」，而是宿主没有 **执行现场** 这个概念：浏览器打 A 世界，脚本在 B 世界起进程。

### 产品不变量

一次 run 里，**浏览器打到的世界和脚本执行的世界必须是同一个**：

| 浏览器 | 脚本现场 | 判定 |
|---|---|---|
| 本机 base_url（localhost / 本机 IP） | `local`（freeze worktree） | 合法 |
| 远程 base_url | 远程 transport（ssh / jms-k8s / docker / raw / delegate） | 合法 |
| 远程 base_url | `local` | **配置错误**：正是本次事故。`check-env` 与 `resolve_executor` 拒绝，除非显式 `allow_cross_site: true`（调试用，日志打警告） |

不按环境名特殊判断（不写死「名字叫 test 就必须远程」）。只看 base_url 是否本机、exec 是否 local。

### 目标

- 选 env = 选现场。脚本（造数 / 清理 / 将来的远程 SQL）走该 env 的 exec 配方。
- 执行机制对**公司基础设施差异**开放：内置常见形状，其余用数据表达，再不行走冻结 I/O 的外部执行器。新公司接入以配置为主，代码为辅。
- 环境故障**秒级暴露**：跑批前预检、批中按错误类熔断，看板一眼能看出是环境挂了。
- 所有 transport 共用同一套**脚本 I/O 契约**（stdin / 环境变量 / stdout），生成规则与执行器对齐。

### 非目标

- 不解决 worktree 本地配置同步（根因 b，另案：worktree_add 后同步 + 禁止 example 静默顶替）。
- 不自动部署分支到测试环境。check-env 可以**对比** pod 内 git sha 与 freeze 分支，不一致只警告。
- 不把 qa-powers 的 `grep Running \| awk \| grep` 管道原样焊进核心——那条管道是已知陷阱，L2 用 kubectl 结构化选 pod。

## 2. 核心抽象

任何公司的任何测试环境，执行一个脚本最终都是：

> **宿主把脚本字节送到执行现场、在现场用 runner 跑完、把 stdout / stderr / 退出码带回来。**

拆开两件被焊死的事：

- **runner**：现场怎么解释脚本（`bin/rails runner -` / `python -` / `node -` / `psql -f -`），应用语义。
- **transport**：怎么到达现场（local / ssh / jms-k8s / docker / raw argv / 外部执行器），基础设施语义。

**宿主永远拥有 stdin。** 配方只描述「现场那条读 stdin 的命令」，不在用户字符串里写 `< {script}`。`{script}` 不进入命令模板——一进模板就变成转义与注入面。

差异在 transport，transport 尽量是数据。代码只为「脏活」存在：选 pod、JMS 四段身份、引号、stdin 模式、脱敏、超时。

## 3. 配置模型

`qa.yaml` 的 `envs.<env>` 增加 `exec` 段。判别键只有一个：`use`。旧 `script.runner` 在没有 `exec` 时等于 `use: local`。

```yaml
envs:
  test:
    base_url: http://research.dev1.rccchina.com
    exec:
      use: jms-k8s          # local | ssh | jms-k8s | docker | raw | delegate
      with:                 # 该 use 的结构化参数，见 §3.2 / §3.3 / §3.4
        ...
```

校验：

- `use` 必填（或由旧 `script.runner` 推出 `local`）。
- `use` 与 `with` 的键必须匹配该层 schema，多键 / 错键 → `TestRejected`，中文说明缺什么。
- 不允许再写一套平行的 `profile:` / `raw:` / `delegate:` 抢优先级。没有优先级，只有 `use`。
- `exec` 与旧 `script.runner` 同时出现：`script.runner` 只作为 `with.runner` 的缺省；两者都写且不一致 → 配置错误。

### 3.1 脚本 I/O（所有 use 共用，生成规则的唯一真相）

宿主调用一次 `run` 时保证：

| 通道 | 约定 |
|---|---|
| stdin | 脚本文件的 UTF-8 字节。现场 runner 必须从 stdin 读（`bin/rails runner -` / `python -` / `node -`）。local 也走 stdin，不走 argv 路径——否则同一份脚本在本地能读 `ARGV`、远程就丢。 |
| 环境变量 | 始终注入：`QA_ENV`、`QA_JIRA`、`QA_CASE_ID`、`QA_SCRIPT_KIND`（`setup` \| `cleanup`）。**禁止**靠 runner argv 传业务参数。 |
| stdout | 唯一回传通道。seed id、影响行数只许打 stdout。 |
| stderr | 诊断；错误分类用它，不当业务数据。 |
| 退出码 | 非 0 仍返回 `ExecResult`，由上层标 `TestRejected` / case `blocked`。 |
| cwd | `local`：freeze worktree。远程：`with.workdir`（空 = 镜像默认工作目录，配方不加 `cd`）。 |

脚本规则（qa-design 与 `context.md` 必须写上，否则远程必翻）：

1. **单文件。** 默认不支持 `require` 邻居。需要多文件时走 `payload: bundle`（§3.5），不是悄悄依赖盘上的相对路径。
2. **状态落 DB。** 禁止把 setup→cleanup 的约定写到执行现场本地文件。反例：`setup_seed_task.rb` 写 `tmp/qa_pg13054_seed_ids.txt`——pod 多副本 + 重启，cleanup 打到另一台就丢。标记用数据侧约定（固定命名 / 专属列 / `QA_CASE_ID` 前缀）。
3. **幂等，且不假设两次执行落在同一副本。**
4. 业务参数只读 `ENV['QA_*']`，不读 `ARGV`。

`local` 仍可注入 `DATABASE_URL=db.url`（让 worktree 的 `database.yml` 让路）。远程 **禁止** 注入 host 的 `DATABASE_URL`：现场用自己的配置。远程 **不要求** host 配 `db.url`（那是 usql 用的）。远程 **不要求** freeze worktree 存在。

worktree revert（`_revert_new_paths`）仅 `use: local`。

### 3.2 L2：内置 profile（结构化，代码兜底脏活）

`use` 为下列之一时走 profile。ping 由实现派生，无需配置。

#### `local`

```yaml
exec:
  use: local
  with:
    runner: bin/rails runner   # 缺省吃旧 script.runner
```

行为：`cd {worktree}`，stdin 喂脚本，`{runner} -`，注入 `DATABASE_URL`（若配了 `db.url`）。保留 revert。这是旧行为的契约升级（argv → stdin），旧种子脚本若读 `ARGV` 必须改读 `QA_*`。

#### `ssh`

```yaml
exec:
  use: ssh
  with:
    target: user@host          # 或拆 host/user/port
    port: 22
    workdir: /app              # 可空
    runner: bin/rails runner
```

宿主：`ssh -p {port} {target} 'cd {workdir} && {runner} -'`，stdin = 脚本。ping：同管道 `echo ok`（不启动 runner）。

#### `docker`

```yaml
exec:
  use: docker
  with:
    container: research-web    # 或 service 名，见 compose
    runner: bin/rails runner
    workdir: ""                # 空 = 不 cd
```

宿主：`docker exec -i {container} {runner} -`。ping：`docker exec {container} echo ok`。

#### `jms-k8s`

按现网 JumpServer 四段身份建模，**不要**写成 `{user}@{node}@{bastion}` 三段，也**不要**把 `user` 配成 `dev` 这种假值。qa-powers init 的真实形状：

```
ssh -p {port} '{jms_user}@{os_user}@{node_ip}@{bastion}'
# 例：alice@root@172.16.4.23@jump.example.com
```

`jms.user` 存前两段（`alice@root`），节点 IP 从 `nodes` 表取，host 是堡垒机域名，端口单独配。认证失败会锁号：ping/run 对 JMS 认证类错误最多重试 0 次，立刻 `ExecUnreachable`。

```yaml
exec:
  use: jms-k8s
  with:
    jms:
      host: jms.example.com
      port: 22222
      user: alice@root          # 前两段，明文；与 qa.yaml 其它凭据同一信任模型
    default_node: k8s-1
    nodes:
      k8s-1: 172.16.4.23
    namespace: research
    container: web
    runner: bin/rails runner
    workdir: /var/www/research  # 空 = 不加 cd；禁止猜 /app
    # 选 pod：prefer selector；pattern 仅作退路
    pod:
      selector: "app=research-web,tier=web"   # kubectl -l
      # pattern: "^research.{,17}$"           # 仅当没有稳定 label 时
```

选 pod（每次 ping/run 现解析，禁止跨调用缓存名字——hash 随重启变）：

1. 有 `pod.selector`：`kubectl get pods -n {ns} -l {selector} --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}'`
2. 否则 `pod.pattern`：同一 field-selector，再用正则滤名字。**实现里完成**，不在 shell 里拼 `grep Running | awk | grep`。
3. 0 个 / 多个且未定义选哪个：失败，错误类 `PodNotFound`。多副本时取 Running 列表的第一项可以，但脚本必须遵守 §3.1 第 3 条。
4. `kubectl exec` **必须** `-c {container}`。

stdin 模式：`kubectl exec -i ... -- {runner} -`。多层引号由代码生成 argv 数组，不经用户 shell 模板。

SSH 复用：同一 run 内对同一 `{port,身份串}` 开 `ControlMaster`，避免 50 次 JMS 握手。进程退出或 run 结束拆掉 master。这是实现细节，配置不出现。

runner 启动慢（Rails 约 1 分钟）不等于卡死。`with.timeout` 缺省 300s，ping 缺省 30s（ping 不启动 runner）。

### 3.3 L1：raw（任意可拼成「读 stdin 的命令」的入口）

覆盖「我们有自己的 CLI / 网关」的公司。默认 **argv 数组**，宿主 `subprocess` 直接 exec，stdin 接脚本。变量只替换连接参数，**没有 `{script}`**。

```yaml
exec:
  use: raw
  run: ["mycorp-cli", "exec", "--env", "test", "--app", "research", "--", "bin/rails", "runner", "-"]
  ping: ["mycorp-cli", "exec", "--env", "test", "--app", "research", "--", "echo", "ok"]
```

`ping` 必配。缺 ping = 配置错误（预检是一等公民）。

需要 shell 特性（管道、命令替换）时显式打开，避免把 argv 做不成的事偷偷塞进 `sh -c`：

```yaml
exec:
  use: raw
  shell: true
  run: "mycorp-cli exec --env test -- bin/rails runner -"
  ping: "mycorp-cli exec --env test -- echo ok"
```

`shell: true` 时宿主仍然自己接 stdin，用户字符串里出现重定向符号视为配置错误。secrets 若不想写进 yaml，用 `{env:NAME}` 替换**单个 argv 元素**（或 shell 模式下的 token），不拼接进更大的字符串。日志打渲染前的 argv + 变量名，不打渲染后全文。

L1 的职责是表达「一条读 stdin 的进程」。选 pod、JMS 四段、ControlMaster 这类脏活不要用 raw 复刻——那是 L2 的存在理由。能用 profile 就用 profile。

### 3.4 L3：外部执行器（现场不是一条 stdin 进程时）

L3 不是「每次 setup 找个 LLM 当 subprocess」。它是一条**冻结 I/O 协议**，两种适配器：

| 适配器 | 何时 | 热路径是否可接受 |
|---|---|---|
| `command` | 公司能提供一个二进制 / 脚本，自己知道怎么进现场 | 是（确定性，批跑用这个） |
| `skill` | 进现场必须走 agent 会话（带审批的堡垒机、只在对话里可用的控制台） | 否（慢、贵、有噪声）。仅逃生舱；`check-env` 可用，批跑要写进文档让接入方知道代价 |

协议（两种适配器共用）：

- 宿主准备：脚本路径、结果路径 `exec-result.json`、环境变量 `QA_*`。
- 执行器把脚本送到现场（stdin/argv 传内容，不内联改写），跑完写：

```json
{"exit_code": 0, "stdout": "...", "stderr": ""}
```

- 宿主**只信这个文件**。缺文件 / JSON 不合法 / 超时 → 执行失败。stdout 里的 agent 废话全部丢掉。
- ping：`command` 应支持 `--ping`（或单独 `ping` argv）。`skill` 的 ping 等于跑一行 hello——成本与一次 `run` 同级，要写进接入文档。

```yaml
exec:
  use: delegate
  command: ["company-qa-exec", "--env", "test"]
  ping: ["company-qa-exec", "--env", "test", "--ping"]
  timeout: 600
```

或：

```yaml
exec:
  use: delegate
  skill: k8s
  timeout: 600
```

`command` 与 `skill` 配两个 → 配置错误。`skill` 走现有 `pi_argv` + `run_pi_print`，prompt 冻结成版本化模板（脚本路径、结果路径、`QA_*`、禁止修改脚本内容），不复用交互式 k8s skill 的原文当热路径。k8s skill 仍给人机排查，不给 50 次 setup。

公司侧最小 command 骨架：读 stdin 或 `--script`，进现场，写 `exec-result.json`（路径由 `--result` 或 env 给）。skill 骨架同此 I/O，装在项目 `.pi/skills/` 或该公司插件的 skills 目录。

### 3.5 可选：bundle payload

缺省 `payload: file`（单文件 stdin）。多文件脚本：

```yaml
exec:
  use: ssh
  payload: bundle
  with: { ... }
```

宿主把脚本所在目录打 tar 送到现场，解到临时目录，跑入口文件，最后删临时目录。现场仍禁止把跨调用状态写进这个临时目录。第一批实现可以只做 `file`，但接口从第一天带上 `payload`，避免 local 用路径、远程用 stdin 再分叉一次。

### 3.6 `.sql` 走同一套现场

本期实现非 SQL 即可跑通事故，但抽象必须一次定对：`.sql` 不是永远的「本机 usql」。

| `db.exec`（新，可选） | 含义 |
|---|---|
| 缺省 / `host` | 现状：宿主 usql + `db.url`。host 能直连测试库的公司继续用。 |
| `inherit` | 同一 env 的 exec 配方，runner 换成 `usql` / `psql -f -`，stdin = SQL。host 够不着 DB、只能进 pod 的公司用这个。 |

`inherit` 未实现时：`.sql` 仍走 host usql；`check-env` 若 `db.url` 不通，提示改 `db.exec: inherit` 而不是让 50 条 SQL 全超时。

## 4. 宿主侧接口

```python
# src/dev_yard/script_exec.py

class ExecErrorClass(StrEnum):
    UNREACHABLE = "unreachable"   # 网络 / 路由 / 握手
    AUTH = "auth"                 # JMS/SSH 认证；禁止狂重试
    POD_NOT_FOUND = "pod_not_found"
    TIMEOUT = "timeout"
    RUNNER = "runner"             # 现场 runner 崩了、boot 失败
    SCRIPT = "script"             # 脚本自己 abort / 非 0（业务）
    CONFIG = "config"

@dataclass
class ExecResult:
    code: int
    stdout: str
    stderr: str
    error_class: ExecErrorClass | None = None  # code==0 则为 None

class ExecUnreachable(TestRejected):
    error_class: ExecErrorClass

class ScriptExecutor:
    label: str
    use: str  # 配置里的 use
    site: Literal["local", "remote"]

    def ping(self, *, timeout: int = 30) -> None:
        """同 transport、不启动 runner。失败抛 ExecUnreachable。"""

    def run(self, script: Path, *, on_log=None, timeout: int = 300,
            env_extra: dict[str, str] | None = None) -> ExecResult:
        """退出码非 0 仍返回 ExecResult。"""

def resolve_executor(env_cfg: QaEnv, *, base_url: str) -> ScriptExecutor:
    """校验 use/with、跨站规则。配置错误抛 TestRejected。"""
```

`run_case_script`：

```python
if script.suffix.lower() == ".sql" and db_exec != "inherit":
    return _run_sql(cfg, script, on_log)
executor = resolve_executor(cfg.env, base_url=cfg.env.base_url)
result = executor.run(script, on_log=on_log, env_extra={...QA_*...})
if result.code != 0:
    raise TestRejected(...)  # 带 error_class，供熔断
return result.stdout
```

- `script_lock` 默认仍串行：造数脚本通常共享业务表，QA_CASE_ID 让它们**可以**隔离，但不默认并行。`exec.parallel: true` 是显式选择。
- 复用 `_run` 的超时 / label / 日志格式。远程命令的 argv 经脱敏再进 `on_log`。

## 5. 运行时护栏

### 预检（run 开始前，`qa.py` 编排处）

```
resolve_executor → ping
  不通 → 不再起 worker：
    progress.yaml / result.yaml 写 env_fault: { class, message }
    所有 setup 依赖 case → blocked，reason 统一前缀 "env fault: ..."
    无 setup 的 case 正常跑（本次事故里纯前端用例本可全绿）
```

看板：run 级 `env_fault` 有值时，测试页顶栏展示，避免人连点四轮。

### 熔断（批中）

按 `ExecErrorClass` 计数，不按 stderr 前 N 字节（超时文案带耗时，字节前缀对不上）。

- `UNREACHABLE` / `AUTH` / `POD_NOT_FOUND` / `TIMEOUT` / `RUNNER`：连续 ≥2 条 setup 同一 class → 环境故障，剩余 setup 依赖 case 跳过，无 setup 继续。
- `SCRIPT`：脚本业务失败（找不到种子资源等），**不**熔断。
- JMS `AUTH`：第一次失败即熔断，不等第二条（锁号）。

### 提测与 test 环境

`req test` 已要求 `phase=testing`，不要再做一层「选 test 必须先提测」的假门。要写进 `context.md` 与 qa-run skill 的是语义：

> 本 run 的 env 若 `site=remote`：浏览器和脚本都打**已部署**现场，不是 freeze worktree。DB 断言、造数用的模型以部署版为准。worktree 只供读代码 / 变异检测。

`check-env` 可打印现场应用 sha 与 freeze 分支 tip，不一致警告，不阻断（部署滞后是运维问题）。

### `dev-yard qa check-env`

接入与回归的黄金路径，不是配套：

1. 解析配方（配置错误这一步就失败）
2. ping
3. 经配方跑一行 hello 脚本，断言 stdout 精确回显
4. 跨站规则检查
5. 可选：现场 sha vs freeze

新公司：**填 exec → check-env 全绿 → 跑批**。Web 配置页提供同一按钮。

## 6. 安全与脱敏

qa.yaml 已 gitignore，凭据明文直存（账号、db.url、JMS user）是本项目既有信任模型。exec 不另搞一套「只能 `{env:NAME}`」。

- 连接参数写在 `with` 里，与 `db.url` 同等对待。
- `{env:NAME}` 是可选项，给不想把某密钥落 yaml 的人。
- `on_log` / 看板 / 报错走现有 `redact_qa_yaml` / `redact_url`。
- raw 日志：渲染前 argv + 变量名列表。
- JMS 认证失败：错误信息可含「核对四段身份」，**不得**把完整身份串打进 progress.yaml。

## 7. 兼容与产品面

- 旧 `envs.<env>.script.runner` 且无 `exec` = `use: local`。行为变化只有 argv→stdin 与 `QA_*`；这是有意的，qa-design 同步改生成规则，现有 `setup_seed_task.rb` 一并改掉（它还违规写了 tmp 文件）。
- Web 配置页按 `use` 切换表单：profile 给结构化字段（jms-k8s 不要做成任意 with 键值对），raw 给 argv 列表编辑器 + ping，delegate 给 command 或 skill。底部「检查环境」= check-env。校验与 §3 同一套 schema。
- 配方示例：`docs/recipes/` 每种 `use` 一份可抄 yaml（local / ssh / docker / jms-k8s / raw / delegate-command / delegate-skill）。本文件只留契约，示例不堆在这里。

边界：exec 只管「把脚本送到现场跑完」。若公司连测试流程整体都不同（设计、报告、编排），那是 TestProcess 插件 + `InboundReport`，与本方案正交。任何 TestProcess 都复用同一套 ScriptExecutor。

## 8. 测试策略

- 单测：`resolve_executor` 的 `use` 校验、跨站拒绝、旧 `script.runner` 糖、`{env:}` 缺失、raw 模板里出现 `{script}` 或重定向则报错、脱敏。
- local 回归：同一 seed 脚本 stdin 模式输出与黄金文件一致；revert 仍只发生在 local。
- transport argv：PATH 注入 stub `ssh` / `kubectl` / `docker`，断言收到的 argv 与 stdin 字节。jms-k8s 断言四段身份、`-i`、`-c`、runner `-`、选 pod 走 jsonpath/field-selector 而不是 grep 管道。
- 错误类：超时 / 认证 / pod 空 / 脚本 abort 分别打到对应 class；熔断只对非 `SCRIPT`。
- 护栏：ping 失败 → run 级 `env_fault`、有 setup 的 case 零执行、无 setup 的照跑。
- L3：stub command 写合法/缺失/坏 JSON 的 result 文件；skill 路径 stub `run_pi_print`，宿主仍只信文件。
- check-env：hello 回显失败为红。

## 9. 落地顺序（可独立合入，但契约一次定死）

接口、stdin 契约、错误类、跨站规则从第 1 步就冻结，后面只加 transport，不加新语义。

1. **`script_exec.py` + local stdin 实现** + 跨站校验 + 单测。`run_case_script` 改走 executor。qa-design / context.md / 现有种子脚本改到 §3.1。
2. **护栏 + check-env**：ping、`env_fault`、按 class 熔断、CLI `qa check-env`。没有远程 transport 时，local 的 ping 可以是 `true` / runner `--version`，先把编排跑通。
3. **L2 `jms-k8s` / `ssh` / `docker`**：stub 测试先行，再对本机 JMS 跑 check-env。这是修掉 PG-13054 的那一刀。
4. **L1 raw**（argv 默认，`shell: true` 显式）。
5. **L3 command**，然后 **L3 skill**（skill 最后，避免批跑误用）。
6. **Web 表单 + `docs/recipes/`**。`db.exec: inherit` 可与 L2 并行，不挡非 SQL 路径。

## 10. 新公司接入

原则：dev-yard 核心不动。接入物是 qa.yaml 的一段 `exec`，或一个遵守 exec-result.json 的 command/skill。qa.yaml 按部署隔离，公司之间互不影响。

| 现场形态 | `use` | 新公司要做的 |
|---|---|---|
| 本机 worktree | `local` | 旧 `script.runner` 即可 |
| 裸 ssh / docker | `ssh` / `docker` | 填 with |
| JumpServer + kubectl | `jms-k8s` | 填四段 JMS + namespace/container/pod.selector/runner/workdir |
| 自有 CLI，能读 stdin | `raw` | `run` + `ping` 两组 argv |
| 自有执行器二进制 | `delegate` + `command` | 遵守 result json |
| 只有 agent 能进现场 | `delegate` + `skill` | 窄契约 skill；接受批跑成本 |

自检：`dev-yard qa check-env --env test` 三绿再跑批。

## Considered Options

- **三层都做，但 L1 是 argv、L3 是 I/O 协议（采纳）**：灵活性来自分层。L1 若做成带 `{script}` 的 shell 字符串，等于把 L2 要消灭的转义地狱交给每家公司。L3 若只有 skill，等于用 LLM 当热路径——和「全部委托 skill」是同一类错，只是披了层 yaml。
- **只硬编码 jms-k8s（否决）**：修得了本次事故，下一家公司再焊一条。profile 可以内置 jms-k8s，但不能是唯一通道。
- **全部委托 skill（否决）**：慢、贵、stdout 不可靠。协议可以共用，适配器必须先有 command。
- **每公司 fork（否决）**：升级地狱。
- **`profile > raw > delegate` 优先级（否决）**：与「配多个报错」矛盾。单一判别键 `use`。
- **local 继续 argv、远程 stdin（否决）**：同一份脚本两种 I/O，生成规则无法写成一条。统一 stdin + `QA_*`。
- **按 stderr 字节前缀熔断（否决）**：超时文案不稳定。按 `ExecErrorClass`。
- **exec 密钥只走环境变量（否决）**：与 qa.yaml 明文凭据模型不一致。同文件同信任，`{env:NAME}` 可选。
- **选 test 必须再做一次提测门（否决）**：`req test` 已要求 `phase=testing`。要写的是「远程现场 = 已部署代码」，不是新 gating。
- **把 qa-powers §0.5 管道逐字移植（否决）**：grep/awk 顺序是已知陷阱；JMS 是四段不是三段。L2 用结构化 kubectl + argv 数组。
