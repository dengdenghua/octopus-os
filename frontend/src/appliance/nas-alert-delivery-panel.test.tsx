import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyNasAlertDelivery,
  fetchNasAlertDeliveryStatus,
  planNasAlertDelivery,
  type NasAlertDeliveryStatus,
} from "./nas-alert-delivery";
import { NasAlertDeliveryPanel } from "./nas-alert-delivery-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./nas-alert-delivery", () => ({
  NAS_ALERT_CONFIGURE_ACTION: "notifications.webhook.configure",
  NAS_ALERT_TEST_ACTION: "notifications.webhook.test",
  applyNasAlertDelivery: vi.fn(),
  fetchNasAlertDeliveryStatus: vi.fn(),
  planNasAlertDelivery: vi.fn(),
  testNasAlertDelivery: vi.fn(),
}));

const disabled: NasAlertDeliveryStatus = {
  schema: "echo.nas-alert-delivery.v1",
  configured: false,
  enabled: false,
  destinationHost: null,
  hasBearerToken: false,
  deliveredActiveAlerts: 0,
  consecutiveFailures: 0,
  nextRetryAt: null,
  lastAttemptAt: null,
  lastSuccessAt: null,
  lastError: null,
  persistenceHealthy: true,
  monitoring: true,
  revision: "b".repeat(64),
  secretsRedacted: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchNasAlertDeliveryStatus).mockResolvedValue(disabled);
  vi.mocked(planNasAlertDelivery).mockResolvedValue({
    schema: "echo.nas-alert-delivery.v1",
    planId: "a".repeat(64),
    changes: [],
    requiresApproval: true,
    secretsPersistedEncrypted: true,
    redirectsAllowed: false,
    publicHttpsOnly: true,
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "configure-once",
    expiresIn: 90,
    action: "notifications.webhook.configure",
    target: "a".repeat(64),
  });
  vi.mocked(applyNasAlertDelivery).mockResolvedValue({
    ...disabled,
    configured: true,
    enabled: true,
    destinationHost: "hooks.example.com",
    hasBearerToken: true,
    revision: "c".repeat(64),
  });
});

describe("headless NAS alert settings", () => {
  it("previews and password-approves encrypted webhook configuration", async () => {
    const user = userEvent.setup();
    render(<NasAlertDeliveryPanel />);

    await screen.findByText(/浏览器关闭时不会发送外部告警/);
    await user.type(
      screen.getByRole("textbox", { name: "NAS 告警 Webhook URL" }),
      "https://hooks.example.com/echo?key=secret",
    );
    await user.type(
      screen.getByLabelText("Webhook Bearer Token（可选）"),
      "bearer-secret",
    );
    await user.click(screen.getByRole("button", { name: "启用…" }));

    expect(
      await screen.findByRole("alertdialog", { name: "启用无人值守告警？" }),
    ).toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("输入密码以证明是你本人"),
      "admin-password",
    );
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "notifications.webhook.configure",
        "a".repeat(64),
        "admin-password",
      ),
    );
    expect(applyNasAlertDelivery).toHaveBeenCalledWith(
      "a".repeat(64),
      "configure-once",
    );
    expect(await screen.findByText(/hooks\.example\.com/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("bearer-secret")).not.toBeInTheDocument();
  });
});
