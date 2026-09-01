// company/PM surface 已在 6495f88 从 OS appliance 剥离。
// workspace-sidebar 仍引用 useCompanyEnabled:重建为恒 false 的守卫,
// 保证"隐藏工作 surface 入口"语义成立,同时让 vite 构建不再 ENOENT
// (VM 装机验证实测发现)。
export function useCompanyEnabled(): boolean {
  return false;
}
