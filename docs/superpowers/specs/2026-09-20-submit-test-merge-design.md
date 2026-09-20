# 提测 = 合并各仓冻结分支到测试分支并 push（设计草案）

- 日期：2026-09-20
- 状态：草案（关键决策已定，见 §14；待实现）
- 影响命令：`dev-yard req submit-test`、Web 看板「提测」、`dev-yard req test`
- 关联：`src/dev_yard/test_report.py`、`src/dev_yard/service.py`、`src/dev_yard/reqboard.py`、`src/dev_yard/web/jobs.py`

## 1. 背景与问题

现状 `submit_test` 只做状态翻转（`test_report.py:68-88`）：

```
校验：全部票 done + contract_review == passed + 未 passed + phase != testing
动作：data["phase"] = "testing"; test.status = "awaiting"
```

它不碰 git。真正的分支推送另有一条独立命令 `dev-yard req push`（`service.py:1879`），推的是**冻结分支** `req/<JIRA>`，用于提 PR。项目里甚至不存在「测试分支」这个概念（全库无 `test_branch` 配置）。

期望语义：**提测时，对每个已登记仓库，把该需求的冻结分支 merge 进该仓配置的测试分支，并 push 测试分支。** 本设计把这个 merge+push 收进 `submit-test`，并补上冲突处理与可重入语义。

## 2. 目标与非目标

### 目标

1. 每个仓库可各自配置测试分支（长命共享分支，如 `PG-test`）；没配的仓库直接跳过。
2. 提测对每个命中仓库执行 `fetch → 在隔离 worktree 中 merge 冻结分支 → push 测试分支`。
3. merge 冲突即停：默认让 AI 自动解冲突；AI 解不掉或校验不过，保留现场、不 push、不改 phase，交人工。
4. `submit-test` 可重入：测试失败修完 B 票后能再次提测（重新 merge+push）。
5. 全流程非交互（CLI 与 Web job 均可跑），进度可流式输出。
6. 全部命中仓库 push 成功后才置 `phase=testing`。

### 非目标

- 不改 `req push`（仍推冻结分支提 PR）。
- 不做远端 MR / PR 创建。
- 不做跨需求测试分支的排队/锁表（只在进程内做串行锁，见 §5.1）。
- 不让 agent 改业务逻辑以消除冲突（只做冲突消解，见 §7）。
- 不引入新的 phase；仍是 `open/frozen/testing/done`。

## 3. 配置变更（`repos.yaml`）

### 3.1 每仓测试分支

```yaml
repos:
  research:
    url: git@git.rccchina.com:leads-in/research.git
    default_base: master
    role: 后端
    test_branch: PG-test        # 新增；缺省/留空 = 该仓不提测，跳过
  research-front:
    url: git@git.rccchina.com:leads-in/research-front.git
    default_base: master
    role: 前端
    test_branch: tina/test      # 每仓可不同
  research-report-service:
    url: git@git.rccchina.com:leads-in/research-report-service.git
    default_base: master
    role: 分期报告服务
    # 无 test_branch → submit-test 跳过
```

- 普通分支名（**不支持 `{jira}` 模板**）：测试分支是长期共享的，同一仓对所有需求都是同一个分支。
- 校验：非空即走 `gitops.assert_branch_name`，非法名在 `save_repos` / `repo_add` 时报错。
- 空值/缺省 → `skip`。

### 3.2 全局开关

```yaml
git:
  freeze_branch: req/{jira}
  ai_resolve_conflicts: true    # 新增，默认 true；false 时冲突直接停
```

### 3.3 配置层改动

| 文件 | 改动 |
|---|---|
| `config.py` `Repo` | 新增 `test_branch`（`str \| None`，默认 `None`） |
| `config.py` `load_repos` | 读 `raw.get("test_branch")`，`_blank` 后**不**做硬校验 |
| `config.py` `save_repos` | 非空时 `assert_branch_name` 校验并写回 `test_branch` |
| `config.py` `GitSettings` | 新增 `ai_resolve_conflicts: bool = True` + load/save |
| `config.py` `git_settings_out` | 输出该开关供 Web 配置页 |
| `web/schemas.py` `RepoAddIn` / `RepoPiIn` | 增加 `test_branch` 字段（若走 repo 编辑 UI） |
| `web/routes/api_settings.py` | `PUT /api/repos/{alias}` 支持改 test_branch（新增 `RepoConfigIn` 或扩展 `RepoPiIn`） |

校验位置：非法分支名只在 **写入路径**（`save_repos` / `repo_add` / Web 编辑接口）报错。`load_repos` 是所有读路径的公共入口，若在此抛错，一个手改坏的值会让整个工作区读取失败；因此 `load_repos` 不硬校验、原样读出，非法/空值在**提测使用时**按 `skip` 处理并写进该仓 `integration.detail` 警告（实测实现见 `test_integrate._integrate_one`）。

> 若第一刀不在 Web 编辑仓库配置，至少 `repo add` 支持 `--test-branch`，并允许手改 `repos.yaml`。

## 4. 核心流程

`submit_test(root, jira, *, remote="origin", ai_resolve=None, on_progress=None)`：

```
1. 校验（保持现有语义）
   - 全部票 done
   - contract_review == passed
   - 非 test_passed（已通过则拒绝）
2. 重入判定（§8）
   - 若 phase == testing 且 test 未通过：允许「重新提测」
3. 解析目标仓库
   - aliases = 该需求所有票的 repo（去重，排序）
   - 对每个 alias：repo.test_branch 为空 → 记 skipped，跳过
   - 全部 skipped → 不碰 git，仅按旧行为置 phase=testing（向后兼容）
4. 逐仓集成（§5、§6、§7），任一失败 → 中止，不置 phase，抛出带明细的错误
5. 全部成功 → 写 STATUS（§9）→ phase=testing，test.status=awaiting
```

要点：

- 现有校验顺序与错误文案尽量不变，既有测试 `test_submit_test_*` 不应回归。
- `phase` 只在**所有**命中仓库 push 成功后翻转；部分成功时保持原 phase，避免「状态说已提测、测试分支却缺仓」。
- **锁粒度与脏写**：`submit_test` 现有实现全程持 `st.jira_lock(jira)`（`test_report.py:70`）。AI 冲突消解是 `pi` 长任务（`YARD_PI_TIMEOUT` 默认 3600s），把它放在 `jira_lock` 内会把看板对该需求的其它操作锁死一小时。改为：校验阶段短持 `jira_lock`；merge/push/AI 消解在**锁外**执行（并发由 §5.1 的 `(source, test_branch)` 锁串行化）。
- **锁外执行期间状态可能被别人改**（改/完成 ticket、`req reset-phase`、`req delete`）。因此末尾重新获取 `jira_lock` 时**必须重新 `data = st.load(root, jira)`**，校验需求仍在（目录存在、phase 仍是 `frozen`/`testing`），再在**最新 data** 上合并 `integration` 结果并翻 phase；绝不把锁外读到的旧 `data` 直接 `st.save` 回去。参照 `run_stage` 写回前的重新加载（`service.py:826`）。
- `submit_test` 只负责在无锁区调用 `test_integrate.integrate_test_branches`，不在外层包大锁。

## 5. 集成 worktree 方案

**用临时 detached worktree，不占用共享测试分支的本地引用。**

对每个 alias：

```
source = repo.source_path(root)                     # .repos/<alias>
freeze = resolve_freeze_branch(root, jira, data)    # 冻结分支名
test  = repo.test_branch
merge_wt = paths.test_merge_worktree(root, jira, alias)
        = root/.yard-worktrees/<jira>/<alias>/_test-merge

git -C source fetch origin <test>                               # 更新 origin/<test>
git -C source worktree remove --force <merge_wt> 2>/dev/null; git -C source worktree prune
git -C source worktree add --detach <merge_wt> origin/<test>   # 复用/重建
git -C <merge_wt> merge --no-edit <freeze>
  → 无冲突：commit 由 merge/ff 产生
  → 有冲突：见 §6/§7
git -C <merge_wt> push origin HEAD:refs/heads/<test>           # 非 force
完成后 worktree remove（成功时）
```

- `git fetch origin <test>` 在标准克隆下会更新 `refs/remotes/origin/<test>`（`ensure_clone` 是普通 `git clone`，refspec 齐全）；无需显式 refspec。
- 远端分支不存在时 `fetch` 报 `couldn't find remote ref`。**不自动创建**，转为清晰错误：`remote test branch '<test>' not found on <remote>; 请先创建或检查 repos.yaml`（见 §10）。
- `worktree add` 前必须先 `worktree remove --force` + `worktree prune` 清理上次冲突/异常遗留的 `_test-merge` 与注册信息，否则会报 `already exists`。
- 冻结 worktree 若有未提交改动，这些内容不在 `<freeze>` commit 里、也不会进测试分支。与 `req_push` 一致（`service.py:1931-1934`）：集成前对该仓 `req_worktree` 执行 `gitops.commit_all(...)`，或至少 `has_changes` 时明确报错让人先提交。

为什么用 detached + `HEAD:refs/heads/<test>`：

- 测试分支**不在本地建分支**，避免「同一仓同一测试分支被多个需求 worktree 同时 checkout」的 git 冲突。
- 合并通过 `push <sha>:refs/heads/<test>` 落地远端；本地无需长期 ref。
- 失败/冲突时保留 `_test-merge` 目录，供人工/AI 继续（路径记进 STATUS）。

路径选择注意：**不能**放 `reqs/<JIRA>/worktrees/` 下——`req_push` 会扫该目录，把 `_test-merge` 当仓库 alias（`service.py:1892`）。放在 `.yard-worktrees/<jira>/<alias>/_test-merge`，这里不受 `req_push` 影响。

`.yard-worktrees/<jira>/<alias>/*` 会被 `_teardown_worktrees` 当 ticket 目录遍历（`service.py:431-446`）；detached worktree 的 `_checked_out_branch` 返回 None，因此 `branch_delete("HEAD")` 静默失败、`worktree_remove` 能删掉目录。所以它其实不会残留；为语义清晰，仍在 `_teardown_worktrees` 的遍历里跳过以 `_` 开头的目录，并显式清理 `_test-merge`。

### 5.1 非快进 / 并发推

- `fetch` 后 push 之间若远端被推进，push 被拒（non-fast-forward）。策略：重新 `fetch origin <test>`，**重建 `_test-merge` detached worktree 到新的 `origin/<test>`**（等价于 `reset --hard` 且顺带清掉上一轮未跟踪残留），再重新 merge+重推，最多重试 1 次；仍失败按失败处理。
- 进程内对 `(source, test_branch)` 加一把锁（模块级 dict + `threading.Lock`，类似 `status.jira_lock`），串行化同仓同测试分支的集成，防止两个 Web job 并发推同一分支。
- 跨进程（CLI 与 Web 同时跑）不覆盖，靠上面的 non-ff 重试兜底。

## 6. 冲突即停的默认行为

- `git merge` 返回非零且 `gitops.unmerged_files(merge_wt)` 非空 → 冲突。
- 若 `git.ai_resolve_conflicts` 为 false 或调用方显式 `--no-resolve`：
  - 不 `merge --abort`（保留冲突现场）。
  - 写 STATUS：`test.integration.<alias>.conflict = true`、`worktree = <merge_wt>`。
  - 抛 `ReportRejected`，文案含冲突文件清单 + 现场路径 + 人工解决后重跑 `dev-yard req submit-test`。
  - phase 不变。
- 不允许把带冲突标记的 merge 提交后 push。

## 7. AI 自动解决冲突

### 7.1 触发

默认开启。逐仓 merge 冲突时，先尝试 AI，再决定是否停。

### 7.2 调用方式

复用现有 runner 机制（`runners.get_runner` + `PiRunner.start`），**不走 `run_stage`**（避免它按需求文件做快照/恢复和写 `stage_runs`）。

- **不注册**一个内置 stage，而是在 `test_integrate.py`（或 `stages.py`）定义模块级常量 `RESOLVE_MERGE_SPEC = StageSpec(...)`，直接以 `get_runner(root, "resolve-merge", spec=RESOLVE_MERGE_SPEC, provider=..., model=...)` 传入 runner。
  - `tools = ("read","bash","grep","find","ls","edit","write")`
  - `bundles = ("resolve-merge",)`，专属 skill 目录 `.pi/skills/resolve-merge/SKILL.md`
  - **必须写进 `bundles`**：`pi_argv` 只通过 `spec.bundles` 调 `resolve_skill_dir` 挂 `--skill`（`runners/__init__.py:88`、`stages.py:368-377`）。`bundles=()` 会让 `SKILL.md` 根本不加载（参考 implement 的 `bundles=("implement","tdd","codebase-design")`）。
  - **不要放进 `BUILTIN_STAGES`**：`dev-yard stages` 遍历 `load_registry`（`cli.py:718`），`dev-yard run <stage>` 也从 registry 查找（`cli.py:636`）；注册进去会暴露成可跑阶段并误走 `run_stage` 的快照/`stage_runs`。`RESERVED_STAGE_NAMES` 只是插件名黑名单（`stages.py:266`），对隐藏内置 stage 无效。传 `spec=` 则对 `stages`/`run` 自然完全隐形。
- 模型：**复用 implement**——`resolve_pi_choice(root, "implement", repo=alias)`（含仓级覆盖 `repos.yaml repos.<alias>.provider/model`）。不新增 `pi.stages.merge`。
- cwd = `merge_wt`（冲突现场）。
- prompt 注入：
  - 冲突文件清单 + 每个文件的 conflict 段落（`<<<<<<<`/`=======`/`>>>>>>>`）截断摘录
  - `reqs/<JIRA>/{REQUIREMENT,SPEC,TICKETS}.md` 路径（只读）
  - 冻结分支最近提交标题（`git log --oneline <test>..<freeze>`）说明本需求带来了什么
  - 消解策略（写入 skill，prompt 简要重申）：
    - 目标是「把本需求改动并入测试分支」，**保留测试分支上与本需求无关的既有修复**；
    - 同语义改动冲突时，采用能同时满足两侧的合并写法，而非整段取舍；
    - 不得为通过而删除测试分支已有的行为/测试；
    - 不得引入与本需求无关的新功能。
  - TDD 模式文案：按 `dev.tdd` 注入，与 implement 同源（见 §7.4）。
- 运行超时沿用 `YARD_PI_TIMEOUT`。

### 7.3 结果校验（宿主，不信任 agent 自述）

AI 返回后，宿主必须（**不信任 agent 自述，也要兼容 agent 已自行 commit**）：

1. `gitops.unmerged_files(merge_wt)` 为空。
2. 无残留冲突标记：对本次改动文件做**内容扫描**（不是看 `git diff --check` 退出码）。`<<<<<<< `/`>>>>>>> ` 视为强标记；`=======` 只有在同文件出现强标记时才计入。
   - **不能**用 `git diff --cached --check`：git 对任何首 7 字符为 `<`/`=`/`>` 且后接空白/行尾的**新增行**都报 `leftover conflict marker`——实测 Markdown/RST 的 setext 标题下划线 `=======` 会被误报。也不能用全仓 `git grep '^======='`（同样误报 setext）。
3. 生成 merge commit，分两种情况：
   - `git rev-parse -q --verify MERGE_HEAD` 存在 → 宿主 `git commit --no-edit` 收尾；
   - `MERGE_HEAD` 已不存在（agent 自己 commit 了）→ 不再调用 commit，直接校验。
   统一判据：**HEAD 前进**（`HEAD != 合并前 base`）且工作树 clean。

任一不满足 → 视为 AI 失败：保留现场、记 `conflict`、抛错，不 push、不改 phase。人工可进 `merge_wt` 手解后重跑。

### 7.4 TDD 校验（按 `dev.tdd` 配置）

`resolve-merge` 的收尾要求跟随全局 `dev.tdd`（`config.DevSettings`，默认 on），与 implement 完全一致：

- `dev.tdd = true`：AI 解完冲突后，必须按仓库既有约定运行受影响的测试套件，并保证**绿**才结束；skill 与 prompt 明确要求。宿主在 AI 返回后仍做 §7.3 的客观校验（无冲突标记 + merge commit），但不另跑测试。
- `dev.tdd = false`：不要求写/跑测试；只做静态检查（语法/明显断链），与 `skillbind.py` 对 implement 的关闭文案一致。
- 实现上：在 `skillbind.session_prompt_for` 增加 `spec.name == "resolve-merge"` 分支，复用现有 `dev_settings.tdd` 判断；prompt 由宿主在调用 runner 前拼好（沿用 `session_prompt_for` 或等价函数）。
- 宿主不新增 `verify_command`；测试由 agent 按仓库约定执行。

## 8. 重入设计（回应「有什么好思路」）

问题根因有两个：

1. `submit_test` 在 `phase == testing` 时直接拒绝（`test_report.py:82`）。
2. 测试失败后 `accept_test_report(failed)` 不改 phase，仍是 `testing`；B 票修复合进的是冻结分支，测试分支不会自动更新。

思路：**把「提测」从一次性状态翻转，改成幂等的「同步冻结分支 → 测试分支」操作，phase 只是它的副作用。**

- 记录每个仓库上次集成时的冻结分支 head：`test.integration.<alias>.freeze_sha` + `pushed`。
- **增量重推（默认开启）**：重入时逐仓判定，只有满足下列之一的仓库才重新集成：
  - 从未 `pushed`（含上次失败/冲突/部分成功），或
  - 记录的 `freeze_sha` != 当前冻结分支 head（该仓有新的修复提交）。
  其余仓库直接跳过并复用上次结果（避免对未变更的仓重复 merge/push，也避免无谓触碰共享测试分支）。`--all` 可强制全部重跑。
- `submit-test` 重入规则：
  - `test_passed` → 拒绝（不变）。
  - `phase == testing` 且未通过：
    - 计算当前 `freeze_sha`；若与所有已 push 仓库记录的 `freeze_sha` 相同 → **无新东西**，返回既有状态（幂等成功，不报错）。
    - 有任一仓库 `freeze_sha` 变化或未 push → 视为「重新提测」，重新 merge+push。
  - `phase == frozen` → 正常首提。
- B 票修完（`fix-test` 已把修复 merge 进冻结分支）→ 冻结分支 head 变化 → `submit-test` 自然可重跑，无需新命令。
- 看板「提测」按钮：`phase == testing` 时若存在 `freeze_sha != integration.freeze_sha` 则按钮可用，标签改「重新提测」；否则禁用并提示「没有新的改动」。
- 可选：`--force-resubmit` 忽略 above 判定强制重跑（远端测试分支被外部回退等场景）。

> 备选方案（不采用）：新增 `resubmit-test` 命令。会分裂入口、看板与 CLI 各加一套；用幂等 `submit-test` 更省。

## 9. STATUS.yaml 结构

在现有 `test` 槽下新增 `integration`（不动 `status/latest_verdict/findings`）：

```yaml
test:
  status: awaiting
  integration:
    research:
      test_branch: PG-test
      freeze_sha: 641dc8df09...      # 本次集成的冻结分支 head
      test_sha_before: 4b799a699e... # 集成前远端测试分支 head
      merge_sha: 1a2b3c...           # 集成后 HEAD
      pushed: true
      pushed_at: "2026-09-20T16:30:00+08:00"
      skipped: false
      conflict: false
      worktree: null                 # 冲突/失败时保留现场路径
    research-report-service:
      test_branch: null
      skipped: true
```

- phase 只在所有非 skipped 仓库 `pushed == true` 时改。
- `accept_test_report` 不改 `integration`；下一轮重提时覆盖。

## 10. 失败与部分成功语义

| 场景 | 行为 |
|---|---|
| 某仓无 `test_branch` | 跳过，`skipped: true`，不影响整体成功 |
| 全部仓都被跳过 | 不碰 git，按旧行为置 `phase=testing`（向后兼容） |
| 远端测试分支不存在 | 报清晰错误 `remote test branch '<test>' not found on <remote>; 请先创建或检查 repos.yaml`；不自动创建 |
| 冻结 worktree 有未提交改动 | 与 `req_push` 一致先 `commit_all`；或 `has_changes` 时报错让人先提交 |
| 上次遗留 `_test-merge` 目录/注册 | `worktree add` 前先 `worktree remove --force` + `worktree prune` |
| 某仓 fetch/merge/push 失败 | 中止；已 push 的仓保留（远端已变），未做的仓不动；phase 不变；错误信息列出每仓结果；下次重跑默认增量，只重推失败/未推的仓 |
| 冲突且 AI 关闭/失败 | 保留 `_test-merge` 现场；phase 不变；提示人工路径与重跑命令 |
| 推进失败（non-ff） | `fetch` + `reset --hard origin/<test>` 后重试一次；仍失败 → 按失败处理 |
| 锁外期间需求状态被改/被删 | 末尾取锁后重载；phase 不再合法或目录已删 → 中止写入并报错，不覆盖他人改动 |
| 已 `test_passed` | 拒绝 |
| `phase == testing` 且无新提交 | 幂等返回，不报错 |

是否对已 push 的仓做回滚：**不做**。回滚需 force-push 长命共享测试分支，风险高且可能覆盖他人提交。改为在输出里明确「哪些仓已推、哪些未推」，由人决定；已推的仓下次重跑会被增量判定跳过。

### 10.1 产品风险（需在文档/输出中警示）

- **共享测试分支无需求隔离**：测试分支是长期共享的，A 需求测试期间 B 需求合并会污染 A 的验证环境。本设计不做排队/锁表（§2 非目标），但提测输出与 README 必须明确这点，并提示「同一测试分支不要并行提测多个需求」。
- **合并会把基线历史一并带入**：若测试分支落后于 `default_base`，merge 冻结分支会顺带合并大量无关提交，放大冲突面。建议流程里先 `fetch` 并将测试分支 ff 到 `default_base`（或在输出里说明本设计不处理，交由人工先同步）。

## 11. CLI / Web / 看板改动

### CLI（`cli.py`）

```
dev-yard req submit-test <key>
    --remote origin              # 默认 origin
    --no-resolve                 # 关闭 AI 自动解冲突（默认开）
    --all                        # 忽略增量判定，强制集成所有命中仓
    --force-resubmit             # 忽略「无新改动」判定强制重跑（等价 --all + 忽略幂等）
```

- 逐仓进度打到 stderr（`on_progress`），最后打印每仓 `pushed/skipped/conflict` 摘要。
- 默认增量：只处理未推 / `freeze_sha` 变化的仓，其余报 `skipped(unchanged)`。

### Web（`web/jobs.py`）

- `submit-test` job 调 `submit_test(root, jira, ai_resolve=..., on_progress=job.append)`。
- `ActionIn` 增加 `resolve: bool | None`（或复用 `force`）。

### 看板（`reqboard.py`）

- `can_submit` 增加重入判定（§8）；reason 增加「没有新的改动 / 可重新提测」。
- 可选：详情里展示每仓测试分支与上次 push 时间。

### 配置页

- 仓库列表展示 `test_branch`；若第一刀不做编辑 UI，README 注明手改 `repos.yaml`。

## 12. 待实现文件清单

| 文件 | 改动 |
|---|---|
| `config.py` | `Repo.test_branch`；`GitSettings.ai_resolve_conflicts`；load/save/render；校验 |
| `repos.yaml.example` | 示例补 `test_branch` 与 `git.ai_resolve_conflicts` |
| `paths.py` | `test_merge_worktree(root, jira, alias)` |
| `gitops.py` | `push_ref(worktree, remote, src, dst)`、`fetch_branch(source, branch)`、`detached_worktree`、`add_all`、`has_merge_head`、setext-safe `check_conflict_markers` / `check_commit_conflict_markers`、`merge_into` 复用 |
| **新** `test_integrate.py` | 集成编排：逐仓 merge/push、AI 冲突消解、STATUS 写入；模块级 `RESOLVE_MERGE_SPEC` 与进程内 `(source,test_branch)` 串行锁 |
| `test_report.py` | `submit_test` 委托 `test_integrate.integrate_test_branches`，保留签名与校验；锁外执行 + 末尾重载写回 |
| `stages.py` | 仅导出 `RESOLVE_MERGE_SPEC`（**不**注册进 `BUILTIN_STAGES`）；内置示例 `StageSpec` 复用 |
| `skillbind.py` | `resolve-merge` 按 `dev.tdd` 注入 TDD/非 TDD 文案（与 implement 同源）；接受显式 `spec` |
| **新** `.pi/skills/resolve-merge/SKILL.md` | 冲突消解策略（含 TDD 收尾要求） |
| `service.py` | `_teardown_worktrees` 跳过/清理 `_test-merge` |
| `cli.py` | `submit-test` 新参数、进度、摘要 |
| `web/jobs.py` | 传参 + 进度 |
| `web/schemas.py` + `routes/api_settings.py`（可选） | 仓库 `test_branch` 编辑 |
| `reqboard.py` | 重入按钮门控与文案 |
| `README.md` / `AGENTS.md` | 更新提测语义与 `test_branch` 配置说明 |

## 13. 测试计划

- `tests/test_config.py`：`test_branch` 解析/保存/非法名写入校验；`load_repos` 读到非法/空 `test_branch` 降级为 skip 而非抛错；`ai_resolve_conflicts` 默认与读写。
- `tests/test_test_report.py`（或新 `test_submit_integration.py`）：
  - 无 `test_branch` 的仓被跳过；有 test_branch 的仓 merge 后本地/远端测试分支包含冻结分支提交（用临时 bare remote fixture）。
  - 全部仓无 test_branch → 旧行为（仅 phase）。
  - 冲突 + `ai_resolve=False` → phase 不变、现场保留、错误含冲突文件。
  - 冲突 + 桩 runner（monkeypatch）成功解冲突 → 无标记、merge commit、push 成功、phase=testing。
  - 冲突 + 桩 runner 留标记 → 判失败、不 push。
  - 桩 runner 自行 commit（`MERGE_HEAD` 已消失）→ 宿主不重复 commit、判成功、push。
  - 改动含 trailing whitespace 但无冲突标记 → 不被误判失败（回归 §7.3 的字符串判定）。
  - 文档含 setext 标题（`=======`）不被误判为残留冲突（回归 §7.3 的检测方式）。
  - non-ff：桩远端在 fetch 后推进 → push 被拒后 `reset --hard origin/<test>` + 重 merge 成功。
  - 远端无该测试分支 → 报清晰错误、不自动创建、不 push。
  - 上次遗留 `_test-merge` 目录 → 自动清理后仍能集成成功。
  - 冻结 worktree 有未提交改动 → 先 commit 或报错，行为与 `req_push` 一致。
  - 锁外期间 phase 被改（如 `reset-phase`）→ 末尾重载后中止写入，不把 phase 写回 testing。
  - `dev.tdd=true/false` 时注入 `resolve-merge` 的 prompt 文案分别含「必须跑绿测试」/「不要求测试」。
  - 重入：testing 下无新提交 → 幂等；冻结分支前进后再提 → 仅变化仓重新 push，未变仓 `unchanged` 跳过；`--all` 强制全量。
  - `test_passed` 仍拒绝。
- `tests/test_req_delete.py`：teardown 清理 `_test-merge`，不误删/报错。
- `tests/test_board.py`：重入按钮门控。

## 14. 已定决策

| # | 决策 |
|---|---|
| 1 | `ai_resolve_conflicts` 放**全局** `git` 开关，默认 true；不支持每仓覆盖 |
| 2 | AI 消解模型**复用 implement**（含仓级 provider/model 覆盖），不新增 stage 配置项 |
| 3 | 冲突消解后的测试校验**跟随 `dev.tdd`**：on 时 agent 必须跑绿受影响测试，off 时只做静态检查；宿主不另加 `verify_command` |
| 4 | 冲突现场保留在 `.yard-worktrees/<jira>/<alias>/_test-merge`，可接受随 worktree 清理 / `req delete` 删除 |
| 5 | **增加增量重推**：默认只处理未推 / `freeze_sha` 变化的仓，`--all` 全量 |
| 6 | `resolve-merge` 定义成模块级 `RESOLVE_MERGE_SPEC`，以 `spec=` 传入 runner，**不**注册进 `BUILTIN_STAGES`（`RESERVED_STAGE_NAMES` 隐藏不了内置 stage）；skill 必须写进 `bundles=("resolve-merge",)` |
| 7 | 残留冲突标记用**内容扫描**判定（`<<<<<<<`/`>>>>>>>` 为强标记，`=======` 需同文件存在强标记才计入），**不用** `git diff --check`（setext `=======` 会被 git 误报）也不用全仓 `git grep` |
| 8 | non-ff 重试前重新 `fetch` 并**重建** `_test-merge` detached worktree 到新的 `origin/<test>`（等价 `reset --hard` 且清掉未跟踪残留），再重 merge |
| 9 | AI 消解在 `jira_lock` **外**执行，末尾取锁后**重新 `st.load` 最新 STATUS**、校验 phase/目录有效再写回，避免覆盖并发改动；并发靠 `(source, test_branch)` 锁 |
| 10 | `test_branch` 合法性只在写入路径校验；`load_repos` 不硬校验原样读出，非法/空值在提测时按 skip 处理并写进 `integration.detail` 警告 |
| 11 | 共享测试分支无隔离、不先同步基线是已知产品风险，写进 README/提测输出警示，不在本设计内解决 |
| 12 | 远端测试分支不存在不自动创建，报清晰错误让人先建；`worktree add` 前清理遗留 `_test-merge`；集成前按 `req_push` 同款处理冻结 worktree 脏改动 |
| 13 | 校验兼容 agent 自行 commit：`MERGE_HEAD` 不存在时不重复 `git commit`，只校验 HEAD 前进且工作树 clean |
