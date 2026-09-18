# local

本机 freeze worktree 跑脚本。stdin 喂文件，注入 `DATABASE_URL`（若配了 db.url）。

```yaml
envs:
  local:
    base_url: http://127.0.0.1:8080
    script:
      runner: bin/rails runner
    # 等价于：
    # exec:
    #   use: local
    #   with:
    #     runner: bin/rails runner
```

自检：`dev-yard qa check-env --env local --jira <JIRA>`
