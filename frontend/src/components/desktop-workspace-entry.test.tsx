import { fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useState } from "react";
import {
  MemoryRouter,
  Route,
  parsePath,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopWorkspaceEntry } from "./desktop-workspace-entry";
import { RetainedShellRoutes } from "./retained-shell-routes";
import { useDesktopWorkspaceRequest } from "@/appliance/use-desktop-workspace-request";
import { desktopWorkspaceRoute } from "@/core/router/desktop-workspace-route";

const hosts = vi.hoisted(() => ({ iframe: false, native: false }));
vi.mock("./workspace/embedded-window-bridge", () => ({
  isEmbeddedWindow: () => hosts.iframe,
}));
vi.mock("@/core/apps/desktop-apps", () => ({
  shouldOpenDesktopWindow: () => hosts.native,
}));
afterEach(() => {
  hosts.iframe = false;
  hosts.native = false;
});

function Location() {
  const location = useLocation();
  return (
    <output data-testid="outer-location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function show(initialRoute: string, ready = true) {
  const onOpen = vi.fn();
  const legacyMount = vi.fn();
  function Legacy() {
    legacyMount();
    return <div>standalone workspace</div>;
  }
  function Desktop() {
    const [authenticated, setAuthenticated] = useState(ready);
    const navigate = useNavigate();
    useDesktopWorkspaceRequest({ ready: authenticated, onOpen });
    return (
      <>
        <input aria-label="桌面草稿" defaultValue="" />
        <button onClick={() => setAuthenticated(true)}>完成登录</button>
        <button onClick={() => navigate("/browser")}>浏览器</button>
      </>
    );
  }
  function Browser() {
    const navigate = useNavigate();
    return (
      <button onClick={() => navigate("/workspace/realtime/new?prompt=继续")}>
        旧链接
      </button>
    );
  }
  render(
    <StrictMode>
      <MemoryRouter
        initialEntries={[
          {
            ...parsePath(initialRoute),
            state: { workspacePath: "C:/项目", taskNonce: "draft-1" },
          },
        ]}
      >
        <Location />
        <RetainedShellRoutes>
          <Route path="/desktop" element={<Desktop />} />
          <Route path="/browser" element={<Browser />} />
          <Route element={<DesktopWorkspaceEntry />}>
            <Route path="/workspace/*" element={<Legacy />} />
          </Route>
        </RetainedShellRoutes>
      </MemoryRouter>
    </StrictMode>,
  );
  return { onOpen, legacyMount };
}

it.each([
  "/workspace",
  "/workspace/realtime/new?prompt=中文%20%26%20plan&agent=coder&workspace_path=C%3A%2F项目",
  "/workspace/realtime/task-42?agent=coder#result",
  "/workspace/storage?library=images&file=C%3A%2F资料%2F照片.png&sourceThread=task-42",
  "/workspace/team/join?token=invite-token#details",
  "/workspace/realtime/new?embedded=app",
  "/workspace/realtime/new?shell=workbench&agent=coder#result",
])(
  "opens old URL %s inside the desktop, without mounting the old surface",
  (route) => {
    const { onOpen, legacyMount } = show(route);
    expect(onOpen).toHaveBeenCalledTimes(1);
    const expected = new URL(route, "https://echo.invalid");
    if (expected.searchParams.has("shell"))
      expected.searchParams.delete("shell");
    expect(onOpen).toHaveBeenCalledWith(
      `${expected.pathname}${expected.search}${expected.hash}`,
      { state: { workspacePath: "C:/项目", taskNonce: "draft-1" } },
    );
    expect(legacyMount).not.toHaveBeenCalled();
    expect(screen.getByTestId("outer-location")).toHaveTextContent(
      /^\/desktop$/,
    );
  },
);

it.each(["workspace", "returnTo"])(
  "retains a pending %s link through login and consumes it only once",
  (param) => {
    const route = "/workspace/realtime/task-42?agent=coder#result";
    const { onOpen } = show(
      `/desktop?${param}=${encodeURIComponent(route)}`,
      false,
    );
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.getByTestId("outer-location")).toHaveTextContent(param);
    fireEvent.click(screen.getByRole("button", { name: "完成登录" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0]?.[0]).toBe(route);
    expect(screen.getByTestId("outer-location")).toHaveTextContent(
      /^\/desktop$/,
    );
  },
);

it("keeps existing desktop drafts when an ordinary link returns from the browser", () => {
  const { onOpen, legacyMount } = show("/desktop");
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "还没发送的任务" },
  });
  fireEvent.click(screen.getByRole("button", { name: "浏览器" }));
  fireEvent.click(screen.getByRole("button", { name: "旧链接" }));
  expect(screen.getByRole("textbox")).toHaveValue("还没发送的任务");
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(legacyMount).not.toHaveBeenCalled();
});

it("does not dispatch external or non-workspace URLs as workspace requests", () => {
  const { onOpen } = show("/desktop?workspace=https%3A%2F%2Fevil.example");
  expect(onOpen).not.toHaveBeenCalled();
  expect(screen.getByTestId("outer-location")).toHaveTextContent(/^\/desktop$/);
  expect(desktopWorkspaceRoute("/workspace/../login")).toBe("/desktop");
});

it.each(["iframe", "native"] as const)(
  "keeps an explicitly embedded app in its %s host",
  (host) => {
    hosts[host] = true;
    const { onOpen } = show("/workspace/realtime/new?embedded=design");
    expect(screen.getByText("standalone workspace")).toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
  },
);
