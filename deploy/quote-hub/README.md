# QuoteHub 独立服务部署

QuoteHub 作为只读行情平面独立运行：一条合并后的官方上游订阅，按用户代码过滤后由
SSE 分发。账户登录仍由 `127.0.0.1:8081` 的账户服务负责；QuoteHub 仅监听
`127.0.0.1:8091`，不能直接暴露公网。

## 目录与安全边界

```text
/opt/echo-cloud/quote-hub/
├── current -> releases/20260825T120000Z-1234  # 原子软链
├── releases/<release>/.venv/                  # 每个版本独立 venv
└── deploy.lock

/etc/echo/quote-hub.env                     # 0600
/etc/echo/quote-hub-secret.json             # 0600
/var/lib/echo/quote-hub/                     # token/cache，可写
```

外部请求链路为：

```text
用户 HTTPS -> Nginx auth_request -> 账户服务 :8081
                             `----> QuoteHub :8091 -> 受限 HTTPS/WSS 路径
                                                      -> 官方上游 :58868
```

`8091` 只绑定 loopback；Nginx 在认证通过后才转发报价接口，并在转发前移除用户的
`Authorization`/`X-API-Key`。上游桥只允许 `127.0.0.1`、`::1` 和本机公网地址
`47.85.24.213`。官方最后一跳仍是 `http://114.66.32.152:58868`：密码在应用协议中
会先加密，但 token 所在的最终网络段不是 TLS。该反代的价值是让 QuoteHub 坚持
HTTPS/WSS、收口访问来源；它无法替官方 HTTP 服务补上端到端 TLS。如果以后能控制
上游，应优先改为 TLS 或专用隧道。

## 首次安装

Ubuntu 主机执行：

```bash
# 先安装 curl、coreutils（sha256sum）、util-linux（flock）以及 uv；发布脚本会逐项检查。
sudo useradd --system --home /var/lib/echo/quote-hub \
  --shell /usr/sbin/nologin echo-quote
sudo install -d -o root -g root -m 0755 /etc/echo
sudo install -d -o echo-quote -g echo-quote -m 0700 \
  /var/lib/echo/quote-hub
sudo install -d -o root -g root -m 0755 \
  /opt/echo-cloud/quote-hub/releases

sudo install -o echo-quote -g echo-quote -m 0600 \
  deploy/quote-hub/quote-hub.env.example /etc/echo/quote-hub.env
sudo install -o echo-quote -g echo-quote -m 0600 \
  deploy/quote-hub/quote-hub-secret.json.example \
  /etc/echo/quote-hub-secret.json
sudoedit /etc/echo/quote-hub.env
sudoedit /etc/echo/quote-hub-secret.json
```

秘密 JSON 的字段是 `phone` 与 `password`；不要把真实值写进 Git、shell 历史或
systemd unit。两个文件都必须是 `echo-quote:echo-quote`、权限 `0600`，否则
部署脚本会拒绝继续。

安装服务并先做静态校验：

```bash
sudo install -o root -g root -m 0644 deploy/quote-hub/quote-hub.service \
  /etc/systemd/system/echo-quote-hub.service
sudo systemd-analyze verify /etc/systemd/system/echo-quote-hub.service
sudo systemctl daemon-reload
sudo systemctl enable echo-quote-hub.service
```

将 `deploy/quote-hub/nginx-api.echo-age.com.conf` 的 locations 放入
`api.echo-age.com` 的 **443 server block**。不要放入 80 端口。随后验证并平滑加载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 独立行情域名

`api.echo-age.com` 的既有行情路径保留为兼容入口，不能用 301/308 跳转到新域名，
否则跨域客户端可能丢失 Bearer header。独立域名按以下三阶段上线：

1. 将 `nginx-quotes-http-context.conf` 安装为
   `/etc/nginx/conf.d/quote-hub-http-context.conf`；它只允许 Echo 第一方 HTTPS Origin，
   并定义单 IP 请求/连接限额与不记录查询串的日志格式。
2. 在 DNS 和证书准备期间启用 `nginx-quotes-http.echo-age.com.conf`。它只响应 ACME
   challenge，其余明文请求一律 404。
3. DNS 生效后，用 webroot 模式为 `quotes.echo-age.com` 申请独立证书，再用
   `nginx-quotes.echo-age.com.conf` 原子替换临时 vhost，执行 `nginx -t` 后 reload。

最终域名只暴露 `status`、`snapshot` 与 `stream` 三类受账户 Bearer token 保护的行情
接口。`/internal/paper-origin/*` 永远保留在 `api.echo-age.com` 的本机白名单路径，
`/health`、`/readyz` 和 8091 不对公网开放。跨域预检不要求登录，但非白名单 Origin
直接 403；不带 Origin 的原生应用/CLI 仍须通过账户鉴权。生产配置不允许 `*`、
`null`、`file://`、localhost 或通配子域名。

`QUOTE_HUB_UPSTREAM_URL` 必须保持为：

```text
https://api.echo-age.com/internal/paper-origin/api
```

路径重写约定已经固定：REST 的 `/internal/paper-origin/api/...` 映射到上游
`/api/...`；LivePush 去掉末尾 `/api` 后连接
`/internal/paper-origin/socket.io/...`，再映射到上游 `/socket.io/...`。

## 发布

先在可信构建机从锁定的 `uv.lock` 生成独立发布包。包内只有 runtime wheel、带哈希的
生产依赖清单、构建清单与校验文件，不会把本地工作区、缓存、登录凭证或开发文件同步
到服务器：

```bash
chmod +x deploy/quote-hub/build-release-artifact.sh \
  deploy/quote-hub/deploy-release.sh \
  deploy/quote-hub/rollback-release.sh
artifact=$(deploy/quote-hub/build-release-artifact.sh /absolute/output/directory)
scp -r "$artifact" root@47.85.24.213:/opt/echo-cloud/quote-hub/incoming/
```

服务器发布脚本只接受这个独立发布包的绝对路径。它先严格校验 `SHA256SUMS` 与 manifest，
再新建不可变 release 和独立 venv，按带哈希的依赖清单安装，并单独安装已校验 wheel。
检查 ASGI 入口后，用 `ln` + `mv -T` 原子切换 `current`。重启后
`/readyz` 会用 3 秒超时向受限上游桥发起一次真实行情请求，而不是只检查配置。
30 秒内探测未通过，会自动恢复旧软链并重启旧版本。

```bash
sudo deploy/quote-hub/deploy-release.sh \
  /opt/echo-cloud/quote-hub/incoming/<artifact-directory>
```

核验：

```bash
sudo systemctl status echo-quote-hub.service --no-pager
curl --fail http://127.0.0.1:8091/health
curl --fail http://127.0.0.1:8091/readyz
sudo journalctl -u echo-quote-hub.service -n 100 --no-pager
```

未登录访问公网报价应返回 `401`；携带账户服务签发的 Bearer token 才能访问：

```bash
curl -i https://api.echo-age.com/api/plugins/paper-trading/quotes/status
curl -i -H 'Authorization: Bearer <ACCESS_TOKEN>' \
  'https://api.echo-age.com/api/plugins/paper-trading/quotes/snapshot?codes=600000.sh'
curl -N -H 'Authorization: Bearer <ACCESS_TOKEN>' \
  'https://api.echo-age.com/api/plugins/paper-trading/quotes/stream?codes=600000.sh'
```

原生 `EventSource` 不能设置 Bearer header；网页登录态未改为安全 Cookie 前，前端应
使用 `fetch()` 读取 `text/event-stream`，不能把 token 放进 URL。
服务会在 10 分钟后主动关闭每条 SSE，促使客户端刷新访问令牌并重新经过
`auth_request`；客户端必须把 `reauth` 事件或流结束视为正常重连信号。

## 回滚

列出 releases，选择一个完整的 release ID，再执行：

```bash
ls -1 /opt/echo-cloud/quote-hub/releases
sudo deploy/quote-hub/rollback-release.sh 20260825T120000Z-1234
```

回滚脚本只接受固定格式且 realpath 位于 `releases/` 内的目标；同样采用原子软链，
目标健康检查失败会恢复原版本。脚本不会自动删除历史 release，避免误删可恢复版本。

## Nginx 与路径验证

上线前至少检查：

```bash
sudo nginx -T | grep -F '/api/plugins/paper-trading/quotes/'
sudo nginx -T | grep -F '/internal/paper-origin/socket.io/'
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://api.echo-age.com/internal/paper-origin/api/system/systemConfigs/getPublicKey
```

最后一条从非白名单来源应是 `403`；在服务器本机执行时应由上游正常响应。SSE 响应
须包含 `Cache-Control: no-cache, no-transform` 与 `X-Accel-Buffering: no`，且 Nginx
不能启用 proxy cache、gzip 或响应缓冲。
