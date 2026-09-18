# jms-k8s

JumpServer 四段身份 + kubectl exec。`jms.user` 是前两段（`alice@root`），节点 IP 来自 `nodes`。

```yaml
envs:
  test:
    base_url: http://research.dev1.example.com
    exec:
      use: jms-k8s
      with:
        jms:
          host: jump.example.com
          port: 22222
          user: alice@root
        default_node: k8s-1
        nodes:
          k8s-1: 172.16.4.23
        namespace: research
        container: web
        runner: bin/rails runner
        workdir: /var/www/research
        pod:
          selector: "app=research-web"
          # pattern: "^research.{,17}$"   # 没有稳定 label 时
```

选 pod 用 `kubectl get pods --field-selector=status.phase=Running -l ... -o jsonpath=`，每次现解析。认证失败不要连着重试（会锁号）。

自检：`dev-yard qa check-env --env test`
