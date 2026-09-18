# delegate command

公司提供二进制，遵守 `exec-result.json`。

```yaml
envs:
  test:
    base_url: https://app.example.com
    exec:
      use: delegate
      command: ["company-qa-exec", "--env", "test"]
      ping: ["company-qa-exec", "--env", "test", "--ping"]
      timeout: 600
```

二进制约定：`--script` 路径、`--result` 路径（或 `QA_RESULT_PATH`），stdin 为脚本字节。写：

```json
{"exit_code": 0, "stdout": "...", "stderr": ""}
```

宿主只信这个文件。
