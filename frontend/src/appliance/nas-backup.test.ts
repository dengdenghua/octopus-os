import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyNasBackupCredential,
  applyNasBackupRestore,
  applyNasBackupSchedule,
  fetchNasBackupRestoreSets,
  fetchNasBackupRestoreTargets,
  fetchNasBackupSchedule,
  planNasBackupCredential,
  planNasBackupRestore,
  planNasBackupSchedule,
} from "./nas-backup";

vi.mock("./auth", () => ({
  authHeader: () => ({ Authorization: "Bearer operator-token" }),
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("NAS backup API", () => {
  it("uses authenticated status, plan and approval-bound apply endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({}),
    } as Response);
    const desired = {
      schema: "echo.nas-data-backup-schedule.v1" as const,
      enabled: true,
      repository: "/mnt/backup/repository",
      repositoryMount: "/mnt/backup",
    };

    await fetchNasBackupSchedule();
    await planNasBackupCredential({
      schema: "echo.nas-data-backup-credential-desired.v1",
      mode: "initialize",
      repository: desired.repository,
      repositoryMount: desired.repositoryMount,
      password: "correct-horse-battery",
    });
    await applyNasBackupCredential(
      {
        schema: "echo.nas-data-backup-credential-desired.v1",
        mode: "initialize",
        repository: desired.repository,
        repositoryMount: desired.repositoryMount,
        password: "correct-horse-battery",
      },
      "b".repeat(64),
      "credential-once",
    );
    await planNasBackupSchedule(desired);
    await applyNasBackupSchedule(desired, "a".repeat(64), "approval-once");
    const restoreDesired = {
      schema: "echo.nas-data-backup-restore-desired.v2" as const,
      selector: "11111111-2222-4333-8444-555555555555",
      repository: "/mnt/backup/repository",
      repositoryMount: "/mnt/backup",
      targets: [
        {
          sourceSharedFolderRef: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          targetSharedFolderRef: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
        },
      ],
    };
    await fetchNasBackupRestoreSets({
      schema: "echo.nas-data-backup-restore-repository.v1",
      repository: restoreDesired.repository,
      repositoryMount: restoreDesired.repositoryMount,
    });
    await fetchNasBackupRestoreTargets();
    await planNasBackupRestore(restoreDesired);
    await applyNasBackupRestore(
      restoreDesired,
      "c".repeat(64),
      "RESTORE ECHO NAS SET 11111111-2222-4333-8444-555555555555 SNAPSHOT " +
        "d".repeat(64),
      "restore-once",
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/appliance/storage/backups/schedule",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/appliance/storage/backups/credential/plan",
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/appliance/storage/backups/credential/apply",
    );
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer operator-token",
      "X-Echo-Approval": "credential-once",
    });
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      "/api/appliance/storage/backups/schedule/plan",
    );
    expect(fetchMock.mock.calls[4]?.[0]).toBe(
      "/api/appliance/storage/backups/schedule/apply",
    );
    expect(fetchMock.mock.calls[4]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer operator-token",
      "X-Echo-Approval": "approval-once",
    });
    expect(fetchMock.mock.calls[5]?.[0]).toBe(
      "/api/appliance/storage/backups/restore/sets?limit=50",
    );
    expect(fetchMock.mock.calls[6]?.[0]).toBe(
      "/api/appliance/storage/backups/restore/targets",
    );
    expect(fetchMock.mock.calls[7]?.[0]).toBe(
      "/api/appliance/storage/backups/restore/plan",
    );
    expect(fetchMock.mock.calls[8]?.[0]).toBe(
      "/api/appliance/storage/backups/restore/apply",
    );
    expect(fetchMock.mock.calls[8]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer operator-token",
      "X-Echo-Approval": "restore-once",
    });
  });
});
