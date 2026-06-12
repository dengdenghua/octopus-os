# Electron 桌面壳

> 2026-06-13 重建。原 `frontend/electron/` 从未进入 git(`git log --all -- frontend/electron`
> 为空),在本地清理中丢失。本次按 `src/types/electron.d.ts` 留存的完整契约重写。

## 运行

```bash
pnpm electron:dev    # 启动 Vite(:3000)并在就绪后拉起 Electron
pnpm electron        # 仅拉起 Electron(假定 dev server 已在运行)
```

后端默认 `http://127.0.0.1:8000`,可用 `OCTOPUS_BACKEND_URL` 覆盖;
前端地址可用 `ELECTRON_START_URL` 覆盖(默认 `http://127.0.0.1:3000`)。

## 实现状态(对照 electron.d.ts)

| 命名空间                                                                                       | 状态                                                        |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `app` / `dialog` / `window` / `backend.getBaseURL`                                             | ✅ 完整                                                     |
| `desktop`(桌面助手:列举/打开/分类移动/批量/撤销/系统信息 + items-changed 监听)                 | ✅ 完整                                                     |
| `browser`(导航/JS/截图/取文/click/type/hover/scroll/waitFor/pressKey/ariaTree/清站点数据/下载) | ✅ 完整(ariaTree 走 CDP)                                    |
| `extensions`(list/installFromFolder/setEnabled/remove)                                         | ✅ 基本(Electron loadExtension 的 API 子集限制)             |
| `on` 八个事件通道                                                                              | ✅ 已接线(`app:update-downloaded` 暂无触发源——未接自动更新) |
| `desktop.installContextMenu`(Windows 右键菜单)                                                 | ⛔ 诚实降级:返回 `{ok:false}`,前端按钮会提示失败            |
| `backend.restart`(打包模式重启子进程)                                                          | ⛔ 开发模式下后端独立运行,返回 `{ok:false, reason}`         |

## 已知边界

- 打包(electron-builder)未配置——当前只支持开发模式;打包时需把
  `packaging/desktop/config.desktop.yaml` 放入 resources(main.cjs 已按此约定读取)。
- `webview` 标签已启用(workspace 内嵌浏览器依赖它)。
- 安全基线:contextIsolation 开、nodeIntegration 关、`window.open` 一律转系统浏览器。
