import { getControlPlaneBaseURL } from "@/core/config";

const PROBE_TIMEOUT_MS = 2_000;
const READY_POLL_MS = 500;
const STARTUP_WAIT_MS = 30_000;

export async function probeBackendAvailability(): Promise<boolean> {
  try {
    const response = await fetch(`${getControlPlaneBaseURL()}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function waitForBackendAvailability({
  maxWaitMs = STARTUP_WAIT_MS,
  probe = probeBackendAvailability,
  sleep = (delayMs: number) =>
    new Promise<void>((resolve) => window.setTimeout(resolve, delayMs)),
}: {
  maxWaitMs?: number;
  probe?: () => Promise<boolean>;
  sleep?: (delayMs: number) => Promise<void>;
} = {}): Promise<void> {
  const deadline = Date.now() + maxWaitMs;
  do {
    if (await probe()) return;
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await sleep(Math.min(READY_POLL_MS, remaining));
  } while (Date.now() < deadline);

  throw new Error("Backend did not become ready");
}
