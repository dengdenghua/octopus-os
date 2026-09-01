import { useEffect, useState } from "react";
import { BadgeCheckIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  buyMarketItem,
  type MarketItem,
} from "@/components/workspace/market/market-data";
import { getCommunityCredits } from "@/core/credits/ledger";

/** 商品详情弹层：大图 + 描述 + 购买。 */
export function MarketDetail({
  item,
  onClose,
  onSold,
}: {
  item: MarketItem;
  onClose: () => void;
  onSold: () => void;
}) {
  const [balance] = useState(() => getCommunityCredits());
  const enough = balance >= item.price;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleBuy = () => {
    if (item.sold || item.mine) return;
    const res = buyMarketItem(item.id);
    if (res.ok) {
      toast.success(`已购得「${item.title}」，扣 ${res.need} 积分`);
      onSold();
      onClose();
    } else if (res.need > 0 && !enough) {
      toast.error("社区积分不足，先去签到或创作赚积分吧");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-center bg-background/95 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-md flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative shrink-0">
          <img
            src={item.cover}
            alt={item.title}
            className="aspect-[3/4] w-full object-cover"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="absolute right-3 top-3 rounded-full bg-black/40 p-1.5 text-white transition hover:bg-black/60"
          >
            <XIcon className="size-4" />
          </button>
          {item.sold && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/55">
              <span className="rotate-[-12deg] rounded border-2 border-white/80 px-4 py-1 text-sm font-bold tracking-widest text-white">
                已售出
              </span>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div className="flex items-center gap-1.5 text-mini text-muted-foreground">
            <span
              className="flex size-4 items-center justify-center rounded-full text-micro font-bold text-white"
              style={{ backgroundColor: item.sellerColor }}
            >
              {item.sellerInitial}
            </span>
            <span>{item.seller}</span>
            {item.mine && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-micro">我的</span>
            )}
          </div>
          <h2 className="mt-2 text-lg font-bold leading-snug">{item.title}</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
            {item.desc}
          </p>
        </div>

        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border-subtle bg-background px-5 py-3">
          <div>
            <p className="text-mini text-muted-foreground">售价</p>
            <p className="flex items-baseline gap-0.5 text-lg font-bold text-rose-500">
              {item.price}
              <span className="text-xs font-medium text-rose-400">积分</span>
            </p>
          </div>
          <button
            type="button"
            onClick={handleBuy}
            disabled={item.sold || item.mine}
            className={cn(
              "flex items-center gap-1.5 rounded-lg px-5 py-2 text-sm font-semibold transition-colors",
              item.sold || item.mine
                ? "cursor-default bg-muted text-muted-foreground/60"
                : "bg-rose-500 text-white hover:bg-rose-600",
            )}
          >
            <BadgeCheckIcon className="size-4" />
            {item.mine ? "这是你的商品" : item.sold ? "已售出" : "立即购买"}
          </button>
        </div>
      </div>
    </div>
  );
}
