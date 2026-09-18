# raw

自有 CLI，能读 stdin。默认 argv 列表；需要管道时 `shell: true`。不要写 `{script}` 或重定向。

```yaml
envs:
  test:
    base_url: https://app.example.com
    exec:
      use: raw
      run: ["mycorp-cli", "exec", "--env", "test", "--", "bin/rails", "runner", "-"]
      ping: ["mycorp-cli", "exec", "--env", "test", "--", "echo", "ok"]
```

`{env:NAME}` 只能作为整个 argv 元素。
