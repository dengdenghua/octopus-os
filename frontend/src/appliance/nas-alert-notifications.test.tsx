import { describe, expect, it } from "vitest";

import type { OmvHealthSnapshot, OmvUpsSnapshot } from "./omv";
import {
  buildNasNotification,
  storageAlertCandidates,
  upsAlertCandidates,
} from "./nas-alert-notifications";

describe("NAS alert notification classification", () => {
  it("uses stable storage alert ids and retains severity", () => {
    const snapshot = {
      activeAlerts: [
        {
          id: "disk-a",
          code: "smart.failed",
          severity: "critical",
          resource: "/dev/sda",
          message: "磁盘异常",
          firstSeenAt: "2026-01-01T00:00:00Z",
          lastSeenAt: "2026-01-01T00:00:00Z",
          occurrences: 1,
        },
      ],
    } as OmvHealthSnapshot;

    expect(storageAlertCandidates(snapshot)).toEqual([
      {
        id: "storage:disk-a",
        severity: "critical",
        message: "磁盘异常",
      },
    ]);
  });

  it("notifies operational UPS faults but ignores normal or unconfigured UPS", () => {
    const snapshot = {
      configured: true,
      devices: [
        { name: "main", state: "online", chargePercent: 100 },
        { name: "backup", state: "lowBattery", chargePercent: 12 },
      ],
    } as OmvUpsSnapshot;
    expect(upsAlertCandidates(snapshot)).toEqual([
      {
        id: "ups:backup:lowBattery",
        severity: "critical",
        message: "UPS backup 电量低（电量 12%）",
      },
    ]);
    expect(upsAlertCandidates({ ...snapshot, configured: false })).toEqual([]);
  });

  it("aggregates alerts and keeps critical notifications resident", () => {
    expect(
      buildNasNotification([
        { id: "a", severity: "warning", message: "A" },
        { id: "b", severity: "critical", message: "B" },
        { id: "c", severity: "warning", message: "C" },
        { id: "d", severity: "warning", message: "D" },
      ]),
    ).toEqual({
      title: "NAS 严重告警",
      body: "A\nB\nC\n另有 1 项告警",
      requireInteraction: true,
    });
  });
});
