/** URL understood by the packaged Electron shell's auxiliary-window handler. */
export function desktopWindowURL(route: string): string {
  const normalized = route.startsWith("/") ? route : `/${route}`;
  const separator = normalized.includes("?") ? "&" : "?";
  return `echo-app://app/index.html#${normalized}${separator}embedded=app`;
}

export function shouldOpenDesktopWindow(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean(window.echo?.isElectron) &&
    window.location.protocol === "echo-app:"
  );
}
