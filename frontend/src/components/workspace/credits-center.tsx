import { useMemo, useState } from "react";
import { Coins, Gift, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { useOctLink } from "@/core/oct/hooks";
import {
  claimDailySignIn,
  getCommunityCredits,
  getLedgerSummary,
  getLedgerTxns,
  getSignInStatus,
  LIKE_EARN_REWARD,
  PUBLISH_REWARD,
  SIGN_IN_REWARD,
  FORK_EARN_REWARD,
  type CreditTxn,
} from "@/core/credits/ledger";
import { cn } from "@/lib/utils";

function formatN(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "0";
  return n.toLocaleString();
}

const TXN_LABEL: Record<
  CreditTxn["type"],
  { label: string; positive: boolean }
> = {
  "sign-in": { label: "签到", positive: true },
  publish: { label: "发布", positive: true },
  "fork-earn": { label: "复刻分成", positive: true },
  "like-earn": { label: "互动奖励", positive: true },
  "fork-spend": { label: "复刻消费", positive: false },
  "market-spend": { label: "集市购买", positive: false },
};

/** 积分中心 —— 本地积分账本总览：总余额 / 签到 / 收支明细 / 赚积分指引。 */
export function CreditsCenterDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const { data: oct } = useOctLink();
  const [version, setVersion] = useState(0);

  // 每次打开 / 签到后重读账本。
  const ledgerData = useMemo(() => {
    if (!open) return null;
    const community = getCommunityCredits();
    const summary = getLedgerSummary();
    const txns = getLedgerTxns();
    const signIn = getSignInStatus();
    return { community, summary, txns, signIn };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, version]);

  const octBalance = oct?.credits?.surplusCredits;
  const total = (octBalance ?? 0) + (ledgerData?.community ?? 0);
  const earned =
    (ledgerData?.summary["sign-in"] ?? 0) +
    (ledgerData?.summary.publish ?? 0) +
    (ledgerData?.summary["fork-earn"] ?? 0) +
    (ledgerData?.summary["like-earn"] ?? 0);
  const spent = Math.abs(ledgerData?.summary["fork-spend"] ?? 0);

  const doSignIn = () => {
    const res = claimDailySignIn();
    setVersion((v) => v + 1);
    if (res.claimed) {
      toast.success(t.creditsCenter.signInSuccess(res.amount));
    } else {
      toast.info(t.creditsCenter.signInDone);
    }
  };

  const earnHints = [
    { text: t.creditsCenter.earnHintSignIn(SIGN_IN_REWARD), icon: Gift },
    { text: t.creditsCenter.earnHintPublish(PUBLISH_REWARD), icon: Sparkles },
    { text: t.creditsCenter.earnHintFork(FORK_EARN_REWARD), icon: Coins },
    { text: t.creditsCenter.earnHintLike(LIKE_EARN_REWARD), icon: Coins },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-md overflow-hidden p-0 gap-0 border-0"
      >
        {/* Header */}
        <div className="flex items-center justify-between bg-gradient-to-br from-pink-500 to-rose-500 px-5 py-4">
          <DialogTitle className="text-base font-bold text-white">
            {t.creditsCenter.title}
          </DialogTitle>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
            className="flex size-7 items-center justify-center rounded-md bg-white/15 text-white transition hover:bg-white/25"
          >
            <Sparkles className="size-4" />
          </button>
        </div>

        <div className="space-y-4 bg-card px-5 py-4">
          {/* 总积分 */}
          <div className="rounded-xl bg-muted/50 px-4 py-3">
            <p className="text-xs text-muted-foreground">
              {t.creditsCenter.totalBalance}
            </p>
            <p className="mt-0.5 text-2xl font-bold tabular-nums">
              {formatN(total)}
              <span className="ml-1 text-sm font-medium text-muted-foreground">
                {t.credits.credits}
              </span>
            </p>
            <div className="mt-2 flex gap-2">
              <div className="flex-1 rounded-lg bg-background px-3 py-2">
                <p className="text-mini text-muted-foreground">
                  {t.creditsCenter.accountBalance}
                </p>
                <p className="text-sm font-semibold tabular-nums">
                  {formatN(octBalance)}
                </p>
              </div>
              <div className="flex-1 rounded-lg bg-background px-3 py-2">
                <p className="text-mini text-muted-foreground">
                  {t.creditsCenter.communityBalance}
                </p>
                <p className="text-sm font-semibold tabular-nums">
                  {formatN(ledgerData?.community)}
                </p>
              </div>
            </div>
          </div>

          {/* 签到 */}
          <div className="flex items-center justify-between rounded-xl border border-border-subtle px-4 py-3">
            <div>
              <p className="text-sm font-semibold">{t.creditsCenter.signIn}</p>
              <p className="text-xs text-muted-foreground">
                {t.creditsCenter.earnHintSignIn(SIGN_IN_REWARD)}
              </p>
            </div>
            <Button
              type="button"
              onClick={doSignIn}
              disabled={ledgerData?.signIn.claimedToday}
              className={cn(
                "rounded-lg px-4",
                ledgerData?.signIn.claimedToday &&
                  "cursor-default bg-muted text-muted-foreground",
              )}
            >
              {ledgerData?.signIn.claimedToday
                ? t.creditsCenter.signInDone
                : t.creditsCenter.signIn}
            </Button>
          </div>

          {/* 账本统计 */}
          <div className="flex gap-2 text-center">
            <div className="flex-1 rounded-lg bg-emerald-500/10 px-3 py-2">
              <p className="text-mini text-muted-foreground">
                {t.creditsCenter.earned}
              </p>
              <p className="text-base font-bold tabular-nums text-emerald-600">
                +{formatN(earned)}
              </p>
            </div>
            <div className="flex-1 rounded-lg bg-rose-500/10 px-3 py-2">
              <p className="text-mini text-muted-foreground">
                {t.creditsCenter.spent}
              </p>
              <p className="text-base font-bold tabular-nums text-rose-600">
                -{formatN(spent)}
              </p>
            </div>
          </div>

          {/* 赚积分指引 */}
          <div>
            <p className="mb-2 text-xs text-muted-foreground">
              {t.creditsCenter.earnHints}
            </p>
            <div className="grid grid-cols-2 gap-1.5">
              {earnHints.map((h, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs"
                >
                  <h.icon className="size-3.5 text-primary" />
                  {h.text}
                </div>
              ))}
            </div>
          </div>

          {/* 收支明细 */}
          <div>
            <p className="mb-2 text-xs text-muted-foreground">
              {t.creditsCenter.ledger}
            </p>
            {ledgerData && ledgerData.txns.length > 0 ? (
              <ul className="max-h-56 space-y-1 overflow-y-auto">
                {ledgerData.txns.map((txn) => {
                  const meta = TXN_LABEL[txn.type];
                  const positive = meta.positive;
                  return (
                    <li
                      key={txn.id}
                      className="flex items-center justify-between rounded-lg px-3 py-1.5 text-sm hover:bg-muted/50"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-[13px] text-foreground">
                          {meta.label}
                          <span className="ml-1.5 text-xs text-muted-foreground">
                            {txn.reason}
                          </span>
                        </p>
                        <p className="text-mini text-muted-foreground/70">
                          {new Date(txn.createdAt).toLocaleString()}
                        </p>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 font-semibold tabular-nums",
                          positive ? "text-emerald-600" : "text-rose-600",
                        )}
                      >
                        {positive ? "+" : ""}
                        {txn.amount}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="rounded-lg bg-muted/40 px-3 py-4 text-center text-xs text-muted-foreground">
                {t.creditsCenter.emptyLedger}
              </p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
