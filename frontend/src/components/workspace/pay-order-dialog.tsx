/* Implementation note. */
import { ArrowLeft, Loader2, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { octApi } from "@/core/oct/api";

const POLL_INTERVAL_MS = 2_000;
const POLL_MAX_ATTEMPTS = 30; // 30 * 2s = 60s window

export interface PayOrderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** WeChat Native URL used as the QR payload. */
  paymentLink: string | null;
  /** Order number passed to findByOrderNo while polling. */
  orderNo: string | null;
  /** Display name shown on the right column. */
  goodsName?: string;
  /** Display amount in yuan (already converted from fen). */
  amountYuan?: string;
}

export function PayOrderDialog({
  open,
  onOpenChange,
  paymentLink,
  orderNo,
  goodsName,
  amountYuan,
}: PayOrderDialogProps) {
  const { t } = useI18n();
  const [polling, setPolling] = useState(false);
  const cancelRef = useRef(false);

  // Reset internal state whenever the dialog is closed so a subsequent
  // open for a different order starts clean.
  useEffect(() => {
    if (!open) {
      setPolling(false);
      cancelRef.current = true;
    } else {
      cancelRef.current = false;
    }
  }, [open]);

  const handleConfirm = async () => {
    if (!orderNo || polling) return;
    setPolling(true);
    cancelRef.current = false;

    for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt++) {
      if (cancelRef.current) {
        setPolling(false);
        return;
      }
      try {
        const o = await octApi.orders.findByOrderNo(orderNo);
        if (o.status === "PAID") {
          toast.success(t.payOrder.paidSuccess);
          setPolling(false);
          onOpenChange(false);
          return;
        }
      } catch (err) {
        // Network/upstream transient — keep trying within the budget.
        // Only bail on a hard failure signaled by repeated errors;
        // otherwise log-and-continue so temporary hiccups don't abort
        // a payment the user just completed.
        if (attempt === POLL_MAX_ATTEMPTS - 1) {
          toast.error(
            err instanceof Error ? err.message : t.payOrder.pollingTimeout,
          );
          setPolling(false);
          return;
        }
      }
      // Sleep between attempts, but cancellable via the close button.
      await new Promise<void>((resolve) => {
        const id = setTimeout(resolve, POLL_INTERVAL_MS);
        // Best-effort: clearTimeout on cancel isn't wired here since
        // the loop checks cancelRef on each iteration anyway.
        void id;
      });
    }

    toast.info(t.payOrder.pollingTimeout);
    setPolling(false);
  };

  const handleClose = () => {
    cancelRef.current = true;
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? undefined : handleClose())}>
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-[880px] p-0 overflow-hidden border-0 gap-0"
      >
        {/* Header bar: back + close */}
        <div className="relative flex items-center px-6 pt-6 pb-4">
          <button
            type="button"
            onClick={handleClose}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition"
          >
            <ArrowLeft className="size-4" />
            <span>{t.payOrder.backToPricing}</span>
          </button>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close"
            className="absolute right-4 top-4 flex size-8 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground transition"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Hidden for a11y — DialogContent requires one accessible name */}
        <DialogTitle className="sr-only">{t.payOrder.title}</DialogTitle>

        <div className="grid gap-5 px-6 pb-6 md:grid-cols-2">
          {/* Left column — QR + description */}
          <div className="rounded-lg bg-muted/40 px-6 py-8 flex flex-col items-center">
            <h2 className="text-2xl font-semibold text-center">
              {t.payOrder.title}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground text-center">
              {t.payOrder.subtitle}
            </p>
            <div className="mt-6 flex size-[260px] items-center justify-center rounded-lg bg-white p-4 shadow-[var(--shadow-xs)]">
              {paymentLink ? (
                <QRCodeSVG
                  value={paymentLink}
                  size={220}
                  // level "M" ~= 15% redundancy — more forgiving for
                  // phone-camera scanning while keeping the code dense
                  // enough for WeChat URLs.
                  level="M"
                  includeMargin={false}
                />
              ) : (
                <div className="text-xs text-muted-foreground">
                  {t.payOrder.qrExpired}
                </div>
              )}
            </div>
          </div>

          {/* Right column — purchase info + confirm button */}
          <div className="flex flex-col">
            <h3 className="text-base font-semibold pb-3 border-b">
              {t.payOrder.purchaseInfo}
            </h3>

            <dl className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">
                  {t.payOrder.planName}:
                </dt>
                <dd className="font-medium">{goodsName || "—"}</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-muted-foreground">{t.payOrder.amount}:</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  ¥{amountYuan || "0"}
                </dd>
              </div>
            </dl>

            <div className="mt-auto pt-6">
              <Button
                type="button"
                onClick={handleConfirm}
                disabled={polling || !orderNo}
                className="h-12 w-full rounded-full text-base font-medium disabled:opacity-50"
              >
                {polling && <Loader2 className="mr-2 size-4 animate-spin" />}
                {polling ? t.payOrder.polling : t.payOrder.confirmPayment}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
