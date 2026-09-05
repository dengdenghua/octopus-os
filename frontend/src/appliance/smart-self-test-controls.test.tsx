import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvSmartSelfTest,
  fetchOmvSmartSelfTest,
  planOmvSmartSelfTest,
  type OmvSmartSelfTestStatus,
} from "./omv";
import { SmartSelfTestControls } from "./smart-self-test-controls";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvSmartSelfTest: vi.fn(),
  fetchOmvSmartSelfTest: vi.fn(),
  planOmvSmartSelfTest: vi.fn(),
}));

const idle: OmvSmartSelfTestStatus = {
  devicefile: "/dev/sda",
  model: "Family Disk",
  identityHash: "c".repeat(64),
  supported: true,
  state: "idle",
  kind: null,
  progressPercent: null,
  readOnly: true,
  source: "smartctl",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchOmvSmartSelfTest).mockResolvedValue(idle);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "smart-once",
    expiresIn: 300,
    action: "storage.smart.self-test.start",
    target: "a".repeat(64),
  });
});

describe("SMART self-test controls", () => {
  it("previews, password-approves, and starts only the selected test", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.smart-self-test-desired.v1" as const,
      devicefile: "/dev/sda",
      test: "short" as const,
    };
    const plan = {
      schema: "echo.omv.smart-self-test-plan.v1" as const,
      planId: "a".repeat(64),
      operation: "start" as const,
      requiresApproval: true as const,
      desired,
      identityHash: "c".repeat(64),
      before: { state: "idle" as const, kind: null, progressPercent: null },
      safety: {
        target: "enumeratedWholeDisk" as const,
        allowedTests: ["short", "long"] as ["short", "long"],
        activeTest: "mustBeAbsent" as const,
        captive: false as const,
        abort: false as const,
      },
    };
    const running: OmvSmartSelfTestStatus = {
      ...idle,
      state: "inProgress",
      kind: "short",
      progressPercent: 5,
    };
    vi.mocked(planOmvSmartSelfTest).mockResolvedValue(plan);
    vi.mocked(applyOmvSmartSelfTest).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
      selfTest: running,
    });
    render(<SmartSelfTestControls devicefile="/dev/sda" />);

    await user.click(await screen.findByRole("button", { name: "短时自检" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(
      "不会使用 captive 模式",
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认启动" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "storage.smart.self-test.start",
        "a".repeat(64),
        "device-password",
      ),
    );
    expect(applyOmvSmartSelfTest).toHaveBeenCalledWith(
      desired,
      "a".repeat(64),
      "smart-once",
    );
    expect(await screen.findByText(/短时自检进行中 · 5%/)).toBeInTheDocument();
  });

  it("shows progress and does not offer a second test while one runs", async () => {
    vi.mocked(fetchOmvSmartSelfTest).mockResolvedValue({
      ...idle,
      state: "inProgress",
      kind: "long",
      progressPercent: 37,
    });
    render(<SmartSelfTestControls devicefile="/dev/sda" />);

    expect(await screen.findByText(/长时自检进行中 · 37%/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "短时自检" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "长时自检" }),
    ).not.toBeInTheDocument();
  });
});
