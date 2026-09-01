import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyOmvFilesystemQuota,
  applyOmvGroup,
  applyOmvNfsShare,
  applyOmvSharedFolder,
  applyOmvSharePrivilege,
  applyOmvSmbShare,
  applyOmvUser,
  applyOmvUserPassword,
  fetchOmvFilesystems,
  fetchOmvSharePrivileges,
  fetchOmvSharingOverview,
  fetchOmvStatus,
  planOmvFilesystemQuota,
  planOmvGroup,
  planOmvNfsShare,
  planOmvSharedFolder,
  planOmvSharePrivilege,
  planOmvSmbShare,
  planOmvUser,
  planOmvUserPassword,
} from "./omv";
import { requestHighRiskApproval } from "./approval";
import {
  applyEchoAccountLink,
  applyEchoAccountPassword,
  applyEchoAccountStatus,
  applyEchoAccountUnlink,
  fetchEchoAccounts,
  planEchoAccountLink,
  planEchoAccountPassword,
  planEchoAccountStatus,
  planEchoAccountUnlink,
} from "./accounts";
import { OmvSharingPanel } from "./omv-sharing-panel";

vi.mock("./omv", () => ({
  applyOmvFilesystemQuota: vi.fn(),
  applyOmvGroup: vi.fn(),
  applyOmvNfsShare: vi.fn(),
  applyOmvSharedFolder: vi.fn(),
  applyOmvSharePrivilege: vi.fn(),
  applyOmvSmbShare: vi.fn(),
  applyOmvUser: vi.fn(),
  applyOmvUserPassword: vi.fn(),
  fetchOmvFilesystems: vi.fn(),
  fetchOmvSharePrivileges: vi.fn(),
  fetchOmvSharingOverview: vi.fn(),
  fetchOmvStatus: vi.fn(),
  planOmvFilesystemQuota: vi.fn(),
  planOmvGroup: vi.fn(),
  planOmvNfsShare: vi.fn(),
  planOmvSharedFolder: vi.fn(),
  planOmvSharePrivilege: vi.fn(),
  planOmvSmbShare: vi.fn(),
  planOmvUser: vi.fn(),
  planOmvUserPassword: vi.fn(),
}));

vi.mock("./approval", () => ({
  requestHighRiskApproval: vi.fn(),
}));

vi.mock("./accounts", () => ({
  applyEchoAccountLink: vi.fn(),
  applyEchoAccountPassword: vi.fn(),
  applyEchoAccountStatus: vi.fn(),
  applyEchoAccountUnlink: vi.fn(),
  fetchEchoAccounts: vi.fn(),
  planEchoAccountLink: vi.fn(),
  planEchoAccountPassword: vi.fn(),
  planEchoAccountStatus: vi.fn(),
  planEchoAccountUnlink: vi.fn(),
}));

const shareUuid = "11111111-2222-4333-8444-555555555555";
const filesystemUuid = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchOmvStatus).mockResolvedValue({
    configured: true,
    available: true,
    readOnly: true,
    adminUrl: "https://nas.example.test",
    capabilities: [],
  });
  vi.mocked(fetchOmvSharingOverview).mockResolvedValue({
    sharedFolders: [
      {
        uuid: shareUuid,
        name: "Family",
        comment: "Family files",
        relativePath: "Family/",
        device: "/dev/md0",
        status: "OK",
        inUse: true,
        supportsAcl: true,
      },
    ],
    sharedFolderTargets: [
      {
        mountPointRef: "99999999-8888-4777-8666-555555555555",
        filesystemUuid,
        label: "Family volume",
        type: "ext4",
        sizeBytes: 2 * 1024 ** 4,
        availableBytes: 1024 ** 4,
        readOnly: false,
      },
    ],
    users: [
      {
        name: "alice",
        uid: 1000,
        gid: 100,
        comment: "Alice",
        groups: ["users"],
      },
    ],
    groups: [{ name: "users", gid: 100, members: ["alice"] }],
    smb: {
      enabled: true,
      shares: [
        {
          uuid: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          sharedFolderRef: shareUuid,
          sharedFolderName: "Family",
          enabled: true,
          readOnly: false,
          guest: "no",
          browseable: true,
          recycleBin: false,
          comment: "Home share",
        },
      ],
    },
    nfs: { enabled: false, shares: [] },
  });
  vi.mocked(fetchOmvFilesystems).mockResolvedValue([
    {
      devicefile: "/dev/md0",
      parentdevicefile: null,
      uuid: filesystemUuid,
      label: "Family",
      type: "ext4",
      mountpoint: "Family",
      sizeBytes: 2 * 1024 ** 4,
      availableBytes: 1024 ** 4,
      usedPercent: 50,
      readOnly: false,
      supportsAcl: true,
      supportsQuota: true,
    },
  ]);
  vi.mocked(fetchOmvSharePrivileges).mockResolvedValue([
    {
      type: "user",
      id: 1000,
      name: "alice",
      permission: "readWrite",
    },
  ]);
  vi.mocked(fetchEchoAccounts).mockResolvedValue({
    schema: "echo.account-directory.v1",
    accounts: [
      {
        username: "admin",
        displayName: "管理员",
        role: "admin",
        omvUsername: null,
        active: true,
      },
    ],
    canManage: true,
  });
  vi.mocked(requestHighRiskApproval).mockResolvedValue({
    approvalToken: "approval-token",
    expiresIn: 90,
    action: "omv.smb.apply",
    target: "a".repeat(64),
  });
});

describe("OMV sharing and users settings", () => {
  it("shows protocol state and loads a sanitized permission matrix", async () => {
    const user = userEvent.setup();
    render(<OmvSharingPanel />);

    expect((await screen.findAllByText("Family")).length).toBe(2);
    expect(screen.getByText("1 位成员 · 1 个组")).toBeInTheDocument();
    expect(screen.getByText(/已启用 · 读写 ·/)).toBeInTheDocument();
    expect(screen.getByText("没有 NFS 共享规则")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /在 OMV 中管理/ })).toHaveAttribute(
      "href",
      "https://nas.example.test",
    );
    await user.click(screen.getByRole("button", { name: "查看用户/组权限" }));

    await waitFor(() =>
      expect(fetchOmvSharePrivileges).toHaveBeenCalledWith(shareUuid),
    );
    expect(screen.getByText("用户 alice · 读写")).toBeInTheDocument();
  });

  it("links an existing OMV member to an independent Echo login", async () => {
    const user = userEvent.setup();
    const desired = {
      omvUsername: "alice",
      displayName: "Alice",
      password: "Alice-independent-Echo-42!",
    };
    const plan = {
      schema: "echo.account-link-plan.v1" as const,
      planId: "e".repeat(64),
      operation: "linkExistingOmvMember" as const,
      requiresApproval: true as const,
      account: {
        username: "alice",
        displayName: "Alice",
        role: "member" as const,
        omvUsername: "alice",
      },
      changes: ["echoLogin"],
      safety: {
        omvPasswordReused: false as const,
        privateDatabaseRead: false as const,
        passwordReturned: false as const,
      },
    };
    vi.mocked(planEchoAccountLink).mockResolvedValue(plan);
    vi.mocked(applyEchoAccountLink).mockResolvedValue({
      linked: true,
      account: plan.account,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "echo-account-approval",
      expiresIn: 90,
      action: "account.member.link",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "开通 Echo" }));
    expect(screen.getByText("开通 Echo 登录 · alice")).toBeInTheDocument();
    await user.type(screen.getByLabelText("独立 Echo 密码"), desired.password);
    await user.type(screen.getByLabelText("确认 Echo 密码"), desired.password);
    await user.click(screen.getByRole("button", { name: "预览登录开通" }));

    await waitFor(() =>
      expect(planEchoAccountLink).toHaveBeenCalledWith(desired),
    );
    await user.click(screen.getByRole("button", { name: "管理员确认并开通" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(
      screen.getByRole("button", { name: "确认开通 Echo 登录" }),
    );

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "account.member.link",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyEchoAccountLink).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "echo-account-approval",
    );
    expect(
      screen.queryByDisplayValue(desired.password),
    ).not.toBeInTheDocument();
  });

  it("disables a member and resets only the independent Echo password", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchEchoAccounts).mockResolvedValue({
      schema: "echo.account-directory.v1",
      accounts: [
        {
          username: "admin",
          displayName: "管理员",
          role: "admin",
          omvUsername: null,
          active: true,
        },
        {
          username: "alice",
          displayName: "Alice",
          role: "member",
          omvUsername: "alice",
          active: true,
        },
      ],
      canManage: true,
    });
    const statusPlan = {
      schema: "echo.account-directory.v1" as const,
      planId: "7".repeat(64),
      operation: "setMemberStatus" as const,
      requiresApproval: true as const,
      account: { username: "alice", active: false },
      changes: ["echoLogin", "memberSessions"],
    };
    const passwordPlan = {
      schema: "echo.account-directory.v1" as const,
      planId: "8".repeat(64),
      operation: "resetMemberPassword" as const,
      requiresApproval: true as const,
      account: { username: "alice", active: true },
      changes: ["echoPassword", "memberSessions"],
      safety: {
        omvPasswordChanged: false as const,
        passwordReturned: false as const,
      },
    };
    vi.mocked(planEchoAccountStatus).mockResolvedValue(statusPlan);
    vi.mocked(planEchoAccountPassword).mockResolvedValue(passwordPlan);
    vi.mocked(applyEchoAccountStatus).mockResolvedValue({
      updated: true,
      sessionsRevoked: true,
      sessionNotBefore: 123,
    });
    vi.mocked(applyEchoAccountPassword).mockResolvedValue({
      updated: true,
      sessionsRevoked: true,
      sessionNotBefore: 124,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "停用 Echo" }));
    await waitFor(() =>
      expect(planEchoAccountStatus).toHaveBeenCalledWith({
        username: "alice",
        active: false,
      }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认停用 Echo" }));
    await waitFor(() =>
      expect(applyEchoAccountStatus).toHaveBeenCalledWith(
        { username: "alice", active: false },
        statusPlan.planId,
        "approval-token",
      ),
    );

    await user.click(screen.getByRole("button", { name: "重置 Echo 密码" }));
    const replacement = "Alice-replacement-Echo-84!";
    await user.type(screen.getByLabelText("新 Echo 密码"), replacement);
    await user.type(screen.getByLabelText("确认新 Echo 密码"), replacement);
    await user.click(
      screen.getByRole("button", { name: "预览 Echo 密码重置" }),
    );
    await waitFor(() =>
      expect(planEchoAccountPassword).toHaveBeenCalledWith({
        username: "alice",
        newPassword: replacement,
      }),
    );
    await user.click(screen.getByRole("button", { name: "管理员确认并重置" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(
      screen.getByRole("button", { name: "确认重置 Echo 密码" }),
    );
    await waitFor(() =>
      expect(applyEchoAccountPassword).toHaveBeenCalledWith(
        { username: "alice", newPassword: replacement },
        passwordPlan.planId,
        "approval-token",
      ),
    );
    expect(screen.queryByDisplayValue(replacement)).not.toBeInTheDocument();
  });

  it("removes only an already disabled Echo login", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchEchoAccounts).mockResolvedValue({
      schema: "echo.account-directory.v1",
      accounts: [
        {
          username: "admin",
          displayName: "管理员",
          role: "admin",
          omvUsername: null,
          active: true,
        },
        {
          username: "alice",
          displayName: "Alice",
          role: "member",
          omvUsername: "alice",
          active: false,
        },
      ],
      canManage: true,
    });
    const plan = {
      schema: "echo.account-directory.v1" as const,
      planId: "9".repeat(64),
      operation: "unlinkMember" as const,
      requiresApproval: true as const,
      account: { username: "alice", active: false },
      changes: ["echoLogin", "agentPrincipal", "memberSessions"],
      safety: { omvUserDeleted: false, nasDataDeleted: false },
    };
    vi.mocked(planEchoAccountUnlink).mockResolvedValue(plan);
    vi.mocked(applyEchoAccountUnlink).mockResolvedValue({
      unlinked: true,
      sessionsRevoked: true,
      sessionNotBefore: 125,
    });
    render(<OmvSharingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "移除 Echo 登录" }),
    );
    await waitFor(() =>
      expect(planEchoAccountUnlink).toHaveBeenCalledWith({ username: "alice" }),
    );
    expect(screen.getByText(/OMV 用户、SMB 密码/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(
      screen.getByRole("button", { name: "确认移除 Echo 登录" }),
    );
    await waitFor(() =>
      expect(applyEchoAccountUnlink).toHaveBeenCalledWith(
        { username: "alice" },
        plan.planId,
        "approval-token",
      ),
    );
  });

  it("creates an empty normal user group only after preview and password approval", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.group-desired.v1" as const,
      name: "family",
      comment: "Family members",
    };
    const plan = {
      schema: "echo.omv.group-plan.v1" as const,
      planId: "5".repeat(64),
      baseRevision: "3".repeat(64),
      operation: "create" as const,
      requiresApproval: true as const,
      desired,
      changes: [
        { field: "name" as const, before: null, after: desired.name },
        { field: "comment" as const, before: null, after: desired.comment },
      ],
      safety: {
        scope: "newNormalOmvGroup" as const,
        initialMembers: "empty" as const,
        systemGroups: "never" as const,
        update: "notManaged" as const,
        delete: "rollbackOnlyBeforeUse" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["account.group.create.v1"],
    });
    vi.mocked(planOmvGroup).mockResolvedValue(plan);
    vi.mocked(applyOmvGroup).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "group-approval-token",
      expiresIn: 90,
      action: "omv.group.create",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "新建用户组" }));
    await user.type(screen.getByLabelText("用户组名称"), desired.name);
    await user.type(screen.getByLabelText("用户组备注"), desired.comment);
    await user.click(screen.getByRole("button", { name: "预览用户组创建" }));

    await waitFor(() => expect(planOmvGroup).toHaveBeenCalledWith(desired));
    expect(screen.getByText("将创建空用户组 family")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "管理员确认并创建组" }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认创建用户组" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.group.create",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvGroup).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "group-approval-token",
    );
  });

  it("creates a no-shell family member and clears the member password after apply", async () => {
    const user = userEvent.setup();
    const overview = await fetchOmvSharingOverview();
    vi.mocked(fetchOmvSharingOverview).mockResolvedValueOnce({
      ...overview,
      groups: [...overview.groups, { name: "family", gid: 1000, members: [] }],
    });
    const desired = {
      schema: "echo.omv.user-desired.v1" as const,
      name: "mother",
      displayName: "Mother",
      password: "Echo-Family-2026!",
      groups: ["family"],
    };
    const plan = {
      schema: "echo.omv.user-plan.v1" as const,
      planId: "4".repeat(64),
      baseRevision: "2".repeat(64),
      operation: "create" as const,
      requiresApproval: true as const,
      desired: {
        schema: desired.schema,
        name: desired.name,
        displayName: desired.displayName,
        groups: desired.groups,
        passwordBound: true as const,
      },
      changes: [
        { field: "name" as const, before: null, after: desired.name },
        {
          field: "displayName" as const,
          before: null,
          after: desired.displayName,
        },
        { field: "groups" as const, before: [] as [], after: desired.groups },
      ],
      safety: {
        scope: "newNormalOmvUser" as const,
        password: "hmacBoundNeverReturnedOrAudited" as const,
        loginShell: "nologin" as const,
        sshKeys: "none" as const,
        homeDirectory: "automaticHomesMustBeDisabled" as const,
        systemGroups: "notEnumeratedNotSelectable" as const,
        update: "notManaged" as const,
        delete: "rollbackOnlyBeforeUse" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["account.user.create.v1"],
    });
    vi.mocked(planOmvUser).mockResolvedValue(plan);
    vi.mocked(applyOmvUser).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "user-approval-token",
      expiresIn: 90,
      action: "omv.user.create",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "添加家庭成员" }),
    );
    await user.type(screen.getByLabelText("成员账号"), desired.name);
    await user.type(screen.getByLabelText("显示名称"), desired.displayName);
    await user.type(screen.getByLabelText("成员密码"), desired.password);
    await user.type(screen.getByLabelText("确认成员密码"), desired.password);
    await user.click(screen.getByRole("checkbox", { name: "family" }));
    await user.click(screen.getByRole("button", { name: "预览成员创建" }));

    await waitFor(() => expect(planOmvUser).toHaveBeenCalledWith(desired));
    expect(screen.getByText(/密码已绑定计划且不会回传/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "管理员确认并创建成员" }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认创建家庭成员" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.user.create",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvUser).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "user-approval-token",
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("成员密码")).not.toBeInTheDocument(),
    );
  });

  it("resets an existing constrained member password after preview and approval", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.user-password-desired.v1" as const,
      name: "alice",
      password: "Replacement-Family-2026!",
    };
    const plan = {
      schema: "echo.omv.user-password-plan.v1" as const,
      planId: "6".repeat(64),
      baseRevision: "7".repeat(64),
      operation: "resetPassword" as const,
      requiresApproval: true as const,
      desired: {
        schema: desired.schema,
        name: desired.name,
        passwordBound: true as const,
      },
      changes: [
        {
          field: "password" as const,
          before: "currentCredential" as const,
          after: "replacementCredential" as const,
        },
      ],
      safety: {
        scope: "existingConstrainedNormalOmvUser" as const,
        password: "hmacBoundNeverReturnedOrAudited" as const,
        accountFields: "preservedAndVerified" as const,
        loginShell: "nologin" as const,
        sshKeys: "none" as const,
        rollback: "notAvailableAfterAcceptedSecretRpc" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["account.user.password.reset.v1"],
    });
    vi.mocked(planOmvUserPassword).mockResolvedValue(plan);
    vi.mocked(applyOmvUserPassword).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "password-reset-token",
      expiresIn: 90,
      action: "omv.user.password.reset",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "重置密码" }));
    await user.type(screen.getByLabelText("新密码"), desired.password);
    await user.type(screen.getByLabelText("确认新密码"), desired.password);
    await user.click(screen.getByRole("button", { name: "预览密码重置" }));

    await waitFor(() =>
      expect(planOmvUserPassword).toHaveBeenCalledWith(desired),
    );
    expect(screen.getByText(/新密码已绑定计划且不会回传/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "管理员确认并重置" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认重置成员密码" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.user.password.reset",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvUserPassword).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "password-reset-token",
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument(),
    );
  });

  it("still exposes configured share privileges without POSIX ACL support", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchOmvSharingOverview).mockResolvedValueOnce({
      ...(await fetchOmvSharingOverview()),
      sharedFolders: [
        {
          uuid: shareUuid,
          name: "Family",
          comment: "Family files",
          relativePath: "Family/",
          device: "/dev/md0",
          status: "OK",
          inUse: true,
          supportsAcl: false,
        },
      ],
    });
    render(<OmvSharingPanel />);

    expect(await screen.findByText("基础权限")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看用户/组权限" }));

    await waitFor(() =>
      expect(fetchOmvSharePrivileges).toHaveBeenCalledWith(shareUuid),
    );
  });

  it("previews one existing principal permission before password approval and apply", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.share-privilege-desired.v1" as const,
      sharedFolderRef: shareUuid,
      principalType: "user" as const,
      principalName: "alice",
      permission: "readWrite" as const,
    };
    const plan = {
      schema: "echo.omv.share-privilege-plan.v1" as const,
      planId: "7".repeat(64),
      baseRevision: "6".repeat(64),
      operation: "update" as const,
      requiresApproval: true,
      sharedFolder: { uuid: shareUuid, name: "Family", status: "OK" },
      principal: {
        type: "user" as const,
        id: 1000,
        name: "alice",
        before: "read" as const,
        after: "readWrite" as const,
      },
      desired,
      changes: [
        {
          field: "permission" as const,
          before: "read" as const,
          after: "readWrite" as const,
        },
      ],
      safety: {
        scope: "sharedFolderConfigPrivilege" as const,
        principal: "existingOmvUserOrGroup" as const,
        filesystemAcl: "notModified" as const,
        recursive: "never" as const,
        serviceDeploy: "sambaAndRsyncdWhenDirty" as const,
        delete: "notManaged" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["shared-folder.privilege.simple.v1"],
    });
    vi.mocked(fetchOmvSharePrivileges).mockResolvedValue([
      {
        type: "user",
        id: 1000,
        name: "alice",
        permission: "read",
      },
      {
        type: "group",
        id: 100,
        name: "users",
        permission: "inherit",
      },
    ]);
    vi.mocked(planOmvSharePrivilege).mockResolvedValue(plan);
    vi.mocked(applyOmvSharePrivilege).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
      deployedServices: ["samba"],
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "privilege-approval-token",
      expiresIn: 90,
      action: "omv.share-privilege.apply",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "管理用户/组权限" }),
    );
    expect(
      await screen.findByRole("heading", { name: /用户\/组访问权限/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/不会创建账户、修改/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("共享访问"), "readWrite");
    await user.click(screen.getByRole("button", { name: "预览权限变更" }));

    await waitFor(() =>
      expect(planOmvSharePrivilege).toHaveBeenCalledWith(desired),
    );
    expect(screen.getAllByText("只读").length).toBeGreaterThan(0);
    expect(screen.getByText("将更新用户 alice 的访问权限")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "管理员确认并应用权限" }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认应用权限" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.share-privilege.apply",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvSharePrivilege).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "privilege-approval-token",
    );
  });

  it("previews a bounded SMB change before password approval and apply", async () => {
    const user = userEvent.setup();
    const plan = {
      schema: "echo.omv.smb-share-plan.v1" as const,
      planId: "a".repeat(64),
      baseRevision: "b".repeat(64),
      operation: "update" as const,
      requiresApproval: true,
      shareUuid: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      sharedFolder: { uuid: shareUuid, name: "Family", status: "OK" },
      desired: {
        schema: "echo.omv.smb-share-desired.v1" as const,
        sharedFolderRef: shareUuid,
        enabled: true,
        readOnly: false,
        browseable: true,
        recycleBin: true,
        comment: "Home share",
      },
      changes: [
        {
          field: "recycleBin" as const,
          before: false,
          after: true,
        },
      ],
      safety: { guestAccess: "disabled" },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["smb.share.desired.v1"],
    });
    vi.mocked(planOmvSmbShare).mockResolvedValue(plan);
    vi.mocked(applyOmvSmbShare).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "管理 SMB" }));
    expect(screen.getByText(/SMB 期望状态/)).toBeInTheDocument();
    const recycleBin = screen.getByRole("checkbox", { name: "启用回收站" });
    expect(recycleBin).not.toBeChecked();
    await user.click(recycleBin);
    await user.click(screen.getByRole("button", { name: "预览变更" }));

    await waitFor(() =>
      expect(planOmvSmbShare).toHaveBeenCalledWith(plan.desired),
    );
    expect(screen.getByText("将更新 SMB 规则")).toBeInTheDocument();
    expect(screen.getByText("回收站")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "管理员确认并应用" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认应用" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.smb.apply",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvSmbShare).toHaveBeenCalledWith(
      plan.desired,
      plan.planId,
      "approval-token",
    );
  });

  it("creates a simple shared folder only after preview and password approval", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.shared-folder-desired.v1" as const,
      mountPointRef: "99999999-8888-4777-8666-555555555555",
      name: "Family_Photos",
      comment: "Family photos",
    };
    const plan = {
      schema: "echo.omv.shared-folder-plan.v1" as const,
      planId: "1".repeat(64),
      baseRevision: "2".repeat(64),
      operation: "create" as const,
      requiresApproval: true,
      shareUuid: "22222222-3333-4444-8555-666666666666",
      target: {
        mountPointRef: desired.mountPointRef,
        filesystemUuid,
        label: "Family volume",
        type: "ext4",
        sizeBytes: 2 * 1024 ** 4,
        availableBytes: 1024 ** 4,
        readOnly: false as const,
      },
      desired,
      changes: [
        { field: "name" as const, before: null, after: desired.name },
        { field: "comment" as const, before: null, after: desired.comment },
      ],
      safety: {
        filesystem: "existingMountedWritableOnly" as const,
        relativePath: "derivedFromPortableName" as const,
        directoryMode: "2770UsersGroup" as const,
        acl: "notManaged" as const,
        update: "notManaged" as const,
        delete: "notManaged" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["shared-folder.create.simple.v1"],
    });
    vi.mocked(planOmvSharedFolder).mockResolvedValue(plan);
    vi.mocked(applyOmvSharedFolder).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "folder-approval-token",
      expiresIn: 90,
      action: "omv.shared-folder.create",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    await user.click(
      await screen.findByRole("button", { name: "新建共享文件夹" }),
    );
    const folderName = screen.getByLabelText("文件夹名称");
    await user.type(folderName, "a..b");
    await user.click(screen.getByRole("button", { name: "预览创建" }));
    expect(screen.getByRole("alert")).toHaveTextContent("包含连续两点");
    expect(planOmvSharedFolder).not.toHaveBeenCalled();
    await user.clear(folderName);
    await user.type(folderName, desired.name);
    await user.type(screen.getByLabelText("备注"), desired.comment);
    await user.click(screen.getByRole("button", { name: "预览创建" }));

    await waitFor(() =>
      expect(planOmvSharedFolder).toHaveBeenCalledWith(desired),
    );
    expect(screen.getByText(`将创建 ${desired.name}/`)).toBeInTheDocument();
    expect(screen.getByText(/目录权限 2770/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "管理员确认并创建" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认创建" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.shared-folder.create",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvSharedFolder).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "folder-approval-token",
    );
  });

  it("previews a private NFS rule before password approval and apply", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.nfs-share-desired.v1" as const,
      sharedFolderRef: shareUuid,
      clientCidr: "192.168.1.0/24",
      readOnly: true,
      comment: "Family files",
    };
    const plan = {
      schema: "echo.omv.nfs-share-plan.v1" as const,
      planId: "e".repeat(64),
      baseRevision: "f".repeat(64),
      operation: "create" as const,
      requiresApproval: true,
      shareUuid: "99999999-8888-4777-8666-555555555555",
      sharedFolder: { uuid: shareUuid, name: "Family", status: "OK" },
      desired,
      changes: [
        { field: "readOnly" as const, before: null, after: true },
        { field: "comment" as const, before: null, after: "Family files" },
      ],
      safety: {
        clientScope: "privateCidrOnly" as const,
        rootSquash: "required" as const,
        syncWrites: "required" as const,
        advancedOptions: "notManaged" as const,
        delete: "notManaged" as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["nfs.share.private-network.v1"],
    });
    vi.mocked(fetchOmvSharingOverview).mockResolvedValueOnce({
      ...(await fetchOmvSharingOverview()),
      nfs: { enabled: true, shares: [] },
    });
    vi.mocked(planOmvNfsShare).mockResolvedValue(plan);
    vi.mocked(applyOmvNfsShare).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    render(<OmvSharingPanel />);

    await user.click(await screen.findByRole("button", { name: "启用 NFS" }));
    expect(screen.getByText(/NFS 私网规则/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "预览 NFS 变更" }));
    await waitFor(() => expect(planOmvNfsShare).toHaveBeenCalledWith(desired));
    expect(screen.getByText("将创建 NFS 私网规则")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "管理员确认并应用" }));
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认应用 NFS" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.nfs.apply",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvNfsShare).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "approval-token",
    );
  });

  it("previews an owner-based cross-protocol hard quota before approval", async () => {
    const user = userEvent.setup();
    const desired = {
      schema: "echo.omv.filesystem-quota-desired.v1" as const,
      filesystemUuid,
      subjectType: "user" as const,
      subjectName: "alice",
      hardLimitBytes: 10 * 1024 ** 3,
    };
    const plan = {
      schema: "echo.omv.filesystem-quota-plan.v1" as const,
      planId: "c".repeat(64),
      baseRevision: "d".repeat(64),
      operation: "update" as const,
      requiresApproval: true,
      filesystem: {
        uuid: filesystemUuid,
        label: "Family",
        type: "ext4",
        readOnly: false as const,
        supportsQuota: true as const,
      },
      subject: {
        type: "user" as const,
        name: "alice",
        hardLimitBytes: 0,
        used: "4 MiB",
      },
      desired,
      changes: [
        {
          field: "hardLimitBytes" as const,
          before: 0,
          after: 10 * 1024 ** 3,
        },
      ],
      safety: {
        scope: "filesystemUserOrGroup" as const,
        protocolCoverage: ["local", "SMB", "NFS"] as ["local", "SMB", "NFS"],
        sharedFolderQuota: "notSupportedByOmvQuotaRpc" as const,
        minimumUnitBytes: 1024 as const,
      },
    };
    vi.mocked(fetchOmvStatus).mockResolvedValueOnce({
      configured: true,
      available: true,
      readOnly: false,
      adminUrl: "https://nas.example.test",
      capabilities: ["filesystem.quota.user-group.v1"],
    });
    vi.mocked(planOmvFilesystemQuota).mockResolvedValue(plan);
    vi.mocked(applyOmvFilesystemQuota).mockResolvedValue({
      ...plan,
      applied: true,
      verified: true,
    });
    vi.mocked(requestHighRiskApproval).mockResolvedValueOnce({
      approvalToken: "quota-approval-token",
      expiresIn: 90,
      action: "omv.quota.apply",
      target: plan.planId,
    });
    render(<OmvSharingPanel />);

    expect(await screen.findByText("文件系统硬配额")).toBeInTheDocument();
    expect(
      screen.getByText(/这不是某个共享文件夹的独立空间上限/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("文件系统")).toHaveValue(filesystemUuid);
    expect(screen.getByLabelText("用户")).toHaveValue("alice");
    expect(screen.getByLabelText("硬限制（GiB）")).toHaveValue(10);
    await user.click(screen.getByRole("button", { name: "预览配额变更" }));

    await waitFor(() =>
      expect(planOmvFilesystemQuota).toHaveBeenCalledWith(desired),
    );
    expect(screen.getByText("将更新用户 alice 的硬配额")).toBeInTheDocument();
    expect(screen.getByText("不限制")).toBeInTheDocument();
    expect(screen.getByText("10 GiB")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "管理员确认并应用配额" }),
    );
    await user.type(screen.getByLabelText("设备管理员密码"), "device-password");
    await user.click(screen.getByRole("button", { name: "确认应用配额" }));

    await waitFor(() =>
      expect(requestHighRiskApproval).toHaveBeenCalledWith(
        "omv.quota.apply",
        plan.planId,
        "device-password",
      ),
    );
    expect(applyOmvFilesystemQuota).toHaveBeenCalledWith(
      desired,
      plan.planId,
      "quota-approval-token",
    );
  });
});
