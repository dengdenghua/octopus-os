import { useCallback, useEffect, useRef } from "react";

import {
  fetchNativeHealth,
  fetchOmvUpsStatus,
  type OmvHealthSnapshot,
  type OmvUpsSnapshot,
} from "@/appliance/omv";
import { useNotification } from "@/core/notification/hooks";

const POLL_INTERVAL_MS = 60_000;
const STORAGE_POLL_EVERY = 5;
const NOTIFIED_IDS_KEY = "echo.nas-alerts.notified-active.v1";
const MAX_TRACKED_IDS = 256;

export type NasAlertCandidate = {
  id: string;
  severity: "warning" | "critical";
  message: string;
};

export function storageAlertCandidates(
  snapshot: OmvHealthSnapshot,
): NasAlertCandidate[] {
  return snapshot.activeAlerts.slice(0, 128).map((alert) => ({
    id: `storage:${alert.id}`,
    severity: alert.severity,
    message: String(alert.message || alert.resource).slice(0, 512),
  }));
}

export function upsAlertCandidates(
  snapshot: OmvUpsSnapshot,
): NasAlertCandidate[] {
  if (!snapshot.configured) return [];
  return snapshot.devices.flatMap<NasAlertCandidate>((device) => {
    const charge =
      device.chargePercent == null ? "" : `（电量 ${device.chargePercent}%）`;
    switch (device.state) {
      case "onBattery":
        return [
          {
            id: `ups:${device.name}:onBattery`,
            severity: "warning" as const,
            message: `UPS ${device.name} 已切换到电池供电${charge}`,
          },
        ];
      case "lowBattery":
        return [
          {
            id: `ups:${device.name}:lowBattery`,
            severity: "critical" as const,
            message: `UPS ${device.name} 电量低${charge}`,
          },
        ];
      case "replaceBattery":
        return [
          {
            id: `ups:${device.name}:replaceBattery`,
            severity: "critical" as const,
            message: `UPS ${device.name} 报告需要更换电池`,
          },
        ];
      case "shutdownPending":
        return [
          {
            id: `ups:${device.name}:shutdownPending`,
            severity: "critical" as const,
            message: `UPS ${device.name} 已发出强制关机信号`,
          },
        ];
      case "offline":
      case "unknown":
        return [
          {
            id: `ups:${device.name}:${device.state}`,
            severity: "warning" as const,
            message: `UPS ${device.name} 状态${device.state === "offline" ? "离线" : "未知"}`,
          },
        ];
      default:
        return [];
    }
  });
}

export function buildNasNotification(candidates: NasAlertCandidate[]): {
  title: string;
  body: string;
  requireInteraction: boolean;
} | null {
  if (candidates.length === 0) return null;
  const critical = candidates.some((item) => item.severity === "critical");
  const messages = candidates.slice(0, 3).map((item) => item.message);
  if (candidates.length > messages.length) {
    messages.push(`另有 ${candidates.length - messages.length} 项告警`);
  }
  return {
    title: critical ? "NAS 严重告警" : "NAS 状态提醒",
    body: messages.join("\n").slice(0, 2048),
    requireInteraction: critical,
  };
}

function readNotifiedIds(): Set<string> {
  try {
    const raw = window.localStorage.getItem(NOTIFIED_IDS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed
        .filter(
          (value): value is string =>
            typeof value === "string" &&
            value.length <= 256 &&
            /^(storage|ups):/.test(value),
        )
        .slice(0, MAX_TRACKED_IDS),
    );
  } catch {
    return new Set();
  }
}

function writeNotifiedIds(ids: Set<string>): void {
  try {
    window.localStorage.setItem(
      NOTIFIED_IDS_KEY,
      JSON.stringify([...ids].slice(0, MAX_TRACKED_IDS)),
    );
  } catch {
    // Notification delivery must remain best-effort when storage is blocked.
  }
}

function replaceDomainIds(
  notified: Set<string>,
  prefix: "storage:" | "ups:",
  active: NasAlertCandidate[],
): void {
  const activeIds = new Set(active.map((item) => item.id));
  for (const id of notified) {
    if (id.startsWith(prefix) && !activeIds.has(id)) notified.delete(id);
  }
}

export function NasAlertNotifications(): null {
  const { showNotification } = useNotification();
  const notifiedRef = useRef<Set<string> | null>(null);
  const pollCountRef = useRef(0);
  const pollingRef = useRef(false);

  const poll = useCallback(async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    if (notifiedRef.current === null) notifiedRef.current = readNotifiedIds();
    const notified = notifiedRef.current;
    const includeStorage = pollCountRef.current % STORAGE_POLL_EVERY === 0;
    pollCountRef.current += 1;

    try {
      const [upsResult, storageResult] = await Promise.allSettled([
        fetchOmvUpsStatus(),
        includeStorage ? fetchNativeHealth() : Promise.resolve(null),
      ]);
      const active: NasAlertCandidate[] = [];

      if (upsResult.status === "fulfilled") {
        const ups = upsAlertCandidates(upsResult.value);
        replaceDomainIds(notified, "ups:", ups);
        active.push(...ups);
      }
      if (
        storageResult.status === "fulfilled" &&
        storageResult.value !== null
      ) {
        const storage = storageAlertCandidates(storageResult.value);
        replaceDomainIds(notified, "storage:", storage);
        active.push(...storage);
      }

      const unseen = active.filter((item) => !notified.has(item.id));
      const notification = buildNasNotification(unseen);
      if (
        notification &&
        showNotification(notification.title, {
          body: notification.body,
          tag: "echo-nas-health",
          requireInteraction: notification.requireInteraction,
        })
      ) {
        for (const item of unseen) notified.add(item.id);
      }
      writeNotifiedIds(notified);
    } finally {
      pollingRef.current = false;
    }
  }, [showNotification]);

  useEffect(() => {
    void poll();
    const interval = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [poll]);

  return null;
}
