import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApertureIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  LinkIcon,
  Loader2Icon,
  MessageCircleMoreIcon,
  QrCodeIcon,
  Share2Icon,
  UnlinkIcon,
} from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { copyTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import {
  clearCachedPublicThreadShare,
  createPublicThreadShare,
  getCachedPublicThreadShare,
  isPublicThreadShareUrl,
  resolvePublicThreadShareUrl,
  revokePublicThreadShare,
} from "@/core/sharing/public-thread-share";
import { cn } from "@/lib/utils";

type CreatedShare = Awaited<ReturnType<typeof createPublicThreadShare>>;
type QrMode = "wechat" | "moments" | "qr";
type ShareAction = QrMode | "copy" | "browser" | "revoke";

interface ShareMenuProps {
  /** Durable thread id used to capture a server-side public snapshot. */
  threadId?: string;
  /** Headline — usually the thread title / task. */
  title: string;
  /** Kept for existing call sites. Public sharing reads the server snapshot. */
  prompt?: string;
  summary?: string;
  footer?: string;
  className?: string;
  /** Render only the icon (compact header placement). */
  iconOnly?: boolean;
  /** Optional private/offline replay export; separated from public sharing. */
  onExportReplay?: () => void;
}

/**
 * WorkBuddy/Tencent-style public sharing for a task. The first channel action
 * lazily captures one privacy-bounded snapshot on the server; later channels
 * reuse that link until the menu unmounts or the owner revokes it.
 */
export function ShareMenu({
  threadId,
  className,
  iconOnly = false,
  onExportReplay,
}: ShareMenuProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState<ShareAction | null>(null);
  const [createdShare, setCreatedShare] = useState<CreatedShare | null>(null);
  const [qrMode, setQrMode] = useState<QrMode>("qr");
  const [qrOpen, setQrOpen] = useState(false);
  const [qrUrl, setQrUrl] = useState("");
  const [qrError, setQrError] = useState("");
  const shareRef = useRef<CreatedShare | null>(null);
  const pendingRef = useRef<Promise<CreatedShare> | null>(null);

  useEffect(() => {
    const cachedShare =
      threadId && threadId !== "new"
        ? getCachedPublicThreadShare(threadId)
        : null;
    shareRef.current = cachedShare;
    pendingRef.current = null;
    setCreatedShare(cachedShare);
    setQrOpen(false);
    setQrUrl("");
    setQrError("");
  }, [threadId]);

  const ensureShare = useCallback(async () => {
    if (!threadId || threadId === "new") {
      throw new Error(t.share.unavailable);
    }
    if (shareRef.current) return shareRef.current;
    if (pendingRef.current) return pendingRef.current;

    const pending = createPublicThreadShare(threadId)
      .then((share) => {
        shareRef.current = share;
        setCreatedShare(share);
        return share;
      })
      .finally(() => {
        pendingRef.current = null;
      });
    pendingRef.current = pending;
    return pending;
  }, [t.share.unavailable, threadId]);

  const prepareUrl = useCallback(async () => {
    const share = await ensureShare();
    return resolvePublicThreadShareUrl(share.share_path, share.share_url);
  }, [ensureShare]);

  const preparePublicUrl = useCallback(async () => {
    const url = await prepareUrl();
    if (!isPublicThreadShareUrl(url)) {
      throw new Error(t.share.localOnlyHint);
    }
    return url;
  }, [prepareUrl, t.share.localOnlyHint]);

  const runAction = useCallback(
    async (action: ShareAction, operation: () => Promise<void>) => {
      setBusy(action);
      try {
        await operation();
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t.share.linkFailed,
        );
      } finally {
        setBusy(null);
      }
    },
    [t.share.linkFailed],
  );

  const handleCopyLink = useCallback(() => {
    void runAction("copy", async () => {
      await copyTextToClipboard(await prepareUrl());
      toast.success(t.share.linkCopied);
    });
  }, [prepareUrl, runAction, t.share.linkCopied]);

  const handleOpenBrowser = useCallback(() => {
    void runAction("browser", async () => {
      const url = await prepareUrl();
      window.open(url, "_blank", "noopener,noreferrer");
    });
  }, [prepareUrl, runAction]);

  const handleQr = useCallback(
    (mode: QrMode) => {
      setQrMode(mode);
      setQrOpen(true);
      setQrUrl("");
      setQrError("");
      void runAction(mode, async () => {
        try {
          setQrUrl(await preparePublicUrl());
        } catch (error) {
          const message =
            error instanceof Error ? error.message : t.share.linkFailed;
          setQrError(message);
          throw error;
        }
      });
    },
    [preparePublicUrl, runAction, t.share.linkFailed],
  );

  const handleStopSharing = useCallback(() => {
    const share = shareRef.current;
    if (!share) return;
    void runAction("revoke", async () => {
      await revokePublicThreadShare(share.share_id);
      if (threadId) clearCachedPublicThreadShare(threadId);
      shareRef.current = null;
      setCreatedShare(null);
      setQrOpen(false);
      setQrUrl("");
      toast.success(t.share.sharingStopped);
    });
  }, [runAction, t.share.sharingStopped, threadId]);

  const qrTitle =
    qrMode === "wechat"
      ? t.share.wechatQrTitle
      : qrMode === "moments"
        ? t.share.momentsQrTitle
        : t.share.qrTitle;
  const qrHint =
    qrMode === "wechat"
      ? t.share.wechatQrHint
      : qrMode === "moments"
        ? t.share.momentsQrHint
        : t.share.qrHint;
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            title={t.share.share}
            aria-label={t.share.share}
            data-slot="share-menu-trigger"
            className={cn(
              "flex h-[42px] items-center gap-1.5 border text-xs font-medium shadow-none transition-colors duration-base sm:h-7",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              iconOnly
                ? "w-[42px] justify-center rounded-lg border-transparent bg-transparent px-0 text-muted-foreground hover:border-border-default hover:bg-muted/55 hover:text-foreground sm:w-8"
                : "rounded-lg border-transparent bg-transparent px-2.5 text-muted-foreground hover:bg-muted/55 hover:text-foreground",
              className,
            )}
          >
            {busy ? (
              <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
            ) : (
              <Share2Icon className="size-4 text-muted-foreground" />
            )}
            {!iconOnly && <span>{t.share.share}</span>}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-[340px] p-2">
          <div className="px-2 pb-2 pt-1">
            <p className="text-sm font-semibold text-foreground">
              {t.share.shareTask}
            </p>
            <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
              {t.share.shareDescription}
            </p>
          </div>
          <div className="grid grid-cols-5 gap-1">
            <ShareChannelItem
              label={t.share.wechat}
              disabled={busy !== null}
              onSelect={() => handleQr("wechat")}
              icon={
                <MessageCircleMoreIcon className="size-[18px] text-white" />
              }
              iconClassName="bg-[#07c160]"
            />
            <ShareChannelItem
              label={t.share.moments}
              disabled={busy !== null}
              onSelect={() => handleQr("moments")}
              icon={<ApertureIcon className="size-[18px] text-white" />}
              iconClassName="bg-[#22a447]"
            />
            <ShareChannelItem
              label={t.share.copyLink}
              disabled={busy !== null}
              onSelect={handleCopyLink}
              icon={<LinkIcon className="size-[18px] text-[#3976f6]" />}
              iconClassName="bg-[#3976f6]/10"
            />
            <ShareChannelItem
              label={t.share.qrCode}
              disabled={busy !== null}
              onSelect={() => handleQr("qr")}
              icon={<QrCodeIcon className="size-[18px] text-foreground" />}
              iconClassName="bg-muted"
            />
            <ShareChannelItem
              label={t.share.openInBrowser}
              disabled={busy !== null}
              onSelect={handleOpenBrowser}
              icon={
                <ExternalLinkIcon className="size-[18px] text-foreground" />
              }
              iconClassName="bg-muted"
            />
          </div>
          {(onExportReplay || createdShare) && <DropdownMenuSeparator />}
          {onExportReplay && (
            <DropdownMenuItem onSelect={() => onExportReplay()}>
              <DownloadIcon className="size-4" />
              {t.share.exportReplay}
            </DropdownMenuItem>
          )}
          {createdShare && (
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              disabled={busy !== null}
              onSelect={handleStopSharing}
            >
              <UnlinkIcon className="size-4" />
              {t.share.stopSharing}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={qrOpen} onOpenChange={setQrOpen}>
        <DialogContent className="max-w-[390px]" closeLabel={t.common.close}>
          <DialogHeader>
            <DialogTitle>{qrTitle}</DialogTitle>
            <DialogDescription>{qrHint}</DialogDescription>
          </DialogHeader>
          <div className="flex min-h-[236px] items-center justify-center rounded-2xl border border-border-default bg-white p-5">
            {qrUrl ? (
              <QRCodeSVG
                value={qrUrl}
                size={196}
                level="M"
                marginSize={1}
                aria-label={qrTitle}
              />
            ) : qrError ? (
              <div className="max-w-64 text-center text-sm text-destructive">
                {qrError}
              </div>
            ) : (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                {t.share.creatingLink}
              </div>
            )}
          </div>
          {qrUrl && (
            <div className="rounded-xl bg-muted/55 px-3 py-2">
              <p
                className="truncate text-xs text-muted-foreground"
                title={qrUrl}
              >
                {qrUrl}
              </p>
            </div>
          )}
          <DialogFooter className="sm:justify-between">
            <Button
              variant="outline"
              disabled={!qrUrl}
              onClick={() => {
                if (!qrUrl) return;
                void copyTextToClipboard(qrUrl)
                  .then(() => toast.success(t.share.linkCopied))
                  .catch(() => toast.error(t.share.linkFailed));
              }}
            >
              <CopyIcon className="size-4" />
              {t.share.copyLink}
            </Button>
            <Button
              disabled={!qrUrl}
              onClick={() => {
                if (qrUrl) window.open(qrUrl, "_blank", "noopener,noreferrer");
              }}
            >
              <ExternalLinkIcon className="size-4" />
              {t.share.openInBrowser}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ShareChannelItem({
  label,
  icon,
  iconClassName,
  disabled,
  onSelect,
}: {
  label: string;
  icon: ReactNode;
  iconClassName: string;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      disabled={disabled}
      onSelect={onSelect}
      className="flex min-w-0 cursor-pointer flex-col gap-1.5 rounded-xl px-1 py-2 text-center focus:bg-muted"
    >
      <span
        className={cn(
          "grid size-9 shrink-0 place-items-center rounded-full",
          iconClassName,
        )}
      >
        {icon}
      </span>
      <span className="w-full truncate text-[11px] leading-4 text-muted-foreground">
        {label}
      </span>
    </DropdownMenuItem>
  );
}
