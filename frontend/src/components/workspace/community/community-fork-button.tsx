import { useState } from "react";
import { CheckIcon, ZapIcon } from "lucide-react";
import { toast } from "sonner";
import { installAgent } from "@/core/agents/agent-world-api";
import { creditForkEarn, debitForkSpend } from "@/core/credits/ledger";
import { markForked, readForked, type CommunityPost } from "./community-data";
import { cn } from "@/lib/utils";

/**
 * 复刻按钮 —— "从看到用"的闭环入口。
 *
 * 优先调用后端 `installAgent(appRef)` 把 mini-app 真实加入工作台；
 * 后端无此模板时（纯种子/离线）回退为本地持久化标记。
 * 复刻状态存 localStorage，已复刻后按钮置灰。
 *
 * 积分经济：付费内容（priceCredits>0）复刻前先扣社区积分；
 * 若复刻的是自己发布的内容，作者获得复刻分成（共创赚钱）。
 */
export function CommunityForkButton({
  post,
  className,
}: {
  post: CommunityPost;
  className?: string;
}) {
  const [forked, setForked] = useState(() => readForked().includes(post.id));
  const [busy, setBusy] = useState(false);

  const handleFork = async () => {
    if (forked || busy || !post.appRef) return;

    // 付费内容先扣社区积分（习惯通货：花在付费 agent 上）。
    if (post.priceCredits > 0) {
      const ok = debitForkSpend(post.title, post.priceCredits);
      if (!ok) {
        toast.error("社区积分不足，先去签到或创作赚积分吧");
        return;
      }
    }

    setBusy(true);
    try {
      // 优先真实安装到工作台；失败则本地标记（种子模板无后端实体）。
      try {
        await installAgent(post.appRef);
        toast.success(`已复刻「${post.title}」，已加入工作台`);
      } catch {
        toast.success(`已复刻「${post.title}」`);
      }
      // 复刻的是自己发布的内容 → 作者获得复刻分成（共创赚钱闭环）。
      if (post.author === "我") creditForkEarn(post.title);
      markForked(post.id);
      setForked(true);
    } catch {
      toast.error("复刻失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleFork}
      disabled={forked || busy}
      aria-label={
        forked
          ? `已复刻 ${post.title}`
          : post.priceCredits > 0
            ? `复刻 ${post.title}，需要 ${post.priceCredits} 积分`
            : `免费复刻 ${post.title}`
      }
      title={forked ? "已加入工作台" : post.priceCredits > 0 ? `复刻需要 ${post.priceCredits} 积分` : "免费加入工作台"}
      className={cn(
        "flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-bold transition-colors",
        forked
          ? "cursor-default bg-muted/60 text-muted-foreground"
          : "bg-primary/10 text-primary hover:bg-primary/15",
        className,
      )}
    >
      {forked ? (
        <CheckIcon className="size-3" />
      ) : (
        <ZapIcon className="size-3" />
      )}
      {forked
        ? "已复刻"
        : post.priceCredits > 0
          ? `${post.priceCredits}积分`
          : "可复刻"}
    </button>
  );
}
