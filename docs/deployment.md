# 部署 · echo-agent

一份**最小可用**的部署指南。

## 🚀 一键部署速查

| 场景 | 命令 | 需要什么 |
|---|---|---|
| **开发单机** | `make up` | docker + docker compose |
| **单机生产基线**（Agent + 单 Redis + Jaeger + Grafana） | `make up-full` | 同上 |
| **k8s 集群** | `make k8s-apply` | kubectl + kustomize + 集群 |
| **裸金属 / VPS** | 见 [§5 systemd](#5-裸金属--vps--systemd) | systemd · Python 3.11+ |
| **Python 直接跑** | `pip install -e ".[serve]"` + `echo serve` | Python 3.11+ |

一键停：`make down` · 看日志：`make logs` · 重启：`make restart`

---

## 五种跑法

### 1. Python 虚拟环境（开发 / 单机实验）

```bash
pip install -e ".[serve]"
echo serve --config config.yaml --port 8000
```

需要反思学习 / MCP / Anthropic？按需加 extras：
```bash
pip install -e ".[serve,anthropic,mcp,web,tracing]"
```

### 2. Docker（单容器）

```bash
docker build -t echo-os .

docker run --rm -p 127.0.0.1:8000:8000 \
    -v $(pwd)/data:/data \
    -v echo-resources:/app/resources \
    -v $(pwd)/config.yaml:/etc/echo/config.yaml:ro \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    echo-os
```

本地 `docker build -t echo-os .` 产生的隐式 `latest` 只是开发标签，不是发布身份。
正式 GHCR 发布只生成 SemVer 标签，同时为 `linux/amd64` 和 `linux/arm64`
组成的 manifest 生成 keyless cosign 签名。上线前必须验签，然后按验证过的
digest 部署：

```bash
image=ghcr.io/dengdenghua/echo-os:v0.2.0
cosign verify \
  --certificate-identity 'https://github.com/dengdenghua/echo-agent/.github/workflows/release.yml@refs/tags/v0.2.0' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  --certificate-github-workflow-sha '<v0.2.0 对应的已审核 commit SHA>' \
  "$image"
docker buildx imagetools inspect "$image"
# 生产实际使用 ghcr.io/dengdenghua/echo-os@sha256:<上一步已验签 digest>
```

### 3. docker compose · 单容器（推荐开发）

```bash
make up                     # 首次只生成 config.yaml/.env 并停止
# 编辑 config.yaml：启用 oct 或 local_auth；推荐让 jwt_secret/users.admin
# 分别引用 ${ECHO_LOCAL_AUTH_JWT_SECRET}/${ECHO_ADMIN_PASSWORD_HASH}，
# 并在 .env 填强随机 secret 与单引号包裹的 bcrypt hash
make up                     # 复核后再次运行才启动容器
```

即使端口只发布到宿主 `127.0.0.1`，容器内服务仍监听 `0.0.0.0`；因此应用层启动门禁
要求认证开启。`make up` 不会用未认证的示例配置假装启动成功。
`make up-full` 首次运行遵循同一生成后停止契约。

### 4. docker compose · 全栈（单机生产基线）

`docker-compose.full.yml` 额外拉起单实例 Redis（Hearts RedisCoordinator 后端）+ Jaeger（OTel trace）+ Prometheus + Grafana。它提供可观测、可持久化的单机基线，但单 Redis 本身不是 HA：

```bash
make up-full
# →  Agent    http://localhost:8000/
# →  Jaeger   http://localhost:16686/
# →  Grafana  http://localhost:3000/   (admin / configured GRAFANA_PASSWORD)
```

Compose 中 `echo-os:latest` 仅命名当前本地 build；不会被发布到 GHCR，也不得作为
远程部署依据。该基线的 Redis 镜像固定了明确版本与多架构 manifest digest。

Agent 容器自动读带密码的 `ECHO_HEARTS_REDIS_URL`。跨机或多副本生产需改接托管 Redis / Sentinel / Cluster，并为 `/data` 配置 RWX 共享存储或外部状态后端；`RedisCoordinator` 只负责协调，不能把本地文件状态自动变成共享状态。
`REDIS_PASSWORD` 会嵌入 `redis://` URL，使用 `openssl rand -hex 32` 生成 URL-safe
高熵值；不要直接使用含 `/+@:` 的 base64 值。

### 5. 裸金属 / VPS · systemd

```bash
# 1. 装代码（从 PyPI 或源码）
# PyPI 发行名是 echo-os；安装后的命令仍是 echo-agent。
# 不要安装同名的 echo-agent 发行包，它属于无关的第三方项目。
sudo useradd -r -s /usr/sbin/nologin echo
sudo mkdir -p /opt/echo-os /var/lib/echo /etc/echo
sudo chown -R echo:echo /opt/echo-os /var/lib/echo /etc/echo
sudo -u echo python -m venv /opt/echo-os/.venv
sudo -u echo /opt/echo-os/.venv/bin/pip install \
    "echo-os[serve,local-auth,anthropic]"

# 2. 放生产配置并生成认证材料（缺失/弱值会 fail closed）
sudo cp deploy/systemd-config.yaml /etc/echo/config.yaml
sudo cp deploy/systemd.env.example /etc/echo/echo.env
# 把下面两个生成结果分别写入 echo.env 对应的空值；bcrypt hash 必须保留单引号
printf 'Echo!9%s\n' "$(openssl rand -base64 48)"
/opt/echo-os/.venv/bin/python -c \
    'from getpass import getpass; from runtime.adapters.integrations.local_auth.config import hash_password; print(hash_password(getpass("Admin password: ")))'
sudo chmod 600 /etc/echo/echo.env
sudoedit /etc/echo/echo.env

# 3. 装 unit · 开机自启
sudo cp deploy/echo-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now echo-agent

# 4. 管理
sudo systemctl status echo-agent
sudo journalctl -u echo-agent -f
```

`deploy/systemd-config.yaml` 默认启用单管理员认证、8 小时 JWT 与 production/strict
进程沙箱；unit 继续绑定 `0.0.0.0`，但任何密钥缺失、弱密钥或硬沙箱不可用都会拒绝启动。
内置安全加固：`NoNewPrivileges` / `ProtectSystem=strict` / `MemoryDenyWriteExecute` /
`CapabilityBoundingSet=`（全砍）/ `MemoryMax=2G`。

### 6. Kubernetes（跨机生产）

```bash
# 先把 deploy/k8s/kustomization.yaml 的 images[].digest 设置为已验签摘要；
# deployment.yaml 中的全零摘要是 fail-closed 哨兵，未替换时下面命令会拒绝执行。
make k8s-apply
make k8s-status    # 看命名空间里所有资源
make k8s-delete    # 卸载
```

`deploy/k8s/` 里含：
- `namespace.yaml` · `configmap.yaml`（真实 planner、生产 sandbox、内置认证）·
  `secret.yaml`（模板 · 必须填 API key、JWT secret、管理员密码 hash）
- `pvc.yaml` · `redis.yaml`（Hearts 后端）· `deployment.yaml` · `service.yaml` ·
  `networkpolicy.yaml` · `ingress.yaml`
- `kustomization.yaml` · 用 kustomize 统一 apply

基线清单固定为单副本，因为两个 PVC 都是 `ReadWriteOnce`，并使用 `Recreate`
升级策略避免旧、新 Pod 短暂并发写 SQLite、journal、credentials、资源目录或 Redis
AOF，以及跨节点 Multi-Attach。扩到多副本前，必须先把 Redis 换成高可用部署，并为
`/data`、模型缓存配置 RWX 或外部状态后端；之后才可用 `RedisCoordinator` 做 leader
选举。详见 `deploy/k8s/README.md`。

访问：
- `http://localhost:8000/`           · Web dashboard
- `http://localhost:8000/api/stream` · Server-Sent Events · journal 事件实时推送
- `http://localhost:8000/v1/chat/completions` · OpenAI-compat API
- `http://localhost:8000/api/progress` · 所有 task 的当前进度

## 数据持久化

`/data`（容器内）映射到宿主 `./data`；Compose 还把 `/app/resources` 放在
`echo-resources` 命名卷，确保管理员安装的 skills/agents 不随容器重建丢失。包含：
- `events.jsonl` · journal（如 config.journal_file 指向它）
- `kg.sqlite3` · KG 跨 session 持久化（用 `SqliteKnowledgeGraph` 时）
- `skills/` · 云目录安装的运行时技能
- `plugins/codex/` · 管理员安装的 Codex-compatible 插件
- 其他 agent 运行产出

**备份**：备份 `./data`，并用
`docker run --rm -v echo-resources:/source -v "$PWD":/backup alpine tar -czf /backup/echo-resources.tgz -C /source .`
导出命名卷；Kubernetes 则备份对应 PVC。只备份 `./data` 会漏掉 Compose 的动态资源。

## 运维

### 健康检查

```bash
curl --fail http://localhost:8000/livez   # 进程 liveness；失败返回 503
curl --fail http://localhost:8000/readyz  # 流量 readiness；依赖异常/排空返回 503
curl http://localhost:8000/api/health     # 诊断聚合；可能 degraded 但仍返回 200
curl http://localhost:8000/api/status     # 环境能力盘点（extras 装了哪些）
```

两个 docker compose 清单的容器 healthcheck 都命中 `/readyz`。Kubernetes 分别以
`/livez` 和 `/readyz` 做 liveness/readiness；不要把总是 HTTP 200 的 `/api/health`
当作编排器探针。

### 查 scheduler 状态

进程退出时 stderr 会打印每个 periodic task 的 success/error 计数。实时查：
```bash
docker logs echo-agent | grep scheduler
```

### 热更新 config

```bash
# 改 config.yaml 后
docker compose restart echo-agent
```

### 消费 OpenAI-compat 端点

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
resp = client.chat.completions.create(
    model="echo-agent",
    messages=[{"role": "user", "content": "list the current dir"}],
)
print(resp.choices[0].message.content)
```

## 资源占用参考

| 组件 | CPU | RSS |
|---|---|---|
| 基础 `serve`（空闲）| < 1% | ~80 MB |
| 每个并发 plan+execute | 10-30% 脉冲 | +20-50 MB 峰值 |
| MCP persistent client | < 1% | +30 MB per server |
| BackgroundRunner（intel 3600s）| < 1% 平均 · 抓时脉冲 | 取决于 fetch_top_n |

## 安全要点

- `ANTHROPIC_API_KEY` 等密钥通过 env 注 · 不要入 config.yaml
- 不可信 skill 强制走 `SubprocessBackend`（Unix 下加 RLIMIT_AS / RLIMIT_CPU）
- 把 `/data` 设独立 volume · 不与源代码目录共享
- 项目内置 `oct` 与 `local_auth`。任何非回环 `serve` 绑定在两者都关闭时都会拒绝启动；
  反向代理/TLS 不能替代应用层认证
- 商业/共享部署必须在 `config.yaml` 设置 `execution.deployment_mode: commercial`
  或 `shared`；启动时会检查硬进程 sandbox，不满足条件直接拒绝服务。

## 生产检查清单

- [ ] `config.yaml` 用真 planner（非 mock）
- [ ] `ANTHROPIC_API_KEY` 或等价 provider 已设
- [ ] `oct.enabled` 或 `local_auth.enabled` 已开启，JWT secret 强随机且密码只存 hash
- [ ] K8s 单管理员 JWT 为 8h；已知 logout 不做服务端撤销，紧急失效流程是轮换 secret + 滚动重启；多人部署改接外部 IdP
- [ ] `/livez` 与 `/readyz` 监控已接入；未使用 `/api/health` 作为 readiness
- [ ] K8s agent/init/Redis 镜像均已固定 `@sha256:` digest，而不是只固定可变 tag
- [ ] 已在 GHCR 验证 release pipeline 随该 digest 发布的 SBOM/provenance，revision 与本次门禁 SHA 一致
- [ ] 入口控制器 namespace 已加 `echo-agent.io/ingress-access=true`；Redis 入站只允许 Agent Pod；按实际 provider/DNS 设计过 Egress
- [ ] 真实 HTTPS 响应含 HSTS、CSP、`X-Content-Type-Options`、`Referrer-Policy`；未启用 NGINX `configuration-snippet`
- [ ] 节点支持并允许 `bwrap` 无特权 user namespace 或 Linux >= 5.13 Landlock，strict sandbox probe 已通过
- [ ] `/app/resources` 由 PVC-backed `/data/resources` 提供，管理员安装 skills/agents 后重启仍存在
- [ ] 已理解资源 init 只补缺失路径、不会覆盖 PVC 同名内容；镜像内置资源升级前已备份并显式迁移目标路径
- [ ] 已接受单副本/RWO 基线在节点维护时中断；完成共享状态与 2+ 副本前未用 PDB 伪装 HA
- [ ] `immunity.trusted_sources` 白名单配齐
- [ ] `immunity.unknown_policy=quarantine`（或 reject）
- [ ] `budget.max_usd` 合理（单 task 上限）
- [ ] `ink` CircuitBreaker 参数按负载调（如需在代码里开）
- [ ] journal 目录备份策略 · cron tar 或挂云盘
- [ ] `/api/status` 监控接入
