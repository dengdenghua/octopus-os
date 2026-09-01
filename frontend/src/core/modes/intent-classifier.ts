/**
 * Intent-based mode classification for the workspace work strategy.
 *
 * Decides whether a stretch of user conversation is asking for a coding task
 * (develop), a code/security review (audit), or a UI/design task (uxui).
 *
 * Pure, deterministic, dependency-free: a keyword lexicon + time-weighted
 * scoring. No LLM calls, no network, no React — easy to unit test and reason
 * about. It is intentionally conservative: weak or ambiguous signals resolve
 * to "none" so the current mode is never changed on a whim.
 */

import type { AgentModeName } from "@/components/workspace/mode-selector";

export type IntentHandle = "none" | "suggest" | "auto";

export interface IntentClassification {
  /** Highest-scoring mode, or the caller's current mode when nothing matches. */
  mode: AgentModeName;
  /** Relative dominance of the winning mode, 0..1. */
  confidence: number;
  /** Matched signal words (for transparency / debugging). */
  signals: string[];
  /** Suggested action. */
  handle: IntentHandle;
}

export interface IntentClassifierOptions {
  /** Per-message time weights, newest first. */
  weights?: number[];
  /** Confidence at or above which we auto-switch. */
  highThreshold?: number;
  /** Confidence at or above which we suggest (below high). */
  mediumThreshold?: number;
}

const DEFAULT_WEIGHTS = [1.0, 0.8, 0.6, 0.45, 0.3];
const DEFAULT_HIGH = 0.7;
const DEFAULT_MEDIUM = 0.45;
/**
 * Absolute weighted-score floor before a "confident" verdict may auto-switch.
 * Guards against a single stray keyword (e.g. "检查一下") reporting a 1.0
 * relative confidence just because there is no runner-up signal.
 */
const AUTO_MIN_SCORE = 2.0;
/** Absolute weighted-score floor before we even suggest a switch. */
const SUGGEST_MIN_SCORE = 1.0;
const MAX_MESSAGES = 5;

type Lexicon = Record<AgentModeName, string[]>;

/**
 * Signal lexicon per mode (Chinese + English). Terms are matched as
 * substrings on the lowercased, punctuation-stripped message. Keep the lists
 * discriminative: a term that routinely appears in harmless conversation
 * (e.g. "检查") must be balanced by a strong positive around it.
 */
const LEXICON: Lexicon = {
  develop: [
    // 中文
    "实现",
    "编写",
    "写个",
    "开发",
    "创建",
    "修复",
    "修一下",
    "重构",
    "添加",
    "加一个",
    "新增",
    "功能",
    "函数",
    "组件",
    "接口",
    "后端",
    "前端",
    "算法",
    "调试",
    "报错",
    "错误",
    "改一下代码",
    "写代码",
    "写界面",
    "冒烟",
    "跑通",
    // 英文
    "implement",
    "code",
    "build",
    "fix",
    "refactor",
    "create",
    "add",
    "api",
    "backend",
    "frontend",
    "debug",
    "bug",
    "function",
    "component",
  ],
  audit: [
    // 中文
    "审查",
    "审计",
    "检查一下",
    "代码质量",
    "安全",
    "风险",
    "评估",
    "漏洞",
    "越权",
    "注入",
    "性能问题",
    "规范",
    "最佳实践",
    "review",
    "审查代码",
    "有没有问题",
    "隐患",
    // 英文
    "review",
    "audit",
    "security",
    "risk",
    "assess",
    "vulnerability",
    "injection",
    "quality",
    "best practice",
  ],
  uxui: [
    // 中文
    "界面",
    "ui",
    "ux",
    "样式",
    "外观",
    "设计",
    "布局",
    "美化",
    "视觉",
    "交互",
    "配色",
    "字体",
    "间距",
    "圆角",
    "阴影",
    "动效",
    "好看",
    "漂亮",
    "美观",
    "主题",
    "改一下界面",
    "调整样式",
    // 英文
    "interface",
    "design",
    "layout",
    "theme",
    "style",
    "appearance",
    "polish",
    "styling",
  ],
};

function normalize(text: string): string {
  return text
    .toLowerCase()
    // Strip markdown fences / inline code backticks placeholders.
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`/g, " ")
    // Collapse punctuation/whitespace so "ui" and "ui." both match cleanly.
    .replace(/[\s.,!?;:。，！？；：、…()（）"'“”‘’[\]]+/g, " ");
}

function matchTerm(text: string, term: string): boolean {
  return text.includes(term);
}

/**
 * Classify the most recent conversation's user intents into a suggested work
 * mode. `messages` should be newest-first (index 0 = the latest message).
 */
export function classifyModeIntent(
  messages: string[],
  opts: IntentClassifierOptions = {},
  fallbackMode: AgentModeName = "develop",
): IntentClassification {
  const weights = opts.weights ?? DEFAULT_WEIGHTS;
  const high = opts.highThreshold ?? DEFAULT_HIGH;
  const medium = opts.mediumThreshold ?? DEFAULT_MEDIUM;

  const recent = messages.slice(0, MAX_MESSAGES);
  const scores: Record<AgentModeName, number> = {
    develop: 0,
    audit: 0,
    uxui: 0,
  };
  const signalsPerMode: Record<AgentModeName, string[]> = {
    develop: [],
    audit: [],
    uxui: [],
  };

  recent.forEach((raw, index) => {
    if (!raw) return;
    const text = normalize(raw);
    if (!text) return;
    const weight = weights[index] ?? weights[weights.length - 1] ?? 0.1;
    (Object.keys(LEXICON) as AgentModeName[]).forEach((mode) => {
      LEXICON[mode].forEach((term) => {
        if (matchTerm(text, term)) {
          scores[mode] += weight;
          signalsPerMode[mode].push(term);
        }
      });
    });
  });

  const entries = (Object.keys(scores) as AgentModeName[]).map((mode) => ({
    mode,
    score: scores[mode],
  }));
  entries.sort((a, b) => b.score - a.score);

  const top = entries[0];

  if (!top || top.score <= 0) {
    return { mode: fallbackMode, confidence: 0, signals: [], handle: "none" };
  }

  const runner = entries[1] ?? top;
  const confidence = (top.score - runner.score) / top.score;
  const signals = signalsPerMode[top.mode];

  let handle: IntentHandle;
  if (top.score >= AUTO_MIN_SCORE && confidence >= high) {
    handle = "auto";
  } else if (top.score >= SUGGEST_MIN_SCORE && confidence >= medium) {
    handle = "suggest";
  } else {
    handle = "none";
  }

  return { mode: top.mode, confidence, signals, handle };
}

export {
  AUTO_MIN_SCORE,
  DEFAULT_HIGH,
  DEFAULT_MEDIUM,
  LEXICON,
  MAX_MESSAGES,
  SUGGEST_MIN_SCORE,
};
