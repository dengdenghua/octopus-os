import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { RuntimeSelfCheckPanel } from "./runtime-self-check-panel";

const fetchMock = vi.fn();

const SAMPLE = {
  schema: "echo.runtime_self_check.v1",
  ready: false,
  status: "degraded",
  generated_at: "2026-06-29T08:00:00Z",
  version: "0.2.0",
  version_drift: {
    runtime_matches_pyproject: true,
    frontend_matches_runtime: false,
    version_sources: {
      runtime: "0.2.0",
      pyproject: "0.2.0",
      frontend_package: "0.1.0",
    },
  },
  backend: {
    canonical_base_url: "http://127.0.0.1:8000",
    request_origin_base_url: "http://localhost:8000",
    request_url: "http://localhost:8000/api/runtime/self-check",
    host: "localhost",
    canonical_host: "127.0.0.1",
    port: 8000,
    env_port: null,
    server_host: "0.0.0.0",
    server_port: 8000,
  },
  process: {
    pid: 4242,
    python: "3.12.13",
    cwd: "/repo",
    argv: ["python", "-m", "runtime", "serve"],
  },
  frontend: {
    observed_origin: "http://localhost:3000",
    canonical_origin: "http://localhost:3000",
    canonical_host: "localhost",
    port: 3000,
    env_port: null,
    dev_proxy_mode: true,
    proxy_target: "http://127.0.0.1:8000",
    proxy_targets_backend: true,
    origin_normalized: true,
    loopback_aliases: ["http://127.0.0.1:3000", "http://localhost:3000"],
  },
  webui: {
    available: true,
    selected_dist: "/repo/frontend/dist",
    env_dist: "/missing/dist",
    env_dist_invalid: true,
    assets_count: 12,
    dev_fallback_expected: false,
    detail:
      "configured ECHO_WEBUI_DIST is invalid: /missing/dist; fallback=/repo/frontend/dist",
  },
  model_compat: {
    available: true,
    profile_count: 13,
    domestic_profile_count: 13,
    profile_ids: ["kimi_coding", "deepseek", "qwen", "glm", "doubao"],
    required_profile_ids: ["kimi_coding", "deepseek", "qwen", "glm", "doubao"],
    missing_required_profile_ids: [],
    required_profiles_present: true,
  },
  orchestration: {
    schema: "echo.runtime_orchestration_self_check.v1",
    ready: true,
    route_count: 22,
    missing_required_routes: [],
    missing_route_methods: [],
    capabilities: {
      parallel_dispatch: true,
      sse_event_replay: true,
      review_queue: true,
    },
  },
  run_evidence: {
    schema: "echo.runtime_run_evidence_self_check.v1",
    ready: true,
    route_count: 17,
    missing_required_routes: [],
    missing_route_methods: [],
    missing_methods: [],
    capabilities: {
      task_run_replay_case: true,
      replay_gate: true,
      checkpoint_resume: true,
    },
  },
  automation: {
    schema: "echo.runtime_automation_self_check.v1",
    ready: true,
    route_count: 31,
    missing_required_routes: [],
    missing_route_methods: [],
    missing_methods: [],
    capabilities: {
      browser_replay_queue: true,
      computer_preview_execute: true,
      pixel_replay_gate: true,
    },
  },
  api_surface: {
    route_count: 180,
    required_routes_present: true,
    missing_required_routes: [],
  },
  loopback_aliases: {
    requested_host: "localhost",
    canonical_host: "127.0.0.1",
    same_loopback_family: true,
    aliases: ["http://127.0.0.1:8000", "http://localhost:8000"],
  },
  paths: {
    project_root: "/repo",
    journal_source: "/repo/data/journal.jsonl",
  },
  checks: [
    {
      id: "runtime_version",
      passed: true,
      severity: "error",
      detail: "runtime=0.2.0 pyproject=0.2.0",
    },
    {
      id: "frontend_version",
      passed: false,
      severity: "error",
      detail: "frontend=0.1.0 runtime=0.2.0",
    },
    {
      id: "webui_dist",
      passed: false,
      severity: "warn",
      detail:
        "configured ECHO_WEBUI_DIST is invalid: /missing/dist; fallback=/repo/frontend/dist",
    },
    {
      id: "openai_compat_profiles",
      passed: true,
      severity: "error",
      detail: "profiles=13 missing=none",
    },
  ],
  next_actions: ["frontend=0.1.0 runtime=0.2.0"],
  warnings: [
    "configured ECHO_WEBUI_DIST is invalid: /missing/dist; fallback=/repo/frontend/dist",
  ],
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockOnce(body: unknown) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

describe("RuntimeSelfCheckPanel", () => {
  it("renders runtime version, backend URL, loopback aliases, and next actions", async () => {
    mockOnce(SAMPLE);
    renderWithProviders(
      <RuntimeSelfCheckPanel baseUrl="http://127.0.0.1:8000" />,
    );

    await waitFor(() => {
      expect(screen.getByText("Runtime Self-Check")).toBeInTheDocument();
      expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/runtime/self-check",
    );
    expect(screen.getAllByText("0.2.0").length).toBeGreaterThan(0);
    expect(screen.getAllByText("http://127.0.0.1:8000").length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText("http://localhost:8000").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("Frontend")).toBeInTheDocument();
    expect(screen.getByText("Client backend base URL")).toBeInTheDocument();
    expect(
      screen.getByText("http://127.0.0.1:8000/api/runtime/self-check"),
    ).toBeInTheDocument();
    expect(screen.getByText("Self-check endpoint")).toBeInTheDocument();
    expect(screen.getByText("Observed origin")).toBeInTheDocument();
    expect(screen.getAllByText("http://localhost:3000").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("Vite proxy target")).toBeInTheDocument();
    expect(screen.getByText("Proxy matches backend")).toBeInTheDocument();
    expect(screen.getByText("Process")).toBeInTheDocument();
    expect(screen.getByText("4242")).toBeInTheDocument();
    expect(screen.getByText("WebUI static bundle")).toBeInTheDocument();
    expect(screen.getAllByText("/repo/frontend/dist").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("Model compatibility")).toBeInTheDocument();
    expect(screen.getByText("Domestic profiles")).toBeInTheDocument();
    expect(
      screen.getByText("kimi_coding, deepseek, qwen, glm, doubao"),
    ).toBeInTheDocument();
    expect(screen.getByText("API surface")).toBeInTheDocument();
    expect(screen.getByText("180")).toBeInTheDocument();
    expect(screen.getByText("Capability surfaces")).toBeInTheDocument();
    expect(screen.getByText("Orchestration")).toBeInTheDocument();
    expect(screen.getByText("Run evidence")).toBeInTheDocument();
    expect(screen.getByText("Automation")).toBeInTheDocument();
    expect(screen.getByText("parallel_dispatch")).toBeInTheDocument();
    expect(screen.getByText("replay_gate")).toBeInTheDocument();
    expect(screen.getByText("browser_replay_queue")).toBeInTheDocument();
    expect(screen.getByText("frontend_version")).toBeInTheDocument();
    expect(screen.getByText("webui_dist")).toBeInTheDocument();
    expect(screen.getByText("openai_compat_profiles")).toBeInTheDocument();
    expect(screen.getByText("warn")).toBeInTheDocument();
    expect(screen.getByText("Warnings")).toBeInTheDocument();
    expect(screen.getAllByText("frontend=0.1.0 runtime=0.2.0").length).toBe(2);
  });

  it("refreshes the self-check on demand", async () => {
    mockOnce(SAMPLE);
    mockOnce({
      ...SAMPLE,
      ready: true,
      status: "degraded",
      next_actions: [],
      warnings: ["configured ECHO_WEBUI_DIST is invalid"],
    });
    renderWithProviders(<RuntimeSelfCheckPanel />);

    await waitFor(() => {
      expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByRole("button", { name: /Refresh/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
    });
  });

  it("labels same-origin dev proxy backend resolution", async () => {
    mockOnce({ ...SAMPLE, ready: true, status: "ok", next_actions: [] });
    renderWithProviders(<RuntimeSelfCheckPanel />);

    await waitFor(() => {
      expect(screen.getByText("Runtime Self-Check")).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/runtime/self-check");
    expect(screen.getByText("same-origin / Vite proxy")).toBeInTheDocument();
    expect(screen.getByText("/api/runtime/self-check")).toBeInTheDocument();
  });

  it("shows fetch failures", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({}),
    });
    renderWithProviders(<RuntimeSelfCheckPanel />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503");
    });
  });
});
