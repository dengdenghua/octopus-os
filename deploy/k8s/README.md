# Kubernetes 部署

生产基线 k8s 清单。它默认启用真实 planner、内置 `local_auth`、Redis
分布式 tool-effect receipt，并要求 hard process sandbox。先把 Secret 模板里的
所有 `<CHANGE_ME>` 替换掉；缺失或弱 JWT secret 会让服务启动失败，而不会退化成
未认证的 `0.0.0.0` 服务。

生成认证材料：

```bash
openssl rand -base64 48
python -c 'from getpass import getpass; from runtime.adapters.integrations.local_auth.config import hash_password; print(hash_password(getpass("Admin password: ")))'
```

把第一行结果写入 `ECHO_LOCAL_AUTH_JWT_SECRET`，第二行 bcrypt 值写入
`ECHO_ADMIN_PASSWORD_HASH`。生产环境推荐改用 ExternalSecrets/AWS Secrets
Manager 等托管密钥源，不直接提交 `secret.yaml` 的真实值。

`REDIS_PASSWORD` 同时嵌入 `redis://` URL，必须使用 URL-safe 高熵值，例如
`openssl rand -hex 32`；不要直接使用会包含 `/+@:` 的普通 base64 输出。

基线只配置一个 `admin` 账号，并显式授予 `admin/operator`；当前 local-auth
`default_roles` 对所有本地账号统一生效，不能直接在同一 `users` 映射里追加普通用户。
JWT 有效期固定为 8 小时。`/logout` 只清客户端 token、没有服务端撤销列表；需要
全量失效时轮换 `ECHO_LOCAL_AUTH_JWT_SECRET` 并滚动重启。多人/企业生产应接入
支持会话撤销与细粒度 RBAC 的外部 IdP，而不是扩展此单管理员基线。

`local_auth` 内置的登录失败锁定是单进程内存状态，只保护当前 Pod。基线
单副本可直接使用；扩容到多副本前，必须同时在 Ingress/受管网关为
`/api/auth/local/login` 配置按源 IP 的分布式限速，否则攻击者可在 Pod 之间轮转绕过
本地锁定。应由入口控制器正确解析可信代理链；应用自身故意不信任客户端传入的
`X-Forwarded-For`。

确认 Secret 和镜像 digest 后 apply：

先把入口控制器所在 namespace 显式加入 NetworkPolicy 白名单（下面以
`ingress-nginx` 为例；使用其他控制器时替换 namespace）：

```bash
kubectl label namespace ingress-nginx echo-agent.io/ingress-access=true --overwrite
```

```bash
kubectl apply -k deploy/k8s/          # 用 kustomize 按顺序 apply
# 或逐个 apply：
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/secret.yaml   # 先按说明填值
kubectl apply -f deploy/k8s/pvc.yaml
kubectl apply -f deploy/k8s/redis.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/networkpolicy.yaml
kubectl apply -f deploy/k8s/ingress.yaml  # 可选 · 按你的 ingress-controller
```

## 资源设定（MVP）

| 对象 | replicas | CPU req / limit | Mem req / limit | 备注 |
|---|---|---|---|---|
| `echo-agent` | 1 | 100m / 500m | 256Mi / 1Gi | 基线 PVC 为 `ReadWriteOnce`，不可直接水平扩容 |
| `redis` | 1 | 50m / 200m | 64Mi / 256Mi | 基线不是 HA；生产请接管到 Redis Sentinel / Cluster |

## 扩容前提

- 当前两个 PVC 都是 `ReadWriteOnce`，部署清单因此固定 `replicas: 1`
- Agent 与 Redis 都使用 `Recreate` 升级策略。升级会有短暂停机，但不会让旧、新 Pod
  同时写 SQLite、journal、credentials 或 Redis AOF，也不会要求同一 RWO 卷跨节点
  Multi-Attach；完成全部共享状态与写入并发证明前不要改回 `RollingUpdate`
- 单副本基线刻意不附带 PDB：节点维护/驱逐会中断服务。只有完成共享状态改造并扩到
  至少 2 个副本后，才能增加 `minAvailable: 1` 的 PDB；PDB 不能把单副本伪装成 HA
- 扩到 `replicas: 2+` 前，先为 `/data`、模型缓存提供 RWX 存储或外部状态后端，并把单 Redis 换成托管 Redis / Sentinel / Cluster
- 完成共享状态改造后，agent 之间可用 `RedisCoordinator` 做 leader 选举；反思 / 调度类单点任务由 leader 执行，lease 过期后其他副本接管
- `RedisCoordinator` 负责协调和分布式 receipt，不会共享挂载在本地 PVC 上的文件状态

## 接进来的改造

- 发布流程不推送 `latest`；SemVer tag 也只是可读指针，不是不可变供应链标识。
  上线前先用 cosign 的 GitHub Actions OIDC 发行者和精确 workflow/tag 身份验证 GHCR
  多架构 manifest，再取其 digest，把 agent 和 init container 都替换为
  `ghcr.io/dengdenghua/echo-os@sha256:<真实 digest>`；也可取消
  `kustomization.yaml` 的 `images[].digest` 示例注释，一次转换两个容器。Redis 镜像同样固定
  `7.4.11-alpine` 及其多架构 digest；tag pipeline 会随镜像发布 SBOM 与 provenance，
  部署前在 GHCR 验证它们属于同一已通过门禁的 commit。清单里的全零 digest 是故意的
  fail-closed 哨兵；未替换时 `make k8s-apply` 会拒绝执行，直接 `kubectl apply` 也无法拉起 Pod
- `Secret` 填真 API key、JWT secret 和管理员密码 bcrypt hash，或改用托管 Secret
- `Ingress` host 改成你的域名 · tls 用 cert-manager
- 当前 NetworkPolicy 对 Agent 入站默认拒绝，只允许带
  `echo-agent.io/ingress-access=true` 的 controller namespace；Redis 只接受 Agent Pod。
  若要限制出站，按真实模型/provider/registry/DNS 目的地补集群专用 Egress 规则，
  不要套一个会阻断外部模型调用的伪通用白名单
- 在 ingress controller 全局 ConfigMap/受管网关策略启用 HSTS，并追加 CSP、
  `X-Content-Type-Options`、`Referrer-Policy` 等安全头。不要启用可注入任意 NGINX
  指令的 `configuration-snippet`；上线前用 `curl -sI https://<host>/` 验证真实响应头

## 沙箱与可写资源前提

- 镜像内置 `bwrap`；服务以 `strict` 启动时会实际执行 sandbox probe。集群必须允许
  无特权 user namespace，或使用 Linux >= 5.13 且启用 Landlock。若
  `RuntimeDefault` seccomp / 节点内核阻断两者，Pod 会 fail closed，不能通过改成
  `soft` 绕过生产门禁
- 根文件系统保持只读。init container 将镜像内 `/app/resources` 种入
  `/data/resources`，主容器再把该 PVC 子目录挂回 `/app/resources`；因此管理员安装的
  skills/agents 能写入并跨重启保留，而不是写入容器层。Codex-compatible 插件写入
  `/data/plugins/codex`，同样落在 PVC 上
- init container 只复制 PVC 中尚不存在的资源路径，不会在升级时覆盖同名的管理员
  安装/修改内容。代价是镜像中同路径的新版内置资源不会自动替换旧副本；升级前先备份
  PVC，再显式删除或迁移要刷新的具体路径，随后重建 Pod。不要清空整个资源卷来省事

探针契约：`/livez` 只判断进程存活；`/readyz` 会在 journal 或要求的分布式
tool-effect store 不可用时返回 503。不要用始终返回 HTTP 200 的 `/api/health`
作为 Kubernetes readiness。

## 卸载

```bash
kubectl delete -k deploy/k8s/
```

