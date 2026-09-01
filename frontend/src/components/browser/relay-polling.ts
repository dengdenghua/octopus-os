export const RELAY_STATUS_REFRESH_MS = 3000;
export const RELAY_AUTH_RETRY_MS = 60000;
export const RELAY_MAX_RETRY_MS = 30000;

export function getRelayStatusRetryDelay(
  status: number | null,
  consecutiveFailures: number,
): number {
  if (status === 401 || status === 403) return RELAY_AUTH_RETRY_MS;

  const failureCount = Math.max(1, consecutiveFailures);
  return Math.min(
    RELAY_STATUS_REFRESH_MS * 2 ** failureCount,
    RELAY_MAX_RETRY_MS,
  );
}
