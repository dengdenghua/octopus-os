/**
 * oct 账号网关 client（echo 自己的网关，echo.aurest.ai）。
 *
 * 邮箱验证码登录 + 积分/会员/用量/商品/订单。所有请求经 agent 后端
 * /api/auth/oct/* 与 /api/account/oct/*(后端用存的网关 JWT 调上游 + 网关计费)。
 * 这是 additive 模块;登录页/消费者切到这里在 ③b。
 */
import { authHeaders } from "@/core/auth/api";
import { getBackendBaseURL } from "@/core/config";

export class OctApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "OctApiError";
  }
}

function messageFromRecord(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  for (const key of ["detail", "message", "error"] as const) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
    const nested = messageFromRecord(candidate);
    if (nested) return nested;
  }
  return null;
}

function embeddedGatewayMessage(raw: string): string | null {
  for (
    let index = raw.indexOf("{");
    index >= 0;
    index = raw.indexOf("{", index + 1)
  ) {
    try {
      const parsed = JSON.parse(raw.slice(index)) as unknown;
      const message = messageFromRecord(parsed);
      if (message) return message;
    } catch {
      // A wrapper can contain non-JSON braces; continue with the next one.
    }
  }
  return null;
}

/** Convert gateway/proxy diagnostics into concise copy suitable for UI. */
export function octErrorMessage(error: unknown, fallback: string): string {
  const raw = error instanceof Error ? error.message.trim() : "";
  if (!raw) return fallback;
  const embedded = embeddedGatewayMessage(raw);
  const concise = (embedded ?? raw)
    .replace(/^oct\s+[^:：]+[:：]\s*/i, "")
    .replace(/^gateway rejected[:：]\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!concise || /<\/?(?:html|body)\b/i.test(concise)) return fallback;
  return concise.length > 240 ? `${concise.slice(0, 239)}…` : concise;
}

async function _request<T>(
  path: string,
  init?: RequestInit & { signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(`${getBackendBaseURL()}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const detail = body?.detail;
    throw new OctApiError(
      res.status,
      typeof detail === "string"
        ? detail
        : (messageFromRecord(detail) ??
            `${init?.method ?? "GET"} ${path} → ${res.status}`),
    );
  }
  return (await res.json()) as T;
}

// ─── 认证(邮箱验证码)───────────────────────────────────

export interface OctEmailSendResponse {
  sent: boolean;
  ttl_seconds: number;
  dev_code?: string | null;
}

export interface OctEmailLoginResponse {
  success: boolean;
  actor_id: string;
  access_token?: string | null;
  token_type: string;
  expires_in?: number | null;
  is_new_user: boolean;
  user: Record<string, unknown>;
  credits: Record<string, unknown>;
}

export const octAuthApi = {
  /** 发送邮箱验证码。 */
  emailSend: (email: string) =>
    _request<OctEmailSendResponse>("/api/auth/oct/email/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }),

  /** 邮箱验证码登录 → 返回会话 access_token(agent 自有 JWT 或网关 JWT)。 */
  emailLogin: (email: string, code: string) =>
    _request<OctEmailLoginResponse>("/api/auth/oct/email/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, code }),
    }),
};

// ─── 账号 / 积分 / 会员 ─────────────────────────────────

/** oct 网关余额(/account/balance)。credits = paid + gift。 */
export interface OctBalance {
  credits?: number;
  paidCredits?: number;
  giftCredits?: number;
  membershipActive?: boolean;
  membershipExpireAt?: number;
  membership?: OctMembership;
  // hooks 归一补出的兼容字段(消费者读总余额/会员态)
  surplusCredits?: number;
  isMember?: boolean;
  // 兼容展示字段：oct 无 credit 分桶/套餐名 → 永不填，使旧渲染块优雅显示空
  // (会员信息改由 OctMembership 提供;消费者可逐步迁到 membership 展示)
  plan?: string;
  modelDisplayName?: string;
  creditsSummary?: {
    by_type?: Record<string, { granted: number; remaining: number }>;
    total_granted?: number;
    total_remaining?: number;
  };
  [key: string]: unknown;
}

export interface OctMembership {
  active?: boolean;
  expireAt?: number;
  remainingDays?: number;
  benefits?: string[];
  dailyFreeCredits?: number;
  dailyFreeRemaining?: number;
  [key: string]: unknown;
}

/** agent /api/account/oct 的返回(后端 OctLinkResponse)。 */
export interface OctLink {
  oct_user_id: string;
  email?: string | null;
  linked_at: number;
  last_synced_at?: number | null;
  credits: OctBalance;
  token_invalid?: boolean;
  token_invalid_reason?: string | null;
}

export interface OctUsage {
  total?: number;
  summary?: {
    tokensIn?: number;
    tokensOut?: number;
    credits?: number;
    calls?: number;
  };
  items?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** oct 网关商品(/billing/goods → { items: [...] })。 */
export interface OctGoods {
  id: string;
  title?: string;
  credits?: number;
  bonusCredits?: number;
  priceFen?: number;
  priceUsdCents?: number;
  memberDays?: number;
  kind?: string;
  [key: string]: unknown;
}

export interface OctGoodsResponse {
  items?: OctGoods[];
  [key: string]: unknown;
}

/** 下单(/billing/orders)。 */
export interface OctOrder {
  orderNo: string;
  status?: string;
  credits?: number;
  payUrl?: string | null;
  amountFen?: number;
  [key: string]: unknown;
}

export const octApi = {
  /** 缓存视图 — 不打上游。null = 未登录绑定。 */
  get: () => _request<OctLink | null>("/api/account/oct"),

  /** 强制刷新(拉网关 balance + membership)。 */
  refresh: () =>
    _request<OctLink>("/api/account/oct/refresh", { method: "POST" }),

  /** 解绑。 */
  unlink: () =>
    _request<{ unlinked: boolean }>("/api/account/oct/link", {
      method: "DELETE",
    }),

  membership: () => _request<OctMembership>("/api/account/oct/membership"),

  usage: (page = 1, pageSize = 50) =>
    _request<OctUsage>(
      `/api/account/oct/usage?page=${page}&page_size=${pageSize}`,
    ),

  /** 每日签到领免费积分。 */
  dailyClaim: () =>
    _request<Record<string, unknown>>("/api/account/oct/daily-claim", {
      method: "POST",
    }),

  goods: () => _request<OctGoodsResponse>("/api/account/oct/goods"),

  orders: {
    /** 下单充值(网关返回 payUrl / Stripe 收银台)。 */
    create: (goodsId: string, currency: "CNY" | "USD" = "CNY") =>
      _request<OctOrder>("/api/account/oct/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goods_id: goodsId, currency }),
      }),

    /** 查单(status=PAID 时后端顺手刷余额)。 */
    findByOrderNo: (orderNo: string) =>
      _request<OctOrder>(
        `/api/account/oct/orders/${encodeURIComponent(orderNo)}`,
      ),
  },
};

/** 商品列表归一为数组。 */
export function extractOctGoods(
  resp: OctGoodsResponse | null | undefined,
): OctGoods[] {
  return Array.isArray(resp?.items) ? resp.items : [];
}
