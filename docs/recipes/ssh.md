# ssh

```yaml
envs:
  test:
    base_url: https://app.example.com
    exec:
      use: ssh
      with:
        target: deploy@10.0.0.8
        port: 22
        workdir: /var/www/app
        runner: bin/rails runner
```

宿主：`ssh -p 22 deploy@10.0.0.8 'cd /var/www/app && bin/rails runner -'`，stdin = 脚本。
