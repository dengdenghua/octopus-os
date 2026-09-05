import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvZfsPoolExport,
  applyOmvZfsPoolImport,
  fetchOmvZfsImportCandidates,
  fetchOmvZfsPools,
  planOmvZfsPoolExport,
  planOmvZfsPoolImport,
  type OmvZfsPoolExportPlan,
  type OmvZfsPoolImportPlan,
} from "./omv";
import { ZfsLifecyclePanel } from "./zfs-lifecycle-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvZfsPoolExport: vi.fn(),
  applyOmvZfsPoolImport: vi.fn(),
  fetchOmvZfsImportCandidates: vi.fn(),
  fetchOmvZfsPools: vi.fn(),
  planOmvZfsPoolExport: vi.fn(),
  planOmvZfsPoolImport: vi.fn(),
}));

const pool = {
  name: "family",
  poolGuid: "15451357997522795478",
  health: "ONLINE",
  sizeBytes: 16 * 1024 ** 3,
  rootMountpoint: "/data/family",
  datasetCount: 1,
  mountedCount: 1,
  safeToExport: true as const,
};

const candidate = {
  name: "archive",
  poolGuid: "9876543210123456789",
  state: "ONLINE",
  layout: "mirror" as const,
  configHash: "c".repeat(64),
  safeToImport: true as const,
};

const exportPlan: OmvZfsPoolExportPlan = {
  schema: "echo.omv.zfs-pool-export-plan.v1",
  planId: "1".repeat(64),
  baseRevision: "2".repeat(64),
  operation: "export",
  requiresApproval: true,
  desired: {
    schema: "echo.omv.zfs-pool-export-desired.v1",
    name: pool.name,
    poolGuid: pool.poolGuid,
    dataPreserved: true,
  },
  pool,
  datasetCount: 1,
  mountedCount: 1,
  safety: {
    data: "preserved",
    force: false,
    poolState: "onlineOnly",
    mounts: "echoDataRootOnly",
    dependentShares: "mustBeDetached",
    activeMaintenance: "mustBeAbsent",
    rollback: "notAttemptedAfterConfirmedExport",
  },
};

const importPlan: OmvZfsPoolImportPlan = {
  schema: "echo.omv.zfs-pool-import-plan.v1",
  planId: "3".repeat(64),
  baseRevision: "4".repeat(64),
  operation: "import",
  requiresApproval: true,
  desired: {
    schema: "echo.omv.zfs-pool-import-desired.v1",
    name: candidate.name,
    poolGuid: candidate.poolGuid,
    mountPolicy: "echoDataRootOnly",
  },
  candidate,
  safety: {
    data: "preserved",
    identity: "guidBound",
    force: false,
    recoveryFlags: false,
    destroyedPools: false,
    inspection: "readOnlyNoMountBeforeWritableImport",
    mounts: "echoDataRootOnly",
    encryption: "notYetSupported",
    rollback: "exportOnFailure",
  },
};

const status = {
  configured: true,
  available: true,
  readOnly: false,
  adminUrl: null,
  capabilities: [
    "storage.pool.zfs.export.safe.v1",
    "storage.pool.zfs.import.echo-root.v1",
  ],
  source: "native" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchOmvZfsPools).mockResolvedValue([pool]);
  vi.mocked(fetchOmvZfsImportCandidates).mockResolvedValue([candidate]);
  vi.mocked(planOmvZfsPoolExport).mockResolvedValue(exportPlan);
  vi.mocked(planOmvZfsPoolImport).mockResolvedValue(importPlan);
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "one-shot",
    expiresIn: 90,
    action: "omv.zfs-pool.export",
    target: exportPlan.planId,
  });
  vi.mocked(applyOmvZfsPoolExport).mockResolvedValue({
    ...exportPlan,
    applied: true,
    verified: true,
    dataPreserved: true,
  });
  vi.mocked(applyOmvZfsPoolImport).mockResolvedValue({
    ...importPlan,
    applied: true,
    verified: true,
    dataPreserved: true,
  });
});

describe("ZFS data-preserving lifecycle panel", () => {
  it("exports an exact GUID only after preview and step-up approval", async () => {
    const user = userEvent.setup();
    render(<ZfsLifecyclePanel status={status} />);

    await user.click(
      await screen.findByRole("button", { name: "预览导出 family" }),
    );
    expect(planOmvZfsPoolExport).toHaveBeenCalledWith(exportPlan.desired);
    expect(screen.getByText(/导出完成后数据仍保留/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码（导出）"),
      "correct-password",
    );
    await user.click(screen.getByRole("button", { name: "确认安全导出" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.zfs-pool.export",
        exportPlan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvZfsPoolExport).toHaveBeenCalledWith(
      exportPlan.desired,
      exportPlan.planId,
      "one-shot",
    );
  });

  it("imports by GUID after showing the read-only no-mount inspection", async () => {
    const user = userEvent.setup();
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "import-token",
      expiresIn: 90,
      action: "omv.zfs-pool.import",
      target: importPlan.planId,
    });
    render(<ZfsLifecyclePanel status={status} />);

    await user.click(
      await screen.findByRole("button", { name: "预览导入 archive" }),
    );
    expect(planOmvZfsPoolImport).toHaveBeenCalledWith(importPlan.desired);
    expect(screen.getByText(/先以只读且不挂载方式检查/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码（导入）"),
      "correct-password",
    );
    await user.click(screen.getByRole("button", { name: "确认按 GUID 导入" }));

    await waitFor(() =>
      expect(applyOmvZfsPoolImport).toHaveBeenCalledWith(
        importPlan.desired,
        importPlan.planId,
        "import-token",
      ),
    );
  });
});
