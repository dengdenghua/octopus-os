import { getBackendBaseURL } from "@/core/config";

export async function reflexFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, init);
  if (!res.ok) {
    throw new Error(
      `Reflex API ${path} failed: ${res.status} ${res.statusText}`,
    );
  }
  return res.json() as Promise<T>;
}
