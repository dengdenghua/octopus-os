import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyNutDeviceConfig,
  fetchNutDeviceConfig,
  planNutDeviceConfig,
  type NutDeviceConfigStatus,
} from "./nut-device-config";
import { NutDeviceConfigPanel } from "./nut-device-config-panel";
import { fetchNativeStatus } from "./omv";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./nut-device-config", () => ({
  applyNutDeviceConfig: vi.fn(),
  fetchNutDeviceConfig: vi.fn(),
  planNutDeviceConfig: vi.fn(),
}));
vi.mock("./omv", () => ({ fetchNativeStatus: vi.fn() }));

const status: NutDeviceConfigStatus = {
  schemaVersion: 1,
  configured: false,
  enabled: false,
  name: "echo-ups",
  driver: null,
  port: "auto",
  localOnly: true,
  externallyManaged: false,
  externalDeviceCount: 0,
  allowedDrivers: ["usbhid-ups"],
  shutdownOwner: "echo-ups-shutdown-guard",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNutDeviceConfig).mockResolvedValue(status);
  vi.mocked(fetchNativeStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: false,
    adminUrl: null,
    capabilities: ["power.ups.local-usb.configure.v1"],
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "nut-once",
    expiresIn: 300,
    action: "power.ups.local-usb.configure",
    target: "a".repeat(64),
  });
});

describe("local USB UPS configuration", () => {
  it("previews and approves one fixed local USB device", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.nut-local-ups-desired.v1" as const,
      enabled: true,
      driver: "usbhid-ups" as const,
    };
    vi.mocked(planNutDeviceConfig).mockResolvedValue({
      schema: desired.schema,
      planId: "a".repeat(64),
      operation: "enable",
      requiresApproval: true,
      baseRevision: "b".repeat(64),
      current: { enabled: false, driver: null },
      desired,
      safety: {
        device: "singleLocalUsbUps",
        port: "auto",
        server: "loopbackOnly",
        upsmon: "notConfigured",
        shutdownOwner: "echo-ups-shutdown-guard",
      },
    });
    vi.mocked(applyNutDeviceConfig).mockResolvedValue({} as never);
    render(<NutDeviceConfigPanel />);

    await user.click(
      await screen.findByRole("button", { name: "登记本机 UPS" }),
    );
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "重启本机 NUT 驱动和数据服务",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认登记" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "power.ups.local-usb.configure",
        "a".repeat(64),
        "device-password",
      ),
    );
    expect(applyNutDeviceConfig).toHaveBeenCalledWith(
      desired,
      "a".repeat(64),
      "nut-once",
    );
  });

  it("refuses to edit an externally managed NUT setup", async () => {
    vi.mocked(fetchNutDeviceConfig).mockResolvedValue({
      ...status,
      externallyManaged: true,
      externalDeviceCount: 1,
    });
    render(<NutDeviceConfigPanel />);

    expect(
      await screen.findByText(/非 Echo 管理的 NUT 设备段/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登记本机 UPS" })).toBeDisabled();
  });
});
