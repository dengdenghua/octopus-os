import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchAuditAnchor, fetchAuditKeyStatus, rotateAuditKey } from "./audit";
import { AuditEvidencePanel } from "./audit-evidence-panel";
import { requestHighRiskApproval } from "./approval";

vi.mock("./audit", () => ({
  fetchAuditAnchor: vi.fn(),
  fetchAuditKeyStatus: vi.fn(),
  rotateAuditKey: vi.fn(),
}));

vi.mock("./approval", () => ({
  requestHighRiskApproval: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(fetchAuditKeyStatus).mockResolvedValue({
    schema: "echo.appliance-audit-keyring.v1",
    activeKeyId: "echo-appliance-v1",
    keyIds: ["echo-appliance-v1"],
    keyCount: 1,
    maximumKeys: 64,
    secretsPersisted: false,
  });
  vi.mocked(fetchAuditAnchor).mockResolvedValue({
    schema: "echo.appliance-audit-anchor.v1",
    createdAt: "2026-08-27T01:00:00+00:00",
    audit: {
      entries: 12,
      tailSeq: 11,
      tailMac: "a".repeat(64),
      tailKeyId: "echo-appliance-v1",
      logSha256: "b".repeat(64),
      checkpointSha256: "c".repeat(64),
      keyringSha256: null,
    },
    signing: {
      algorithm: "Ed25519",
      keyId: `sha256:${"d".repeat(64)}`,
      publicKey: "public-key",
    },
    signature: "signature",
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "audit-once.signature",
    expiresIn: 90,
    action: "audit.key.rotate",
    target: "audit-chain",
  });
  vi.mocked(rotateAuditKey).mockResolvedValue({
    schema: "echo.appliance-audit-keyring.v1",
    activeKeyId: "echo-appliance-k2-0123456789abcdef",
    keyIds: ["echo-appliance-v1", "echo-appliance-k2-0123456789abcdef"],
    keyCount: 2,
    maximumKeys: 64,
    secretsPersisted: false,
    previousKeyId: "echo-appliance-v1",
    rotationEventSeq: 14,
  });
});

describe("audit evidence settings", () => {
  it("shows the device anchor and rotates only after password approval", async () => {
    const user = userEvent.setup();
    render(<AuditEvidencePanel />);

    expect(
      await screen.findByText("已验证 12 条记录；当前尾序号为 11。"),
    ).toBeInTheDocument();
    expect(screen.getByText(`sha256:${"d".repeat(64)}`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "轮换密钥…" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认轮换" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "audit.key.rotate",
        "audit-chain",
        "device-password",
      ),
    );
    expect(rotateAuditKey).toHaveBeenCalledWith("audit-once.signature");
    expect(fetchAuditAnchor).toHaveBeenCalledTimes(2);
  });
});
