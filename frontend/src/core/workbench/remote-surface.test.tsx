import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import { WORKBENCH_BUILTIN_APPS } from "./apps";
import { RemoteWorkbenchSurface } from "./remote-surface";

const apiMocks = vi.hoisted(() => ({
  fetchCloudInstalled: vi.fn(),
  fetchRuntimePluginStatus: vi.fn(),
  setCloudPluginEnabled: vi.fn(),
  setRuntimePluginEnabled: vi.fn(),
}));

vi.mock("@/core/agents/agent-world-api", () => apiMocks);

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8000",
}));

vi.mock("@/core/auth/api", () => ({
  authHeaders: () => ({ Authorization: "Bearer test-token" }),
}));

const APP = WORKBENCH_BUILTIN_APPS.find(
  (candidate) => candidate.id === "narrative",
)!;

const MANIFEST = {
  schema: "echo.workbench_app.v1",
  id: "narrative_studio",
  name: "Narrative Studio",
  description: "Narrative tools",
  route: "/workspace/narrative",
  module_id: "narrative",
  version: "1.0.0",
  entry: "dist/index.html",
  entry_url: "/api/workbench-packages/narrative_studio/assets/dist/index.html",
  isolation: "iframe",
  permissions: [],
};

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function renderSurface() {
  return render(
    <MemoryRouter initialEntries={["/workspace/narrative?chapter=1"]}>
      <RemoteWorkbenchSurface app={APP} />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function manifestResponse() {
  return new Response(JSON.stringify(MANIFEST), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RemoteWorkbenchSurface", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(manifestResponse()));
    apiMocks.fetchRuntimePluginStatus.mockReset().mockResolvedValue({
      installed: true,
      enabled: true,
      lifecycle_state: "enabled",
    });
    apiMocks.setRuntimePluginEnabled.mockReset().mockResolvedValue({
      installed: true,
      enabled: true,
      lifecycle_state: "enabled",
    });
    apiMocks.fetchCloudInstalled.mockReset().mockResolvedValue({
      plugins: ["narrative_studio"],
      skills: [],
      plugin_states: {
        narrative_studio: {
          installed: true,
          enabled: true,
          lifecycle_state: "enabled",
        },
      },
    });
    apiMocks.setCloudPluginEnabled.mockReset().mockResolvedValue({
      installed: true,
      enabled: true,
      lifecycle_state: "enabled",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("validates the installed manifest and mounts its entry in a restricted iframe", async () => {
    renderSurface();

    const iframe = await screen.findByTitle("Narrative Studio");
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/workbench-packages/narrative_studio/manifest",
      expect.objectContaining({
        headers: { Authorization: "Bearer test-token" },
      }),
    );
    expect(iframe).toHaveAttribute(
      "sandbox",
      expect.stringContaining("allow-scripts"),
    );
    expect(iframe).not.toHaveAttribute(
      "sandbox",
      expect.stringContaining("allow-top-navigation"),
    );
    expect(iframe.getAttribute("src")).toContain(
      "http://localhost:8000/api/workbench-packages/narrative_studio/assets/dist/index.html",
    );
    expect(iframe.getAttribute("src")).toContain(
      "echo_host_path=%2Fworkspace%2Fnarrative%3Fchapter%3D1",
    );
  });

  it("accepts navigation only from the mounted frame and trusted backend origin", async () => {
    renderSurface();
    const iframe = (await screen.findByTitle(
      "Narrative Studio",
    )) as HTMLIFrameElement;

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "http://malicious.invalid",
          source: iframe.contentWindow,
          data: {
            type: "echo.workbench.navigate",
            href: "/workspace/design",
          },
        }),
      );
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "http://localhost:8000",
          source: window,
          data: {
            type: "echo.workbench.navigate",
            href: "/workspace/design",
          },
        }),
      );
    });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/workspace/narrative?chapter=1",
    );

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "http://localhost:8000",
          source: iframe.contentWindow,
          data: {
            type: "echo.workbench.navigate",
            href: "/workspace/design?canvas=2",
          },
        }),
      );
    });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/workspace/design?canvas=2",
    );
  });

  it("shows a repair path for missing packages and can retry without a reload", async () => {
    vi.mocked(fetch)
      .mockReset()
      .mockResolvedValueOnce(new Response("missing", { status: 404 }))
      .mockResolvedValueOnce(manifestResponse());

    renderSurface();
    expect(
      await screen.findByRole("heading", { name: "叙事工坊暂时不可用" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/尚未安装/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重新检查/ }));
    await waitFor(() =>
      expect(screen.getByTitle("Narrative Studio")).toBeInTheDocument(),
    );
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("offers one-click enable when the runtime is installed but disabled", async () => {
    apiMocks.fetchRuntimePluginStatus
      .mockReset()
      .mockResolvedValueOnce({
        installed: true,
        enabled: false,
        lifecycle_state: "disabled",
      })
      .mockResolvedValueOnce({
        installed: true,
        enabled: true,
        lifecycle_state: "enabled",
      });

    renderSurface();
    expect(
      await screen.findByRole("heading", { name: "叙事工坊已停用" }),
    ).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "启用应用" }));
    await waitFor(() =>
      expect(apiMocks.setCloudPluginEnabled).toHaveBeenCalledWith(
        "workbench_narrative",
        true,
      ),
    );
    expect(await screen.findByTitle("Narrative Studio")).toBeInTheDocument();
  });

  it("explains integrity failures without exposing raw backend JSON", async () => {
    vi.mocked(fetch)
      .mockReset()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "digest mismatch: abc123" }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      );

    renderSurface();
    expect(
      await screen.findByText(/安装包损坏或完整性校验失败/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/digest mismatch/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "前往重新安装" }),
    ).toBeInTheDocument();
  });

  it("distinguishes an unreachable local service from a missing app", async () => {
    vi.mocked(fetch)
      .mockReset()
      .mockRejectedValueOnce(new TypeError("offline"));

    renderSurface();
    expect(await screen.findByText(/无法连接本地服务/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "重新检查" }),
    ).toBeInTheDocument();
  });
});
