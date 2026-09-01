/**
 * Echo OS · Echo Storage 前端 API 客户端。
 *
 * Storage 是 sibling 项目(本地安全数据小脑 / File Agent),OS 不拥有自己的
 * 文档索引,只通过窄 HTTP API 调用它。所有接口 best-effort + self-gating:
 * storage 未启动/未配置时返回可用性信息,不崩溃页面。
 */

const DEFAULT_URL = "http://127.0.0.1:8767";
const LEGACY_STORAGE_ROLE = "octo" + "pus-storage";

function baseUrl(): string {
  const env =
    typeof window !== "undefined" ? window.__ECHO_STORAGE_URL__ : undefined;
  return (env || DEFAULT_URL).replace(/\/$/, "");
}

declare global {
  interface Window {
    /** 部署/后端可注入,指向外部 storage 服务地址。 */
    __ECHO_STORAGE_URL__?: string;
  }
}

export type StorageHit = {
  path: string;
  title: string;
  snippet: string;
  score?: number;
  citation?: Record<string, unknown>;
};

export type StorageSearchResult = {
  ok: boolean;
  available: boolean;
  query?: string;
  mode?: string;
  hits: StorageHit[];
  count: number;
  message?: string;
  error?: string;
};

export type StorageAnswerResult = {
  ok: boolean;
  available: boolean;
  answer?: string;
  citations?: StorageHit[];
  error?: string;
};

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T | null> {
  try {
    const res = await fetch(`${baseUrl()}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });
    if (!res.ok) return null;
    const text = await res.text();
    return text ? (JSON.parse(text) as T) : null;
  } catch {
    return null;
  }
}

export async function probeStorage(): Promise<{ available: boolean }> {
  const data = await request<{ role?: string }>("/v1/manifest", {
    method: "GET",
  });
  return {
    available:
      data?.role === "local-secure-data-cerebellum" ||
      data?.role === "echo-storage" ||
      data?.role === LEGACY_STORAGE_ROLE,
  };
}

export async function searchStorage(
  query: string,
  topK = 8,
): Promise<StorageSearchResult> {
  if (!query.trim()) {
    return {
      ok: false,
      available: true,
      hits: [],
      count: 0,
      error: "query is required",
    };
  }
  const data = await request<{
    hits?: StorageHit[];
    mode?: string;
    message?: string;
  }>("/v1/search", {
    method: "POST",
    body: JSON.stringify({ query: query.trim(), top_k: topK }),
  });
  if (data === null) {
    return {
      ok: false,
      available: false,
      hits: [],
      count: 0,
      message:
        "本地文档库(Echo Storage)未运行或不可达。请先启动 Storage 服务并在文件管家中配置授权目录。",
    };
  }
  const hits = (data.hits || []).map((h) => ({
    ...h,
    snippet: h.snippet || "",
  }));
  return {
    ok: true,
    available: true,
    query: query.trim(),
    mode: data.mode,
    hits,
    count: hits.length,
    message: data.message,
  };
}

export async function answerStorage(
  query: string,
  topK = 8,
): Promise<StorageAnswerResult> {
  if (!query.trim()) {
    return {
      ok: false,
      available: true,
      error: "query is required",
    };
  }
  const data = await request<{
    answer?: string;
    citations?: StorageHit[];
  }>("/v1/answer", {
    method: "POST",
    body: JSON.stringify({ query: query.trim(), top_k: topK }),
  });
  if (data === null) {
    return {
      ok: false,
      available: false,
      error:
        "本地文档库(Echo Storage)未运行或不可达。请先启动 Storage 服务并在文件管家中配置授权目录。",
    };
  }
  return {
    ok: true,
    available: true,
    answer: data.answer,
    citations: data.citations,
  };
}
