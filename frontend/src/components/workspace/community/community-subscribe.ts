/**
 * 发现社区「订阅」数据层 —— 主题订阅 + 作者资料聚合。
 *
 * 订阅体系（关注是轻量交互，订阅是可长期追踪的关系）：
 *   - 关注  作者  → following（见 community-data.ts，卡片上的「关注」）
 *   - 订阅  主题  → 本文档 SUBSCRIBED_TOPICS，出现在「关注」tab 的订阅栏
 *   - 订阅  作者  → 本文档 SUBSCRIBED_AUTHORS，长期追踪该作者更新
 *
 * 作者资料聚合：把全量 feed 里某作者的所有帖子聚合成一份个人主页资料，
 * 供独立个人主页（CommunityProfile）与订阅卡片使用。
 */

import {
  type CommunityPost,
  readFollowing,
  toggleFollowing,
} from "./community-data";

/* ------------------------------------------------------------------ */
/* 订阅主题持久化                                                      */
/* ------------------------------------------------------------------ */

const SUBSCRIBED_TOPICS_KEY = "echo.community.subscribe-topics.v1";

export function readSubscribedTopics(): string[] {
  try {
    const raw = window.localStorage.getItem(SUBSCRIBED_TOPICS_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeSubscribedTopics(keys: string[]) {
  try {
    window.localStorage.setItem(SUBSCRIBED_TOPICS_KEY, JSON.stringify(keys));
  } catch {
    /* ignore */
  }
}

/** 切换订阅某主题（topic key），返回最新订阅列表。 */
export function toggleSubscribedTopic(topicKey: string): string[] {
  const next = readSubscribedTopics().includes(topicKey)
    ? readSubscribedTopics().filter((k) => k !== topicKey)
    : [...readSubscribedTopics(), topicKey];
  writeSubscribedTopics(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 订阅作者持久化（与关注独立，可只订阅不关注）                          */
/* ------------------------------------------------------------------ */

const SUBSCRIBED_AUTHORS_KEY = "echo.community.subscribe-authors.v1";

export function readSubscribedAuthors(): string[] {
  try {
    const raw = window.localStorage.getItem(SUBSCRIBED_AUTHORS_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function writeSubscribedAuthors(names: string[]) {
  try {
    window.localStorage.setItem(SUBSCRIBED_AUTHORS_KEY, JSON.stringify(names));
  } catch {
    /* ignore */
  }
}

/** 切换订阅某作者，返回最新订阅列表。 */
export function toggleSubscribedAuthor(author: string): string[] {
  const next = readSubscribedAuthors().includes(author)
    ? readSubscribedAuthors().filter((a) => a !== author)
    : [...readSubscribedAuthors(), author];
  writeSubscribedAuthors(next);
  return next;
}

/* ------------------------------------------------------------------ */
/* 作者资料聚合                                                        */
/* ------------------------------------------------------------------ */

export interface AuthorProfile {
  name: string;
  initial: string;
  color: string;
  /** 该作者的帖子（按时间倒序）。 */
  posts: CommunityPost[];
  /** 笔记数。 */
  postCount: number;
  /** 获赞总数。 */
  totalLikes: number;
  /** 关注数（当前用户关注该作者，纳入该作者指标时展示为 1）。 */
  followingCount: number;
  /** 粉丝数（确定性估算，保证稳定）。 */
  followerCount: number;
  /** 主题覆盖数（该作者发帖涉及的主题数）。 */
  topicCoverage: string[];
}

/** 从帖子集合里找到作者的确定性头像素描（首字 + 颜色）。 */
export function authorLook(
  name: string,
  posts: CommunityPost[],
): { initial: string; color: string } {
  const first = posts.find((p) => p.author === name);
  return {
    initial: first?.authorInitial || name.slice(0, 1) || "?",
    color: first?.authorColor || "#7C6FF0",
  };
}

/** 聚合某作者的全量资料（输入该作者全部帖子）。 */
export function buildAuthorProfile(
  name: string,
  authorPosts: CommunityPost[],
): AuthorProfile {
  const { initial, color } = authorLook(name, authorPosts);
  const totalLikes = authorPosts.reduce((s, p) => s + p.likesCount, 0);
  const topics = Array.from(
    new Set(authorPosts.map((p) => p.topic).filter(Boolean)),
  );
  // 粉丝数用确定性哈希估算，保证同一作者每次一致且稳定。
  const followerCount = 120 + (hashName(name) % 4800);
  return {
    name,
    initial,
    color,
    posts: [...authorPosts].sort((a, b) => b.createdAt - a.createdAt),
    postCount: authorPosts.length,
    totalLikes,
    followingCount: readFollowing().includes(name) ? 1 : 0,
    followerCount,
    topicCoverage: topics,
  };
}

function hashName(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/** 关注作者（复用 community-data 的关注逻辑）。 */
export { readFollowing, toggleFollowing };
