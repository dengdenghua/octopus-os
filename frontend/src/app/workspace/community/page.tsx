import {
  BellRingIcon,
  CheckIcon,
  CompassIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  StoreIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { CommunityFeed } from "@/components/workspace/community/community-feed";
import { CommunityPostDetail } from "@/components/workspace/community/community-post-detail";
import { CommunityProfile } from "@/components/workspace/community/community-profile";
import {
  COMMUNITY_CATEGORIES,
  addPublishedPost,
  fetchCommunityFeed,
  readFavorites,
  readFollowing,
  toggleFavorite,
  toggleFollowing,
  type CommunityPost,
  type CommunitySort,
} from "@/components/workspace/community/community-data";
import {
  authorLook,
  readSubscribedAuthors,
  readSubscribedTopics,
  toggleSubscribedAuthor,
  toggleSubscribedTopic,
} from "@/components/workspace/community/community-subscribe";
import { MarketBoard } from "@/components/workspace/market/market-board";
import { cn } from "@/lib/utils";

type ViewMode = "community" | "market";

/** 顶栏 tab：推荐 + 订阅 + 关注 + 六大分类。 */
const TABS = [
  { key: "recommend", label: "推荐", color: "#FC466B" },
  { key: "subscribe", label: "订阅", color: "#EC4899" },
  ...COMMUNITY_CATEGORIES.filter((c) => c.key !== "recommend"),
];

export default function CommunityPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [viewMode, setViewMode] = useState<ViewMode>(
    () => (searchParams.get("view") as ViewMode) || "community",
  );
  const profileAuthor = searchParams.get("profile");

  const [activeTab, setActiveTab] = useState("recommend");
  const [posts, setPosts] = useState<CommunityPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [sort, setSort] = useState<CommunitySort>("latest");
  const [query, setQuery] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);

  // 个人主页 / 订阅：页面级状态
  const [activePost, setActivePost] = useState<CommunityPost | null>(null);
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites());
  const [following, setFollowing] = useState<string[]>(() =>
    readFollowing(),
  );
  const [subTopics, setSubTopics] = useState<string[]>(() =>
    readSubscribedTopics(),
  );
  const [subAuthors, setSubAuthors] = useState<string[]>(() =>
    readSubscribedAuthors(),
  );

  const switchView = useCallback(
    (mode: ViewMode) => {
      setViewMode(mode);
      setQuery("");
      setActivePost(null);
      setSearchParams(
        mode === "market" ? { view: "market" } : {},
        { replace: true },
      );
    },
    [setSearchParams],
  );

  /** 打开作者个人主页（通过 URL 参数，可分享）。 */
  const openProfile = useCallback(
    (author: string) => {
      // react-router 会自行 URL 编码，这里直接传原文，避免双重编码导致解码错乱。
      setSearchParams({ profile: author }, { replace: true });
    },
    [setSearchParams],
  );

  const closeProfile = useCallback(() => {
    setSearchParams({}, { replace: true });
    setActiveTab("recommend");
    setActivePost(null);
  }, [setSearchParams]);

  /** 订阅 tab / 个人主页需要全量 feed 以聚合，故用 recommend。 */
  const fetchTopic = useMemo(
    () =>
      profileAuthor || activeTab === "subscribe" ? "recommend" : activeTab,
    [profileAuthor, activeTab],
  );

  const load = useCallback(
    async (tab: string, s: CommunitySort, reset: boolean) => {
      if (reset) setLoading(true);
      else setLoadingMore(true);
      setLoadError(null);
      try {
        const res = await fetchCommunityFeed(tab, s, 0);
        setPosts(res.posts);
        setHasMore(res.hasMore);
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : "内容加载失败");
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (viewMode !== "community") return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const timeout = window.setTimeout(() => {
      if (!cancelled) {
        setLoading(false);
        setLoadError("加载超时，请检查网络后重试");
      }
    }, 8000);
    void fetchCommunityFeed(fetchTopic, sort, 0)
      .then((res) => {
        if (cancelled) return;
        setPosts(res.posts);
        setHasMore(res.hasMore);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : "内容加载失败");
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [fetchTopic, sort, viewMode]);

  const loadMore = useCallback(async () => {
    if (loadingMore || loading || !hasMore) return;
    setLoadingMore(true);
    const res = await fetchCommunityFeed(fetchTopic, sort, posts.length);
    setPosts((prev) => [...prev, ...res.posts]);
    setHasMore(res.hasMore);
    setLoadingMore(false);
  }, [fetchTopic, sort, posts.length, loadingMore, loading, hasMore]);

  const handleSwitchTab = (tab: string) => {
    setActiveTab(tab);
    setQuery("");
    setActivePost(null);
  };

  /** 订阅 tab：过滤为「订阅主题 or 关注/订阅作者」的内容。 */
  const filtered = useMemo(() => {
    let list = posts;
    if (activeTab === "subscribe") {
      list = posts.filter(
        (p) =>
          subTopics.includes(p.topic) ||
          subAuthors.includes(p.author) ||
          following.includes(p.author),
      );
    }
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.content.toLowerCase().includes(q) ||
        p.author.toLowerCase().includes(q) ||
        p.tag.toLowerCase().includes(q),
    );
  }, [posts, activeTab, query, subTopics, subAuthors, following]);

  const isMarket = viewMode === "market";
  const isProfile = Boolean(profileAuthor);

  const handleToggleFavorite = useCallback((id: string) => {
    setFavorites(() => toggleFavorite(id));
  }, []);

  const handleToggleFollow = useCallback((author: string) => {
    setFollowing(() => toggleFollowing(author));
  }, []);

  const handleToggleSubTopic = useCallback((key: string) => {
    toggleSubscribedTopic(key);
    setSubTopics(() => readSubscribedTopics());
  }, []);

  const handleToggleSubAuthor = useCallback((author: string) => {
    toggleSubscribedAuthor(author);
    setSubAuthors(() => readSubscribedAuthors());
  }, []);

  const handleCommentAdded = useCallback(
    (postId: string, count: number) => {
      setPosts((prev) =>
        prev.map((p) =>
          p.id === postId ? { ...p, commentsCount: count } : p,
        ),
      );
    },
    [],
  );

  const isFavorite = useCallback(
    (id: string) => favorites.includes(id),
    [favorites],
  );

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="p-0">
        <div className="flex min-h-full w-full flex-col p-4">
          {isProfile ? (
            <CommunityProfile
              author={profileAuthor!}
              posts={posts}
              onBack={closeProfile}
              onOpenPost={setActivePost}
            />
          ) : (
            <>
              {/* 顶栏 */}
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <div
                    className={cn(
                      "flex size-6 items-center justify-center rounded-md",
                      isMarket ? "bg-rose-500/10" : "bg-chart-3/10",
                    )}
                  >
                    {isMarket ? (
                      <StoreIcon className="size-4 text-rose-500" />
                    ) : (
                      <CompassIcon className="size-4 text-chart-3" />
                    )}
                  </div>
                  <h1 className="hidden text-lg font-bold sm:block">发现</h1>
                  <div className="flex items-center gap-1 rounded-md border border-border-default bg-background/60 p-0.5 sm:ml-2">
                    {(
                      [
                        { key: "community", label: "社区", icon: CompassIcon },
                        { key: "market", label: "集市", icon: StoreIcon },
                      ] as const
                    ).map((v) => (
                      <button
                        key={v.key}
                        type="button"
                        onClick={() => switchView(v.key)}
                        className={cn(
                          "flex items-center gap-1.5 rounded px-3 py-1 text-xs font-medium transition-colors",
                          viewMode === v.key
                            ? v.key === "market"
                              ? "bg-rose-500 text-white"
                              : "bg-chart-3 text-white"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        <v.icon className="size-3.5" />
                        {v.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {!isMarket && (
                    <>
                      <div className="relative w-56">
                        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                        <input
                          type="text"
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder="搜索灵感、作者、标签…"
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
                        onClick={() => void load(activeTab, sort, true)}
                        aria-label="刷新"
                        className="shrink-0 rounded-md border border-border-default bg-background/60 p-2 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                      >
                        <RefreshCwIcon
                          className={cn("size-3.5", loading && "animate-spin")}
                        />
                      </button>
                      <button
                        type="button"
                        onClick={() => setPublishOpen(true)}
                        className="flex shrink-0 items-center gap-1 rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
                      >
                        <PlusIcon className="size-4" />
                        发布
                      </button>
                    </>
                  )}
                </div>
              </div>

              {isMarket ? (
                <div className="mt-4 flex-1">
                  <MarketBoard />
                </div>
              ) : (
                <>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <div className="flex flex-1 flex-wrap items-center gap-1.5">
                      {TABS.map((tab) => {
                        const isActive = tab.key === activeTab;
                        return (
                          <button
                            key={tab.key}
                            type="button"
                            onClick={() => handleSwitchTab(tab.key)}
                            className={cn(
                              "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                              isActive
                                ? "bg-primary text-primary-foreground"
                                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                            )}
                          >
                            <span
                              className="size-1.5 rounded-full"
                              style={{ backgroundColor: tab.color }}
                            />
                            {tab.label}
                          </button>
                        );
                      })}
                    </div>
                    <div className="flex shrink-0 items-center gap-1 rounded-md border border-border-default bg-background/60 p-0.5">
                      {(
                        [
                          { key: "latest", label: "最新" },
                          { key: "hot", label: "热门" },
                        ] as const
                      ).map((opt) => (
                        <button
                          key={opt.key}
                          type="button"
                          onClick={() => setSort(opt.key)}
                          className={cn(
                            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                            sort === opt.key
                              ? "bg-primary/10 text-primary"
                              : "text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {activeTab === "subscribe" && (
                    <SubscriptionPanel
                      posts={posts}
                      subTopics={subTopics}
                      subAuthors={subAuthors}
                      following={following}
                      onToggleTopic={handleToggleSubTopic}
                      onToggleAuthor={handleToggleSubAuthor}
                      onToggleFollow={handleToggleFollow}
                      onOpenProfile={openProfile}
                    />
                  )}

                  <div className="mt-4 flex-1">
                    <CommunityFeed
                      posts={filtered}
                      loading={loading}
                      error={loadError}
                      onRetry={() => void load(fetchTopic, sort, true)}
                      hasMore={hasMore && !query.trim()}
                      onLoadMore={loadMore}
                      highlightKeyword={query.trim() || undefined}
                      following={following}
                      onToggleFollow={handleToggleFollow}
                      onOpenProfile={openProfile}
                    />
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {activePost && (
          <CommunityPostDetail
            post={activePost}
            isFavorite={isFavorite(activePost.id)}
            onClose={() => setActivePost(null)}
            onToggleFavorite={handleToggleFavorite}
            onCommentAdded={handleCommentAdded}
          />
        )}

        {publishOpen && (
          <PublishModal
            onClose={() => setPublishOpen(false)}
            onPublished={() => {
              setPublishOpen(false);
              setActiveTab("recommend");
              setSort("latest");
              void load("recommend", "latest", true);
            }}
          />
        )}
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

/** 订阅管理面板：订阅主题 + 订阅/关注作者。 */
function SubscriptionPanel({
  posts,
  subTopics,
  subAuthors,
  following,
  onToggleTopic,
  onToggleAuthor,
  onToggleFollow,
  onOpenProfile,
}: {
  posts: CommunityPost[];
  subTopics: string[];
  subAuthors: string[];
  following: string[];
  onToggleTopic: (key: string) => void;
  onToggleAuthor: (author: string) => void;
  onToggleFollow: (author: string) => void;
  onOpenProfile: (author: string) => void;
}) {
  const topics = COMMUNITY_CATEGORIES.filter(
    (c) => c.key !== "recommend" && c.key !== "following",
  );
  const authors = Array.from(new Set([...following, ...subAuthors]));

  return (
    <div className="mt-3 space-y-3 rounded-xl bg-card p-3 shadow-[var(--shadow-card)]">
      {/* 订阅的主题 */}
      <div>
        <p className="text-xs font-semibold text-muted-foreground">
          订阅的主题
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {topics.map((t) => {
            const active = subTopics.includes(t.key);
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => onToggleTopic(t.key)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  active
                    ? "bg-chart-3/10 text-chart-3"
                    : "border border-border-default text-muted-foreground hover:bg-muted/60",
                )}
              >
                <span
                  className="size-1.5 rounded-full"
                  style={{ backgroundColor: t.color }}
                />
                {t.label}
                {active && <BellRingIcon className="size-3" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* 订阅 / 关注的作者 */}
      <div>
        <p className="text-xs font-semibold text-muted-foreground">
          订阅 / 关注的作者
        </p>
        {authors.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground/60">
            还没有订阅的作者，去「推荐」里关注你感兴趣的创作者。
          </p>
        ) : (
          <div className="mt-2 flex flex-col gap-1">
            {authors.map((name) => {
              const look = authorLook(name, posts);
              const isFollowing = following.includes(name);
              const isSub = subAuthors.includes(name);
              return (
                <div
                  key={name}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/50"
                >
                  <button
                    type="button"
                    onClick={() => onOpenProfile(name)}
                    className="flex min-w-0 items-center gap-2"
                  >
                    <span
                      className="flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold"
                      style={{
                        backgroundColor: `${look.color}1f`,
                        color: look.color,
                      }}
                    >
                      {look.initial}
                    </span>
                    <span className="truncate text-sm font-medium">{name}</span>
                  </button>
                  <div className="ml-auto flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onToggleFollow(name)}
                      className={cn(
                        "rounded px-2 py-0.5 text-mini font-semibold transition-colors",
                        isFollowing
                          ? "text-muted-foreground/60 hover:text-foreground"
                          : "text-chart-3 hover:text-chart-3/80",
                      )}
                    >
                      {isFollowing ? "已关注" : "关注"}
                    </button>
                    <button
                      type="button"
                      onClick={() => onToggleAuthor(name)}
                      className={cn(
                        "flex items-center gap-1 rounded px-2 py-0.5 text-mini font-semibold transition-colors",
                        isSub
                          ? "text-muted-foreground/60 hover:text-foreground"
                          : "text-primary hover:text-primary/80",
                      )}
                    >
                      {isSub && <CheckIcon className="size-3" />}
                      {isSub ? "已订阅" : "订阅"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

const PUBLISH_TOPICS = COMMUNITY_CATEGORIES.filter(
  (c) => c.key !== "recommend" && c.key !== "following",
);

function PublishModal({
  onClose,
  onPublished,
}: {
  onClose: () => void;
  onPublished: () => void;
}) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [topic, setTopic] = useState("life");
  const [tag, setTag] = useState("");
  const canSubmit = title.trim().length > 0;

  const handleSubmit = () => {
    if (!canSubmit) return;
    addPublishedPost({ title, content, tag, topic });
    onPublished();
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
          <span className="text-sm font-semibold">发布灵感</span>
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
            placeholder="一句话标题，让灵感被看见…"
            maxLength={60}
            className="w-full rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="详细描述你的灵感 / 用法（可选）…"
            rows={5}
            className="mt-3 w-full resize-none rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
          />
          <input
            type="text"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="标签（可选），如：自动化"
            maxLength={12}
            className="mt-3 w-full rounded-md border border-border-default bg-background/60 px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
          />
          <div className="mt-3">
            <p className="mb-1.5 text-xs text-muted-foreground">选择分类</p>
            <div className="flex flex-wrap gap-1.5">
              {PUBLISH_TOPICS.map((c) => {
                const isActive = c.key === topic;
                return (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => setTopic(c.key)}
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                      isActive
                        ? "bg-primary text-primary-foreground"
                        : "border border-border-default text-muted-foreground hover:bg-muted/60",
                    )}
                  >
                    <span
                      className="size-1.5 rounded-full"
                      style={{ backgroundColor: c.color }}
                    />
                    {c.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border-subtle bg-background px-4 py-3">
          <span className="text-xs text-muted-foreground">
            {title.trim().length}/60
          </span>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={cn(
              "rounded-md px-4 py-1.5 text-sm font-semibold transition-colors",
              canSubmit
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "cursor-not-allowed bg-muted text-muted-foreground/50",
            )}
          >
            发布
          </button>
        </div>
      </div>
    </div>
  );
}
