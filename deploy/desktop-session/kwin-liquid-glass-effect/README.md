# Echo OS KWin Liquid Glass Effect

This KWin 6 effect owns the Wayland compositor blur pass for the bounded
Liquid Glass regions in the Echo desktop window. Electron can call only the
fixed `SyncSurfaces(string)` and `Clear()` D-Bus methods registered at
`/org/echoos/KWin/LiquidGlass` on KWin's existing session-bus name. The effect
validates the versioned JSON contract again, caps it at eight surfaces, builds
rounded `QRegion` masks, and applies a two-pass Dual Kawase blur before KWin
adds bounded refraction, restrained chromatic separation and one internal
Fresnel highlight.

Contract version 2 adds the validated material name to each surface. Version 1
remains accepted as `thick` for rolling upgrades. When the effect confirms the
surface update, Chromium suspends the WebGL optics layer completely. If the
effect is unavailable, Electron leaves the ordinary WebGL/CSS fallback active.

The blur pipeline is derived from KWin 6.3.6's GPL-2.0-or-later Blur effect.
All local files in this directory are distributed under GPL-2.0-or-later.
