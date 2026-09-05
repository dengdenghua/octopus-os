import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchOmvUpsStatus, type OmvUpsSnapshot } from "./omv";
import { UpsPanel } from "./ups-panel";

vi.mock("./omv", () => ({ fetchOmvUpsStatus: vi.fn() }));

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
  });
});
