import { fireEvent, screen } from "@testing-library/react";
import { useState } from "react";
import { Route, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { AppRouter } from "./router";
import { renderWithProviders } from "@/test/harness";
import { useDesktopWorkspaceRequest } from "@/appliance/use-desktop-workspace-request";

const auth = vi.hoisted(() => ({
  isLoading: false,
  isAuthenticated: true,
  authStatus: { enabled: true },
  user: { actor_id: "same-user" },
}));
const opened = vi.hoisted(() => vi.fn());
vi.mock("@/providers/AuthProvider", () => ({ useAuth: () => auth }));
vi.mock("@/components/electron-title-bar", () => ({
  ElectronTitleBar: () => null,
  ElectronTitleBarProvider: ({ children }: { children: React.ReactNode }) =>
    children,
}));
vi.mock("@/app/workspace/workspace-routes", () => ({
  createWorkspaceRoute: () => (
    <Route path="/workspace/*" element={<div>不应加载的全屏工作台</div>} />
  ),
}));
vi.mock("./app/desktop/page", () => ({
  default: function Desktop() {
    const [ready, setReady] = useState(auth.isAuthenticated);
    const location = useLocation();
    useDesktopWorkspaceRequest({ ready, onOpen: opened });
    return (
      <>
        <output>
          {location.pathname}
          {location.search}
        </output>
        <button
          onClick={() => {
            auth.isAuthenticated = true;
            setReady(true);
          }}
        >
          登录
        </button>
      </>
    );
  },
}));
beforeEach(() => {
  opened.mockClear();
  auth.isAuthenticated = true;
  auth.user = { actor_id: "same-user" };
});

it.each([
  [
    "/workspace/realtime/new?agent=coder",
    "/workspace/realtime/new?agent=coder",
  ],
  ["/plugins", "/workspace/agents?surface=chat&tab=plugins"],
  ["/settings", "/workspace/settings"],
  ["/workspace/realtime/new?shell=workbench", "/workspace/realtime/new"],
])(
  "routes %s through the desktop in the actual AppRouter",
  async (initialRoute, target) => {
    renderWithProviders(<AppRouter />, { initialRoute });
    expect(await screen.findByText("/desktop")).toBeInTheDocument();
    expect(opened).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledWith(target, { state: null });
    expect(screen.queryByText("不应加载的全屏工作台")).not.toBeInTheDocument();
  },
);

it("keeps an unauthenticated old task link pending at the OS login", async () => {
  auth.isAuthenticated = false;
  renderWithProviders(<AppRouter />, {
    initialRoute: "/workspace/realtime/task-42?agent=coder#result",
  });
  expect(await screen.findByText(/\/desktop\?returnTo=/)).toBeInTheDocument();
  expect(opened).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(await screen.findByText("/desktop")).toBeInTheDocument();
  expect(opened).toHaveBeenCalledWith(
    "/workspace/realtime/task-42?agent=coder#result",
    { state: null },
  );
  expect(screen.queryByText("不应加载的全屏工作台")).not.toBeInTheDocument();
});

it.each([
  "/plugins?presentation=workbench",
  "/settings?presentation=workbench",
])(
  "keeps workbench legacy routes inside the workspace host: %s",
  async (initialRoute) => {
    renderWithProviders(<AppRouter />, { initialRoute });
    expect(await screen.findByText("不应加载的全屏工作台")).toBeInTheDocument();
    expect(opened).not.toHaveBeenCalled();
  },
);
