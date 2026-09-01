import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  HeartIcon,
  MessageCircleIcon,
  SendIcon,
  StarIcon,
} from "lucide-react";
import {
  addUserComment,
  formatCount,
  formatRelativeTime,
  mergeComments,
  type CommunityComment,
  type CommunityPost,
} from "./community-data";
import { CommunityForkButton } from "./community-fork-button";
import { creditLikeEarn } from "@/core/credits/ledger";
import { cn } from "@/lib/utils";

const LIKES_KEY = "echo.community.likes.v1";

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

/**
 * 帖子详情视图 —— 小红书式点击卡片进入的单帖详情。
 *
 * 全屏覆盖层：封面大图 + 标题/正文 + 作者 + 点赞/收藏/评论互动 + 评论区。
 * 保留返回，关闭后回到 feed 且不丢失列表状态。
 */
export function CommunityPostDetail({
  post,
  isFavorite,
  onClose,
  onToggleFavorite,
  onCommentAdded,
}: {
  post: CommunityPost;
  isFavorite: boolean;
  onClose: () => void;
  onToggleFavorite?: (id: string) => void;
  onCommentAdded?: (postId: string, count: number) => void;
}) {
  const [liked, setLiked] = useState(() => readLiked().includes(post.id));
  const [comments, setComments] = useState<CommunityComment[]>(() =>
    mergeComments(post),
  );
  const [draft, setDraft] = useState("");
  const [imageBroken, setImageBroken] = useState(false);
  const [imageIndex, setImageIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const images = (post.images?.length ? post.images : [post.coverUrl]).filter(
    Boolean,
  );
  const hasImageCover = images.length > 0 && !imageBroken;
  const canSend = draft.trim().length > 0;
  const likeCount = post.likesCount + (liked ? 1 : 0);
  const multi = images.length > 1;

  const goImage = useCallback(
    (dir: number) => {
      setImageIndex((i) => (i + dir + images.length) % images.length);
    },
    [images.length],
  );

  // 打开时锁定背景滚动。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") goImage(-1);
      if (e.key === "ArrowRight") goImage(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goImage]);

  const toggleLike = () => {
    setLiked((prev) => {
      const next = prev
        ? readLiked().filter((x) => x !== post.id)
        : [...readLiked(), post.id];
      writeLiked(next);
      // 点赞自己发布的内容 → 作者获得互动奖励（共创赚钱闭环）。
      if (!prev && post.author === "我") creditLikeEarn(post.title);
      return !prev;
    });
  };

  const handleSend = () => {
    const text = draft.trim();
    if (!text) return;
    const count = addUserComment(post.id, text, "我", "我");
    setComments(mergeComments(post));
    setDraft("");
    onCommentAdded?.(post.id, count);
    inputRef.current?.focus();
  };

  // 打开时锁定背景滚动。
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-background/95 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-xl flex-col overflow-hidden">
        {/* 顶栏 */}
        <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-3 py-2">
          <button
            type="button"
            onClick={onClose}
            aria-label="返回"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <ChevronLeftIcon className="size-4" />
          </button>
          <span className="text-sm font-semibold">帖子详情</span>
          <span className="w-7" />
        </div>

        {/* 可滚动内容 */}
        <div className="flex-1 overflow-y-auto">
          {/* 封面（多图轮播） */}
          <div
            className="relative w-full overflow-hidden"
            style={{
              height: Math.min(320, Math.max(200, post.coverHeight + 60)),
              background: `linear-gradient(135deg, ${post.coverGradient.join(", ")})`,
            }}
          >
            {hasImageCover && (
              <img
                key={images[imageIndex]}
                src={images[imageIndex]}
                alt={post.title}
                onError={() => setImageBroken(true)}
                className="size-full object-cover"
              />
            )}
            {multi && (
              <>
                <button
                  type="button"
                  onClick={() => goImage(-1)}
                  aria-label="上一张"
                  className="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/40 p-1.5 text-white backdrop-blur-sm transition-colors hover:bg-black/60"
                >
                  <ChevronLeftIcon className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => goImage(1)}
                  aria-label="下一张"
                  className="absolute right-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/40 p-1.5 text-white backdrop-blur-sm transition-colors hover:bg-black/60"
                >
                  <ChevronRightIcon className="size-4" />
                </button>
                <span className="absolute bottom-2 right-2 rounded-full bg-black/40 px-2 py-0.5 text-mini font-semibold text-white backdrop-blur-sm">
                  {imageIndex + 1}/{images.length}
                </span>
              </>
            )}
            <span className="absolute left-3 top-3 flex items-center gap-1 rounded-full bg-black/40 px-2 py-0.5 text-mini font-semibold text-white backdrop-blur-sm">
              <span
                className="size-1.5 rounded-full"
                style={{ backgroundColor: post.tagColor }}
              />
              {post.kind === "mini-app" ? "小程序" : post.tag}
            </span>
          </div>

          {/* 标题 / 正文 / 作者 */}
          <div className="p-4">
            <h2 className="text-base font-semibold leading-snug">
              {post.title || "未命名"}
            </h2>
            {post.content && (
              <p className="mt-2 text-sm leading-relaxed text-foreground/90">
                {post.content}
              </p>
            )}

            <div className="mt-4 flex items-center gap-2">
              <span
                className="flex size-6 shrink-0 items-center justify-center rounded-full text-mini font-bold"
                style={{
                  backgroundColor: `${post.authorColor}1f`,
                  color: post.authorColor,
                }}
              >
                {post.authorInitial}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {post.author}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground/70">
                {formatRelativeTime(post.createdAt)}
              </span>
            </div>

            {/* 互动区 */}
            <div className="mt-4 flex items-center gap-5 border-t border-border-subtle pt-3">
              <button
                type="button"
                onClick={toggleLike}
                aria-label={liked ? "取消点赞" : "点赞"}
                className={cn(
                  "flex items-center gap-1 text-sm tabular-nums transition-colors",
                  liked
                    ? "text-chart-3"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <HeartIcon
                  className={cn(
                    "size-4 transition-transform",
                    liked && "animate-community-pop",
                  )}
                  fill={liked ? "currentColor" : "none"}
                />
                {formatCount(likeCount)}
              </button>
              <button
                type="button"
                onClick={() => onToggleFavorite?.(post.id)}
                aria-label={isFavorite ? "取消收藏" : "收藏"}
                className={cn(
                  "flex items-center gap-1 text-sm transition-colors",
                  isFavorite
                    ? "text-amber-500"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <StarIcon
                  className="size-4"
                  fill={isFavorite ? "currentColor" : "none"}
                />
                收藏
              </button>
              <span className="flex items-center gap-1 text-sm text-muted-foreground">
                <MessageCircleIcon className="size-4" />
                {comments.length}
              </span>
              {post.appRef && (
                <div className="ml-auto">
                  <CommunityForkButton post={post} />
                </div>
              )}
            </div>
          </div>

          {/* 评论区 */}
          <div className="border-t border-border-subtle px-4 py-3">
            <h3 className="mb-3 text-sm font-semibold">
              评论 · {comments.length}
            </h3>
            {comments.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                还没有评论，来抢沙发吧
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {comments.map((c) => (
                  <div key={c.id} className="flex items-start gap-2.5">
                    <span
                      className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-mini font-bold"
                      style={{
                        backgroundColor: `${c.authorColor}1f`,
                        color: c.authorColor,
                      }}
                    >
                      {c.authorInitial}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span className="text-xs font-medium">{c.author}</span>
                        <span className="text-mini text-muted-foreground/60">
                          {formatRelativeTime(c.createdAt)}
                        </span>
                      </div>
                      <p className="mt-0.5 text-sm leading-snug text-foreground/90">
                        {c.content}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 输入区 */}
        <div className="flex shrink-0 items-center gap-2 border-t border-border-subtle bg-background px-4 py-3">
          <input
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSend) handleSend();
            }}
            placeholder="写下你的评论…"
            className={cn(
              "min-w-0 flex-1 rounded-md border border-border-default bg-background/60 px-3 py-1.5 text-sm outline-none",
              "placeholder:text-muted-foreground/60",
              "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
            )}
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            aria-label="发送评论"
            className={cn(
              "shrink-0 rounded-md p-2 transition-colors",
              canSend
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "cursor-not-allowed text-muted-foreground/40",
            )}
          >
            <SendIcon className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
