import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvUpsShutdownPolicy,
  fetchOmvStatus,
  fetchOmvUpsShutdownPolicy,
  fetchOmvUpsStatus,
  planOmvUpsShutdownPolicy,
  type OmvUpsSnapshot,
} from "./omv";
import { UpsPanel } from "./ups-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvUpsShutdownPolicy: vi.fn(),
  fetchOmvStatus: vi.fn(),
  fetchOmvUpsShutdownPolicy: vi.fn(),
  fetchOmvUpsStatus: vi.fn(),
  planOmvUpsShutdownPolicy: vi.fn(),
}));

const ready: OmvUpsSnapshot = {
  schemaVersion: 1,
  source: "nut",
  readOnly: true,
  configured: true,
  available: true,
  state: "ready",
  code: null,
  devices: [
    {
      name: "family-ups",
      available: true,
      state: "onBattery",
      statusFlags: ["DISCHRG", "OB"],
      chargePercent: 73.5,
      runtimeSeconds: 3_720,
      loadPercent: 31,
      inputVoltage: 0,
      outputVoltage: 229.4,
      batteryVoltage: 24.8,
      temperatureC: 32,
      manufacturer: "APC",
      model: "Back-UPS",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchOmvUpsStatus).mockResolvedValue(ready);
  vi.mocked(fetchOmvStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["power.ups-shutdown-policy.v1"],
  });
  vi.mocked(fetchOmvUpsShutdownPolicy).mockResolvedValue({
    schemaVersion: 1,
    configured: false,
    source: "localPolicy",
    enabled: false,
    requiredConsecutiveSamples: 3,
    shutdownTrigger: "FSD or persistent OB+LB",
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "ups-policy-once",
    expiresIn: 300,
    action: "power.ups-shutdown-policy.set",
    target: "a".repeat(64),
  });
});

describe("UPS power protection panel", () => {
  it("shows bounded battery state and refreshes on demand", async () => {
    const user = userEvent.setup();
    render(<UpsPanel />);

    expect(await screen.findByText("电池供电")).toBeInTheDocument();
    expect(screen.getByText("73.5%")).toBeInTheDocument();
    expect(screen.getByText("约 1 小时 2 分钟")).toBeInTheDocument();
    expect(screen.getByText("APC Back-UPS")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "刷新 UPS 状态" }));
    await waitFor(() => expect(fetchOmvUpsStatus).toHaveBeenCalledTimes(2));
  });

  it("does not present a missing NUT stack as healthy", async () => {
    vi.mocked(fetchOmvUpsStatus).mockResolvedValue({
      schemaVersion: 1,
      source: "nut",
      readOnly: true,
      configured: false,
      available: false,
      state: "unavailable",
      code: "toolMissing",
      devices: [],
    });
    render(<UpsPanel />);

    expect(
      await screen.findByText("系统尚未安装 NUT 客户端，UPS 状态不可观测。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("市电在线")).not.toBeInTheDocument();
  });

  it("states when NUT has no registered UPS or automatic shutdown", async () => {
    vi.mocked(fetchOmvUpsStatus).mockResolvedValue({
      schemaVersion: 1,
      source: "nut",
      readOnly: true,
      configured: false,
      available: true,
      state: "notConfigured",
      code: "emptyInventory",
      devices: [],
    });
    render(<UpsPanel />);

    expect(
      await screen.findByText(/当前不会自动执行低电量关机/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "启用低电量关机" }),
    ).toBeDisabled();
  });

  it("previews, password-approves, and applies the bounded shutdown policy", async () => {
    const user = userEvent.setup();
    const plan = {
      schema: "echo.ups-shutdown-policy-desired.v1" as const,
      planId: "a".repeat(64),
      operation: "enable" as const,
      requiresApproval: true,
      configured: false,
      current: {
        schemaVersion: 1 as const,
        enabled: false,
        requiredConsecutiveSamples: 3,
      },
      desired: {
        schemaVersion: 1 as const,
        enabled: true,
        requiredConsecutiveSamples: 3,
      },
      shutdownTrigger: "FSD or persistent OB+LB" as const,
    };
    vi.mocked(planOmvUpsShutdownPolicy).mockResolvedValue(plan);
    vi.mocked(applyOmvUpsShutdownPolicy).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    render(<UpsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "启用低电量关机" }),
    );
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "连续 3 次低电量确认",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "power.ups-shutdown-policy.set",
        "a".repeat(64),
        "device-password",
      ),
    );
    expect(applyOmvUpsShutdownPolicy).toHaveBeenCalledWith(
      {
        schema: "echo.ups-shutdown-policy-desired.v1",
        enabled: true,
        requiredConsecutiveSamples: 3,
      },
      "a".repeat(64),
      "ups-policy-once",
    );
  });
});
