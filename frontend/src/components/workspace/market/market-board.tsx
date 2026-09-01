import {
  ArrowRightIcon,
  ClockIcon,
  LayoutGridIcon,
  PlusIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MarketGrid } from "@/components/workspace/market/market-feed";
import { MarketDetail } from "@/components/workspace/market/market-detail";
import { communityAssetURL } from "@/components/workspace/community/community-assets";
import {
  MARKET_CATEGORIES,
  getMarketItems,
  listMarketItem,
  type MarketItem,
} from "@/components/workspace/market/market-data";
import { getCommunityCredits } from "@/core/credits/ledger";
import { cn } from "@/lib/utils";

/** 可选封面（复用社区已生成封面图）。 */
const COVER_OPTIONS = [
  communityAssetURL("price-watch(1).jpg"),
  communityAssetURL("weekly-report(1).jpg"),
  communityAssetURL("resume(1).jpg"),
  communityAssetURL("language-coach.jpg"),
  communityAssetURL("study-paper(1).jpg"),
  communityAssetURL("smart-home.jpg"),
  communityAssetURL("travel-plan(1).jpg"),
  communityAssetURL("game-auto-daily.jpg"),
  communityAssetURL("gacha.jpg"),
  communityAssetURL("meeting-notes.jpg"),
  communityAssetURL("plan-tomorrow.jpg"),
  communityAssetURL("coupon.jpg"),
] as const;

/** 集市面板：嵌入社区页的二级视图，自带搜索/分类/余额/上架/购买。 */
/** 资产引导横幅的 localStorage key(关闭后不再显示)。 */
const ASSETS_BANNER_KEY = "echo.market.assets-banner-dismissed.v1";

function readBannerDismissed(): boolean {
  try {
    return window.localStorage.getItem(ASSETS_BANNER_KEY) === "1";
  } catch {
    return false;
  }
}

export function MarketBoard() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("all");
  const [bannerDismissed, setBannerDismissed] = useState(readBannerDismissed);
  const [query, setQuery] = useState("");
  const [version, setVersion] = useState(0);
  const [detail, setDetail] = useState<MarketItem | null>(null);
  const [listOpen, setListOpen] = useState(false);

  // `version` is intentionally read so mutations can refresh the local store
  // snapshot without pretending the store getter is a memo dependency.
  void version;
  const items = getMarketItems();
  const balance = getCommunityCredits();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      if (activeTab !== "all" && it.category !== activeTab) return false;
      if (!q) return true;
      return (
        it.title.toLowerCase().includes(q) ||
        it.desc.toLowerCase().includes(q) ||
        it.seller.toLowerCase().includes(q)
      );
    });
  }, [items, activeTab, query]);

  return (
    <>
      {!bannerDismissed && (
        <div className="mb-3 flex flex-col gap-2 rounded-lg border border-primary/20 bg-gradient-to-r from-primary/10 via-primary/5 to-transparent p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2.5">
            <LayoutGridIcon className="mt-0.5 size-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <p className="text-sm font-semibold">
                这里是社区好物(积分交易),不是资产商店
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                插件 / 技能 / 角色(Codex + WorkBuddy +
                本地)已统一到「统一资产」入口。
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => navigate("/workspace/agents?tab=assets")}
              className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
            >
              前往统一资产
              <ArrowRightIcon className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={() => {
                try {
                  window.localStorage.setItem(ASSETS_BANNER_KEY, "1");
                } catch {
                  /* ignore */
                }
                setBannerDismissed(true);
              }}
              aria-label="关闭提示"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
            >
              <XIcon className="size-4" />
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pb-1 [mask-image:linear-gradient(to_right,#000_calc(100%-1.25rem),transparent)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {MARKET_CATEGORIES.map((cat) => {
            const isActive = cat.key === activeTab;
            return (
              <button
                key={cat.key}
                type="button"
                onClick={() => setActiveTab(cat.key)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                  isActive
                    ? "border-rose-300/60 bg-rose-500/10 text-rose-600 dark:border-rose-400/30 dark:text-rose-300"
                    : "border-transparent bg-muted/35 text-muted-foreground hover:bg-muted/65 hover:text-foreground",
                )}
              >
                <span
                  className="size-1.5 rounded-full"
                  style={{ backgroundColor: cat.color }}
                />
                {cat.label}
              </button>
            );
          })}
        </div>
        <div className="flex w-full shrink-0 items-center gap-2 lg:w-auto">
          <span className="flex min-h-9 shrink-0 items-center gap-1 rounded-md border border-border-subtle bg-card px-2 text-xs text-muted-foreground">
            <ClockIcon className="size-3.5" />
            <strong className="text-sm tabular-nums text-foreground">
              {balance}
            </strong>
            积分
          </span>
          <div className="relative min-w-0 flex-1 lg:w-52 lg:flex-none">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索好物、卖家…"
              className={cn(
                "w-full rounded-md border border-border-default bg-background/60 py-1.5 pl-9 pr-8 text-sm",
                "placeholder:text-muted-foreground/60 outline-none",
                "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
              )}
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="清空搜索"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground/60 hover:text-foreground"
              >
                <XIcon className="size-3.5" />
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={() => setListOpen(true)}
            className="flex min-h-9 shrink-0 items-center gap-1 rounded-md border border-border-default bg-background px-3 py-2 text-sm font-semibold text-foreground shadow-sm transition-colors hover:border-rose-300 hover:bg-rose-500/5 hover:text-rose-600"
          >
            <PlusIcon className="size-4 text-rose-500" />
            上架
          </button>
        </div>
      </div>

      <div className="mt-3 flex-1 sm:mt-4">
        <MarketGrid
          items={filtered}
          onBuy={(item) => setDetail(item)}
          onOpen={(item) => setDetail(item)}
        />
      </div>

      {detail && (
        <MarketDetail
          item={detail}
          onClose={() => setDetail(null)}
          onSold={() => setVersion((v) => v + 1)}
        />
      )}
      {listOpen && (
        <ListModal
          onClose={() => setListOpen(false)}
          onListed={() => setVersion((v) => v + 1)}
        />
      )}
    </>
  );
}

export function ListModal({
  onClose,
  onListed,
}: {
  onClose: () => void;
  onListed: () => void;
}) {
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [price, setPrice] = useState("10");
  const [category, setCategory] = useState("efficiency");
  const [cover, setCover] = useState<string>(COVER_OPTIONS[0]);

  const priceNum = Math.max(0, Math.round(Number(price) || 0));
  const canSubmit = title.trim().length > 0 && priceNum > 0;

  const handleSubmit = () => {
    if (!canSubmit) return;
    listMarketItem({
      title,
      desc,
      price: priceNum,
      category,
      cover,
    });
    onListed();
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-center bg-background/95 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-lg flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-3 py-2">
          <span className="text-sm font-semibold">上架好物</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <XIcon className="size-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="一句话好物标题…"
            maxLength={40}
            className="w-full rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
          />
          <textarea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="描述用途、亮点（可选）…"
            rows={3}
            className="mt-3 w-full resize-none rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
          />
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">
                售价（积分）
              </p>
              <input
                type="number"
                value={price}
                min={1}
                onChange={(e) => setPrice(e.target.value)}
                className="w-full rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
              />
            </div>
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">分类</p>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full rounded-md border border-border-default bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
              >
                {MARKET_CATEGORIES.filter((c) => c.key !== "all").map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-3">
            <p className="mb-1.5 text-xs text-muted-foreground">选择封面</p>
            <div className="grid grid-cols-6 gap-2">
              {COVER_OPTIONS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setCover(c)}
                  className={cn(
                    "aspect-[4/3] overflow-hidden rounded-md ring-2 transition",
                    cover === c
                      ? "ring-rose-500"
                      : "ring-transparent hover:ring-border-default",
                  )}
                >
                  <img src={c} alt="" className="h-full w-full object-cover" />
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border-subtle bg-background px-4 py-3">
          <span className="text-xs text-muted-foreground">
            售出后积分将计入你的账户
          </span>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={cn(
              "rounded-md px-4 py-1.5 text-sm font-semibold transition-colors",
              canSubmit
                ? "bg-rose-500 text-white hover:bg-rose-600"
                : "cursor-not-allowed bg-muted text-muted-foreground/50",
            )}
          >
            上架
          </button>
        </div>
      </div>
    </div>
  );
}
