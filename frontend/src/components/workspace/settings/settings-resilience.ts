export function resolvePricingAccountLabel({
  profileName,
  userName,
  userEmail,
  fallback,
}: {
  profileName?: string | null;
  userName?: string | null;
  userEmail?: string | null;
  fallback: string;
}): string | null {
  return (
    profileName?.trim() || userName?.trim() || userEmail?.trim() || fallback
  );
}

export function getMemoryLoadErrorCopy(locale?: string | null): string {
  const language = (locale || "en").slice(0, 2).toLowerCase();
  if (language === "zh") return "暂时无法读取记忆，请检查服务状态后重试。";
  if (language === "ja")
    return "メモリを読み込めませんでした。サービスの状態を確認して、もう一度お試しください。";
  if (language === "ko")
    return "메모리를 불러오지 못했습니다. 서비스 상태를 확인한 뒤 다시 시도해 주세요.";
  return "Memory could not be loaded. Check the service status and try again.";
}

export function isSupportedMcpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      Boolean(url.hostname)
    );
  } catch {
    return false;
  }
}

export type AiModeDevice =
  | string
  | {
      has_local_model?: boolean;
      has_gpu?: boolean;
      ram_gb?: number;
      cpu_count?: number;
      cloud_reachable?: boolean;
      notes?: string[];
    }
  | null
  | undefined;

export function formatAiModeDevice(
  device: AiModeDevice,
  locale?: string | null,
): string {
  if (typeof device === "string") return device.trim();
  if (!device || typeof device !== "object") return "";

  const language = (locale || "en").slice(0, 2).toLowerCase();
  const labels =
    language === "zh"
      ? { local: "本地模型可用", cloud: "云端可用", offline: "云端不可用" }
      : language === "ja"
        ? {
            local: "ローカルモデル利用可",
            cloud: "クラウド利用可",
            offline: "クラウド利用不可",
          }
        : language === "ko"
          ? {
              local: "로컬 모델 사용 가능",
              cloud: "클라우드 사용 가능",
              offline: "클라우드 사용 불가",
            }
          : {
              local: "Local model available",
              cloud: "Cloud available",
              offline: "Cloud unavailable",
            };

  const parts: string[] = [];
  if (typeof device.ram_gb === "number" && device.ram_gb > 0)
    parts.push(`${device.ram_gb} GB RAM`);
  if (typeof device.cpu_count === "number" && device.cpu_count > 0)
    parts.push(`${device.cpu_count} CPU`);
  if (device.has_gpu) parts.push("GPU");
  if (device.has_local_model) parts.push(labels.local);
  if (typeof device.cloud_reachable === "boolean") {
    parts.push(device.cloud_reachable ? labels.cloud : labels.offline);
  }
  return parts.join(" · ");
}
