import { authHeader } from "@/appliance/auth";

const DIAGNOSTIC_FILENAME = /^echo-diagnostics-\d{8}T\d{6}Z\.zip$/;
const MAX_DIAGNOSTIC_DOWNLOAD_BYTES = 512 * 1024;

export type ServiceHealthState =
  | "healthy"
  | "warning"
  | "critical"
  | "degraded"
  | "unknown";

export interface ServiceHealthStatus {
  schema: "echo.appliance-diagnostics-services.v1";
  state: ServiceHealthState;
  available: boolean;
  checkedAt: string | null;
  counts: {
    monitored: number;
    expected: number;
    active: number;
    failed: number;
    restarts: number;
  };
  alerts: {
    total: number;
    bySeverity: Record<string, number>;
    codes: string[];
  };
}

function diagnosticFilename(response: Response): string {
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/i.exec(disposition);
  const candidate = match?.[1];
  if (candidate && DIAGNOSTIC_FILENAME.test(candidate)) return candidate;
  return "echo-diagnostics.zip";
}

async function errorDetail(
  response: Response,
  fallback = "无法生成支持诊断包",
): Promise<string> {
  const detail: unknown = await response
    .json()
    .then((body) => body?.detail)
    .catch(() => null);
  return typeof detail === "string" ? detail : fallback;
}

function boundedCount(value: unknown): number | null {
  return Number.isSafeInteger(value) &&
    Number(value) >= 0 &&
    Number(value) <= 1_000_000_000
    ? Number(value)
    : null;
}

export async function fetchServiceHealth(): Promise<ServiceHealthStatus> {
  const response = await fetch("/api/appliance/diagnostics/services", {
    headers: authHeader(),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response, "无法读取系统服务健康状态"));
  }
  const body: unknown = await response.json();
  if (!body || typeof body !== "object") {
    throw new Error("系统服务健康响应格式不正确");
  }
  const candidate = body as Partial<ServiceHealthStatus>;
  const states: ServiceHealthState[] = [
    "healthy",
    "warning",
    "critical",
    "degraded",
    "unknown",
  ];
  const counts = candidate.counts;
  const alerts = candidate.alerts;
  const normalizedCounts = counts
    ? {
        monitored: boundedCount(counts.monitored),
        expected: boundedCount(counts.expected),
        active: boundedCount(counts.active),
        failed: boundedCount(counts.failed),
        restarts: boundedCount(counts.restarts),
      }
    : null;
  if (
    candidate.schema !== "echo.appliance-diagnostics-services.v1" ||
    !candidate.state ||
    !states.includes(candidate.state) ||
    typeof candidate.available !== "boolean" ||
    (candidate.checkedAt !== null && typeof candidate.checkedAt !== "string") ||
    !normalizedCounts ||
    Object.values(normalizedCounts).some((value) => value === null) ||
    !alerts ||
    boundedCount(alerts.total) === null ||
    !alerts.bySeverity ||
    typeof alerts.bySeverity !== "object" ||
    Object.entries(alerts.bySeverity).some(
      ([severity, count]) =>
        !["warning", "critical"].includes(severity) ||
        boundedCount(count) === null,
    ) ||
    !Array.isArray(alerts.codes) ||
    alerts.codes.length > 64 ||
    alerts.codes.some(
      (code) =>
        typeof code !== "string" || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(code),
    )
  ) {
    throw new Error("系统服务健康响应格式不正确");
  }
  return candidate as ServiceHealthStatus;
}

export async function fetchDiagnosticBundle(): Promise<{
  blob: Blob;
  filename: string;
}> {
  const response = await fetch("/api/appliance/diagnostics/bundle", {
    headers: authHeader(),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  if (!response.headers.get("content-type")?.startsWith("application/zip")) {
    throw new Error("诊断包响应格式不正确");
  }
  const blob = await response.blob();
  if (blob.size === 0 || blob.size > MAX_DIAGNOSTIC_DOWNLOAD_BYTES) {
    throw new Error("诊断包大小不符合安全限制");
  }
  return { blob, filename: diagnosticFilename(response) };
}

export async function downloadDiagnosticBundle(): Promise<string> {
  const { blob, filename } = await fetchDiagnosticBundle();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  try {
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
  return filename;
}
