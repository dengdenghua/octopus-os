import { useCallback, useMemo, useState } from "react";
import {
  BellRingIcon,
  CheckIcon,
  ChevronLeftIcon,
  HeartIcon,
  MessageCircleIcon,
  StarIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatCount, type CommunityPost } from "./community-data";
import {
  buildAuthorProfile,
  readFollowing,
  readSubscribedAuthors,
  readSubscribedTopics,
  toggleFollowing,
  toggleSubscribedAuthor,
  toggleSubscribedTopic,
  type AuthorProfile,
} from "./community-subscribe";

/**
 * 独立个人主页（发现社区 · 小红书式作者主页）。
 *
 * 通过 URL 参数 `?profile=<author>` 在社区页面内以独立视图展示，
 * 包含：作者信息 + 关注/订阅 + 数据指标 + 主题分布 + 笔记瀑布流。
 */
export function CommunityProfile({
  author,
  posts,
  onBack,
  onOpenPost,
}: {
  author: string;
  /** 全量 feed（用于聚合该作者资料）。 */
  posts: CommunityPost[];
  onBack: () => void;
  onOpenPost: (p: CommunityPost) => void;
}) {
  const authorPosts = useMemo(
    () => posts.filter((p) => p.author === author),
    [posts, author],
  );
  const profile: AuthorProfile = useMemo(
    () => buildAuthorProfile(author, authorPosts),
    [author, authorPosts],
  );

  const [following, setFollowing] = useState(() =>
    readFollowing().includes(author),
  );
  const [subscribed, setSubscribed] = useState(() =>
    readSubscribedAuthors().includes(author),
  );
  const [topicSubs, setTopicSubs] = useState<string[]>(() =>
    readSubscribedTopics(),
  );

  const handleFollow = useCallback(() => {
    toggleFollowing(author);
    setFollowing((v) => !v);
  }, [author]);

  const handleSubscribe = useCallback(() => {
    toggleSubscribedAuthor(author);
    setSubscribed((v) => !v);
  }, [author]);

  const handleTopic = useCallback((key: string) => {
    toggleSubscribedTopic(key);
    setTopicSubs(() => readSubscribedTopics());
  }, []);

  return (
    <div className="flex min-h-full flex-col">
      {/* 顶栏：返回 + 标题 */}
      <div className="flex min-h-9 items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          aria-label="返回"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <ChevronLeftIcon className="size-4" />
        </button>
        <h1 className="text-lg font-bold">个人主页</h1>
      </div>

      {/* 作者信息 */}
      <div className="mt-2 flex flex-col items-center gap-2 rounded-xl bg-card py-6 shadow-[var(--shadow-card)]">
        <span
          className="flex size-16 items-center justify-center rounded-full text-xl font-bold"
          style={{
            backgroundColor: `${profile.color}1f`,
            color: profile.color,
          }}
        >
          {profile.initial}
        </span>
        <span className="text-lg font-bold">{profile.name}</span>
        {profile.topicCoverage.length > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-1.5 px-4">
            {profile.topicCoverage.map((t) => {
              const active = topicSubs.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => handleTopic(t)}
                  className={cn(
                    "flex items-center gap-1 rounded-full px-2.5 py-0.5 text-mini font-medium transition-colors",
                    active
                      ? "bg-chart-3/10 text-chart-3"
                      : "bg-muted/60 text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span
                    className={cn(
                      "size-1 rounded-full",
                      active ? "bg-chart-3" : "bg-muted-foreground/50",
                    )}
                  />
                  {t}
                  {active && <BellRingIcon className="size-2.5" />}
                </button>
              );
            })}
          </div>
        )}

        {/* 数据指标 */}
        <div className="mt-2 flex items-center gap-5 text-xs text-muted-foreground">
          <span className="text-center">
            <b className="block text-sm text-foreground">
              {profile.postCount}
            </b>
            笔记
          </span>
          <span className="text-center">
            <b className="block text-sm text-foreground">
              {formatCount(profile.totalLikes)}
            </b>
            获赞
          </span>
          <span className="text-center">
            <b className="block text-sm text-foreground">
              {formatCount(profile.followerCount)}
            </b>
            粉丝
          </span>
        </div>

        {/* 关注 / 订阅 */}
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={handleFollow}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-5 py-1.5 text-sm font-semibold transition-colors",
              following
                ? "border border-border-default text-muted-foreground hover:bg-muted/60"
                : "bg-primary text-primary-foreground hover:bg-primary/90",
            )}
          >
            {following && <CheckIcon className="size-3.5" />}
            {following ? "已关注" : "+ 关注"}
          </button>
          <button
            type="button"
            onClick={handleSubscribe}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-5 py-1.5 text-sm font-semibold transition-colors",
              subscribed
                ? "border border-border-default text-muted-foreground hover:bg-muted/60"
                : "bg-chart-3 text-white hover:bg-chart-3/90",
            )}
          >
            <BellRingIcon className="size-3.5" />
            {subscribed ? "已订阅" : "订阅"}
          </button>
        </div>
      </div>

      {/* 笔记列表 */}
      <div className="mt-4">
        <h2 className="mb-2 text-sm font-semibold text-muted-foreground">
          笔记 · {profile.postCount}
        </h2>
        {profile.posts.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <StarIcon className="size-6 opacity-50" />
            <p className="text-sm">还没有笔记</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4">
            {profile.posts.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => onOpenPost(p)}
                className="group overflow-hidden rounded-xl bg-card text-left shadow-[var(--shadow-card)] transition-[transform,box-shadow] duration-base hover:-translate-y-0.5 hover:shadow-[var(--shadow-elevated)]"
              >
                <div
                  className="relative aspect-square w-full overflow-hidden"
                  style={{
                    background: `linear-gradient(135deg, ${p.coverGradient.join(", ")})`,
                  }}
                >
                  <img
                    src={p.coverUrl}
                    alt={p.title}
                    loading="lazy"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display =
                        "none";
                    }}
                    className="size-full object-cover transition-transform duration-slow group-hover:scale-[1.03]"
                  />
                  <span className="absolute bottom-1.5 left-1.5 line-clamp-1 max-w-[90%] rounded bg-black/40 px-1.5 py-0.5 text-micro text-white">
                    {p.title}
                  </span>
                </div>
                <div className="flex items-center gap-2 p-2 text-mini text-muted-foreground">
                  <span className="line-clamp-1 flex-1">{p.title}</span>
                  <span className="flex shrink-0 items-center gap-0.5">
                    <HeartIcon className="size-3" />
                    {formatCount(p.likesCount)}
                  </span>
                  <span className="flex shrink-0 items-center gap-0.5">
                    <MessageCircleIcon className="size-3" />
                    {formatCount(p.commentsCount)}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
