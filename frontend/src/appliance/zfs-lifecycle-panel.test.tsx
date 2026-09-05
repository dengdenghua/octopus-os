import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestHighRiskApproval } from "./approval";
import {
  applyOmvZfsMirrorReplace,
  applyOmvZfsPoolExport,
  applyOmvZfsPoolImport,
  applyOmvZfsScrub,
  fetchOmvZfsImportCandidates,
  fetchOmvZfsMirrorReplacementCandidates,
  fetchOmvZfsMaintenance,
  fetchOmvZfsPools,
  planOmvZfsPoolExport,
  planOmvZfsPoolImport,
  planOmvZfsMirrorReplace,
  planOmvZfsScrub,
  type OmvZfsMirrorReplacePlan,
  type OmvZfsPoolExportPlan,
  type OmvZfsPoolImportPlan,
  type OmvZfsScrubPlan,
} from "./omv";
import { ZfsLifecyclePanel } from "./zfs-lifecycle-panel";

vi.mock("./approval", () => ({ requestHighRiskApproval: vi.fn() }));
vi.mock("./omv", () => ({
  applyOmvZfsMirrorReplace: vi.fn(),
  applyOmvZfsPoolExport: vi.fn(),
  applyOmvZfsPoolImport: vi.fn(),
  applyOmvZfsScrub: vi.fn(),
  fetchOmvZfsImportCandidates: vi.fn(),
  fetchOmvZfsMirrorReplacementCandidates: vi.fn(),
  fetchOmvZfsMaintenance: vi.fn(),
  fetchOmvZfsPools: vi.fn(),
  planOmvZfsPoolExport: vi.fn(),
  planOmvZfsPoolImport: vi.fn(),
  planOmvZfsMirrorReplace: vi.fn(),
  planOmvZfsScrub: vi.fn(),
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

const replacementCandidate = {
  pool: {
    name: "family",
    poolGuid: "15451357997522795478",
    health: "DEGRADED",
    sizeBytes: 16 * 1024 ** 3,
  },
  layout: "twoDiskMirror" as const,
  replaceableMember: {
    slot: 2,
    vdevGuid: "2222222222222222222",
    state: "UNAVAIL" as const,
  },
  minimumReplacementBytes: 16 * 1024 ** 3,
  replacementDevices: [
    {
      devicefile: "/dev/sdd",
      sizeBytes: 20 * 1024 ** 3,
      serial: "disk-d",
      wwn: null,
      model: "QEMU HARDDISK",
    },
  ],
};

const replacePlan: OmvZfsMirrorReplacePlan = {
  schema: "echo.omv.zfs-mirror-replace-plan.v1",
  planId: "5".repeat(64),
  baseRevision: "6".repeat(64),
  operation: "replace",
  requiresApproval: true,
  desired: {
    schema: "echo.omv.zfs-mirror-replace-desired.v1",
    name: "family",
    poolGuid: "15451357997522795478",
    oldVdevGuid: "2222222222222222222",
    replacementDevice: "/dev/sdd",
    dataPreserved: true,
  },
  pool: replacementCandidate.pool,
  failedMember: replacementCandidate.replaceableMember,
  replacement: replacementCandidate.replacementDevices[0],
  minimumReplacementBytes: 16 * 1024 ** 3,
  safety: {
    data: "preservedDuringResilver",
    scope: "singleTwoDiskMirrorOnly",
    target: "failedLeafVdevGuid",
    replacement: "wholeBlankNonRemovableWithPersistentIdentity",
    minimumSize: "onlineSiblingDeviceSize",
    force: false,
    sequentialReconstruction: false,
    wait: false,
    activeMaintenance: "mustBeAbsent",
    rollback: "noneAfterReplacementAccepted",
  },
};

const maintenance = {
  pool: {
    name: "family",
    poolGuid: "15451357997522795478",
    health: "ONLINE",
    sizeBytes: 16 * 1024 ** 3,
  },
  rootMountpoint: "/data/family",
  scan: {
    kind: "none" as const,
    state: "idle" as const,
    progressPercent: null,
    errors: null,
    summaryHash: "7".repeat(64),
  },
  canStartScrub: true,
};

const scrubPlan: OmvZfsScrubPlan = {
  schema: "echo.omv.zfs-scrub-plan.v1",
  planId: "8".repeat(64),
  baseRevision: "9".repeat(64),
  operation: "start",
  requiresApproval: true,
  desired: {
    schema: "echo.omv.zfs-scrub-desired.v1",
    name: "family",
    poolGuid: "15451357997522795478",
    operation: "start",
  },
  pool: maintenance.pool,
  before: maintenance.scan,
  safety: {
    data: "checksummedAndRepairableReplicasMayBeRepaired",
    poolState: "onlineOnly",
    mounts: "echoDataRootOnly",
    activeMaintenance: "mustBeAbsent",
    ioLoad: "high",
    wait: false,
    pause: false,
    stop: false,
    rollback: "noneAfterScrubAccepted",
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
  vi.mocked(fetchOmvZfsMirrorReplacementCandidates).mockResolvedValue([]);
  vi.mocked(fetchOmvZfsMaintenance).mockResolvedValue([]);
  vi.mocked(planOmvZfsPoolExport).mockResolvedValue(exportPlan);
  vi.mocked(planOmvZfsPoolImport).mockResolvedValue(importPlan);
  vi.mocked(planOmvZfsMirrorReplace).mockResolvedValue(replacePlan);
  vi.mocked(planOmvZfsScrub).mockResolvedValue(scrubPlan);
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
  vi.mocked(applyOmvZfsMirrorReplace).mockResolvedValue({
    ...replacePlan,
    applied: true,
    verified: true,
    dataPreserved: true,
    maintenanceState: "resilvering",
  });
  vi.mocked(applyOmvZfsScrub).mockResolvedValue({
    ...scrubPlan,
    applied: true,
    verified: true,
    maintenanceState: "scrubbing",
    scan: {
      ...maintenance.scan,
      kind: "scrub",
      state: "inProgress",
      progressPercent: 1.5,
    },
  });
});

describe("ZFS data-preserving lifecycle panel", () => {
  it("shows resilver state and starts scrub only after preview and approval", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchOmvZfsMaintenance).mockResolvedValue([maintenance]);
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "scrub-token",
      expiresIn: 90,
      action: "omv.zfs.scrub.start",
      target: scrubPlan.planId,
    });
    render(
      <ZfsLifecyclePanel
        status={{
          ...status,
          capabilities: ["storage.pool.zfs.scrub.start.v1"],
        }}
      />,
    );

    await user.click(
      await screen.findByRole("button", { name: "预览校验 family" }),
    );
    expect(planOmvZfsScrub).toHaveBeenCalledWith(scrubPlan.desired);
    expect(screen.getByText(/命令立即返回/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码（校验）"),
      "correct-password",
    );
    await user.click(screen.getByRole("button", { name: "确认启动后台校验" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.zfs.scrub.start",
        scrubPlan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvZfsScrub).toHaveBeenCalledWith(
      scrubPlan.desired,
      scrubPlan.planId,
      "scrub-token",
    );
  });

  it("replaces the exact failed vdev with a server-approved blank disk", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchOmvZfsMirrorReplacementCandidates).mockResolvedValue([
      replacementCandidate,
    ]);
    vi.mocked(requestHighRiskApproval).mockResolvedValue({
      approvalToken: "replace-token",
      expiresIn: 90,
      action: "omv.zfs-mirror.replace",
      target: replacePlan.planId,
    });
    render(
      <ZfsLifecyclePanel
        status={{
          ...status,
          capabilities: ["storage.pool.zfs-mirror.replace.blank.v1"],
        }}
      />,
    );

    await user.click(
      await screen.findByRole("button", {
        name: "预览用 /dev/sdd 修复 family",
      }),
    );
    expect(planOmvZfsMirrorReplace).toHaveBeenCalledWith(replacePlan.desired);
    expect(screen.getByText(/启动带校验的 resilver/)).toBeInTheDocument();
    expect(screen.getByText(/disk-d/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("设备管理员密码（换盘）"),
      "correct-password",
    );
    await user.click(screen.getByRole("button", { name: "确认启动校验重建" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.zfs-mirror.replace",
        replacePlan.planId,
        "correct-password",
      ),
    );
    expect(applyOmvZfsMirrorReplace).toHaveBeenCalledWith(
      replacePlan.desired,
      replacePlan.planId,
      "replace-token",
    );
  });

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
