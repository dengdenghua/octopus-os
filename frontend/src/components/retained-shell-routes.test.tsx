import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import {
  MemoryRouter,
  Route,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { RetainedShellRoutes } from "./retained-shell-routes";
import { ShellModeSwitch } from "./retained-shell-routes";

const actor = vi.hoisted(() => ({ value: "first-user" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => actor.value,
}));

beforeEach(() => {
  actor.value = "first-user";
});

it("keeps an explicit workbench host and its draft while visiting desktop", () => {
  function Surface() {
    const location = useLocation();
    return (
      <>
        <ShellModeSwitch />
        <input aria-label={location.pathname} defaultValue="" />
      </>
    );
  }
  render(
    <MemoryRouter
      initialEntries={["/workspace/realtime/task?presentation=workbench"]}
    >
      <RetainedShellRoutes>
        <Route path="/workspace/*" element={<Surface />} />
        <Route path="/desktop" element={<Surface />} />
      </RetainedShellRoutes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "任务草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "切换到桌面" }));
  expect(
    screen.getByRole("button", { name: "切换到工作台" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "切换到工作台" }));
  expect(
    screen.getByRole("textbox", { name: "/workspace/realtime/task" }),
  ).toHaveValue("任务草稿");
});

it("preserves desktop window drafts while visiting the browser", () => {
  const effects = vi.fn();
  const cleanup = vi.fn();
  function Page({ label }: { label: string }) {
    const [draft, setDraft] = useState("");
    const location = useLocation();
    const navigate = useNavigate();
    useEffect(() => {
      effects(label);
      return () => cleanup(label);
    }, [label]);
    return (
      <div>
        <button
          onClick={() =>
            navigate(label === "桌面草稿" ? "/browser" : "/desktop")
          }
        >
          {label === "桌面草稿" ? "打开浏览器" : "返回桌面"}
        </button>
        <input
          aria-label={label}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <output>
          {location.pathname}
          {location.search}
        </output>
      </div>
    );
  }
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <RetainedShellRoutes>
        <Route path="/desktop" element={<Page label="桌面草稿" />} />
        <Route path="/browser" element={<Page label="浏览器草稿" />} />
      </RetainedShellRoutes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "桌面草稿" }), {
    target: { value: "窗口里的编辑" },
  });
  fireEvent.click(screen.getByRole("button", { name: "打开浏览器" }));
  expect(cleanup).toHaveBeenCalledWith("桌面草稿");
  fireEvent.click(screen.getByRole("button", { name: "返回桌面" }));
  expect(screen.getByRole("textbox", { name: "桌面草稿" })).toHaveValue(
    "窗口里的编辑",
  );
  expect(cleanup).toHaveBeenCalledWith("浏览器草稿");
});

it("clears retained drafts when the authenticated identity changes", () => {
  function Host() {
    const [, rerender] = useState(0);
    return (
      <>
        <button
          onClick={() => {
            actor.value = "second-user";
            rerender((value) => value + 1);
          }}
        >
          切换账号
        </button>
        <RetainedShellRoutes>
          <Route
            path="/desktop"
            element={<input aria-label="草稿" defaultValue="" />}
          />
        </RetainedShellRoutes>
      </>
    );
  }
  render(
    <MemoryRouter initialEntries={["/desktop"]}>
      <Host />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "私有编辑" },
  });
  fireEvent.click(screen.getByRole("button", { name: "切换账号" }));
  expect(screen.getByRole("textbox")).toHaveValue("");
});
