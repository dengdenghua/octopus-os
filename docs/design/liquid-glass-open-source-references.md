# Liquid Glass 视觉与实现参考

以下仓库均已直接核验源码和许可证。参考分为“实现原理”“桌面构图”和“只看不拷”三组，避免把视觉参考误当成可再分发资产。

## 可借鉴的实现原理

| 项目                                                                                          | 许可证     | 采用的关键思路                                                                      | Echo OS 对应实现                                               |
| --------------------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| [rdev/liquid-glass-react](https://github.com/rdev/liquid-glass-react)                         | MIT        | 边缘专属 SDF 位移、R/G/B 三路折射、色散只作用于边缘                                 | 圆角 SDF 位移图、边缘 RGB split、清晰中心区                    |
| [shuding/liquid-glass](https://github.com/shuding/liquid-glass)                               | MIT        | Canvas 生成 rounded-rect SDF，再交给 SVG `feDisplacementMap` 作为 `backdrop-filter` | 启动时生成位移图并复用，避免逐帧 Canvas 回读                   |
| [samasante/liquid-glass](https://github.com/samasante/liquid-glass)                           | MIT        | 实时 DOM 场景采样、位移按镜片尺寸封顶、模糊结果裁回镜片、specular 与透射分层        | viewport 对齐壁纸采样、尺寸化 profile、透射滤镜与可见 rim 分离 |
| [iyinchao/liquid-glass-studio](https://github.com/iyinchao/liquid-glass-studio)               | MIT        | WebGL2/WebGPU 多通道背景采样、superellipse SDF、RGB 折射与 Fresnel 光照             | Snell 位移、色散、独立 specular map 与 Fresnel lighting        |
| [dashersw/liquid-glass-js](https://github.com/dashersw/liquid-glass-js)                       | MIT        | 场景采样、嵌套玻璃、边缘/中心位移和 rim 参数分离                                    | Dock/小组件独立场景采样层，宿主统一管理轮廓与层级              |
| [BorisMalts/Liquid-Glass-Lightweight](https://github.com/BorisMalts/Liquid-Glass-Lightweight) | Apache-2.0 | turbulence、chromatic dispersion、Fresnel edge 与 caustic 分层                      | 低频有机位移、边缘色散和柔和焦散                               |
| [xanaxknight/liquid-glass-css-svg](https://github.com/xanaxknight/liquid-glass-css-svg)       | MIT        | 圆角 SDF、Snell 定律与 viewport scene clone                                         | 物理位移模型和卡片尺寸裁剪                                     |

Echo OS 没有直接打包上述运行时，也没有复制其 shader 源码；实现基于公开原理重新编写，并保留 Chromium、reduced-motion、reduced-transparency 的降级路径。当前 Apple 模式优先使用一层原创 WebGL2 合成器：它用真实 DOM 边界建立 rounded-rect SDF，在同一遍 fragment shader 中完成 viewport 壁纸采样、边缘法线位移、RGB 色散、Fresnel 高光和指针焦散；WebGL 不可用时再退回原有 SVG/CSS 管线。

## 可再分发的桌面构图资产

1. [appletechie/appletechie-macos](https://github.com/appletechie/appletechie-macos)（MIT）
   - 提供 macOS 风格桌面构图、菜单栏、Dock 和壁纸基准。
   - `wallpaper-day2.jpg` 已放入 `frontend/public/third-party/appletechie-macos/`，并附带 `NOTICE.txt`。

## macOS 原生路线

[Meridius-Labs/electron-liquid-glass](https://github.com/Meridius-Labs/electron-liquid-glass)（MIT）把 Electron 窗口接到 macOS 26+ 的原生 `NSGlassEffectView`。已在 macOS 26.5、Node 22、Electron 34 环境核对其 1.1.1 源码与运行要求。它的 `addView()` 会在 `BrowserWindow` 根 `NSView` 下插入一个随整窗缩放的玻璃视图，当前没有给网页 DOM 元素设置独立 frame、更新位置或移除 view 的公开 API；文档中的 variant/scrim/subdued 也明确属于不稳定私有 API。

因此它适合“整个透明 Electron 窗口就是一块玻璃”的应用，却不能直接替代 Echo OS 中分别折射同一张壁纸的卡片、Dock 和菜单。Echo OS 在其 MIT 实现模式上新增了专用桥接层，而不是直接调用全窗 `addView()`：

- 独立无交互原生子窗口承载 Tahoe 壁纸，并始终排在主窗口下方；
- 主窗口保持透明，网页文字、图标和点击区域继续由 Chromium 渲染；
- 最多 8 个按 DOM 边界定位的 `NSGlassEffectView` 插在网页内容下方，分别对应卡片、Dock 和系统浮层；
- 原生 view 使用连续圆角且独占可见轮廓，原生模式会关闭 DOM/WebGL 的第二套 rim；
- 渲染进程只能传有限个经过主进程校验和窗口裁剪的矩形，不能传原生类名、文件路径或私有 material 参数；
- 仅 macOS 26 + Tahoe + Apple 模式启用；其他壁纸、平台或加载失败自动回落到 WebGL/SVG。

原生模块位于 `frontend/native/echo-liquid-glass/`，附带 Meridius Labs 的 MIT 归属说明。模块使用运行时类发现，不调用 variant/scrim/subdued 等不稳定私有调参。

## Linux KWin Wayland 原生路线

Wayland 不允许 Electron 像 X11 那样给窗口写 `_KDE_NET_WM_BLUR_BEHIND_REGION`。Echo OS
因此新增编译型 KWin 6 Effect：`deploy/desktop-session/kwin-liquid-glass-effect/`。它基于
[KWin 6.3.6 Blur effect](https://invent.kde.org/plasma/kwin/-/tree/v6.3.6/src/plugins/blur)
的 GPL-2.0-or-later Dual Kawase 管线，在 compositor 内完成真正的背景采样与模糊；本地
Effect 及 shader 按相同 GPL-2.0-or-later 分发。

- Effect 只识别 Echo Shell 窗口，并排除独立壁纸背景窗；
- Electron 只向 `org.echoos.KWin.LiquidGlass1` 的固定 D-Bus 对象同步经过两次校验的
  圆角矩形，不允许 renderer 选择服务、对象、方法、shader 或进程；
- 合同版本固定为 1，区域最多 8 个，几何有界，KWin 再用 `QPainterPath` 生成圆角
  `QRegion`；
- 模糊固定为两轮 Dual Kawase，并保留很弱的 banding noise；网页 WebGL 只负责折射、
  色散和 Fresnel 边光；
- Effect 缺失、软件渲染或 D-Bus 同步失败时，Electron 关闭原生壁纸窗并回落到原有
  WebGL/SVG/CSS，不留下第二套包边。

## 只作视觉走查，不复制

1. [Sunstar16/macOS-26-Tahoe-for-the-Web](https://github.com/Sunstar16/macOS-26-Tahoe-for-the-Web)
   - 对照 Tahoe 桌面、Dock 和 `glass-distortion` 的层级关系。
   - 仓库未声明可再分发许可证，因此没有复制其素材或代码。

2. [Apple macOS Tahoe 26 官方发布图](https://www.apple.com/newsroom/2025/06/macos-tahoe-26-makes-the-mac-more-capable-productive-and-intelligent-than-ever/)
   - 只用于校准玻璃层级、边缘方向、Dock 密度和控制中心材质。
   - Apple 图标、壁纸和私有材质参数不进入本仓库。

## 当前实现边界

浏览器可以稳定做到：背景模糊、同源壁纸透射、逐像素边缘/中心位移、RGB 色散、Fresnel 高光、指针焦散和 reduced-transparency fallback。macOS 26 Electron 桌面使用系统 `NSGlassEffectView` 与独立原生壁纸层；Linux Wayland 桌面则由 KWin Effect 接管真实背景采样与模糊，WebGL 保留光学边缘。与 Apple 的剩余差距集中在未公开的图标资产、私有材质参数及系统级动态行为，本项目不复制这些内容。

WebGL 主路径把三个高价值透镜合并到同一个透明视口画布中，画布只在真实 DOM 轮廓内部输出像素；卡片实体边框透明，由与宿主完全同尺寸的单个 masked rim 负责轮廓，避免嵌套圆角。SVG/CSS 降级路径仍把“场景克隆折射”和“可见外轮廓”拆成两个滤镜：`LiquidTransmissionFilter` 只负责弯曲壁纸，卡片、Dock 和图标宿主负责唯一的 rim/specular。
