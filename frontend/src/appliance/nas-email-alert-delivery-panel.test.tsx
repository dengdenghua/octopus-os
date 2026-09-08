import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyNasEmailAlertDelivery,
  fetchNasEmailAlertDeliveryStatus,
  planNasEmailAlertDelivery,
  type NasEmailAlertDeliveryStatus,
} from "./nas-email-alert-delivery";
import { NasEmailAlertDeliveryPanel } from "./nas-email-alert-delivery-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./nas-email-alert-delivery", () => ({
  NAS_EMAIL_ALERT_CONFIGURE_ACTION: "notifications.email.configure",
  NAS_EMAIL_ALERT_TEST_ACTION: "notifications.email.test",
  applyNasEmailAlertDelivery: vi.fn(),
  fetchNasEmailAlertDeliveryStatus: vi.fn(),
  planNasEmailAlertDelivery: vi.fn(),
  testNasEmailAlertDelivery: vi.fn(),
}));

const disabled: NasEmailAlertDeliveryStatus = {
  schema: "echo.nas-alert-email.v1",
  configured: false,
  enabled: false,
  destinationHost: null,
  smtpPort: null,
  security: null,
  recipientHint: null,
  hasCredentials: false,
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
  vi.mocked(fetchNasEmailAlertDeliveryStatus).mockResolvedValue(disabled);
  vi.mocked(planNasEmailAlertDelivery).mockResolvedValue({
    schema: "echo.nas-alert-email.v1",
    planId: "a".repeat(64),
    changes: [],
    requiresApproval: true,
    secretsPersistedEncrypted: true,
    publicSmtpOnly: true,
    tlsRequired: true,
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "configure-once",
    expiresIn: 90,
    action: "notifications.email.configure",
    target: "a".repeat(64),
  });
  vi.mocked(applyNasEmailAlertDelivery).mockResolvedValue({
    ...disabled,
    configured: true,
    enabled: true,
    destinationHost: "smtp.example.com",
    smtpPort: 465,
    security: "implicit_tls",
    recipientHint: "o***@example.net",
    hasCredentials: true,
    revision: "c".repeat(64),
  });
});

describe("headless NAS email alert settings", () => {
  it("previews and password-approves encrypted SMTP configuration", async () => {
    const user = userEvent.setup();
    render(<NasEmailAlertDeliveryPanel />);

    await screen.findByText("未启用邮件告警");
    await user.type(screen.getByLabelText("SMTP 服务器"), "smtp.example.com");
    await user.type(screen.getByLabelText("SMTP 用户名"), "echo@example.com");
    await user.type(
      screen.getByLabelText("SMTP 应用密码"),
      "private-app-password",
    );
    await user.type(screen.getByLabelText("发件地址"), "echo@example.com");
    await user.type(screen.getByLabelText("告警收件地址"), "owner@example.net");
    await user.click(screen.getByRole("button", { name: "启用邮件告警…" }));

    expect(
      await screen.findByRole("alertdialog", { name: "启用邮件告警？" }),
    ).toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("输入密码以证明是你本人"),
      "admin-password",
    );
    await user.click(screen.getByRole("button", { name: "确认启用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "notifications.email.configure",
        "a".repeat(64),
        "admin-password",
      ),
    );
    expect(planNasEmailAlertDelivery).toHaveBeenCalledWith({
      enabled: true,
      smtpHost: "smtp.example.com",
      smtpPort: 465,
      username: "echo@example.com",
      password: "private-app-password",
      fromAddress: "echo@example.com",
      recipient: "owner@example.net",
    });
    expect(applyNasEmailAlertDelivery).toHaveBeenCalledWith(
      "a".repeat(64),
      "configure-once",
    );
    expect(
      await screen.findByText(/smtp\.example\.com:465/),
    ).toBeInTheDocument();
    expect(
      screen.queryByDisplayValue("private-app-password"),
    ).not.toBeInTheDocument();
  });
});
