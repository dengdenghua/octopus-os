import {
  BookOpenIcon,
  BotIcon,
  BrainCircuitIcon,
  GraduationCapIcon,
  ImageIcon,
  MessageCircleIcon,
  SearchIcon,
  SparklesIcon,
  type LucideIcon,
} from "lucide-react";

import { swallow } from "@/core/utils/log";

export type DesktopAppCategory = "ai" | "video" | "dev" | "knowledge";

export interface BrowserDesktopApp {
  name: string;
  url: string;
  icon: LucideIcon;
  logoUrl: string;
  color: string;
  description: string;
  descriptionKey?: string;
  category: DesktopAppCategory;
}

export const AI_DESKTOP_APPS: BrowserDesktopApp[] = [
  {
    name: "Gemini",
    url: "https://gemini.google.com/app",
    icon: SparklesIcon,
    logoUrl: "https://cdn.simpleicons.org/googlegemini",
    color: "from-blue-500 to-cyan-400",
    description: "Comprehensive search, multi-turn analysis",
    descriptionKey: "comprehensiveSearch",
    category: "ai",
  },
  {
    name: "NotebookLM",
    url: "https://notebooklm.google.com/",
    icon: BookOpenIcon,
    logoUrl: "https://cdn.simpleicons.org/notebooklm",
    color: "from-warning to-orange-400",
    description: "Library, citations, document research",
    descriptionKey: "researchDocs",
    category: "ai",
  },
  {
    name: "Doubao",
    url: "https://www.doubao.com/chat/",
    icon: MessageCircleIcon,
    logoUrl: "https://www.google.com/s2/favicons?domain=www.doubao.com&sz=128",
    color: "from-success to-teal-400",
    description: "Chinese research, Chinese rewriting",
    descriptionKey: "chineseResearch",
    category: "ai",
  },
  {
    name: "DeepSeek",
    url: "https://chat.deepseek.com/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/deepseek",
    color: "from-blue-700 to-indigo-500",
    description: "Reasoning, coding, Chinese Q&A",
    category: "ai",
  },
  {
    name: "Tongyi Qianwen",
    url: "https://chat.qwen.ai/",
    icon: SparklesIcon,
    logoUrl: "https://cdn.simpleicons.org/qwen",
    color: "from-blue-600 to-cyan-500",
    description: "Tongyi models, multimodal chat",
    category: "ai",
  },
  {
    name: "Wenxin Yiyan",
    url: "https://yiyan.baidu.com/",
    icon: MessageCircleIcon,
    logoUrl: "https://cdn.simpleicons.org/baidu",
    color: "from-indigo-600 to-blue-500",
    description: "Baidu agents, Chinese creation",
    category: "ai",
  },
  {
    name: "Tencent Yuanbao",
    url: "https://yuanbao.tencent.com/",
    icon: BotIcon,
    logoUrl:
      "https://www.google.com/s2/favicons?domain=yuanbao.tencent.com&sz=128",
    color: "from-cyan-600 to-blue-500",
    description: "Chinese search, material summary",
    category: "ai",
  },
  {
    name: "Perplexity",
    url: "https://www.perplexity.ai/",
    icon: SearchIcon,
    logoUrl: "https://cdn.simpleicons.org/perplexity",
    color: "from-sky-500 to-indigo-500",
    description: "Web search, source leads",
    descriptionKey: "webSearch",
    category: "ai",
  },
  {
    name: "ChatGPT",
    url: "https://chatgpt.com/",
    icon: BotIcon,
    logoUrl: "https://chatgpt.com/favicon.ico",
    color: "from-zinc-700 to-zinc-500",
    description: "General chat, coding assistance",
    descriptionKey: "generalChat",
    category: "ai",
  },
  {
    name: "Claude",
    url: "https://claude.ai/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/claude",
    color: "from-stone-600 to-destructive",
    description: "Long-text analysis, writing organization",
    descriptionKey: "longTextAnalysis",
    category: "ai",
  },
  {
    name: "Kimi",
    url: "https://www.kimi.com/",
    icon: GraduationCapIcon,
    logoUrl: "https://www.google.com/s2/favicons?domain=www.kimi.com&sz=128",
    color: "from-violet-500 to-fuchsia-500",
    description: "Long context, Chinese materials",
    descriptionKey: "longContext",
    category: "ai",
  },
  {
    name: "Agnes AI",
    url: "https://app.agnes-ai.com/",
    icon: ImageIcon,
    logoUrl:
      "https://www.google.com/s2/favicons?domain=app.agnes-ai.com&sz=128",
    color: "from-pink-500 to-destructive",
    description: "AI gateway, image/video generation",
    category: "ai",
  },
  {
    name: "YouTube",
    url: "https://www.youtube.com/",
    icon: ImageIcon,
    logoUrl: "https://cdn.simpleicons.org/youtube",
    color: "from-destructive to-destructive",
    description: "Videos, channels, live streams",
    category: "video",
  },
  {
    name: "Bilibili",
    url: "https://www.bilibili.com/",
    icon: ImageIcon,
    logoUrl: "https://cdn.simpleicons.org/bilibili",
    color: "from-sky-500 to-cyan-400",
    description: "Videos, anime, knowledge zone",
    category: "video",
  },
  {
    name: "GitHub",
    url: "https://github.com/",
    icon: BotIcon,
    logoUrl: "https://github.githubassets.com/favicons/favicon.svg",
    color: "from-zinc-900 to-zinc-700",
    description: "Code repos, project collaboration",
    category: "dev",
  },
  {
    name: "Stack Overflow",
    url: "https://stackoverflow.com/",
    icon: BrainCircuitIcon,
    logoUrl: "https://cdn.simpleicons.org/stackoverflow",
    color: "from-orange-500 to-warning",
    description: "Programming Q&A, troubleshooting",
    category: "dev",
  },
  {
    name: "MDN",
    url: "https://developer.mozilla.org/",
    icon: BookOpenIcon,
    logoUrl: "https://cdn.simpleicons.org/mdnwebdocs",
    color: "from-foreground to-blue-600",
    description: "Web docs, API reference",
    category: "dev",
  },
  {
    name: "Zhihu",
    url: "https://www.zhihu.com/",
    icon: SearchIcon,
    logoUrl: "https://cdn.simpleicons.org/zhihu",
    color: "from-blue-600 to-sky-500",
    description: "Q&A, columns, Chinese materials",
    category: "knowledge",
  },
  {
    name: "Wikipedia",
    url: "https://www.wikipedia.org/",
    icon: GraduationCapIcon,
    logoUrl: "https://cdn.simpleicons.org/wikipedia",
    color: "from-muted-foreground to-muted-foreground/70",
    description: "Encyclopedia, background materials",
    category: "knowledge",
  },
];

export const DESKTOP_APP_ORDER_KEY = "echo:browser-desktop-app-order";

export function loadDesktopAppOrder(): string[] {
  if (typeof window === "undefined")
    return AI_DESKTOP_APPS.map((app) => app.url);
  try {
    const parsed = JSON.parse(
      localStorage.getItem(DESKTOP_APP_ORDER_KEY) || "[]",
    );
    if (!Array.isArray(parsed)) return AI_DESKTOP_APPS.map((app) => app.url);
    const known = new Set(AI_DESKTOP_APPS.map((app) => app.url));
    const saved = parsed.filter(
      (item): item is string => typeof item === "string" && known.has(item),
    );
    const missing = AI_DESKTOP_APPS.map((app) => app.url).filter(
      (url) => !saved.includes(url),
    );
    return [...saved, ...missing];
  } catch (e) {
    swallow(e);
    return AI_DESKTOP_APPS.map((app) => app.url);
  }
}

export function orderDesktopApps(order: string[]): BrowserDesktopApp[] {
  const byUrl = new Map(AI_DESKTOP_APPS.map((app) => [app.url, app]));
  return order
    .map((url) => byUrl.get(url))
    .filter((app): app is BrowserDesktopApp => Boolean(app));
}

export function moveDesktopApp(
  order: string[],
  fromUrl: string,
  toUrl: string,
): string[] {
  if (fromUrl === toUrl) return order;
  const next = order.filter((url) => url !== fromUrl);
  const targetIndex = next.indexOf(toUrl);
  if (targetIndex < 0) return order;
  next.splice(targetIndex, 0, fromUrl);
  return next;
}
