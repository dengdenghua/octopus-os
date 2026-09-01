/**
 * Local-model cookbook card: recommends models that fit THIS machine and pulls
 * them in one click via ollama.
 *
 * Self-contained zh/en labels (decoupled from the concurrently-edited locale
 * bundle). Reads the public /api/cookbook/snapshot and posts to the auth-gated
 * pull endpoint. Degrades clearly when ollama isn't running.
 */
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  type CookbookRec,
  useCookbook,
  useCookbookPull,
} from "@/core/cookbook/use-cookbook";

const LABELS = {
  zh: {
    title: "本地模型推荐",
    subtitle: "按你的硬件估算可运行的模型，一键经 Ollama 拉取",
    detecting: "检测硬件中…",
    noOllama: "未检测到 Ollama —— 安装并运行后即可拉取/运行本地模型。",
    install: "拉取",
    installed: "已安装",
    pulling: "拉取中…",
    speedUnit: "tok/s",
    estMem: "约",
    verdict: {
      fits: "可运行",
      tight: "勉强可运行",
      offload: "需 offload（较慢）",
    } as Record<string, string>,
    note: "估算仅供参考；带宽表为人工快照，吞吐为粗略估计。",
    sourceLive: "实时 · HuggingFace 热门",
    sourceStatic: "内置快照（正在获取最新…）",
  },
  en: {
    title: "Local model recommendations",
    subtitle: "Models that fit your hardware — pull in one click via Ollama",
    detecting: "Detecting hardware…",
    noOllama:
      "Ollama not detected — install and run it to pull / serve local models.",
    install: "Pull",
    installed: "Installed",
    pulling: "Pulling…",
    speedUnit: "tok/s",
    estMem: "~",
    verdict: {
      fits: "Runs well",
      tight: "Tight fit",
      offload: "Needs offload (slower)",
    } as Record<string, string>,
    note: "Estimates only; bandwidth tables are a snapshot and throughput is approximate.",
    sourceLive: "Live · HuggingFace trending",
    sourceStatic: "Built-in snapshot (fetching latest…)",
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
  if (hw.bandwidth_gbps) parts.push(`${hw.bandwidth_gbps} GB/s`);
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
}: {
  rec: CookbookRec;
  t: (typeof LABELS)["en"];
  pulls: Record<string, string>;
  pendingTag: string | null;
  onPull: (tag: string) => void;
  disabled: boolean;
}) {
  const pullState = pulls[rec.tag];
  const isPulling = pullState === "pulling" || pendingTag === rec.tag;
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
          · {t.estMem}
          {rec.est_mem_gb} GB
          {rec.est_tokens_per_s
            ? ` · ~${rec.est_tokens_per_s} ${t.speedUnit}`
            : ""}
        </div>
      </div>
      {rec.installed ? (
        <span className="shrink-0 text-xs font-medium text-success">
          {t.installed}
        </span>
      ) : (
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 text-xs"
          disabled={disabled || isPulling}
          onClick={() => onPull(rec.tag)}
        >
          {isPulling ? t.pulling : t.install}
        </Button>
      )}
    </div>
  );
}

export function ModelCookbook() {
  const { locale } = useI18n();
  const t =
    (locale || "en").slice(0, 2).toLowerCase() === "zh" ? LABELS.zh : LABELS.en;
  const { snapshot, isLoading } = useCookbook();
  const { pull, pendingTag } = useCookbookPull();

  const recs = snapshot?.recommendations ?? [];
  const ollamaDown = snapshot ? !snapshot.ollama_available : false;

  return (
    <div className="min-w-0 max-w-full overflow-hidden rounded-lg border border-border-default bg-card/50 p-3 sm:p-5">
      <div className="flex min-w-0 flex-col items-start justify-between gap-2 sm:flex-row sm:gap-4">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">{t.title}</h4>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t.subtitle}
          </p>
        </div>
        {snapshot?.hardware && (
          <span className="max-w-full truncate text-left text-xs text-muted-foreground sm:max-w-[45%] sm:shrink-0 sm:text-right">
            {hardwareLine(snapshot.hardware)}
          </span>
        )}
      </div>

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
            {recs.map((rec) => (
              <RecRow
                key={rec.tag}
                rec={rec}
                t={t}
                pulls={snapshot?.pulls ?? {}}
                pendingTag={pendingTag}
                onPull={pull}
                disabled={ollamaDown}
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
