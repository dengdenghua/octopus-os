/**
 * 灵感集市数据层 —— 类闲鱼/小红书的积分交易广场。
 *
 * 用户可浏览、购买社区内的"好物"（工具/模板/素材/玩法），用社区积分结算；
 * 也可自己上架商品。买入扣积分、售出得积分，与积分账本打通，
 * 形成"赚积分 → 集市消费 → 再创作赚钱"的循环。
 *
 * 数据来源：内置种子（永远有货）+ 用户自营（localStorage 持久化）。
 */

import { spendMarketCredits } from "@/core/credits/ledger";
import { communityAssetURL } from "@/components/workspace/community/community-assets";

export interface MarketItem {
  id: string;
  title: string;
  desc: string;
  /** 售价（积分）。 */
  price: number;
  cover: string;
  /** 分类 key（效率/职场/学习/生活/游戏/购物）。 */
  category: string;
  seller: string;
  sellerInitial: string;
  sellerColor: string;
  /** 是否已售出。 */
  sold: boolean;
  createdAt: number;
  /** 上架者是否为当前用户（"我"）。 */
  mine?: boolean;
}

export interface MarketCategory {
  key: string;
  label: string;
  color: string;
}

/** 集市分类 tab（一键筛选）。 */
export const MARKET_CATEGORIES: MarketCategory[] = [
  { key: "all", label: "全部", color: "#FC466B" },
  { key: "efficiency", label: "效率工具", color: "#00C6FF" },
  { key: "work", label: "职场模板", color: "#3F5EFB" },
  { key: "study", label: "学习资源", color: "#71B280" },
  { key: "life", label: "生活好物", color: "#FF6A5B" },
  { key: "game", label: "游戏好物", color: "#5B8C5A" },
  { key: "shopping", label: "购物神器", color: "#F2994A" },
];

/* ------------------------------------------------------------------ */
/* 内置种子商品（复用社区封面图，保证开箱有货）                         */
/* ------------------------------------------------------------------ */

const SEED_ITEMS: MarketItem[] = [
  {
    id: "m.1",
    title: "一键比价监控器",
    desc: "监控历史价格与平台活动，降价即提醒，618 已省 2000+。",
    price: 20,
    cover: communityAssetURL("price-watch(1).jpg"),
    category: "shopping",
    seller: "省钱 Bot",
    sellerInitial: "省",
    sellerColor: "#00C6FF",
    sold: false,
    createdAt: Date.now() - 3_600_000,
  },
  {
    id: "m.2",
    title: "周报自动生成模板",
    desc: "汇总聊天与任务清单，一键生成结构化周报，老板直呼专业。",
    price: 15,
    cover: communityAssetURL("weekly-report(1).jpg"),
    category: "work",
    seller: "打工侠",
    sellerInitial: "打",
    sellerColor: "#FC466B",
    sold: false,
    createdAt: Date.now() - 7_200_000,
  },
  {
    id: "m.3",
    title: "简历 STAR 改写模板",
    desc: "用 STAR 法则重写项目经历，自动标注关键词与量化成果。",
    price: 12,
    cover: communityAssetURL("resume(1).jpg"),
    category: "work",
    seller: "入职顾问",
    sellerInitial: "入",
    sellerColor: "#3F5EFB",
    sold: false,
    createdAt: Date.now() - 12_000_000,
  },
  {
    id: "m.4",
    title: "外语口语每日打卡计划",
    desc: "根据你的水平定制对话练习，实时纠音并生成复习卡。",
    price: 10,
    cover: communityAssetURL("language-coach.jpg"),
    category: "study",
    seller: "语言教练",
    sellerInitial: "语",
    sellerColor: "#71B280",
    sold: false,
    createdAt: Date.now() - 18_000_000,
  },
  {
    id: "m.5",
    title: "论文 10 分钟精读模板",
    desc: "读取 PDF 与网页，自动抽取结论与可引用观点。",
    price: 8,
    cover: communityAssetURL("study-paper(1).jpg"),
    category: "study",
    seller: "学术喵",
    sellerInitial: "学",
    sellerColor: "#134E5E",
    sold: false,
    createdAt: Date.now() - 24_000_000,
  },
  {
    id: "m.6",
    title: "智能家居联动方案",
    desc: "绑定设备后一句话控制全屋灯光与空调，极客首选。",
    price: 25,
    cover: communityAssetURL("smart-home.jpg"),
    category: "life",
    seller: "极客居",
    sellerInitial: "极",
    sellerColor: "#8E2DE2",
    sold: false,
    createdAt: Date.now() - 36_000_000,
  },
  {
    id: "m.7",
    title: "旅行攻略生成器",
    desc: "输入目的地与预算，自动规划路线并生成可分享清单。",
    price: 18,
    cover: communityAssetURL("travel-plan(1).jpg"),
    category: "life",
    seller: "旅行灵感",
    sellerInitial: "旅",
    sellerColor: "#FF6A5B",
    sold: false,
    createdAt: Date.now() - 48_000_000,
  },
  {
    id: "m.8",
    title: "游戏日常任务托管脚本",
    desc: "接管日常重复刷本，自动补血补蓝、拾取掉落，晚上收菜。",
    price: 30,
    cover: communityAssetURL("game-auto-daily.jpg"),
    category: "game",
    seller: "游戏管家",
    sellerInitial: "游",
    sellerColor: "#5B8C5A",
    sold: false,
    createdAt: Date.now() - 60_000_000,
  },
  {
    id: "m.9",
    title: "抽卡冷静计算器",
    desc: "输入抽数快速算概率与期望，帮你判断要不要继续氪。",
    price: 6,
    cover: communityAssetURL("gacha.jpg"),
    category: "game",
    seller: "数据党玩家",
    sellerInitial: "数",
    sellerColor: "#5B8C5A",
    sold: false,
    createdAt: Date.now() - 90_000_000,
  },
  {
    id: "m.10",
    title: "会议纪要排版模板",
    desc: "识别重点、拆分待办，套用公司模板，发前自动检查错别字。",
    price: 14,
    cover: communityAssetURL("meeting-notes.jpg"),
    category: "work",
    seller: "会议小助手",
    sellerInitial: "会",
    sellerColor: "#3F5EFB",
    sold: false,
    createdAt: Date.now() - 120_000_000,
  },
  {
    id: "m.11",
    title: "睡前明日清单模板",
    desc: "结合日历与待办生成明日优先级清单，早晨照着做。",
    price: 5,
    cover: communityAssetURL("plan-tomorrow.jpg"),
    category: "life",
    seller: "清早计划",
    sellerInitial: "清",
    sellerColor: "#FF6A5B",
    sold: false,
    createdAt: Date.now() - 150_000_000,
  },
  {
    id: "m.12",
    title: "优惠券自动领取脚本",
    desc: "定时监控领券入口，到点自动领取并推送提醒，不再错过优惠。",
    price: 22,
    cover: communityAssetURL("coupon.jpg"),
    category: "shopping",
    seller: "薅羊毛王",
    sellerInitial: "薅",
    sellerColor: "#F2994A",
    sold: false,
    createdAt: Date.now() - 180_000_000,
  },
];

/* ------------------------------------------------------------------ */
/* 持久化：已购/已售状态 + 用户自营商品                                */
/* ------------------------------------------------------------------ */

const SOLD_KEY = "echo.market.sold.v1";
const MINE_KEY = "echo.market.mine.v1";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

/** 读取已售出商品 id 集合（含种子与自营）。 */
function readSold(): string[] {
  return readJson<string[]>(SOLD_KEY, []);
}

function writeSold(ids: string[]) {
  writeJson(SOLD_KEY, ids);
}

/** 读取用户自营在售商品。 */
export function readMine(): MarketItem[] {
  return readJson<MarketItem[]>(MINE_KEY, []);
}

function writeMine(items: MarketItem[]) {
  writeJson(MINE_KEY, items);
}

/** 全量商品 = 种子 + 自营，叠加已售状态。 */
export function getMarketItems(): MarketItem[] {
  const sold = readSold();
  const mine = readMine();
  const seeds = SEED_ITEMS.map((s) =>
    sold.includes(s.id) ? { ...s, sold: true } : s,
  );
  return [...mine, ...seeds];
}

/** 购买商品：扣积分并标记已售。余额不足返回 false。 */
export function buyMarketItem(id: string): { ok: boolean; need: number } {
  const item = getMarketItems().find((i) => i.id === id);
  if (!item || item.sold || item.mine) {
    return { ok: false, need: item?.price ?? 0 };
  }
  if (!spendMarketCredits(item.title, item.price)) {
    return { ok: false, need: item.price };
  }
  writeSold([...readSold(), id]);
  return { ok: true, need: item.price };
}

/** 上架商品（自营，不扣费）。 */
export function listMarketItem(input: {
  title: string;
  desc: string;
  price: number;
  category: string;
  cover: string;
}): MarketItem {
  const item: MarketItem = {
    id: `mine.${Date.now()}.${Math.random().toString(36).slice(2, 6)}`,
    title: input.title,
    desc: input.desc,
    price: Math.max(0, Math.round(input.price)),
    cover: input.cover,
    category: input.category,
    seller: "我",
    sellerInitial: "我",
    sellerColor: "#FC466B",
    sold: false,
    createdAt: Date.now(),
    mine: true,
  };
  writeMine([item, ...readMine()]);
  return item;
}

/** 数值缩写：<1k 直显，>=1k 用 1.2k / 3.4w。 */
export function formatCount(count: number): string {
  if (count < 1000) return String(count);
  if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
  return `${(count / 10000).toFixed(1)}w`;
}
