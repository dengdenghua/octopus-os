/** React Query hooks for the oct account gateway. */
import { swallow } from "@/core/utils/log";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { useAuth } from "@/providers/AuthProvider";

import {
  OctApiError,
  extractOctGoods,
  octApi,
  type OctGoods,
  type OctLink,
  type OctOrder,
} from "./api";

const octKey = ["account", "oct"] as const;
const dailyClaimKey = ["account", "oct", "daily-claim"] as const;
const goodsKey = ["account", "oct", "goods"] as const;

function octLinkKey(userId: string | null) {
  return [...octKey, userId ?? "anonymous"] as const;
}

/**
 * 归一:把 oct 网关余额(credits/paidCredits/giftCredits/membershipActive)补出消费者历史读的
 * 兼容字段 surplusCredits(总余额)+ isMember,使积分徽章/会员判断零改动可用。
 */
function normalize(link: OctLink): OctLink {
  const c = link.credits || {};
  const total =
    typeof c.credits === "number"
      ? c.credits
      : (typeof c.paidCredits === "number" ? c.paidCredits : 0) +
        (typeof c.giftCredits === "number" ? c.giftCredits : 0);
  return {
    ...link,
    credits: {
      ...c,
      surplusCredits: total,
      isMember: Boolean(c.membershipActive),
    },
  };
}

/**
 * 读缓存的 oct 绑定(轻量轮询 60s)。404(未登录绑定)/503(网关关)→ null,
 * 让调用方渲染空态而非报错。登录态变化时失效缓存。
 */
export function useOctLink() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const userId = user?.user_id ?? user?.actor_id ?? null;

  useEffect(() => {
    void qc.invalidateQueries({ queryKey: octKey });
  }, [userId, qc]);

  return useQuery<OctLink | null>({
    queryKey: octLinkKey(userId),
    queryFn: async () => {
      try {
        const link = await octApi.get();
        if (!link) return null;
        // 登录后首访 credits 可能为空 → 刷一次拿真实余额
        if (typeof link.credits?.credits !== "number") {
          try {
            return normalize(await octApi.refresh());
          } catch (e) {
            swallow(e);
            return normalize(link);
          }
        }
        return normalize(link);
      } catch (err) {
        swallow(err);
        if (
          err instanceof OctApiError &&
          (err.status === 404 || err.status === 503)
        ) {
          return null;
        }
        throw err;
      }
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

/** 手动强刷(拉网关 balance + membership)。 */
export function useRefreshOctCredits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => octApi.refresh(),
    onSuccess: (data) => {
      qc.setQueriesData({ queryKey: octKey }, normalize(data));
    },
  });
}

/**
 * 今日免费额度状态。oct 网关无独立 info 接口 —— 从 membership 派生
 * (dailyFreeRemaining / dailyFreeCredits)。仅 link 存在时启用。
 */
export function useDailyClaimInfo(enabled = true) {
  return useQuery({
    queryKey: dailyClaimKey,
    enabled,
    queryFn: async () => {
      try {
        const m = await octApi.membership();
        const remaining = Number(
          (m as Record<string, unknown>).dailyFreeRemaining ?? 0,
        );
        const total = Number(
          (m as Record<string, unknown>).dailyFreeCredits ?? 0,
        );
        return {
          data: {
            claimedToday: remaining <= 0,
            canDraw: false,
            directCredits: total,
            fixedCredits: total,
          },
        };
      } catch (err) {
        swallow(err);
        if (
          err instanceof OctApiError &&
          (err.status === 404 || err.status === 503)
        ) {
          return null;
        }
        throw err;
      }
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** 每日签到领免费积分。成功后失效积分 + 签到缓存。 */
export function useClaimDailyCredits() {
  const qc = useQueryClient();
  return useMutation<Record<string, unknown>, Error, boolean>({
    mutationFn: () => octApi.dailyClaim(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dailyClaimKey });
      qc.invalidateQueries({ queryKey: octKey });
    },
  });
}

/** oct 商品目录(/billing/goods → { items })。仅 link 存在时启用。 */
export function useOctGoods(enabled = true) {
  return useQuery<OctGoods[]>({
    queryKey: goodsKey,
    enabled,
    queryFn: async () => {
      try {
        return extractOctGoods(await octApi.goods());
      } catch (err) {
        swallow(err);
        if (
          err instanceof OctApiError &&
          (err.status === 404 || err.status === 503)
        ) {
          return [];
        }
        throw err;
      }
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** 下单充值(返回 payUrl / Stripe 收银台 + orderNo 供轮询)。 */
export function useCreateOrder() {
  return useMutation<
    OctOrder,
    Error,
    { goodsId: string; currency?: "CNY" | "USD" }
  >({
    mutationFn: ({ goodsId, currency }) =>
      octApi.orders.create(goodsId, currency ?? "CNY"),
  });
}

/** 查单(status=PAID 时后端顺手刷余额);支付完成失效积分。 */
export function useFindOrder(orderNo: string | null) {
  const qc = useQueryClient();
  return useQuery<OctOrder>({
    queryKey: [...goodsKey, "order", orderNo],
    enabled: Boolean(orderNo),
    queryFn: async () => {
      const o = await octApi.orders.findByOrderNo(orderNo as string);
      if (o?.status === "PAID") {
        qc.invalidateQueries({ queryKey: octKey });
      }
      return o;
    },
    staleTime: 10_000,
  });
}
