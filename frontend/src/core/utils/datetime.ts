import { format, formatDistanceToNow } from "date-fns";
import { enUS as dateFnsEnUS } from "date-fns/locale/en-US";
import { zhCN as dateFnsZhCN } from "date-fns/locale/zh-CN";

import { getLocaleFromCookie } from "@/core/i18n/cookies";
import { detectLocale, type Locale } from "@/core/i18n/locale";

/**
 * Server timestamps are part of the live-stream surface and may briefly be
 * absent while a new thread is being created. Never let an invalid timestamp
 * take down the whole chat tree (date-fns throws for Invalid Date).
 */
function toValidDate(value: Date | string | number): Date | null {
  const date =
    value instanceof Date ? new Date(value.getTime()) : new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

function getDateFnsLocale(locale: Locale) {
  switch (locale) {
    case "zh-CN":
      return dateFnsZhCN;
    case "en-US":
    default:
      return dateFnsEnUS;
  }
}

export function formatTimeAgo(date: Date | string | number, locale?: Locale) {
  const safeDate = toValidDate(date);
  if (!safeDate) return "";
  const effectiveLocale =
    locale ??
    (getLocaleFromCookie() as Locale | null) ??
    // Fallback when cookie is missing (or on first render)
    detectLocale();
  return formatDistanceToNow(safeDate, {
    addSuffix: true,
    locale: getDateFnsLocale(effectiveLocale),
  });
}

export function formatDate(
  date: Date | string | number,
  locale?: Locale,
): string {
  const d = toValidDate(date);
  if (!d) return "";
  const effectiveLocale =
    locale ?? (getLocaleFromCookie() as Locale | null) ?? detectLocale();

  if (effectiveLocale === "zh-CN") {
    return format(d, "yyyy年MM月dd日");
  }
  return format(d, "MMM d, yyyy");
}

/* Implementation note. */
export function formatRelativeTimestamp(
  date: Date | string | number,
  locale?: Locale,
): string {
  const d = toValidDate(date);
  if (!d) return "";
  const effectiveLocale =
    locale ?? (getLocaleFromCookie() as Locale | null) ?? detectLocale();

  const deltaSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  const isZh = effectiveLocale === "zh-CN";

  if (deltaSec < 30) return isZh ? "刚刚" : "just now";
  if (deltaSec < 3600) {
    const m = Math.floor(deltaSec / 60);
    return isZh ? `${m} 分钟前` : `${m}m ago`;
  }
  if (deltaSec < 86400) {
    const h = Math.floor(deltaSec / 3600);
    return isZh ? `${h} 小时前` : `${h}h ago`;
  }
  if (deltaSec < 7 * 86400) {
    return format(d, "EEE", { locale: getDateFnsLocale(effectiveLocale) });
  }
  return formatDate(d, effectiveLocale);
}

export function formatCompactRelativeTimestamp(
  date: Date | string | number,
  locale?: Locale,
): string {
  const d = toValidDate(date);
  if (!d) return "";
  const effectiveLocale =
    locale ?? (getLocaleFromCookie() as Locale | null) ?? detectLocale();

  const deltaSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  const isZh = effectiveLocale === "zh-CN";

  if (deltaSec < 30) return isZh ? "刚刚" : "now";
  if (deltaSec < 3600) {
    const m = Math.floor(deltaSec / 60);
    return `${m}m`;
  }
  if (deltaSec < 86400) {
    const h = Math.floor(deltaSec / 3600);
    return `${h}h`;
  }
  if (deltaSec < 7 * 86400) {
    return format(d, "EEE", { locale: getDateFnsLocale(effectiveLocale) });
  }
  return format(d, "M/d", {
    locale: getDateFnsLocale(effectiveLocale),
  });
}

export function formatDurationMs(ms: number): string {
  if (ms <= 0) return "--";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (minutes < 60) return `${minutes}m ${secs}s`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `${hours}h ${mins}m`;
}

export function formatCurrency(
  amount: string | number,
  currency = "USD",
  locale?: Locale,
): string {
  const value = typeof amount === "string" ? parseFloat(amount) : amount;
  const effectiveLocale =
    locale ?? (getLocaleFromCookie() as Locale | null) ?? detectLocale();

  return new Intl.NumberFormat(
    effectiveLocale === "zh-CN" ? "zh-CN" : "en-US",
    {
      style: "currency",
      currency,
    },
  ).format(Math.abs(value));
}
