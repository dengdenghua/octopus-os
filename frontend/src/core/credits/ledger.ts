/**
 * 本地积分账本 —— 把积分变成社区内可赚可花的"习惯通货"。
 *
 * 与 oct 网关余额（只读）不同，这里维护一套完整的收支账本：
 * 赚（每日签到 / 发布 / 被复刻分成 / 被点赞互动）+ 花（复刻付费 mini-app）。
 * 数据落 localStorage，与 oct 余额在展示端合并，形成"共创赚钱"闭环。
 *
 * 规则（可在此调整）：
 *  - INITIAL_CREDITS  初始体验积分
 *  - SIGN_IN_REWARD    每日签到
 *  - PUBLISH_REWARD    发布一篇内容
 *  - FORK_EARN_REWARD  内容被复刻一次，分成给作者
 *  - LIKE_EARN_REWARD  内容被点赞一次，奖励给作者
 */
export type CreditTxnType =
  | "sign-in"
  | "publish"
  | "fork-earn"
  | "like-earn"
  | "fork-spend"
  | "market-spend";

export interface CreditTxn {
  id: string;
  type: CreditTxnType;
  /** 正 = 收入，负 = 支出。 */
  amount: number;
  reason: string;
  refId?: string;
  createdAt: number;
}

export interface CreditLedger {
  balance: number;
  txns: CreditTxn[];
  /** YYYY-MM-DD，用于每日签到去重。 */
  lastSignInDate: string;
}

export const INITIAL_CREDITS = 100;
export const SIGN_IN_REWARD = 10;
export const PUBLISH_REWARD = 20;
export const FORK_EARN_REWARD = 5;
export const LIKE_EARN_REWARD = 1;

const LEDGER_KEY = "echo.credits.ledger.v1";

function today(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function defaultState(): CreditLedger {
  return { balance: INITIAL_CREDITS, txns: [], lastSignInDate: "" };
}

function readLedger(): CreditLedger {
  try {
    const raw = window.localStorage.getItem(LEDGER_KEY);
    if (!raw) return defaultState();
    const parsed = JSON.parse(raw) as Partial<CreditLedger>;
    const base = defaultState();
    return {
      balance:
        typeof parsed.balance === "number" ? parsed.balance : base.balance,
      txns: Array.isArray(parsed.txns) ? parsed.txns : [],
      lastSignInDate:
        typeof parsed.lastSignInDate === "string" ? parsed.lastSignInDate : "",
    };
  } catch {
    return defaultState();
  }
}

function writeLedger(state: CreditLedger) {
  try {
    window.localStorage.setItem(LEDGER_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

function recordTxn(
  state: CreditLedger,
  txn: Omit<CreditTxn, "id" | "createdAt">,
): CreditTxn {
  const full: CreditTxn = {
    ...txn,
    id: `${Date.now()}.${Math.random().toString(36).slice(2, 6)}`,
    createdAt: Date.now(),
  };
  state.txns.unshift(full);
  state.balance += txn.amount;
  return full;
}

/** 当前本地账本余额（仅社区积分）。 */
export function getCommunityCredits(): number {
  return readLedger().balance;
}

/** 今日签到状态。 */
export function getSignInStatus(): { claimedToday: boolean; today: string } {
  const l = readLedger();
  const t = today();
  return { claimedToday: l.lastSignInDate === t, today: t };
}

/** 每日签到，返回是否领取成功及领取额度。 */
export function claimDailySignIn(): { claimed: boolean; amount: number } {
  const l = readLedger();
  const t = today();
  if (l.lastSignInDate === t) return { claimed: false, amount: 0 };
  l.lastSignInDate = t;
  recordTxn(l, { type: "sign-in", amount: SIGN_IN_REWARD, reason: "每日签到" });
  writeLedger(l);
  return { claimed: true, amount: SIGN_IN_REWARD };
}

/** 发布奖励。 */
export function creditPublish(title: string): number {
  const l = readLedger();
  recordTxn(l, {
    type: "publish",
    amount: PUBLISH_REWARD,
    reason: `发布「${title}」`,
  });
  writeLedger(l);
  return PUBLISH_REWARD;
}

/** 内容被复刻，分成给作者。 */
export function creditForkEarn(title: string): number {
  const l = readLedger();
  recordTxn(l, {
    type: "fork-earn",
    amount: FORK_EARN_REWARD,
    reason: `「${title}」被复刻`,
  });
  writeLedger(l);
  return FORK_EARN_REWARD;
}

/** 内容被点赞，奖励给作者。 */
export function creditLikeEarn(title: string): number {
  const l = readLedger();
  recordTxn(l, {
    type: "like-earn",
    amount: LIKE_EARN_REWARD,
    reason: `「${title}」被喜欢`,
  });
  writeLedger(l);
  return LIKE_EARN_REWARD;
}

/** 复刻付费：扣减 local.priceCredits。余额不足返回 false，不记账。 */
export function debitForkSpend(title: string, price: number): boolean {
  if (price <= 0) return true;
  const l = readLedger();
  if (l.balance < price) return false;
  recordTxn(l, {
    type: "fork-spend",
    amount: -price,
    reason: `复刻「${title}」`,
  });
  writeLedger(l);
  return true;
}

/** 集市购买：扣减积分。余额不足返回 false，不记账。 */
export function spendMarketCredits(title: string, price: number): boolean {
  if (price <= 0) return true;
  const l = readLedger();
  if (l.balance < price) return false;
  recordTxn(l, {
    type: "market-spend",
    amount: -price,
    reason: `购买「${title}」`,
  });
  writeLedger(l);
  return true;
}

/** 收支流水（新→旧）。 */
export function getLedgerTxns(): CreditTxn[] {
  return readLedger().txns;
}

/** 全部流水按类型汇总（用于展示"我赚了多少/花了多少"）。 */
export function getLedgerSummary(): Record<CreditTxnType, number> {
  const l = readLedger();
  const sum: Record<CreditTxnType, number> = {
    "sign-in": 0,
    publish: 0,
    "fork-earn": 0,
    "like-earn": 0,
    "fork-spend": 0,
    "market-spend": 0,
  };
  for (const txn of l.txns) {
    sum[txn.type] = (sum[txn.type] ?? 0) + txn.amount;
  }
  return sum;
}
