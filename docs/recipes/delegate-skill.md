# delegate skill

进现场必须走 agent 会话时才用。批跑慢且贵；优先 command。

```yaml
envs:
  test:
    base_url: https://app.example.com
    exec:
      use: delegate
      skill: k8s
      timeout: 600
```

skill 装在 `.pi/skills/<name>/`。prompt 冻结为 yard-exec-delegate v1：脚本路径 + 结果路径 + `QA_*`。仍然只信 `exec-result.json`。
