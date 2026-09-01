/* Implementation note. */

const _staticWebsiteOnly =
  (import.meta.env.VITE_STATIC_WEBSITE_ONLY ??
    import.meta.env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY) === "true";

export const env = {
  NEXT_PUBLIC_BACKEND_BASE_URL:
    import.meta.env.VITE_BACKEND_BASE_URL ??
    import.meta.env.NEXT_PUBLIC_BACKEND_BASE_URL ??
    "",

  NEXT_PUBLIC_ECHO_BASE_URL:
    import.meta.env.VITE_ECHO_BASE_URL ??
    import.meta.env.NEXT_PUBLIC_ECHO_BASE_URL ??
    "",

  // Optional Echo OS shell address used when the independently served Agent
  // workbench needs to return to the desktop application.
  ECHO_OS_DESKTOP_URL:
    import.meta.env.VITE_ECHO_OS_DESKTOP_URL ??
    import.meta.env.NEXT_PUBLIC_ECHO_OS_DESKTOP_URL ??
    "",

  // Implementation note.
  NEXT_PUBLIC_STATIC_WEBSITE_ONLY: _staticWebsiteOnly,
  // Short alias used by most callsites (header, chat-box, artifacts,
  // tool-settings, workspace-header, etc.). Both names point at the
  // same boolean so new code can use either.
  STATIC_WEBSITE_ONLY: _staticWebsiteOnly,

  GITHUB_OAUTH_TOKEN:
    import.meta.env.VITE_GITHUB_OAUTH_TOKEN ??
    import.meta.env.GITHUB_OAUTH_TOKEN ??
    "",

  NODE_ENV: import.meta.env.MODE ?? "development",
};
