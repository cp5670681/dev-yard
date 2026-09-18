# docker

浏览器打本机、脚本 `docker exec` 进同一台机器上的容器，算同一世界，不需要 `allow_cross_site`。

```yaml
envs:
  local:
    base_url: http://127.0.0.1:3000
    exec:
      use: docker
      with:
        container: research-web
        runner: bin/rails runner
        workdir: ""
```

宿主：`docker exec -i -e QA_* research-web bin/rails runner -`
