/**
 * 发现社区数据层 —— 桌面端「灵感广场」。
 *
 * 对齐移动端 SquareCatalog.kt 的设计：帖子卡片（小红书式图文帖 + 小程序分享帖）
 * 混合展示，卡片封面优先用图片 URL，否则回退到渐变底。
 *
 * 内容组织采用 WorkBuddy Discover 的 registry / categories / featured 三区结构，
 * 便于运营直接改 JSON 增删内容：
 *   - REGISTRY   帖子总库（每个帖子带真实 topic 分类）
 *   - CATEGORIES 分类定义（key / label / color）
 *   - FEATURED   推荐 tab 的精选帖子 id 列表（可随意调整推送顺序）
 *
 * 数据来源三级回退：
 *   1. 服务端 `/square/feed`（若配置了 squareBaseUrl，后台可随意增删改）
 *   2. 本地缓存（localStorage）
 *   3. 内置种子（永远有内容、不空屏）
 */

import { communityAssetURL } from "./community-assets";

export type CommunityPostKind = "post" | "mini-app" | "";

/** 单条评论。 */
export interface CommunityComment {
  id: string;
  content: string;
  author: string;
  authorInitial: string;
  authorColor: string;
  createdAt: number;
}

export interface CommunityPost {
  id: string;
  title: string;
  content: string;
  author: string;
  authorInitial: string;
  authorColor: string;
  likesCount: number;
  commentsCount: number;
  tag: string;
  tagColor: string;
  /** 封面图 URL（优先于 coverGradient 展示）。 */
  coverUrl: string;
  /** 多图（详情页轮播）。缺省时退化为 [coverUrl] 单图。 */
  images?: string[];
  /** 渐变封面（coverUrl 为空时用），["#RRGGBB", ...]。 */
  coverGradient: string[];
  /** 封面高度（px），瀑布流产生错落感。 */
  coverHeight: number;
  kind: CommunityPostKind;
  /** 关联可复刻物 id（mini-app / skill / routine）。 */
  appRef: string;
  appKind: string;
  /** 复刻定价（积分）；0 = 免费复刻。 */
  priceCredits: number;
  /** 创建时间 epoch millis。 */
  createdAt: number;
  /** 分类 key（recommend/职场/效率/生活/学习/购物/科技/游戏/关注）。 */
  topic: string;
  /** 预置评论（可选）。未提供时用默认评论生成器兜底。 */
  comments?: CommunityComment[];
}

/** 服务端下发的帖子字段（颜色用 "#RRGGBB" 字符串，后台可随意编辑）。 */
interface CommunityPostDto {
  id?: string;
  title?: string;
  content?: string;
  author?: string;
  authorInitial?: string;
  authorColor?: string;
  likesCount?: number;
  commentsCount?: number;
  tag?: string;
  tagColor?: string;
  coverUrl?: string;
  images?: string[];
  coverGradient?: string[];
  coverHeight?: number;
  kind?: string;
  appRef?: string;
  appKind?: string;
  priceCredits?: number;
  createdAt?: number;
  topic?: string;
}

interface CommunityFeedDto {
  posts?: CommunityPostDto[];
  has_more?: boolean;
}

const CACHE_KEY = "echo.community.feed.v1";

const DEFAULT_GRADIENT = ["#667EEA", "#764BA2"];

function parseColor(hex: string | undefined, fallback: string): string {
  if (!hex) return fallback;
  return /^#[0-9a-fA-F]{6}$/.test(hex.trim()) ? hex.trim() : fallback;
}

function toPost(dto: CommunityPostDto): CommunityPost {
  const title = dto.title ?? "";
  const gradient = Array.isArray(dto.coverGradient)
    ? dto.coverGradient.filter((c) => /^#[0-9a-fA-F]{6}$/.test(c))
    : [];
  return {
    id: dto.id || title || `post-${Math.random().toString(36).slice(2, 8)}`,
    title,
    content: dto.content ?? "",
    author: dto.author ?? "",
    authorInitial: dto.authorInitial || (dto.author ?? "?").slice(0, 1),
    authorColor: parseColor(dto.authorColor, "#7C6FF0"),
    likesCount: dto.likesCount ?? 0,
    commentsCount: dto.commentsCount ?? 0,
    tag: dto.tag ?? "",
    tagColor: parseColor(dto.tagColor, "#7C6FF0"),
    coverUrl: dto.coverUrl ?? "",
    images: Array.isArray(dto.images) ? dto.images.filter(Boolean) : undefined,
    coverGradient: gradient.length ? gradient : DEFAULT_GRADIENT,
    coverHeight: Math.min(260, Math.max(120, dto.coverHeight ?? 160)),
    kind: (dto.kind as CommunityPostKind) ?? "",
    appRef: dto.appRef ?? "",
    appKind: dto.appKind ?? "",
    priceCredits: dto.priceCredits ?? 0,
    createdAt: dto.createdAt ?? Date.now(),
    topic: dto.topic || "recommend",
  };
}

/** 数值缩写：<1k 直显，>=1k 用 1.2k / 3.4w 形式。 */
export function formatCount(count: number): string {
  if (count < 1000) return String(count);
  if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
  return `${(count / 10000).toFixed(1)}w`;
}

/** 相对时间："刚刚 / N 分钟前 / N 小时前 / N 天前"。 */
export function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  return `${Math.floor(hour / 24)} 天前`;
}

/** 评论示例池（按帖子 id 确定性取材，保证同一帖子每次评论一致）。 */
const COMMENT_POOL: Array<Omit<CommunityComment, "id" | "createdAt">> = [
  {
    author: "橙子",
    authorInitial: "橙",
    authorColor: "#FC466B",
    content: "太实用了，已经复刻到我的工作台！",
  },
  {
    author: "阿北",
    authorInitial: "阿",
    authorColor: "#3F5EFB",
    content: "思路很清晰，按这个改也能用。",
  },
  {
    author: "Momo",
    authorInitial: "M",
    authorColor: "#00C6FF",
    content: "正好需要，感谢分享！",
  },
  {
    author: "小满",
    authorInitial: "小",
    authorColor: "#F2994A",
    content: "收藏了，明天试试看。",
  },
  {
    author: "阿哲",
    authorInitial: "哲",
    authorColor: "#8E2DE2",
    content: "有没有更详细的参数配置教程？",
  },
  {
    author: "鲸鱼",
    authorInitial: "鲸",
    authorColor: "#71B280",
    content: "效率提升明显，老板都夸了。",
  },
  {
    author: "Luna",
    authorInitial: "L",
    authorColor: "#FF6A5B",
    content: "这个场景我也有痛点，学到了。",
  },
  {
    author: "石头",
    authorInitial: "石",
    authorColor: "#11998E",
    content: "请问能接入我自己的数据源吗？",
  },
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/** 为帖子生成确定性示例评论（2~4 条），保证开箱即有内容可看。 */
export function defaultComments(
  postId: string,
  now = Date.now(),
): CommunityComment[] {
  const seed = hashString(postId);
  const count = 2 + (seed % 3);
  return Array.from({ length: count }, (_, i) => {
    const item = COMMENT_POOL[(seed + i * 3) % COMMENT_POOL.length]!;
    return {
      ...item,
      id: `${postId}.c${i}`,
      createdAt: now - ((seed % 60) + i * 47) * 60_000,
    };
  });
}

/** 用户自评论持久化 key：postId -> CommunityComment[]。 */
const USER_COMMENTS_KEY = "echo.community.user-comments.v1";

export function readUserComments(): Record<string, CommunityComment[]> {
  try {
    const raw = window.localStorage.getItem(USER_COMMENTS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, CommunityComment[]>) : {};
  } catch {
    return {};
  }
}

function writeUserComments(map: Record<string, CommunityComment[]>) {
  try {
    window.localStorage.setItem(USER_COMMENTS_KEY, JSON.stringify(map));
  } catch {
    /* ignore */
  }
}

/** 追加一条用户评论并持久化，返回评论数。 */
export function addUserComment(
  postId: string,
  content: string,
  author: string,
  authorInitial: string,
  authorColor = "#7C6FF0",
): number {
  const map = readUserComments();
  const list = map[postId] ?? [];
  list.push({
    id: `u.${Date.now()}.${Math.random().toString(36).slice(2, 6)}`,
    content,
    author,
    authorInitial,
    authorColor,
    createdAt: Date.now(),
  });
  map[postId] = list;
  writeUserComments(map);
  return list.length;
}

/** 合并默认评论 + 用户评论，按时间倒序（最新在前）。 */
export function mergeComments(post: CommunityPost): CommunityComment[] {
  const defaults = post.comments ?? defaultComments(post.id);
  const users = readUserComments()[post.id] ?? [];
  return [...defaults, ...users].sort((a, b) => b.createdAt - a.createdAt);
}

/** 已复刻帖子 id 持久化 key。 */
const FORKED_KEY = "echo.community.forked.v1";

export function readForked(): string[] {
  try {
    const raw = window.localStorage.getItem(FORKED_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeForked(ids: string[]) {
  try {
    window.localStorage.setItem(FORKED_KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** 把帖子标记为已复刻（本地持久化），返回最新已复刻列表。 */
export function markForked(id: string): string[] {
  const next = Array.from(new Set([...readForked(), id]));
  writeForked(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 关注（作者）持久化                                                  */
/* ------------------------------------------------------------------ */

const FOLLOWING_KEY = "echo.community.following.v1";

export function readFollowing(): string[] {
  try {
    const raw = window.localStorage.getItem(FOLLOWING_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeFollowing(ids: string[]) {
  try {
    window.localStorage.setItem(FOLLOWING_KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** 切换关注某作者，返回最新关注列表。 */
export function toggleFollowing(author: string): string[] {
  const next = readFollowing().includes(author)
    ? readFollowing().filter((a) => a !== author)
    : [...readFollowing(), author];
  writeFollowing(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 收藏（帖子）持久化                                                  */
/* ------------------------------------------------------------------ */

const FAVORITES_KEY = "echo.community.favorites.v1";

export function readFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(FAVORITES_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

export function writeFavorites(ids: string[]) {
  try {
    window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(ids));
  } catch {
    /* ignore */
  }
}

/** 切换收藏某帖子，返回最新收藏列表。 */
export function toggleFavorite(id: string): string[] {
  const next = readFavorites().includes(id)
    ? readFavorites().filter((x) => x !== id)
    : [...readFavorites(), id];
  writeFavorites(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 用户发布（localStorage 持久化，发布后进入全量 feed）                 */
/* ------------------------------------------------------------------ */

const PUBLISHED_KEY = "echo.community.published.v1";

export function readPublished(): CommunityPost[] {
  try {
    const raw = window.localStorage.getItem(PUBLISHED_KEY);
    return raw ? (JSON.parse(raw) as CommunityPost[]) : [];
  } catch {
    return [];
  }
}

function writePublished(posts: CommunityPost[]) {
  try {
    window.localStorage.setItem(PUBLISHED_KEY, JSON.stringify(posts));
  } catch {
    /* ignore */
  }
}

/** 发布一条新帖，返回生成的帖子（含默认头像/分类/渐变）。 */
export function addPublishedPost(input: {
  title: string;
  content: string;
  tag: string;
  topic: string;
  coverUrl?: string;
  priceCredits?: number;
  appRef?: string;
  appKind?: string;
}): CommunityPost {
  const post: CommunityPost = {
    id: `p.${Date.now()}.${Math.random().toString(36).slice(2, 6)}`,
    title: input.title,
    content: input.content,
    author: "我",
    authorInitial: "我",
    authorColor: "#FC466B",
    likesCount: 0,
    commentsCount: 0,
    tag: input.tag || "原创",
    tagColor: "#7C6FF0",
    coverUrl: input.coverUrl ?? "",
    coverGradient: ["#7C6FF0", "#4A00E0"],
    coverHeight: 180,
    kind: input.appRef ? "mini-app" : "post",
    appRef: input.appRef ?? "",
    appKind: input.appKind ?? "",
    priceCredits: input.priceCredits ?? 0,
    createdAt: Date.now(),
    topic: input.topic || "life",
    comments: [],
  };
  const list = readPublished();
  list.unshift(post);
  writePublished(list);
  return post;
}

/** 分类定义：key = 过滤值，label = tab 展示名，color = 分类主题色。 */
export interface CommunityCategory {
  key: string;
  label: string;
  color: string;
}

/** 分类 tab（推荐 + 六大内容分类 + 关注流）。 */
export const COMMUNITY_CATEGORIES: CommunityCategory[] = [
  { key: "recommend", label: "推荐", color: "#FC466B" },
  { key: "following", label: "关注", color: "#FF6A5B" },
  { key: "work", label: "职场", color: "#3F5EFB" },
  { key: "efficiency", label: "效率", color: "#00C6FF" },
  { key: "life", label: "生活", color: "#FF6A5B" },
  { key: "study", label: "学习", color: "#71B280" },
  { key: "shopping", label: "购物", color: "#F2994A" },
  { key: "tech", label: "科技", color: "#8E2DE2" },
  { key: "game", label: "游戏", color: "#5B8C5A" },
];

/** 顶栏 tab 的轻量视图（{key, label}），供页面渲染。 */
export const COMMUNITY_TABS: Array<{ key: string; label: string }> =
  COMMUNITY_CATEGORIES.map(({ key, label }) => ({ key, label }));

/* ------------------------------------------------------------------ */
/* 真实内容媒体：每帖专属封面图（一一对应，杜绝跨帖共用）              */
/* ------------------------------------------------------------------ */

/** 帖子 id → 专属封面图（单图，详情页轮播即该图）。 */
const PER_POST_IMG: Record<string, string> = {
  "seed.1": communityAssetURL("memory-video(1).jpg"),
  "seed.2": communityAssetURL("food-delivery(1).jpg"),
  "seed.3": communityAssetURL("weekly-report(1).jpg"),
  "seed.4": communityAssetURL("voice-reply.jpg"),
  "seed.5": communityAssetURL("price-watch(1).jpg"),
  "seed.6": communityAssetURL("smart-home.jpg"),
  "seed.7": communityAssetURL("travel-plan(1).jpg"),
  "seed.8": communityAssetURL("study-paper(1).jpg"),
  "seed.9": communityAssetURL("resume(1).jpg"),
  "seed.10": communityAssetURL("email-draft.jpg"),
  "seed.11": communityAssetURL("plan-tomorrow.jpg"),
  "seed.12": communityAssetURL("language-coach.jpg"),
  "seed.13": communityAssetURL("game-auto-daily.jpg"),
  "seed.14": communityAssetURL("game-guide(1).jpg"),
  "seed.15": communityAssetURL("meeting-notes.jpg"),
  "seed.16": communityAssetURL("coupon.jpg"),
  "seed.17": communityAssetURL("weekend.jpg"),
  "seed.18": communityAssetURL("wrong-questions.jpg"),
  "seed.19": communityAssetURL("mock-interview.jpg"),
  "seed.20": communityAssetURL("gacha.jpg"),
  "seed.21": communityAssetURL("todo.jpg"),
  "seed.22": communityAssetURL("daily-album.jpg"),
  "seed.23": communityAssetURL("web-summary.jpg"),
  "seed.24": communityAssetURL("weekly-highlights.jpg"),
};

/** 为帖子补全专属封面（未配置图片的帖子保持原渐变）。 */
function applyPostMedia(post: CommunityPost): CommunityPost {
  const cover = post.coverUrl || PER_POST_IMG[post.id] || "";
  if (!cover) return { ...post, images: post.images ?? [] };
  return { ...post, coverUrl: cover, images: [cover] };
}

/** 帖子总库（registry）：每个帖子带真实 topic 分类。 */
const REGISTRY: CommunityPost[] = [
  {
    id: "seed.1",
    title: "让 AI 每天自动整理相册，生成回忆视频",
    content: "输入时间范围，自动挑选照片、配乐并剪辑成一段回忆短片。",
    author: "影像助手",
    authorInitial: "影",
    authorColor: "#FC466B",
    likesCount: 1200,
    commentsCount: 88,
    tag: "自动化",
    tagColor: "#F2994A",
    coverUrl: "",
    coverGradient: ["#667EEA", "#764BA2"],
    coverHeight: 200,
    kind: "mini-app",
    appRef: "memory-video",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 3_600_000,
    topic: "life",
  },
  {
    id: "seed.2",
    title: "3 步搭一个会订外卖的助手",
    content: "绑定地址后，一句话就能帮你下单常点的外卖。",
    author: "效率玩家",
    authorInitial: "效",
    authorColor: "#11998E",
    likesCount: 856,
    commentsCount: 41,
    tag: "教程",
    tagColor: "#00C6FF",
    coverUrl: "",
    coverGradient: ["#11998E", "#38EF7D"],
    coverHeight: 150,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 7_200_000,
    topic: "efficiency",
  },
  {
    id: "seed.3",
    title: "自动写一周周报，老板直呼专业",
    content: "汇总聊天记录与任务清单，一键生成结构化周报。",
    author: "打工侠",
    authorInitial: "打",
    authorColor: "#FC466B",
    likesCount: 2300,
    commentsCount: 156,
    tag: "职场",
    tagColor: "#3F5EFB",
    coverUrl: "",
    coverGradient: ["#FC466B", "#3F5EFB"],
    coverHeight: 220,
    kind: "mini-app",
    appRef: "weekly-report",
    appKind: "mini-app",
    priceCredits: 5,
    createdAt: Date.now() - 12_000_000,
    topic: "work",
  },
  {
    id: "seed.4",
    title: "用语音唤醒助手，开车时也能回消息",
    content: "按下说话，自动识别收件人、调整语气并发送。",
    author: "车载达人",
    authorInitial: "车",
    authorColor: "#F2994A",
    likesCount: 634,
    commentsCount: 29,
    tag: "语音",
    tagColor: "#F2C94C",
    coverUrl: "",
    coverGradient: ["#F2994A", "#F2C94C"],
    coverHeight: 160,
    kind: "post",
    appRef: "voice-reply",
    appKind: "skill",
    priceCredits: 0,
    createdAt: Date.now() - 18_000_000,
    topic: "efficiency",
  },
  {
    id: "seed.5",
    title: "自动比价，618 我省了 2000+",
    content: "监控历史价格、优惠券与平台活动，降价即提醒。",
    author: "省钱 Bot",
    authorInitial: "省",
    authorColor: "#00C6FF",
    likesCount: 3100,
    commentsCount: 203,
    tag: "购物",
    tagColor: "#0072FF",
    coverUrl: "",
    coverGradient: ["#00C6FF", "#0072FF"],
    coverHeight: 190,
    kind: "mini-app",
    appRef: "price-watch",
    appKind: "mini-app",
    priceCredits: 2,
    createdAt: Date.now() - 24_000_000,
    topic: "shopping",
  },
  {
    id: "seed.6",
    title: "接入智能家居，一句话控制全屋",
    content: "绑定设备后，语音或文字即可控制灯光与空调。",
    author: "极客居",
    authorInitial: "极",
    authorColor: "#8E2DE2",
    likesCount: 1500,
    commentsCount: 97,
    tag: "IoT",
    tagColor: "#4A00E0",
    coverUrl: "",
    coverGradient: ["#8E2DE2", "#4A00E0"],
    coverHeight: 170,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 36_000_000,
    topic: "tech",
  },
  {
    id: "seed.7",
    title: "生成旅行攻略，细到每天照着走",
    content: "输入目的地与预算，自动规划路线并生成可分享清单。",
    author: "旅行灵感",
    authorInitial: "旅",
    authorColor: "#EE9CA7",
    likesCount: 987,
    commentsCount: 54,
    tag: "生活",
    tagColor: "#FF6A5B",
    coverUrl: "",
    coverGradient: ["#FFB199", "#FF6A5B"],
    coverHeight: 210,
    kind: "mini-app",
    appRef: "travel-plan",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 48_000_000,
    topic: "life",
  },
  {
    id: "seed.8",
    title: "让助手帮你读论文，10 分钟抓重点",
    content: "读取 PDF 与网页，自动抽取结论与可引用观点。",
    author: "学术喵",
    authorInitial: "学",
    authorColor: "#134E5E",
    likesCount: 742,
    commentsCount: 33,
    tag: "学习",
    tagColor: "#71B280",
    coverUrl: "",
    coverGradient: ["#134E5E", "#71B280"],
    coverHeight: 145,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 60_000_000,
    topic: "study",
  },
  {
    id: "seed.9",
    title: "写简历的十个隐藏技巧，HR 看一遍就约面",
    content: "用 STAR 法则重写项目经历，自动标注关键词与量化成果。",
    author: "入职顾问",
    authorInitial: "入",
    authorColor: "#3F5EFB",
    likesCount: 1680,
    commentsCount: 112,
    tag: "求职",
    tagColor: "#3F5EFB",
    coverUrl: "",
    coverGradient: ["#3F5EFB", "#7C6FF0"],
    coverHeight: 180,
    kind: "mini-app",
    appRef: "resume-writer",
    appKind: "mini-app",
    priceCredits: 3,
    createdAt: Date.now() - 90_000_000,
    topic: "work",
  },
  {
    id: "seed.10",
    title: "自动回复常见邮件，省下每天 30 分钟",
    content: "学习你的回复习惯，草拟并一键确认发送，越用越像你。",
    author: "邮件管家",
    authorInitial: "邮",
    authorColor: "#00C6FF",
    likesCount: 1105,
    commentsCount: 67,
    tag: "办公",
    tagColor: "#00C6FF",
    coverUrl: "",
    coverGradient: ["#00C6FF", "#0072FF"],
    coverHeight: 155,
    kind: "mini-app",
    appRef: "email-draft",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 120_000_000,
    topic: "efficiency",
  },
  {
    id: "seed.11",
    title: "睡前 5 分钟，让 AI 帮你规划明日清单",
    content: "结合日历与待办，生成明日优先级清单，早晨醒来照着做。",
    author: "清早计划",
    authorInitial: "清",
    authorColor: "#FF6A5B",
    likesCount: 920,
    commentsCount: 45,
    tag: "生活",
    tagColor: "#FF6A5B",
    coverUrl: "",
    coverGradient: ["#FFB199", "#FF6A5B"],
    coverHeight: 165,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 150_000_000,
    topic: "life",
  },
  {
    id: "seed.12",
    title: "AI 帮你学一门外语，每天打卡 10 分钟",
    content: "根据你的水平定制对话练习，实时纠音并生成复习卡。",
    author: "语言教练",
    authorInitial: "语",
    authorColor: "#71B280",
    likesCount: 1330,
    commentsCount: 89,
    tag: "学习",
    tagColor: "#71B280",
    coverUrl: "",
    coverGradient: ["#134E5E", "#38EF7D"],
    coverHeight: 195,
    kind: "mini-app",
    appRef: "language-coach",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 180_000_000,
    topic: "study",
  },
  {
    id: "seed.13",
    title: "AI 陪你打副本，自动做每日任务",
    content: "接管日常重复刷本，自动补血补蓝、拾取掉落，晚上回家直接收菜。",
    author: "游戏管家",
    authorInitial: "游",
    authorColor: "#5B8C5A",
    likesCount: 2100,
    commentsCount: 173,
    tag: "手游助手",
    tagColor: "#5B8C5A",
    coverUrl: "",
    coverGradient: ["#5B8C5A", "#2E5E4E"],
    coverHeight: 200,
    kind: "mini-app",
    appRef: "game-auto-daily",
    appKind: "mini-app",
    priceCredits: 3,
    createdAt: Date.now() - 210_000_000,
    topic: "game",
  },
  {
    id: "seed.14",
    title: "一键生成游戏攻略，新区开荒不迷路",
    content: "输入版本与职业，自动整理天赋、毕业装与刷图路线，照着走就行。",
    author: "攻略站",
    authorInitial: "策",
    authorColor: "#8E2DE2",
    likesCount: 1450,
    commentsCount: 96,
    tag: "攻略",
    tagColor: "#5B8C5A",
    coverUrl: "",
    coverGradient: ["#8E2DE2", "#4A00E0"],
    coverHeight: 175,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 240_000_000,
    topic: "game",
  },
  {
    id: "seed.15",
    title: "一键把会议纪要按照老板喜好排版",
    content: "识别重点、拆分待办，套用公司模板，发给老板前自动检查错别字。",
    author: "会议小助手",
    authorInitial: "会",
    authorColor: "#3F5EFB",
    likesCount: 1300,
    commentsCount: 71,
    tag: "办公",
    tagColor: "#00C6FF",
    coverUrl: "",
    coverGradient: ["#00C6FF", "#0072FF"],
    coverHeight: 185,
    kind: "mini-app",
    appRef: "meeting-notes",
    appKind: "mini-app",
    priceCredits: 2,
    createdAt: Date.now() - 270_000_000,
    topic: "work",
  },
  {
    id: "seed.16",
    title: "大促前自动领券，蹲点抢 5 折",
    content: "定时脚本监控领券入口，到点自动领取并推送提醒，不再错过优惠。",
    author: "薅羊毛王",
    authorInitial: "薅",
    authorColor: "#F2994A",
    likesCount: 1890,
    commentsCount: 142,
    tag: "购物",
    tagColor: "#0072FF",
    coverUrl: "",
    coverGradient: ["#F2994A", "#F2C94C"],
    coverHeight: 170,
    kind: "post",
    appRef: "coupon-bot",
    appKind: "skill",
    priceCredits: 0,
    createdAt: Date.now() - 300_000_000,
    topic: "shopping",
  },
  {
    id: "seed.17",
    title: "周末不知道去哪？让 AI 按天气推荐",
    content: "结合天气、距离与兴趣，从周边好玩的地方里挑 3 个给你。",
    author: "周末去哪",
    authorInitial: "周",
    authorColor: "#FF6A5B",
    likesCount: 760,
    commentsCount: 38,
    tag: "生活",
    tagColor: "#FF6A5B",
    coverUrl: "",
    coverGradient: ["#FFB199", "#FF6A5B"],
    coverHeight: 160,
    kind: "mini-app",
    appRef: "weekend-pick",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 330_000_000,
    topic: "life",
  },
  {
    id: "seed.18",
    title: "错题本自动归类，考前针对性刷",
    content: "把错题拍照录入，AI 按知识点归类并生成薄弱点专项练习。",
    author: "上岸学长",
    authorInitial: "岸",
    authorColor: "#71B280",
    likesCount: 1050,
    commentsCount: 63,
    tag: "学习",
    tagColor: "#71B280",
    coverUrl: "",
    coverGradient: ["#134E5E", "#38EF7D"],
    coverHeight: 190,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 360_000_000,
    topic: "study",
  },
  {
    id: "seed.19",
    title: "面试前 10 分钟，模拟 HR 提问",
    content: "按岗位生成高频问题，实时点评你的回答并给出更优话术。",
    author: "面试教练",
    authorInitial: "面",
    authorColor: "#7C6FF0",
    likesCount: 1560,
    commentsCount: 118,
    tag: "求职",
    tagColor: "#3F5EFB",
    coverUrl: "",
    coverGradient: ["#7C6FF0", "#4A00E0"],
    coverHeight: 175,
    kind: "mini-app",
    appRef: "mock-interview",
    appKind: "mini-app",
    priceCredits: 3,
    createdAt: Date.now() - 390_000_000,
    topic: "work",
  },
  {
    id: "seed.20",
    title: "游戏抽卡保底计算器，别上头",
    content: "输入抽数快速算概率与期望，帮你冷静判断要不要继续氪。",
    author: "数据党玩家",
    authorInitial: "数",
    authorColor: "#5B8C5A",
    likesCount: 880,
    commentsCount: 52,
    tag: "游戏",
    tagColor: "#5B8C5A",
    coverUrl: "",
    coverGradient: ["#5B8C5A", "#2E5E4E"],
    coverHeight: 165,
    kind: "post",
    appRef: "gacha-calc",
    appKind: "skill",
    priceCredits: 0,
    createdAt: Date.now() - 420_000_000,
    topic: "game",
  },
  {
    id: "seed.21",
    title: "会议说一句话，自动生成待办清单",
    content: "语音输入一句会议要点，拆解成可执行任务并排好优先级。",
    author: "效率加速器",
    authorInitial: "速",
    authorColor: "#00C6FF",
    likesCount: 690,
    commentsCount: 31,
    tag: "效率",
    tagColor: "#00C6FF",
    coverUrl: "",
    coverGradient: ["#11998E", "#38EF7D"],
    coverHeight: 150,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 450_000_000,
    topic: "efficiency",
  },
  {
    id: "seed.22",
    title: "睡前把今日照片存进专属相册",
    content: "按日期与人脸自动归档，整理成可按回忆章节浏览的相册。",
    author: "生活记录员",
    authorInitial: "纪",
    authorColor: "#FC466B",
    likesCount: 540,
    commentsCount: 26,
    tag: "生活",
    tagColor: "#FFB199",
    coverUrl: "",
    coverGradient: ["#FFB199", "#FC466B"],
    coverHeight: 180,
    kind: "mini-app",
    appRef: "daily-album",
    appKind: "mini-app",
    priceCredits: 0,
    createdAt: Date.now() - 480_000_000,
    topic: "life",
  },
  {
    id: "seed.23",
    title: "把长网页总结成 3 点速览",
    content: "粘贴链接，AI 提取核心结论，适合通勤路上快速掌握。",
    author: "资讯快手",
    authorInitial: "快",
    authorColor: "#8E2DE2",
    likesCount: 1120,
    commentsCount: 74,
    tag: "科技",
    tagColor: "#8E2DE2",
    coverUrl: "",
    coverGradient: ["#8E2DE2", "#4A00E0"],
    coverHeight: 155,
    kind: "post",
    appRef: "",
    appKind: "",
    priceCredits: 0,
    createdAt: Date.now() - 510_000_000,
    topic: "tech",
  },
  {
    id: "seed.24",
    title: "周报不会写？自动从聊天里提炼亮点",
    content: "扫描本周聊天与日志，提炼 3 个可量化成果，直接粘贴进周报。",
    author: "周报救星",
    authorInitial: "救",
    authorColor: "#3F5EFB",
    likesCount: 980,
    commentsCount: 58,
    tag: "职场",
    tagColor: "#3F5EFB",
    coverUrl: "",
    coverGradient: ["#3F5EFB", "#7C6FF0"],
    coverHeight: 170,
    kind: "mini-app",
    appRef: "weekly-highlights",
    appKind: "mini-app",
    priceCredits: 2,
    createdAt: Date.now() - 540_000_000,
    topic: "work",
  },
];

/** 内置种子：无远端/缓存时使用全量帖子库（支持分页与分类）。 */
const SEED: CommunityPost[] = [...REGISTRY];

function feedFromDto(payload: unknown): CommunityPost[] {
  const dto = payload as CommunityFeedDto;
  if (!Array.isArray(dto?.posts)) return [];
  const posts = dto.posts.map(toPost).filter((p) => p.title);
  return posts;
}

function readCache(): CommunityPost[] {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return [];
    return feedFromDto(JSON.parse(raw));
  } catch {
    return [];
  }
}

function writeCache(posts: CommunityPost[]) {
  try {
    window.localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ posts, has_more: false }),
    );
  } catch {
    /* ignore */
  }
}

/** 单页条数（无限滚动每批加载量）。 */
export const PAGE_SIZE = 8;

/** 排序："latest" 按时间倒序，"hot" 按点赞倒序。 */
export type CommunitySort = "latest" | "hot";

export interface CommunityFeedResult {
  posts: CommunityPost[];
  hasMore: boolean;
  total: number;
}

/** 按排序规则排序后返回新数组。 */
function sortPosts(
  posts: CommunityPost[],
  sort: CommunitySort,
): CommunityPost[] {
  return [...posts].sort((a, b) =>
    sort === "hot" ? b.likesCount - a.likesCount : b.createdAt - a.createdAt,
  );
}

/** 按分类过滤：recommend 全量，following 只看已关注作者，其余按 topic。 */
function filterByTopic(posts: CommunityPost[], topic: string): CommunityPost[] {
  if (topic === "recommend") return posts;
  if (topic === "following") {
    const following = readFollowing();
    return posts.filter((p) => following.includes(p.author));
  }
  return posts.filter((p) => p.topic === topic);
}

/** 合并用户发布 + 基础库，并补全真实媒体。 */
function buildPool(base: CommunityPost[]): CommunityPost[] {
  const published = readPublished();
  return [...published, ...base].map(applyPostMedia);
}

/**
 * 拉取社区 feed，支持分类 / 排序 / 分页。
 * 优先远端 `/square/feed`（可选），失败回退缓存，再回退内置种子。
 */
export async function fetchCommunityFeed(
  topic = "recommend",
  sort: CommunitySort = "latest",
  offset = 0,
): Promise<CommunityFeedResult> {
  const cached = readCache();

  // 可选：配置了 squareBaseUrl 时尝试远端。桌面端通常没有，跳过以保持轻量。
  let remoteBase = "";
  try {
    remoteBase = window.localStorage.getItem("echo.squareBaseUrl") ?? "";
  } catch {
    /* ignore */
  }

  let base: CommunityPost[] = [];
  if (remoteBase.trim()) {
    try {
      const params = [`sort=${sort}`];
      if (topic !== "recommend") params.push(`topic=${topic}`);
      const res = await fetch(
        `${remoteBase.trim().replace(/\/$/, "")}/square/feed?${params.join("&")}`,
      );
      if (res.ok) {
        const posts = feedFromDto(await res.json());
        if (posts.length > 0) {
          writeCache(posts);
          base = posts;
        }
      }
    } catch {
      /* 网络失败 → 回退缓存/种子 */
    }
  }

  if (base.length === 0) base = cached.length > 0 ? cached : SEED;

  const pool = buildPool(base);
  const filtered = filterByTopic(pool, topic);
  const sorted = sortPosts(filtered, sort);
  const total = sorted.length;
  const page = sorted.slice(offset, offset + PAGE_SIZE);
  return { posts: page, hasMore: offset + page.length < total, total };
}
