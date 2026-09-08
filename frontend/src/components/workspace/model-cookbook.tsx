/**
 * Local-model cookbook card: recommends models that fit THIS machine and pulls
 * them in one click via ollama.
 *
 * Self-contained zh/en labels (decoupled from the concurrently-edited locale
 * bundle). Reads the public /api/cookbook/snapshot and posts to the auth-gated
 * pull endpoint. Degrades clearly when ollama isn't running.
 */
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { LocalAiDeployment } from "./local-ai-deployment";

import {
  type CookbookRec,
  type CookbookVerification,
  useCookbook,
  useCookbookPull,
} from "@/core/cookbook/use-cookbook";

const LABELS = {
  zh: {
    title: "本地模型推荐",
    subtitle: "检测资源 → 下载并验证 → 设为默认",
    detecting: "检测硬件中…",
    noOllama: "未检测到 Ollama —— 安装并运行后即可拉取/运行本地模型。",
    install: "下载并验证",
    verify: "本机验证",
    verifying: "验证中…",
    activate: "设为默认",
    activated: "已设为默认",
    ready: "文本通过",
    tools: "工具通过",
    noTools: "工具未通过",
    vision: "看图通过",
    noVision: "看图未验证",
    empty: "当前没有适合可用资源的推荐。请释放内存或减少上下文长度。",
    budget: "模型预算",
    context: "估算上下文",
    installed: "已安装",
    pulling: "拉取中…",
    speedUnit: "tok/s",
    estMem: "约",
    verdict: {
      fits: "可运行",
      tight: "勉强可运行",
      offload: "需 offload（较慢）",
      unrated: "待评估",
    } as Record<string, string>,
    note: "已为系统和 NAS 预留内存，估算不保证实际速度。下载会联网；验证只使用内置测试内容，设为默认后供后续任务使用。",
    sourceLive: "实时 · HuggingFace 热门",
    sourceStatic: "内置模型目录",
  },
  en: {
    title: "Local model recommendations",
    subtitle: "Check resources → download and verify → set as default",
    detecting: "Detecting hardware…",
    noOllama:
      "Ollama not detected — install and run it to pull / serve local models.",
    install: "Download & verify",
    verify: "Verify locally",
    verifying: "Verifying…",
    activate: "Set as default",
    activated: "Default saved",
    ready: "Text passed",
    tools: "Tools passed",
    noTools: "Tools not passed",
    vision: "Vision passed",
    noVision: "Vision unverified",
    empty:
      "No recommendation fits the available resources. Free memory or reduce the context estimate.",
    budget: "Model budget",
    context: "Context estimate",
    installed: "Installed",
    pulling: "Pulling…",
    speedUnit: "tok/s",
    estMem: "~",
    verdict: {
      fits: "Runs well",
      tight: "Tight fit",
      offload: "Needs offload (slower)",
      unrated: "Not rated",
    } as Record<string, string>,
    note: "Memory is reserved for the system and NAS. Estimates do not guarantee speed. Downloads use the network; verification uses synthetic content. The default applies to subsequent tasks.",
    sourceLive: "Live · HuggingFace trending",
    sourceStatic: "Built-in catalog",
  },
};

function hardwareLine(
  hw: NonNullable<ReturnType<typeof useCookbook>["snapshot"]>["hardware"],
): string {
  if (!hw) return "";
  const parts: string[] = [];
  if (hw.gpu_name) parts.push(hw.gpu_name);
  parts.push(hw.backend.toUpperCase());
  parts.push(`${hw.unified_memory ? "≈" : ""}${hw.vram_gb} GB`);
  return parts.join(" · ");
}

function verdictTone(verdict: string): string {
  return verdict === "fits"
    ? "bg-success/15 text-success"
    : "bg-warning/15 text-warning";
}

function RecRow({
  rec,
  t,
  pulls,
  pendingTag,
  onPull,
  disabled,
  verification,
  onVerify,
  onActivate,
  activated,
}: {
  rec: CookbookRec;
  t: (typeof LABELS)["en"];
  pulls: Record<string, string>;
  pendingTag: string | null;
  onPull: (tag: string) => void;
  disabled: boolean;
  verification?: CookbookVerification;
  onVerify: (tag: string) => void;
  onActivate: (tag: string) => void;
  activated: boolean;
}) {
  const pullState = pulls[rec.tag];
  const isPulling =
    pullState === "pulling" ||
    pullState === "verifying" ||
    pendingTag === rec.tag;
  return (
    <div className="flex min-w-0 max-w-full items-center justify-between gap-3 overflow-hidden px-3 py-2.5 sm:px-4">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{rec.label}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-xs font-medium",
              verdictTone(rec.verdict),
            )}
          >
            {t.verdict[rec.verdict] ?? rec.verdict}
          </span>
        </div>
        <div
          className="mt-0.5 max-w-full truncate text-xs text-muted-foreground"
          title={`${rec.tag} · ${t.estMem}${rec.est_mem_gb} GB${rec.est_tokens_per_s ? ` · ~${rec.est_tokens_per_s} ${t.speedUnit}` : ""}`}
        >
          <code className="inline-block max-w-full truncate align-bottom">
            {rec.tag}
          </code>{" "}
          {rec.est_mem_gb > 0 ? ` · ${t.estMem}${rec.est_mem_gb} GB` : ""}
          {rec.est_tokens_per_s
            ? ` · ~${rec.est_tokens_per_s} ${t.speedUnit}`
            : ""}
        </div>
        {verification && (
          <p className="mt-1 text-xs text-muted-foreground">
            {t.ready} · {verification.latency_ms} ms ·{" "}
            {verification.supports_tool_use ? t.tools : t.noTools}
            {" · "}
            {verification.supports_vision ? t.vision : t.noVision}
          </p>
        )}
        {pullState?.startsWith("error:") && (
          <p role="alert" className="mt-1 break-words text-xs text-destructive">
            {pullState.slice(6)}
          </p>
        )}
        {activated && (
          <p role="status" className="mt-1 text-xs text-success">
            {t.activated}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant="outline"
        className="h-7 shrink-0 text-xs"
        disabled={disabled || isPulling}
        onClick={() =>
          verification
            ? onActivate(rec.tag)
            : rec.installed
              ? onVerify(rec.tag)
              : onPull(rec.tag)
        }
      >
        {isPulling
          ? pullState === "pulling"
            ? t.pulling
            : t.verifying
          : verification
            ? t.activate
            : rec.installed
              ? t.verify
              : t.install}
      </Button>
    </div>
  );
}

export function ModelCookbook() {
  const [contextTokens, setContextTokens] = useState(4096);
  const { locale } = useI18n();
  const t =
    (locale || "en").slice(0, 2).toLowerCase() === "zh" ? LABELS.zh : LABELS.en;
  const {
    snapshot,
    isLoading,
    error: snapshotError,
  } = useCookbook(contextTokens);
  const { pull, verify, activate, pendingTag, error, activatedTag } =
    useCookbookPull();

  const recs = snapshot?.recommendations ?? [];
  const ollamaDown = snapshot ? !snapshot.ollama_available : false;
  const busy =
    pendingTag !== null ||
    Object.values(snapshot?.pulls ?? {}).some(
      (state) => state === "pulling" || state === "verifying",
    );

  return (
    <div className="min-w-0 max-w-full overflow-hidden rounded-lg border border-border-default bg-card/50 p-3 sm:p-5">
      <div className="flex min-w-0 flex-col items-start justify-between gap-2 sm:flex-row sm:gap-4">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">{t.title}</h4>
          <p className="mt-0.5 text-xs text-muted-foreground">{t.subtitle}</p>
        </div>
        {snapshot?.hardware && (
          <span className="max-w-full truncate text-left text-xs text-muted-foreground sm:max-w-[45%] sm:shrink-0 sm:text-right">
            {t.budget} · {hardwareLine(snapshot.hardware)}
          </span>
        )}
      </div>
      <label className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        {t.context}
        <select
          aria-label={t.context}
          value={contextTokens}
          onChange={(event) => setContextTokens(Number(event.target.value))}
          className="rounded-md border border-border bg-background px-2 py-1 text-foreground"
        >
          {[4096, 8192, 16384, 32768].map((value) => (
            <option key={value} value={value}>
              {value / 1024}K
            </option>
          ))}
        </select>
      </label>
      <LocalAiDeployment tag="auto" disabled={busy} />
      {(error || snapshotError) && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {(error || snapshotError)?.message}
        </p>
      )}

      {isLoading && !snapshot ? (
        <div className="mt-4 text-xs text-muted-foreground">{t.detecting}</div>
      ) : (
        <>
          {ollamaDown && (
            <div className="mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
              {t.noOllama}
            </div>
          )}
          <div className="mt-3 divide-y divide-border/40 rounded-lg border border-border-subtle">
            {recs.length === 0 && !snapshotError && (
              <p className="p-3 text-xs text-muted-foreground">{t.empty}</p>
            )}
            {recs.map((rec) => (
              <RecRow
                key={rec.tag}
                rec={rec}
                t={t}
                pulls={snapshot?.pulls ?? {}}
                pendingTag={pendingTag}
                onPull={pull}
                onVerify={verify}
                onActivate={activate}
                verification={snapshot?.verifications?.[rec.tag]}
                activated={activatedTag === rec.tag}
                disabled={ollamaDown || busy}
              />
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground/70">
            {snapshot?.source === "huggingface" ? t.sourceLive : t.sourceStatic}{" "}
            · {t.note}
          </p>
        </>
      )}
    </div>
  );
}
