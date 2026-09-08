import { fireEvent, render, screen, within } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { expect, it, vi } from "vitest";
import { useDesktopWorkspaces } from "./use-desktop-workspaces";

vi.mock("@/app/workspace/workspace-routes", () => ({
  createWorkspaceRoute: () => {
    function Task() {
      const location = useLocation();
      const navigate = useNavigate();
      return (
        <>
          <output>
            {location.pathname}
            {location.search}
            {location.hash}
          </output>
          <span>{location.state?.workspacePath}</span>
          <input
            aria-label="任务草稿"
            defaultValue={
              new URLSearchParams(location.search).get("prompt") ?? ""
            }
          />
          <button
            onClick={() =>
              navigate("/workspace/realtime/created-thread?agent=coder#result")
            }
          >
            任务已创建
          </button>
        </>
      );
    }
    return <Route path="/workspace/*" element={<Task />} />;
  },
}));

function Host() {
  const { windows, openWorkspace, focusedWin, minimizeWindow, minimized } =
    useDesktopWorkspaces();
  return (
    <>
      <output data-testid="outer-location">{useLocation().pathname}</output>
      <button onClick={() => openWorkspace("/workspace/realtime/new")}>
        Agent 入口
      </button>
      <button
        onClick={() =>
          openWorkspace("/workspace/realtime/new?prompt=示例任务&agent=coder", {
            state: { workspacePath: "C:/项目" },
          })
        }
      >
        引导示例
      </button>
      <button
        onClick={() => openWorkspace("/workspace/realtime/created-thread")}
      >
        任务面板恢复
      </button>
      <button
        onClick={() =>
          openWorkspace(
            "/workspace/realtime/created-thread?artifact=workspace-output%3Afinal%3Areport.txt",
          )
        }
      >
        返回原产物
      </button>
      <button
        onClick={() => {
          if (focusedWin) minimizeWindow(focusedWin);
        }}
      >
        最小化
      </button>
      {windows.map((win) => (
        <section
          aria-label="任务窗口"
          key={win.id}
          data-focused={focusedWin === win.id}
          hidden={minimized.has(win.id)}
        >
          {win.content}
        </section>
      ))}
    </>
  );
}

function show() {
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <Host />
    </MemoryRouter>,
  );
}

it("delivers artifact requests to the existing task without discarding drafts or route context", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "Agent 入口" }));
  fireEvent.click(screen.getByRole("button", { name: "任务已创建" }));
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "继续编辑" },
  });
  fireEvent.click(screen.getByRole("button", { name: "最小化" }));
  fireEvent.click(screen.getByRole("button", { name: "返回原产物" }));
  expect(screen.getAllByRole("region", { name: "任务窗口" })).toHaveLength(1);
  expect(screen.getByRole("textbox")).toHaveValue("继续编辑");
  const region = screen.getByRole("region", { name: "任务窗口" });
  expect(region).toHaveTextContent(
    "agent=coder&artifact=workspace-output%3Afinal%3Areport.txt&artifactRequest=1#result",
  );
  fireEvent.click(screen.getByRole("button", { name: "返回原产物" }));
  expect(region).toHaveTextContent("artifactRequest=2#result");
  expect(screen.getByRole("textbox")).toHaveValue("继续编辑");
});

it("opens an onboarding prompt without swallowing it or overwriting an existing draft", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "Agent 入口" }));
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "我的未发送内容" },
  });
  fireEvent.click(screen.getByRole("button", { name: "引导示例" }));
  expect(screen.getAllByRole("region", { name: "任务窗口" })).toHaveLength(2);
  expect(screen.getByDisplayValue("我的未发送内容")).toBeVisible();
  expect(screen.getByDisplayValue("示例任务")).toBeVisible();
  expect(screen.getByText("C:/项目")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "引导示例" }));
  expect(screen.getAllByRole("region", { name: "任务窗口" })).toHaveLength(2);
  expect(screen.getByTestId("outer-location")).toHaveTextContent(/^\/desktop$/);
});

it("restores the original thread window and can start a new draft after promotion", () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "Agent 入口" }));
  fireEvent.click(screen.getByRole("button", { name: "任务已创建" }));
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "后续提问草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "最小化" }));
  fireEvent.click(screen.getByRole("button", { name: "任务面板恢复" }));
  const original = screen.getByRole("region", { name: "任务窗口" });
  expect(original).toHaveAttribute("data-focused", "true");
  expect(within(original).getByRole("textbox")).toHaveValue("后续提问草稿");
  expect(
    within(original).getByText(
      "/workspace/realtime/created-thread?agent=coder#result",
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Agent 入口" }));
  expect(screen.getAllByRole("region", { name: "任务窗口" })).toHaveLength(2);
  expect(within(original).getByRole("textbox")).toHaveValue("后续提问草稿");
  expect(screen.getByTestId("outer-location")).toHaveTextContent(/^\/desktop$/);
});
