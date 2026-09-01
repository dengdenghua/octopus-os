import { useState } from "react";
import {
  Loader2Icon,
  SparklesIcon,
  TrendingUpIcon,
  CalendarIcon,
  UserIcon,
  AlertCircleIcon,
  RefreshCwIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useSubscription,
  useCancelSubscription,
  useProfile,
} from "@/core/account";
import { formatDate } from "@/core/utils/datetime";
import { useI18n } from "@/core/i18n/hooks";
import { useCreateOrder, useOctGoods, useOctLink } from "@/core/oct/hooks";
import type { OctGoods } from "@/core/oct/api";
import { PayOrderDialog } from "@/components/workspace/pay-order-dialog";
import { useAuth } from "@/providers/AuthProvider";

import { resolvePricingAccountLabel } from "./settings-resilience";

export default function SubscriptionSettingsPage() {
  const { t } = useI18n();
  const {
    data: subscription,
    isLoading: subscriptionLoading,
    isError: subscriptionError,
    refetch: refetchSubscription,
  } = useSubscription();
  const cancelSubscription = useCancelSubscription();
  const [cancelOpen, setCancelOpen] = useState(false);

  const isInitialLoading = subscriptionLoading && !subscription;

  // Current-plan label is sourced from the local subscription record;
  // we no longer cross-reference echo's `plans` JSON since the
  // purchase flow itself is now driven by the official account service.
  const effectiveTier = subscription?.tier ?? "free";
  const currentPlan = subscription
    ? {
        name: effectiveTier
          ? String(effectiveTier).toUpperCase()
          : t.settings.subscription.free,
      }
    : { name: t.settings.subscription.free };

  if (isInitialLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-96" />
        <div className="space-y-4">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {subscriptionError && !subscription && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning/30 bg-warning/5 p-4 text-sm text-warning"
        >
          <span>{t.subscriptionSettings.subscriptionUnavailable}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetchSubscription()}
          >
            <RefreshCwIcon className="mr-1.5 size-3.5" />
            {t.subscriptionSettings.reloadSubscription}
          </Button>
        </div>
      )}
      {/* Current Plan Card */}
      {!subscriptionError && (
        <div className="rounded-lg border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  "flex size-10 items-center justify-center rounded-lg",
                  subscription?.tier &&
                    ["pro", "max"].includes(subscription.tier)
                    ? "bg-gradient-to-br from-violet-500 to-blue-500 text-white"
                    : "bg-muted text-muted-foreground",
                )}
              >
                <SparklesIcon className="size-5" />
              </div>
              <div>
                <span className="font-semibold">
                  {currentPlan?.name || t.settings.subscription.free}
                </span>
                <p className="text-muted-foreground text-xs mt-0.5">
                  {effectiveTier === "free"
                    ? t.settings.subscription.freeTierDesc
                    : t.settings.subscription.paidTierDesc}
                </p>
              </div>
            </div>

            {effectiveTier !== "free" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCancelOpen(true)}
                disabled={cancelSubscription.isPending}
                className="h-8 text-xs"
              >
                {cancelSubscription.isPending && (
                  <Loader2Icon className="mr-1 size-3 animate-spin" />
                )}
                {t.settings.subscription.cancel}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="default"
                className="h-8 text-xs"
                onClick={() => {
                  const el = document.querySelector(
                    '[data-slot="dialog-content"] [data-subscription-pricing]',
                  );
                  el?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                <TrendingUpIcon className="mr-1 size-3.5" />
                {t.subscriptionSettings.upgradeNow}
              </Button>
            )}
          </div>

          {subscription?.expires_at && (
            <div className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
              <CalendarIcon className="size-3.5" />
              <span>
                {t.settings.subscription.expiresOn(
                  formatDate(subscription.expires_at),
                )}
              </span>
              {subscription?.auto_renew && (
                <Badge variant="outline" className="text-xs ml-2">
                  {t.settings.subscription.autoRenewal}
                </Badge>
              )}
            </div>
          )}
        </div>
      )}

      {/* Pricing plans sourced live from the account service. */}
      <OfficialPricingSection />

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{t.subscriptionSettings.cancelTitle}</DialogTitle>
            <DialogDescription>
              {t.subscriptionSettings.cancelDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setCancelOpen(false)}>
              {t.subscriptionSettings.keepPlan}
            </Button>
            <Button
              variant="destructive"
              disabled={cancelSubscription.isPending}
              aria-busy={cancelSubscription.isPending}
              onClick={() =>
                cancelSubscription.mutate(undefined, {
                  onSuccess: () => setCancelOpen(false),
                })
              }
            >
              {cancelSubscription.isPending && (
                <Loader2Icon className="mr-1.5 size-3.5 animate-spin" />
              )}
              {t.subscriptionSettings.confirmCancel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* Implementation note. */
function formatCreditsSummary(
  credits: number | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (typeof credits !== "number" || credits <= 0) return "";
  return t.subscriptionSettings.totalCredits(credits.toLocaleString());
}

function goodsUnit(
  goods: OctGoods,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const days = goods.memberDays ?? 0;
  if (days >= 365) return t.payOrder.perYear;
  if (days >= 28) return t.payOrder.perMonth;
  return t.payOrder.oneTime;
}

interface PayState {
  open: boolean;
  paymentLink: string | null;
  orderNo: string | null;
  goodsName?: string;
  amountYuan?: string;
}

function OfficialPricingSection() {
  const { t } = useI18n();
  const { user } = useAuth();
  const link = useOctLink();
  const linked = Boolean(link.data);
  const goodsQuery = useOctGoods(linked);
  const createOrder = useCreateOrder();
  const { data: profile } = useProfile();
  const accountLabel = resolvePricingAccountLabel({
    profileName: profile?.display_name || profile?.username,
    userName: user?.username,
    userEmail: user?.email,
    fallback: t.auth.currentAccount,
  });
  const isLoggedIn = Boolean(accountLabel);
  const [pay, setPay] = useState<PayState>({
    open: false,
    paymentLink: null,
    orderNo: null,
  });
  const [pendingId, setPendingId] = useState<string | null>(null);

  const onBuy = async (g: OctGoods) => {
    setPendingId(g.id);
    try {
      const o = await createOrder.mutateAsync({
        goodsId: g.id,
        currency: "CNY",
      });
      if (!o?.payUrl || !o?.orderNo) {
        toast.error(t.payOrder.goodsFailed);
        return;
      }
      const priceFen = g.priceFen ?? 0;
      setPay({
        open: true,
        paymentLink: o.payUrl,
        orderNo: o.orderNo,
        goodsName: g.title,
        amountYuan: (priceFen / 100).toFixed(priceFen % 100 === 0 ? 0 : 2),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t.payOrder.goodsFailed);
    } finally {
      setPendingId(null);
    }
  };

  // A local login and the official billing link are separate states. Keep the
  // user oriented and provide a recovery action when the bridge is unavailable.
  if (!linked && !link.isLoading) {
    return (
      <div
        className="rounded-lg border border-dashed bg-muted/30 p-6 text-center space-y-4"
        data-subscription-pricing
        role={link.isError ? "alert" : "status"}
      >
        <AlertCircleIcon className="mx-auto size-5 text-muted-foreground" />
        {isLoggedIn ? (
          <>
            <div className="flex items-center justify-center gap-2 text-sm">
              <UserIcon className="size-4 text-muted-foreground" />
              <span className="font-medium">{accountLabel}</span>
              {accountLabel !== t.auth.currentAccount ? (
                <Badge variant="secondary" className="text-xs">
                  {t.auth.currentAccount}
                </Badge>
              ) : null}
            </div>
            <div>
              <p className="text-sm font-medium">
                {t.subscriptionSettings.billingUnavailableTitle}
              </p>
              <p className="mx-auto mt-1 max-w-lg text-xs leading-relaxed text-muted-foreground">
                {t.subscriptionSettings.billingUnavailableDescription}
              </p>
            </div>
          </>
        ) : (
          <div className="text-sm text-muted-foreground">
            {t.auth.notLoggedIn}
          </div>
        )}
        <Button variant="outline" size="sm" onClick={() => void link.refetch()}>
          <RefreshCwIcon className="mr-1.5 size-3.5" />
          {t.subscriptionSettings.reloadBilling}
        </Button>
      </div>
    );
  }

  if (goodsQuery.isLoading || link.isLoading) {
    return (
      <div className="space-y-4" data-subscription-pricing>
        <div className="text-center">
          <h2 className="text-lg font-semibold">
            {t.subscriptionSettings.upgradeTitle}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            {t.payOrder.loadingGoods}
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-56 w-full rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (goodsQuery.isError || goodsQuery.data?.length === 0) {
    return (
      <div
        className="rounded-lg border border-dashed bg-muted/30 p-8 text-center space-y-3"
        data-subscription-pricing
        role={goodsQuery.isError ? "alert" : "status"}
      >
        <p className="text-sm text-muted-foreground">
          {goodsQuery.isError
            ? t.subscriptionSettings.plansUnavailable
            : t.subscriptionSettings.noPlans}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void goodsQuery.refetch()}
        >
          <RefreshCwIcon className="mr-1.5 size-3.5" />
          {t.subscriptionSettings.reloadPlans}
        </Button>
      </div>
    );
  }

  const goods = (goodsQuery.data ?? [])
    .slice()
    .sort((a, b) => (a.priceFen ?? 0) - (b.priceFen ?? 0));

  return (
    <div className="space-y-6" data-subscription-pricing>
      <div className="text-center">
        <h2 className="text-lg font-semibold">
          {t.subscriptionSettings.upgradeTitle}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          {t.subscriptionSettings.upgradeDesc}
        </p>
      </div>

      <ul
        className={cn(
          "grid gap-4",
          goods.length >= 3 ? "md:grid-cols-3" : "md:grid-cols-2",
        )}
      >
        {goods.map((g) => {
          const isRecommended = (g.memberDays ?? 0) >= 365;
          const isPending = pendingId === g.id;
          const priceFen = g.priceFen ?? 0;
          const yuan = (priceFen / 100).toFixed(priceFen % 100 === 0 ? 0 : 2);
          const originalYuan = null;
          return (
            <li
              key={g.id}
              className={cn(
                "relative flex flex-col rounded-lg border p-5 transition-all",
                isRecommended
                  ? "border-chart-1/60 bg-gradient-to-b from-violet-50/50 to-white shadow-[var(--shadow-sm)] dark:from-violet-950/20 dark:to-transparent dark:border-chart-1/40"
                  : "bg-card border-border-default hover:border-border hover:shadow-[var(--shadow-xs)]",
              )}
            >
              {isRecommended && (
                <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-violet-500 to-violet-600 px-2.5 py-0.5 text-xs font-medium text-white shadow-[var(--shadow-xs)]">
                  <span aria-hidden="true">🔥</span> {t.payOrder.recommended}
                </span>
              )}

              <h3 className="text-sm font-bold text-center">{g.title}</h3>

              <div className="mt-3 text-center">
                <span className="text-2xl font-bold tracking-tight">
                  ¥{yuan}
                </span>
                <span className="text-muted-foreground text-xs">
                  /{goodsUnit(g, t)}
                </span>
              </div>
              {originalYuan && (
                <p className="text-muted-foreground text-xs text-center mt-0.5 line-through">
                  ¥{originalYuan}
                </p>
              )}
              {formatCreditsSummary(
                (g.credits ?? 0) + (g.bonusCredits ?? 0),
                t,
              ) && (
                <p className="text-muted-foreground text-xs text-center mt-1">
                  {formatCreditsSummary(
                    (g.credits ?? 0) + (g.bonusCredits ?? 0),
                    t,
                  )}
                </p>
              )}

              <Button
                className={cn(
                  "mt-4 h-9 w-full rounded-lg text-xs font-medium",
                  isRecommended
                    ? "bg-chart-1 text-white hover:bg-chart-1/90"
                    : "bg-foreground text-background hover:bg-foreground/85",
                )}
                disabled={pendingId !== null}
                aria-busy={isPending}
                onClick={() => onBuy(g)}
              >
                {isPending && (
                  <Loader2Icon className="mr-1 size-3 animate-spin" />
                )}
                {t.payOrder.subscribeNow}
              </Button>
            </li>
          );
        })}
      </ul>

      <p className="text-muted-foreground text-center text-xs">
        {t.subscriptionSettings.contactUs}
        <span className="text-foreground font-medium">
          {t.subscriptionSettings.supportEmail}
        </span>
        {t.subscriptionSettings.invoiceHint}
      </p>

      <PayOrderDialog
        open={pay.open}
        onOpenChange={(open) => setPay((p) => ({ ...p, open }))}
        paymentLink={pay.paymentLink}
        orderNo={pay.orderNo}
        goodsName={pay.goodsName}
        amountYuan={pay.amountYuan}
      />
    </div>
  );
}
