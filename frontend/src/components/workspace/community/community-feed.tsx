import {
  Component,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  HeartIcon,
  MessageCircleIcon,
  StarIcon,
  XIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  formatCount,
  readFollowing,
  toggleFollowing,
  type CommunityPost,
} from "./community-data";
import { CommunityPostDetail } from "./community-post-detail";
import { CommunityForkButton } from "./community-fork-button";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";

const FAVORITES_KEY = "echo.community.favorites.v1";
const LIKES_KEY = "echo.community.likes.v1";
/** 每列最小宽度（px），据此自动计算列数。 */
const MIN_COLUMN_WIDTH = 240;
/** 信息区（标题+描述+作者）等效高度，用于瀑布流高度估算。 */
const INFO_EXTRA_HEIGHT = 96;

/** 收藏状态持久化（localStorage）。 */
function readFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(FAVORITES_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeFavorites(ids: string[]) {
  try {
    window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** 点赞状态持久化（与详情页共用同一 key）。 */
function readLiked(): string[] {
  try {
    const raw = window.localStorage.getItem(LIKES_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeLiked(ids: string[]) {
  try {
    window.localStorage.setItem(LIKES_KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** 根据容器宽度自适应列数（ResizeObserver 监听）。 */
function useColumnCount(minWidth = MIN_COLUMN_WIDTH) {
  const ref = useRef<HTMLDivElement>(null);
  const [count, setCount] = useState(2);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const n = Math.max(1, Math.floor(el.clientWidth / minWidth));
      setCount(n);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [minWidth]);
  return { ref, count };
}

/** 估算卡片高度（封面 3:4 竖图 + 信息区，供瀑布流排序）。 */
function estimateHeight(colW: number): number {
  return colW * (4 / 3) + INFO_EXTRA_HEIGHT;
}

/**
 * 小红书式瀑布流 feed（workbuddy Discover 同款实现）。
 *
 * 手写贪心算法：每个帖子放入当前最矮的列，列高用 `estimateHeight` 估算，
 * 保证各列高度均衡。列数随容器宽度自适应。
 */
export function CommunityFeed({
  posts,
  loading,
  hasMore = false,
  onLoadMore,
  highlightKeyword,
  following,
  onToggleFollow,
  onOpenProfile,
  error,
  onRetry,
}: {
  posts: CommunityPost[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** 是否还有更多可加载（无限滚动哨兵）。 */
  hasMore?: boolean;
  onLoadMore?: () => void;
  highlightKeyword?: string;
  /** 外部关注列表（页面级状态，缺省时用内部状态）。 */
  following?: string[];
  onToggleFollow?: (author: string) => void;
  /** 点击作者跳转独立个人主页。 */
  onOpenProfile?: (author: string) => void;
}) {
  const { ref, count } = useColumnCount();
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites());
  const [liked, setLiked] = useState<string[]>(() => readLiked());
  const [localFollowing, setLocalFollowing] = useState<string[]>(() =>
    readFollowing(),
  );
  const [activePost, setActivePost] = useState<CommunityPost | null>(null);
  const [lightbox, setLightbox] = useState<{
    post: CommunityPost;
    images: string[];
    index: number;
  } | null>(null);
  /** 发评论后覆盖卡片评论数（key: postId -> count）。 */
  const [commentCounts, setCommentCounts] = useState<Record<string, number>>(
    {},
  );
  /** 底部哨兵，用于无限滚动。 */
  const sentinelRef = useRef<HTMLDivElement>(null);

  const toggleFavorite = useCallback((id: string) => {
    setFavorites((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : [...prev, id];
      writeFavorites(next);
      return next;
    });
  }, []);

  const toggleLike = useCallback((id: string) => {
    setLiked((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : [...prev, id];
      writeLiked(next);
      return next;
    });
  }, []);

  const handleToggleFollow = useCallback(
    (author: string) => {
      setLocalFollowing(() => toggleFollowing(author));
      onToggleFollow?.(author);
    },
    [onToggleFollow],
  );

  const handleCommentAdded = useCallback((postId: string, count: number) => {
    setCommentCounts((prev) => ({ ...prev, [postId]: count }));
  }, []);

  const openLightbox = useCallback((post: CommunityPost, index = 0) => {
    const images = post.images?.length ? post.images : [post.coverUrl];
    setLightbox({ post, images, index });
  }, []);

  const isFavorite = useCallback(
    (id: string) => favorites.includes(id),
    [favorites],
  );
  const isLiked = useCallback((id: string) => liked.includes(id), [liked]);
  const effectiveFollowing = following ?? localFollowing;
  const isFollowing = useCallback(
    (author: string) => effectiveFollowing.includes(author),
    [effectiveFollowing],
  );

  // 无限滚动：哨兵进入视口时加载更多。
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !onLoadMore || !hasMore) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) onLoadMore();
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [onLoadMore, hasMore, posts.length]);

  const columns = useMemo(() => {
    // 封面为 3:4 竖图，列高按实际列宽估算，保证瀑布流各列均衡。
    const colW = ref.current?.clientWidth
      ? ref.current.clientWidth / count
      : MIN_COLUMN_WIDTH;
    const cols: CommunityPost[][] = Array.from({ length: count }, () => []);
    const heights = new Array(count).fill(0);
    for (const p of posts) {
      let minIdx = 0;
      for (let i = 1; i < count; i++) {
        if (heights[i]! < heights[minIdx]!) minIdx = i;
      }
      cols[minIdx]!.push(p);
      heights[minIdx] += estimateHeight(colW);
    }
    return cols;
  }, [ref, posts, count]);

  if (loading && posts.length === 0) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="overflow-hidden rounded-xl bg-card shadow-[var(--shadow-card)]"
          >
            <Skeleton
              className="w-full"
              style={{ height: 120 + (i % 3) * 40 }}
            />
            <div className="space-y-2 p-3">
              <Skeleton className="h-3 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error && posts.length === 0) {
    return (
      <div role="alert" className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-destructive/30 bg-destructive/5 px-6 text-center">
        <MessageCircleIcon className="size-6 text-destructive/70" />
        <div>
          <p className="text-sm font-medium">社区内容加载失败</p>
          <p className="mt-1 text-xs text-muted-foreground">{error}</p>
        </div>
        {onRetry && <Button size="sm" variant="outline" onClick={onRetry}>重试</Button>}
      </div>
    );
  }

  if (posts.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 text-muted-foreground">
        <MessageCircleIcon className="size-6 opacity-50" />
        <p className="text-sm">该分类下还没有内容</p>
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className="grid gap-3 sm:gap-4"
      style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
    >
      {columns.map((col, colIdx) => (
        <div key={colIdx} className="flex flex-col gap-3 sm:gap-4">
          {col.map((post) => (
            <CardErrorBoundary key={post.id}>
              <CommunityPostCard
                post={post}
                isFavorite={isFavorite(post.id)}
                onToggleFavorite={toggleFavorite}
                isLiked={isLiked(post.id)}
                onToggleLike={toggleLike}
                isFollowing={isFollowing(post.author)}
                onToggleFollow={handleToggleFollow}
                highlightKeyword={highlightKeyword}
                commentCount={commentCounts[post.id] ?? post.commentsCount}
                onOpenComments={() => setActivePost(post)}
                onOpenAuthor={() => onOpenProfile?.(post.author)}
                onOpenImage={(index) => openLightbox(post, index)}
              />
            </CardErrorBoundary>
          ))}
        </div>
      ))}

      {/* 无限滚动哨兵 */}
      {hasMore && (
        <div ref={sentinelRef} className="col-span-full h-4" aria-hidden />
      )}
      {loading && hasMore && (
        <div className="col-span-full flex justify-center py-3 text-sm text-muted-foreground">
          加载中…
        </div>
      )}

      {activePost && (
        <CommunityPostDetail
          post={activePost}
          isFavorite={isFavorite(activePost.id)}
          onClose={() => setActivePost(null)}
          onToggleFavorite={toggleFavorite}
          onCommentAdded={handleCommentAdded}
        />
      )}

      {lightbox && (
        <ImageLightbox
          images={lightbox.images}
          index={lightbox.index}
          onClose={() => setLightbox(null)}
          onIndexChange={(i) =>
            setLightbox((l) => (l ? { ...l, index: i } : l))
          }
        />
      )}
    </div>
  );
}

/** 单卡错误边界：某张卡片渲染出错时降级为空，不影响整列与整页。 */
class CardErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error: Error) {
    console.warn("[CommunityPostCard] render error:", error.message);
  }
  render() {
    if (this.state.hasError) return null;
    return this.props.children;
  }
}

/**
 * 图片放大（小红书式 lightbox）：点击封面全屏查看，多图可左右切换。
 */
function ImageLightbox({
  images,
  index,
  onClose,
  onIndexChange,
}: {
  images: string[];
  index: number;
  onClose: () => void;
  onIndexChange: (index: number) => void;
}) {
  const multi = images.length > 1;
  const go = (dir: number) => {
    onIndexChange((index + dir + images.length) % images.length);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90">
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭"
        className="absolute right-4 top-4 z-10 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
      >
        <XIcon className="size-5" />
      </button>
      {multi && (
        <>
          <button
            type="button"
            onClick={() => go(-1)}
            aria-label="上一张"
            className="absolute left-4 z-10 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
          >
            <ChevronLeftIcon className="size-5" />
          </button>
          <button
            type="button"
            onClick={() => go(1)}
            aria-label="下一张"
            className="absolute right-4 z-10 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
          >
            <ChevronRightIcon className="size-5" />
          </button>
        </>
      )}
      <img
        key={images[index]}
        src={images[index]}
        alt="查看大图"
        className="max-h-[85vh] max-w-[90vw] rounded-md object-contain"
      />
      {multi && (
        <div className="absolute bottom-5 left-1/2 -translate-x-1/2 rounded-full bg-black/50 px-3 py-1 text-xs text-white">
          {index + 1} / {images.length}
        </div>
      )}
    </div>
  );
}

/** 关键词高亮：把命中片段包成高亮 span。 */
function HighlightedText({
  text,
  keyword,
}: {
  text: string;
  keyword?: string;
}) {
  if (!keyword?.trim()) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(keyword)})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark key={i} className="bg-primary/15 text-foreground">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 单个帖子卡片：封面(多图) + 标题/正文 + 作者(关注) + 点赞 + 评论 + 收藏。 */
export function CommunityPostCard({
  post,
  isFavorite = false,
  onToggleFavorite,
  isLiked = false,
  onToggleLike,
  isFollowing = false,
  onToggleFollow,
  onOpenComments,
  onOpenAuthor,
  onOpenImage,
  commentCount,
  highlightKeyword,
  className,
}: {
  post: CommunityPost;
  isFavorite?: boolean;
  onToggleFavorite?: (id: string) => void;
  isLiked?: boolean;
  onToggleLike?: (id: string) => void;
  isFollowing?: boolean;
  onToggleFollow?: (author: string) => void;
  onOpenComments?: (post: CommunityPost) => void;
  onOpenAuthor?: (post: CommunityPost) => void;
  onOpenImage?: (index: number) => void;
  commentCount?: number;
  highlightKeyword?: string;
  className?: string;
}) {
  const [imageBroken, setImageBroken] = useState(false);
  const isMiniApp = post.kind === "mini-app";
  const images = post.images?.length ? post.images : [post.coverUrl];
  const hasImageCover = post.coverUrl.trim().length > 0 && !imageBroken;
  const likeCount = post.likesCount + (isLiked ? 1 : 0);

  const handleFavorite = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleFavorite?.(post.id);
  };
  const handleLike = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleLike?.(post.id);
  };
  const handleFollow = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleFollow?.(post.author);
  };
  const handleAuthor = (e: React.MouseEvent) => {
    e.stopPropagation();
    onOpenAuthor?.(post);
  };

  return (
    <article
      onClick={() => onOpenComments?.(post)}
      className={cn(
        "group cursor-pointer overflow-hidden rounded-xl bg-card shadow-[var(--shadow-card)]",
        "transition-[transform,box-shadow] duration-base",
        "hover:-translate-y-0.5 hover:shadow-[var(--shadow-elevated)]",
        className,
      )}
    >
      {/* 封面（小红书式 3:4 竖图） */}
      <div
        className="relative aspect-[3/4] w-full overflow-hidden"
        style={{
          background: `linear-gradient(135deg, ${post.coverGradient.join(", ")})`,
        }}
      >
        {hasImageCover && (
          <img
            src={post.coverUrl}
            alt={post.title}
            loading="lazy"
            onError={() => setImageBroken(true)}
            onClick={(e) => {
              e.stopPropagation();
              onOpenImage?.(0);
            }}
            className="size-full cursor-zoom-in object-cover transition-transform duration-slow group-hover:scale-[1.03]"
          />
        )}
        {imageBroken && (
          <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-xs text-white/80">
            封面暂时无法加载
          </div>
        )}

        {/* 分类标签（左上角） */}
        <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-black/40 px-2 py-0.5 text-mini font-semibold text-white backdrop-blur-sm">
          <span
            className="size-1.5 rounded-full"
            style={{ backgroundColor: post.tagColor }}
          />
          {isMiniApp ? "小程序" : post.tag}
        </span>

        {/* 多图指示（右上角） */}
        {images.length > 1 && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenImage?.(0);
            }}
            aria-label="查看多图"
            className="absolute right-2 top-2 rounded-full bg-black/40 px-1.5 py-0.5 text-mini font-semibold text-white backdrop-blur-sm"
          >
            1/{images.length}
          </button>
        )}

        {/* 可复刻徽标（右上角，在小程序模式下与多图并存时位移） */}
        {post.appRef && images.length <= 1 && (
          <span
            onClick={(e) => e.stopPropagation()}
            className="absolute right-2 top-2"
          >
            <CommunityForkButton post={post} />
          </span>
        )}
      </div>

      {/* 内容区 */}
      <div className="p-3">
        <h3 className="line-clamp-2 text-sm font-medium leading-snug">
          <HighlightedText
            text={post.title || "未命名"}
            keyword={highlightKeyword}
          />
        </h3>
        {post.content && !isMiniApp && (
          <p className="mt-1 line-clamp-1 text-xs leading-snug text-muted-foreground">
            <HighlightedText text={post.content} keyword={highlightKeyword} />
          </p>
        )}

        <div className="mt-2.5 flex items-center gap-2">
          <button
            type="button"
            onClick={handleAuthor}
            aria-label={`查看作者 ${post.author}`}
            className="flex min-w-0 shrink-0 items-center gap-1.5"
          >
            <span
              className="flex size-5 shrink-0 items-center justify-center rounded-full text-micro font-bold"
              style={{
                backgroundColor: `${post.authorColor}1f`,
                color: post.authorColor,
              }}
            >
              {post.authorInitial}
            </span>
            <span className="max-w-[72px] truncate text-xs text-muted-foreground">
              {post.author}
            </span>
          </button>

          {onToggleFollow && (
            <button
              type="button"
              onClick={handleFollow}
              aria-label={`${isFollowing ? "取消关注" : "关注"} ${post.author}`}
              className={cn(
                "shrink-0 rounded text-mini font-semibold transition-colors",
                isFollowing
                  ? "text-muted-foreground/60 hover:text-foreground"
                  : "text-chart-3 hover:text-chart-3/80",
              )}
            >
              {isFollowing ? "已关注" : "关注"}
            </button>
          )}

          <span className="ml-auto flex shrink-0 items-center gap-0.5 text-xs tabular-nums text-muted-foreground">
            <HeartIcon className="size-3 text-muted-foreground/60" />
            {formatCount(likeCount)}
          </span>

          {onOpenComments && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpenComments(post);
              }}
              aria-label="查看评论"
              title={`查看 ${post.title} 的 ${formatCount(commentCount ?? post.commentsCount)} 条评论`}
              className="flex shrink-0 items-center gap-0.5 rounded p-0.5 text-xs tabular-nums text-muted-foreground transition-colors hover:text-foreground"
            >
              <MessageCircleIcon className="size-3 text-muted-foreground/60" />
              {formatCount(commentCount ?? post.commentsCount)}
            </button>
          )}

          {onToggleLike && (
            <button
              type="button"
              onClick={handleLike}
              aria-label={isLiked ? "取消点赞" : "点赞"}
              title={isLiked ? "取消点赞" : "点赞"}
              className={cn(
                "shrink-0 rounded p-0.5 transition-colors",
                isLiked
                  ? "text-chart-3"
                  : "text-muted-foreground/50 hover:text-chart-3",
              )}
            >
              <HeartIcon
                className={cn(
                  "size-3.5 transition-transform",
                  isLiked && "animate-community-pop",
                )}
                fill={isLiked ? "currentColor" : "none"}
              />
            </button>
          )}

          {onToggleFavorite && (
            <button
              type="button"
              onClick={handleFavorite}
              aria-label={isFavorite ? "取消收藏" : "收藏"}
              title={isFavorite ? "取消收藏" : "收藏"}
              className={cn(
                "shrink-0 rounded p-0.5 transition-colors",
                isFavorite
                  ? "text-amber-500"
                  : "text-muted-foreground/50 hover:text-amber-500",
              )}
            >
              <StarIcon
                className="size-3.5"
                fill={isFavorite ? "currentColor" : "none"}
              />
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
