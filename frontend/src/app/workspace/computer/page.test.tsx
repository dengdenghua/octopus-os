import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";
import type { ComputerStatus } from "@/core/computer/api";

const mocks = vi.hoisted(() => ({
  getComputerStatus: vi.fn(),
  loadModels: vi.fn(),
}));

vi.mock("@/core/computer/api", () => ({
  getComputerStatus: mocks.getComputerStatus,
  captureComputerScreen: vi.fn(),
  executeComputerAction: vi.fn(),
  groundComputerActions: vi.fn(),
  planComputerActions: vi.fn(),
  previewComputerAction: vi.fn(),
  releaseComputerLease: vi.fn(),
  askVisionModelForComputerActions: vi.fn(),
}));

vi.mock("@/core/models/api", () => ({
  loadModels: mocks.loadModels,
}));

vi.mock("@/components/workspace/workspace-container", () => ({
  WorkspaceContainer: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  WorkspaceBody: ({ children }: { children: ReactNode }) => (
    <main>{children}</main>
  ),
}));

import ComputerAutomationPage from "./page";

function baseStatus(overrides: Partial<ComputerStatus> = {}): ComputerStatus {
  return {
    schema: "echo.computer_runtime_status.v1",
    ok: true,
    ready: true,
    health: "ready",
    pyautogui_available: true,
    uia_available: true,
    lease: {
      held: false,
      ttl_seconds: 0,
      lease_ttl_seconds: 90,
    },
    screen: {
      width: 1440,
      height: 900,
      cursor_x: 20,
      cursor_y: 30,
    },
    readiness: {
      schema: "echo.computer_runtime_readiness.v1",
      ready: true,
      health: "ready",
      capabilities: [
        {
          id: "screen_observation",
          title: "Screen observation",
          available: true,
          critical: true,
          mode: "pyautogui_screen_info",
        },
        {
          id: "preview_execute_contract",
          title: "Preview-confirm-execute contract",
          available: true,
          critical: true,
          mode: "token_preview_with_lease",
        },
      ],
      degraded_capabilities: [],
      critical_blockers: [],
      recommended_actions: [],
      replay_evidence: {
        schema: "echo.computer_replay_evidence_hint.v1",
        replay_ready: true,
        case_id: "case-ready",
      },
    },
    capabilities: [],
    degraded_capabilities: [],
    critical_blockers: [],
    recommended_actions: [],
    replay_evidence: {
      schema: "echo.computer_replay_evidence_hint.v1",
      replay_ready: true,
      case_id: "case-ready",
    },
    skills: ["computer.screenshot", "computer.action.preview"],
    mode: "preview_confirm_execute",
    ...overrides,
  };
}

function renderPage(status: ComputerStatus) {
  mocks.getComputerStatus.mockResolvedValue(status);
  mocks.loadModels.mockResolvedValue([]);
  return renderWithProviders(<ComputerAutomationPage />, {
    initialRoute: "/workspace/computer",
    locale: "zh-CN",
  });
}

describe("<ComputerAutomationPage /> runtime readiness", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  test("keeps actions available when runtime is degraded by non-critical UIA", async () => {
    renderPage(
      baseStatus({
        health: "degraded",
        uia_available: false,
        readiness: {
          schema: "echo.computer_runtime_readiness.v1",
          ready: true,
          health: "degraded",
          capabilities: [],
          degraded_capabilities: [
            {
              id: "uia_semantic_grounding",
              title: "UIA semantic grounding",
              available: false,
              critical: false,
              mode: "accessibility_tree",
              reason: "uiautomation not installed",
              recommended_action: "install_or_enable_uia_backend",
            },
          ],
          critical_blockers: [],
          recommended_actions: ["install_or_enable_uia_backend"],
          replay_evidence: {
            schema: "echo.computer_replay_evidence_hint.v1",
            replay_ready: true,
            case_id: "case-degraded",
          },
        },
        degraded_capabilities: [
          {
            id: "uia_semantic_grounding",
            title: "UIA semantic grounding",
            available: false,
            critical: false,
            mode: "accessibility_tree",
            reason: "uiautomation not installed",
            recommended_action: "install_or_enable_uia_backend",
          },
        ],
        recommended_actions: ["install_or_enable_uia_backend"],
        replay_evidence: {
          schema: "echo.computer_replay_evidence_hint.v1",
          replay_ready: true,
          case_id: "case-degraded",
        },
      }),
    );

    expect(await screen.findByText("运行时 · 降级可用")).toBeInTheDocument();
    expect(
      screen.getByText("install_or_enable_uia_backend"),
    ).toBeInTheDocument();
    expect(screen.getByText("case-degraded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /观察屏幕/ })).not.toBeDisabled();
  });

  test("blocks computer actions when runtime has a critical blocker", async () => {
    renderPage(
      baseStatus({
        ok: false,
        ready: false,
        health: "blocked",
        screen: { error: "screen_info_failed: display unavailable" },
        readiness: {
          schema: "echo.computer_runtime_readiness.v1",
          ready: false,
          health: "blocked",
          capabilities: [],
          degraded_capabilities: [],
          critical_blockers: [
            {
              id: "screen_observation",
              title: "Screen observation",
              available: false,
              critical: true,
              mode: "pyautogui_screen_info",
              reason: "screen_info_failed: display unavailable",
              recommended_action: "check_display_or_desktop_permissions",
            },
          ],
          recommended_actions: ["check_display_or_desktop_permissions"],
          replay_evidence: {
            schema: "echo.computer_replay_evidence_hint.v1",
            replay_ready: true,
            case_id: "case-blocked",
          },
        },
        critical_blockers: [
          {
            id: "screen_observation",
            title: "Screen observation",
            available: false,
            critical: true,
            mode: "pyautogui_screen_info",
            reason: "screen_info_failed: display unavailable",
            recommended_action: "check_display_or_desktop_permissions",
          },
        ],
        recommended_actions: ["check_display_or_desktop_permissions"],
      }),
    );

    expect(await screen.findByText("运行时 · 阻塞")).toBeInTheDocument();
    expect(screen.getByText("本机运行时被阻塞")).toBeInTheDocument();
    expect(
      screen.getAllByText("check_display_or_desktop_permissions").length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /观察屏幕/ })).toBeDisabled();
  });
});
