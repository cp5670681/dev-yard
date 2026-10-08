---
name: resolve-ticket
description: >
  在已通过审查的票的子 worktree 里解决「父冻结分支 → 子分支」的 git merge 冲突。
  只消冲突，不改已经通过的行为，不合回父分支。Use when the host runs
  resolve-conflict on an approved ticket.
---

# resolve-ticket（dev-yard）

这张票**已经审查通过**。宿主把父冻结分支 merge 进当前子 worktree，遇到冲突才叫你。cwd 就是这张票的子 worktree。

## 目标

让两侧改动同时留下。这次 merge 的 HEAD 是这张票，进来的是父冻结分支：

- `<<<<<<<` 到 `=======`（`HEAD`，ours）是**这张已通过的票**，必须保留。
- `=======` 到 `>>>>>>>`（theirs）是父冻结分支上**兄弟票已经合进去的改动**，必须保留。

不要用 `checkout --theirs` 盖掉已经通过的行为。

同语义冲突写成能同时满足两侧的结果。不要为了靠拢 `SPEC.md` 去改已经通过的行为。规约和审查意见不一致时，以这张票已经通过的代码为准，只处理冲突标记。

## 做法

1. 只打开冲突文件，看清两侧各自改了什么。
2. 删掉全部 `<<<<<<<` / `=======` / `>>>>>>>`。
3. `git add` 这些文件。不要 `git commit`，宿主会提交这次 merge。
4. 按启动提示的 TDD 文案收尾：TDD 关闭时只做静态检查，不写不跑测试。

## 不要

- 不要重新实现需求，不要扩大改动，不要顺手格式化无关代码。
- 不要 push，不要合回父分支，不要改 `reqs/` 下的文档。
- 不要把冲突标记留在文件里。

## 命令安全

所有搜索限定在当前 worktree，绝不传 `/`，优先 `rg`。`bash` 带 `timeout`。别遍历 `/mnt/*`、`/usr/lib/wsl/*`。
