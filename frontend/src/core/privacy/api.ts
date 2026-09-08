import { jsonAuthHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export const AI_MODE_CHANGED = "echo:ai-mode-changed";
export type AiMode = "efficiency" | "privacy";

export async function setSystemAiMode(mode: AiMode): Promise<{ mode: AiMode }> {
  const response = await fetch(`${getBackendBaseURL()}/api/ai-mode`, {
    method: "POST",
    headers: jsonAuthHeaders(),
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `HTTP ${response.status}`,
    );
  }
  const body = (await response.json()) as { mode?: unknown };
  if (body.mode !== "privacy" && body.mode !== "efficiency") {
    throw new Error("系统没有返回有效的隐私设置，请刷新后重试。");
  }
  window.dispatchEvent(new Event(AI_MODE_CHANGED));
  return { mode: body.mode };
}
