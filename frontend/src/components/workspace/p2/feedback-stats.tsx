/**
 * Feedback Statistics Component
 *
 * Display aggregated feedback statistics for a thread.
 */

import { ThumbsUpIcon, ThumbsDownIcon, TrendingUpIcon } from "lucide-react";
import { useMessageFeedback } from "@/core/api/p2-hooks";
import { cn } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface FeedbackStatsProps {
  threadId: string;
  className?: string;
  compact?: boolean;
}

export function FeedbackStats({
  threadId,
  className,
  compact = false,
}: FeedbackStatsProps) {
  const { stats, loading } = useMessageFeedback(threadId);

  if (loading || !stats) {
    return null;
  }

  const total = stats.thumbs_up + stats.thumbs_down;
  if (total === 0) {
    return null;
  }

  const positiveRatio = total > 0 ? (stats.thumbs_up / total) * 100 : 0;
  const totalMessages = stats.messages_with_feedback.length;
  const topTags = Object.entries(stats.tags)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  if (compact) {
    return (
      <div className={cn("flex items-center gap-3 text-sm", className)}>
        <div className="flex items-center gap-1.5">
          <ThumbsUpIcon className="text-green-600 dark:text-green-400 size-3.5" />
          <span className="text-muted-foreground">{stats.thumbs_up}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <ThumbsDownIcon className="text-red-600 dark:text-red-400 size-3.5" />
          <span className="text-muted-foreground">{stats.thumbs_down}</span>
        </div>
        {totalMessages > 0 && (
          <div className="text-muted-foreground/70 text-xs">
            {totalMessages} rated
          </div>
        )}
      </div>
    );
  }

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <TrendingUpIcon className="size-4" />
          Feedback Statistics
        </CardTitle>
        <CardDescription className="text-xs">
          User feedback on messages
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Overall stats */}
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <ThumbsUpIcon className="text-green-600 dark:text-green-400 size-4" />
              <span className="text-sm font-medium">{stats.thumbs_up}</span>
            </div>
            <p className="text-muted-foreground text-xs">Positive</p>
          </div>

          <div className="space-y-1">
            <div className="flex items-center gap-1.5">
              <ThumbsDownIcon className="text-red-600 dark:text-red-400 size-4" />
              <span className="text-sm font-medium">{stats.thumbs_down}</span>
            </div>
            <p className="text-muted-foreground text-xs">Negative</p>
          </div>

          <div className="space-y-1">
            <div className="text-sm font-medium">{positiveRatio.toFixed(0)}%</div>
            <p className="text-muted-foreground text-xs">Positive Rate</p>
          </div>
        </div>

        {/* Progress bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              {totalMessages} message{totalMessages !== 1 ? "s" : ""} rated
            </span>
          </div>
          <div className="bg-muted h-2 overflow-hidden rounded-full">
            <div
              className="bg-primary h-full transition-all duration-300"
              style={{
                width: `${stats.total > 0 ? 100 : 0}%`,
              }}
            />
          </div>
        </div>

        {/* Top tags */}
        {topTags.length > 0 && (
          <div className="space-y-2">
            <p className="text-muted-foreground text-xs font-medium">Top Tags</p>
            <div className="flex flex-wrap gap-1.5">
              {topTags.map(([tag, count]) => (
                <span
                  key={tag}
                  className="bg-muted text-muted-foreground rounded-full px-2.5 py-1 text-xs"
                >
                  {tag} ({count})
                </span>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
