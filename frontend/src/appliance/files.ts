/**
 * Octopus OS NAS 文件管理器(前端 API 客户端)。
 * 对接 /api/appliance/files/*,删除走回收站语义。
 */

import { authHeader } from "@/appliance/auth";

export type FileEntry = {
  name: string;
  path: string; // root 相对路径
  kind: "dir" | "file";
  size: number;
  mtime: number;
};

export type TrashEntry = {
  id: string;
  name: string;
  original: string;
  kind: "dir" | "file";
  trashed_at: number;
};

async function jsonGet<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: authHeader() });
  if (!r.ok) throw new Error((await r.text()) || `GET ${url} → ${r.status}`);
  return (await r.json()) as T;
}

async function jsonPost<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    throw new Error(detail || `POST ${url} → ${r.status}`);
  }
  return (await r.json()) as T;
}

export function listDir(
  path: string,
): Promise<{ path: string; entries: FileEntry[] }> {
  return jsonGet(`/api/appliance/files/list?path=${encodeURIComponent(path)}`);
}

export function trashEntry(path: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/trash", { path });
}

export function listTrash(): Promise<{ entries: TrashEntry[] }> {
  return jsonGet("/api/appliance/files/trash");
}

export function restoreTrash(id: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/trash/restore", {
    id,
  });
}

export function emptyTrash() {
  return jsonPost<{ ok: boolean; emptied: number }>(
    "/api/appliance/files/trash/empty",
    {},
  );
}

export function mkdir(path: string) {
  return jsonPost<{ ok: boolean }>("/api/appliance/files/mkdir", { path });
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}
