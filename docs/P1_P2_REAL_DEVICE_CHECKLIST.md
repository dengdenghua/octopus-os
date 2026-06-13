# 真机验证清单(P1/P2 在 Docker/NAS 上的闭环)

> 目的:本机(开发用 Mac)无 Docker,以下事项只能在**有 Docker 的机器或真实 NAS**
> 上验证;在此之前的所有功能均已用 pytest + 浏览器预览验证过(见各 commit)。
> 跑完把结果按"反馈给开发"一节回填即可。

适用环境:任意装了 Docker 的 Linux 主机 / 群晖 / 飞牛 / CasaOS / ZimaOS / 绿联,
或 Mac/Windows 上的 Docker Desktop。

---

## 0. 准备

```bash
git clone https://github.com/dengdenghua/octopus-os.git
cd octopus-os/deploy/appliance
# 可选:指定管理员密码与 NAS 存储目录(不指定则密码随机生成、存储用 ./storage)
export OCTOPUS_ADMIN_PASSWORD=改成你的密码
export NAS_STORAGE=/path/to/your/nas/share   # 群晖如 /volume1/share
```

---

## 1. 镜像构建闭环(P1 第 5 步唯一未验证项)⭐ 最高优先级

```bash
docker compose up -d --build
```

**预期**:多阶段构建成功(node 构前端 → pip 装后端含 appliance extra → 运行时镜像),
容器起来后 `docker compose ps` 显示 healthy。

**检查点**:
- [ ] `docker compose build` 无错(重点看 pip 装 `.[serve,tracing,web,appliance]`
      和 `COPY appliance/` 是否生效)
- [ ] `docker compose ps` → `octopus-os` 状态 healthy
- [ ] `curl http://localhost:8000/api/health` 返回 200
- [ ] `docker compose logs | grep "appliance admin password"`
      → 若没设 OCTOPUS_ADMIN_PASSWORD,这里能看到随机初始密码

> 若 build 失败:大概率是依赖/路径层面(某个 extra 缺包、COPY 漏目录)。
> 把 `docker compose build` 的完整报错贴回来即可定位。

---

## 2. 桌面 + 登录 + 启动器(P1 端到端)

浏览器打开 `http://<本机或NAS_IP>:8000/#/desktop`

**检查点**:
- [ ] 出现原生登录屏(极光壁纸 + 毛玻璃卡),输入管理员密码进入
- [ ] 进入后是极光壁纸桌面 + Dock(图标有邻近放大效果)
- [ ] Dock "本地应用"段列出**宿主上真实运行的 Docker 容器**(运行中带绿点)
- [ ] 应用图标/名称正确(从容器 label 读;用 CasaOS 装的应用图标应能复用)
- [ ] 已停止的应用点击 → 能启动(转绿点)

> 若 Dock 不显示任何应用:确认 compose 挂了 `/var/run/docker.sock`,
> 且宿主上确有带发布端口的容器(`docker ps` 能看到 PORTS 列)。

---

## 3. 窗口化第三方应用(P2 窗口管理器 + 反向代理的关键反馈)⭐

点击 Dock 里一个运行中的应用 → 应在桌面内开成窗口。

**逐个应用记录**(这是反向代理要不要做、怎么做的依据):

| 应用 | iframe 直接显示? | 备注 |
|---|---|---|
| 例:Jellyfin | ☐ 正常 / ☐ 空白 | |
| 例:qBittorrent | ☐ 正常 / ☐ 空白 | |
| 例:Immich | ☐ 正常 / ☐ 空白 | |
| … | | |

- [ ] 窗口能拖拽(标题栏)、缩放(右下角)、最小化(黄)、关闭(红)
- [ ] **空白的应用**:点标题栏"新标签打开"能正常访问 → 说明是 X-Frame-Options
      拦截了内嵌,需要反向代理剥头(P2 后续)
- [ ] 浏览器 DevTools Console 里记录空白应用的报错(通常是
      `Refused to display ... in a frame because it set 'X-Frame-Options'`
      或 CSP `frame-ancestors`)

> 这一张表直接决定下一步:哪些应用开箱即用、哪些必须走反向代理。

---

## 4. NAS 文件管理器 + 回收站(P2,已本机验证,真机复核)

点 Dock 文件夹图标 → 文件管理器。

**检查点**:
- [ ] 列出 `NAS_STORAGE` 挂载的真实目录与文件
- [ ] 进入子文件夹(面包屑可回退)
- [ ] 删除一个文件 → 进"回收站"能看到它,原位置消失
- [ ] 回收站"恢复" → 文件回到原位置
- [ ] 在宿主上确认:删除的文件进了 `<NAS_STORAGE>/.octopus-trash/`,
      **不是被物理删除**(`ls -la` 看得到)
- [ ] "清空回收站"后 → 宿主上 `.octopus-trash/` 内容才真正消失

---

## 4b. 企业版 ↔ agent 服务化联调(PM 归并 D②,可选)

把企业版作为 PM 插件部署后,可让它把 AI 调用走 agent(而非自带 LLM key):

- 企业版 backend 设 `OCTOPUS_AGENT_URL=http://<agent_IP>:8000`(agent 服务地址);
- 在企业版里触发一次需 AI 的操作(如 PRD 导入 / 风险扫描);
- [ ] 看 agent 侧日志收到 `/v1/chat/completions` 请求 → 证明走的是 agent 网关;
- [ ] 不配 `OCTOPUS_AGENT_URL` 时仍能用自带 `LLM_BASE_URL` 直连(回退正常)。

> 本机已用单测验证路由逻辑 + 进程内耦合解除;此项是真实双服务的联调确认。

## 5. 安全复核(挂了 docker.sock = 宿主 root 等价)

- [ ] 确认 8000 端口**只在内网**可达,未直接暴露公网
- [ ] 无 token 直接 `curl http://<IP>:8000/api/appliance/apps` → 应 401
- [ ] 退出登录/换浏览器 → 桌面要求重新登录

---

## 反馈给开发(回填后贴回来)

1. **第 1 节** build 成功?失败贴 `docker compose build` 报错。
2. **第 3 节那张表**:哪些应用 iframe 直接显示、哪些空白 + Console 报错文本。
   ← 这是我接着做反向代理和应用技能(SKILL.md)最需要的输入。
3. 第 2/4/5 节有任何不符预期的,描述现象即可。

---

## 附:当前进度对照(均已推 os-main)

| 阶段 | 项 | 状态 |
|---|---|---|
| P1 | 应用注册器 + Dock 接真实应用 | ✅ 本机验证 |
| P1 | 桌面视觉(极光壁纸 + Dock 邻近缩放) | ✅ 预览验证 |
| P1 | 原生默认主页(去寄生叠加) | ✅ 预览验证 |
| P1 | NAS 部署打包(compose + CasaOS 清单) | ⏳ **本清单第 1 节** |
| P1 | 单用户认证(首启设密码 + 长会话) | ✅ 本机验证 |
| P2 | NAS 文件管理器 + 回收站硬约束 | ✅ 本机验证(第 4 节复核) |
| P2 | 窗口管理器(桌面即窗口系统) | ✅ 预览验证(第 3 节复核) |
| P2 | 反向代理剥 X-Frame-Options | ⏳ 需第 3 节的应用反馈 |
| P2 | 应用技能 SKILL.md | ⏳ 需真实应用 |
| P2 | 语义文件索引 | ⏳ 需 embedding 模型 |

后端 appliance 单元测试:39 个(`python -m pytest tests/appliance/`)。
