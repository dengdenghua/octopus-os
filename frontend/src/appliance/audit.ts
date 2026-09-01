import { approvalHeader } from "@/appliance/approval";
import { authHeader } from "@/appliance/auth";

export type AuditKeyStatus = {
  schema: string;
  activeKeyId: string;
  keyIds: string[];
  keyCount: number;
  maximumKeys: number;
  secretsPersisted: false;
};

export type AuditAnchor = {
  schema: string;
  createdAt: string;
  audit: {
    entries: number;
    tailSeq: number;
    tailMac: string;
    tailKeyId: string;
    logSha256: string | null;
    checkpointSha256: string | null;
    keyringSha256: string | null;
  };
  signing: {
    algorithm: "Ed25519";
    keyId: string;
    publicKey: string;
  };
  signature: string;
};

async function requireJson<T>(
  response: Response,
  fallback: string,
): Promise<T> {
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    throw new Error(detail || fallback);
  }
  return (await response.json()) as T;
}

export async function fetchAuditKeyStatus(): Promise<AuditKeyStatus> {
  return requireJson(
    await fetch("/api/appliance/audit/keys", { headers: authHeader() }),
    "无法读取审计密钥状态",
  );
}

export async function fetchAuditAnchor(): Promise<AuditAnchor> {
  return requireJson(
    await fetch("/api/appliance/audit/anchor", { headers: authHeader() }),
    "无法生成审计锚点",
  );
}

export async function rotateAuditKey(
  approvalToken: string,
): Promise<
  AuditKeyStatus & { previousKeyId: string; rotationEventSeq: number }
> {
  return requireJson(
    await fetch("/api/appliance/audit/keys/rotate", {
      method: "POST",
      headers: { ...authHeader(), ...approvalHeader(approvalToken) },
    }),
    "无法轮换审计密钥",
  );
}
