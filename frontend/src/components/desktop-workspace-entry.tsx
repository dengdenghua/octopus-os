import { Navigate, Outlet, useLocation } from "react-router-dom";
import { shouldOpenDesktopWindow } from "@/core/apps/desktop-apps";
import { desktopWorkspaceRoute } from "@/core/router/desktop-workspace-route";
import { isEmbeddedWindow } from "./workspace/embedded-window-bridge";

/** Old bookmarks and ordinary links enter the desktop before loading content.
 * Design iframes and packaged auxiliary app windows already have their own host.
 */
export function DesktopWorkspaceEntry() {
  const location = useLocation();
  const embedded = new URLSearchParams(location.search).get("embedded");
  if (
    new URLSearchParams(location.search).get("presentation") === "workbench"
  ) {
    return <Outlet />;
  }
  if (
    (embedded === "design" || embedded === "app") &&
    (isEmbeddedWindow() || shouldOpenDesktopWindow())
  )
    return <Outlet />;

  return (
    <Navigate
      to={desktopWorkspaceRoute(
        `${location.pathname}${location.search}${location.hash}`,
      )}
      state={location.state}
      replace
    />
  );
}
