import { useMemo } from "react";
import { BadgeCheckIcon, ClockIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  formatCount,
  MARKET_CATEGORIES,
  type MarketItem,
} from "@/components/workspace/market/market-data";

/** 商品卡片：闲鱼式大图 + 价格 + 卖家。 */
export function MarketCard({
  item,
  onBuy,
  onOpen,
}: {
  item: MarketItem;
  onBuy: (item: MarketItem) => void;
  onOpen?: (item: MarketItem) => void;
}) {
  const category = MARKET_CATEGORIES.find((entry) => entry.key === item.category);

  return (
    <article
      className="group/card relative flex flex-col overflow-hidden rounded-xl border border-border-subtle bg-card transition-[border-color,transform,box-shadow] duration-base hover:-translate-y-0.5 hover:border-border-default hover:shadow-[var(--shadow-sm)]"
    >
      <button
        type="button"
        onClick={() => onOpen?.(item)}
        aria-label={`查看 ${item.title}`}
        className="relative aspect-[4/3] w-full overflow-hidden bg-muted text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/45"
      >
        <img
          src={item.cover}
          alt={item.title}
          loading="lazy"
          className="h-full w-full object-cover transition-transform duration-slow group-hover/card:scale-[1.035]"
        />
        <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-black/35 to-transparent" />
        {category && (
          <span className="absolute left-2.5 top-2.5 inline-flex items-center gap-1.5 rounded-full border border-white/20 bg-black/40 px-2 py-1 text-[11px] font-medium text-white shadow-sm backdrop-blur-md">
            <span
              className="size-1.5 rounded-full"
              style={{ backgroundColor: category.color }}
            />
            {category.label}
          </span>
        )}
        <span className="absolute bottom-2 left-2.5 inline-flex max-w-[calc(100%-1.25rem)] items-center gap-1 rounded-full bg-black/45 px-2 py-1 text-[10px] text-white/90 backdrop-blur-sm sm:hidden">
          <span
            className="flex size-3.5 shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white"
            style={{ backgroundColor: item.sellerColor }}
          >
            {item.sellerInitial}
          </span>
          <span className="truncate">{item.seller}</span>
        </span>
        {item.sold && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/55">
            <span className="rotate-[-12deg] rounded border-2 border-white/80 px-4 py-1 text-sm font-bold tracking-widest text-white">
              已售出
            </span>
          </div>
        )}
      </button>
      <div className="flex flex-1 flex-col px-3 pb-3 pt-3 sm:px-3.5 sm:pb-3.5">
        <button
          type="button"
          onClick={() => onOpen?.(item)}
          className="line-clamp-2 min-h-9 text-left text-[13px] font-semibold leading-snug text-foreground transition-colors hover:text-primary focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 sm:min-h-0 sm:text-sm"
        >
          {item.title}
        </button>
        <p className="mt-1 hidden min-h-9 text-xs leading-[1.45] text-muted-foreground sm:line-clamp-2">
          {item.desc}
        </p>
        <div className="mt-2.5 flex items-center justify-between gap-2 sm:mt-3 sm:gap-3">
          <div className="flex items-baseline gap-1">
            <span className="text-lg font-bold tracking-tight tabular-nums text-foreground sm:text-xl">
              {item.price}
            </span>
            <span className="text-[11px] font-medium text-foreground/60">积分</span>
          </div>
          <button
            type="button"
            onClick={() => onBuy(item)}
            disabled={item.sold || item.mine}
            className={cn(
              "flex min-h-9 min-w-0 items-center justify-center rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-[background-color,transform,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 active:scale-[0.97] sm:min-h-8 sm:min-w-[5.25rem] sm:px-3",
              item.sold || item.mine
                ? "cursor-default bg-muted text-muted-foreground/60"
                : "bg-rose-500 text-white shadow-sm hover:bg-rose-600 hover:shadow-md",
            )}
          >
            {item.mine ? (
              "我的"
            ) : item.sold ? (
              "已售"
            ) : (
              <>
                <span className="sm:hidden">购买</span>
                <span className="hidden sm:inline">立即购买</span>
              </>
            )}
          </button>
        </div>
        <div className="mt-3 hidden items-center gap-1.5 border-t border-border-subtle pt-2.5 text-[11px] text-muted-foreground sm:flex">
          <span
            className="flex size-3.5 items-center justify-center rounded-full text-[9px] font-bold text-white"
            style={{ backgroundColor: item.sellerColor }}
          >
            {item.sellerInitial}
          </span>
          <span className="min-w-0 flex-1 truncate">{item.seller}</span>
        </div>
      </div>
    </article>
  );
}

/** 集市商品网格（响应式列数）。 */
export function MarketGrid({
  items,
  onBuy,
  onOpen,
}: {
  items: MarketItem[];
  onBuy: (item: MarketItem) => void;
  onOpen?: (item: MarketItem) => void;
}) {
  const cols = useMemo(() => {
    if (items.length >= 8)
      return "grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4";
    if (items.length >= 4) return "grid-cols-2 xl:grid-cols-3";
    return "grid-cols-1 min-[360px]:grid-cols-2 xl:grid-cols-3";
  }, [items.length]);

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-24 text-center">
        <BadgeCheckIcon className="size-8 text-muted-foreground/40" />
        <p className="text-sm text-muted-foreground">这个分类还没有商品</p>
        <p className="text-xs text-muted-foreground/60">
          去上架一件好物，赚点积分吧
        </p>
      </div>
    );
  }

  return (
    <div className={cn("grid gap-3 sm:gap-4 xl:gap-5", cols)}>
      {items.map((item) => (
        <MarketCard key={item.id} item={item} onBuy={onBuy} onOpen={onOpen} />
      ))}
    </div>
  );
}

/** 顶部余额条：当前社区积分 + 集市入口提示。 */
export function MarketBalanceBar({ balance }: { balance: number }) {
  return (
    <div className="flex items-center justify-between rounded-xl px-4 py-2.5 text-sm">
      <span className="flex items-center gap-2 text-muted-foreground">
        <ClockIcon className="size-4" />
        社区积分余额
      </span>
      <span className="text-base font-bold tabular-nums text-foreground">
        {formatCount(Math.max(0, balance))}
        <span className="ml-1 text-xs font-medium text-muted-foreground">
          积分
        </span>
      </span>
    </div>
  );
}
