# appliance/ — Octopus OS 专属层

OS(NAS 桌面)形态的专属代码,与 runtime/ 内核分离以最小化与母体的合并冲突面
(策略见 docs/OCTOPUS_OS_PLAN.md §4)。

## app_registry — 桌面启动器的数据源

Docker 容器 → 启动器应用卡片。

**启用**:`OCTOPUS_APPLIANCE=1 python -m runtime serve …`(不开此开关时母体行为零变化);
依赖装 `pip install -e ".[appliance]"`(或 `uv sync --extra appliance`)。

**API**:
- `GET  /api/appliance/apps` — 应用列表;Docker 不可达时 `{available:false}` 优雅降级
- `POST /api/appliance/apps/{id}/start` / `…/stop`

**容器元数据 label 级联**(自有规范优先,兼容主流生态):

| 字段 | label 优先级 |
|---|---|
| 名称 | `sh.octopus.name` → `casaos.name` → `homepage.name` → OCI title → 容器名 |
| 图标 | `sh.octopus.icon` → `casaos.icon` → `icon` → `homepage.icon` → unraid |
| Web 入口 | `sh.octopus.webui` → `casaos.webui` → `homepage.href` → unraid → 端口启发式(80/443/3000/8080/8096…) |
| 隐藏 | `sh.octopus.hide: "1"` |

后端只返回端口号,完整 URL 由前端用 `window.location.hostname` 拼装
(只有浏览器知道用户经由哪个地址访问 NAS)。

**前端**:`frontend/src/appliance/apps.ts`(类型 + fetch + 30s 轮询 hook);
桌面页 Dock 的"本地应用"段消费它,API 不可用时回退占位图标。

**安全注**:start/stop 操作 docker.sock(root 等价)。P2 在 router.py
标注处接入 runtime/safety/approval 审批门;装/删应用功能必须随审批门一起上。

## 测试

```bash
uv sync --extra dev --extra serve --extra appliance
.venv/bin/python -m pytest tests/appliance/ -q   # 12 个用例,无需 Docker
```
