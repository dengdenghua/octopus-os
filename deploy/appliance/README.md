# 在 NAS 上部署 Octopus OS 桌面

原生路线:桌面即系统主页 + Docker 应用启动器。装在任意带 Docker 的 NAS /
主机上,浏览器打开即原生桌面,自动发现并点亮已装的 Docker 应用。

## 快速开始

```bash
git clone https://github.com/dengdenghua/octopus-os.git
cd octopus-os/deploy/appliance
docker compose up -d --build
```

浏览器打开 `http://<NAS_IP>:8000/#/desktop` —— 极光壁纸的原生桌面,
Dock 里"本地应用"段会列出宿主上已装的 Docker 应用(运行中带绿点,
已停止的点击即启动),点击运行中的应用在新标签打开它的 Web UI。

## 配置项(环境变量,均可选)

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8000` | 对外端口 |
| `OCTOPUS_ADMIN_PASSWORD` | 空 | 管理员登录密码(用户名固定 `admin`);**不设则首启随机生成并打印到容器日志** |
| `NAS_STORAGE` | `./storage` | 挂进桌面文件区的宿主共享目录(如 `/DATA` / `/volume1`) |
| `ANTHROPIC_API_KEY` | 空 | 配上才有对话 Agent;桌面/启动器/文件不需要 |
| `OCTOPUS_LOG_LEVEL` | `INFO` | 日志级别 |

### 首次登录

桌面是单用户的,打开即要求输入管理员密码:

- **设了 `OCTOPUS_ADMIN_PASSWORD`** → 用它登录;
- **没设** → 首启随机生成,查容器日志拿初始密码:
  ```bash
  docker compose logs | grep "appliance admin password"
  ```

密码哈希与会话密钥持久化在 data 卷(`appliance-auth.json`,0600),
会话 30 天长效。改 `OCTOPUS_ADMIN_PASSWORD` 不会覆盖已设的密码
(要重置就删掉 data 卷里的 `appliance-auth.json` 再重启)。

例:把群晖 `/volume1/share` 挂进来、换 9000 端口:

```bash
PORT=9000 NAS_STORAGE=/volume1/share docker compose up -d --build
```

## 工作原理

- `OCTOPUS_APPLIANCE=1` 启用启动器应用注册器,挂载 `/api/appliance/*`;
  不开此开关时镜像行为与母体 octopus-agent 一致。
- 容器挂载宿主 `/var/run/docker.sock`,应用注册器据此列举/启停容器。
  应用元数据(名称/图标/Web 端口)从容器 label 读取,**兼容 CasaOS /
  homepage / Unraid 的 label 约定**——已用这些面板装的应用,图标直接复用。

## ⚠ 安全须知

挂载 `docker.sock` 等于把**宿主 root 等价权限**交给容器——这是 CasaOS /
Portainer 等所有 NAS 应用面板的通用惯例,但请知悉:

- **仅在可信内网部署**,不要把 8000 端口直接暴露到公网;
- 启停应用在应用层经 `appliance/app_registry/router.py` 的 approval 审批门
  把关(P2 接入);
- 装/删应用功能尚未开放,需随审批门一起上线。

## 在 CasaOS / ZimaOS 上一键安装

`docker-compose.yml` 内置了 `x-casaos` 应用商店元数据。在 CasaOS:

1. 应用商店 → 右上「自定义安装」;
2. 粘贴本 compose 内容(或导入文件);
3. 按需改端口/存储卷 → 安装。

CasaOS 会用 `x-casaos` 里的标题/图标/描述生成应用卡片,装完点开即桌面。

## 已知边界(P1 阶段)

- **未发布预构建镜像**:当前 `build: context` 从源码本地构建。发布到
  registry 后,CasaOS 商店可改为拉取镜像、免本地构建。
- 桌面文件区(`桌面助手`)在 NAS 形态下尚是只读占位,正经文件管理器
  是 P2 内容(含「删除即回收站」硬约束)。
- 本机无 Docker 环境,compose/Dockerfile 的镜像构建未在本地验证;
  pyproject 打包(appliance 纳入安装包)已本地验证可导入。
