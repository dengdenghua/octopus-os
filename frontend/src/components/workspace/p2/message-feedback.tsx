/**
 * Message Feedback Component
 *
 * Thumbs up/down feedback buttons for individual messages.
 * Supports tags and comments for RLHF data collection.
 */

import { ThumbsUpIcon, ThumbsDownIcon, MessageCircleIcon } from "lucide-react";
import { useState } from "react";
import { useMessageFeedback } from "@/core/api/p2-hooks";
import type { FeedbackType } from "@/core/api/p2";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface MessageFeedbackProps {
  threadId: string;
  messageIndex: number;
  className?: string;
  compact?: boolean;
}

const FEEDBACK_TAGS = {
  thumbs_up: [
    "helpful",
    "accurate",
    "clear",
    "complete",
    "creative",
    "efficient",
  ],
  thumbs_down: [
    "inaccurate",
    "unclear",
    "incomplete",
    "off-topic",
    "harmful",
    "slow",
  ],
} as const;

export function MessageFeedback({
  threadId,
  messageIndex,
  className,
  compact = false,
}: MessageFeedbackProps) {
  const { feedbacks, addFeedback, loading } = useMessageFeedback(threadId);
  const [showDialog, setShowDialog] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [pendingFeedbackType, setPendingFeedbackType] =
    useState<FeedbackType | null>(null);

  // Find existing feedback for this message
  const existingFeedback = feedbacks.find(
    (f) => f.message_index === messageIndex,
  );

  const handleQuickFeedback = async (feedbackType: FeedbackType) => {
    if (loading) return;

    // If there's already feedback of this type, do nothing
    if (existingFeedback?.feedback_type === feedbackType) return;

    await addFeedback(messageIndex, feedbackType, [], "");
  };

  const handleDetailedFeedback = async () => {
    if (loading || !pendingFeedbackType) return;

    await addFeedback(
      messageIndex,
      pendingFeedbackType,
      selectedTags,
      commentText.trim(),
    );

    // Reset state
    setShowDialog(false);
    setCommentText("");
    setSelectedTags([]);
    setPendingFeedbackType(null);
  };

  const openDetailedFeedback = (feedbackType: FeedbackType) => {
    setPendingFeedbackType(feedbackType);
    setSelectedTags([]);
    setCommentText("");
    setShowDialog(true);
  };

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  };

  const availableTags =
    pendingFeedbackType === "thumbs_up"
      ? FEEDBACK_TAGS.thumbs_up
      : FEEDBACK_TAGS.thumbs_down;

  return (
    <div className={cn("flex items-center gap-1", className)}>
      {/* Thumbs Up */}
      <button
        onClick={() => handleQuickFeedback("thumbs_up")}
        onContextMenu={(e) => {
          e.preventDefault();
          openDetailedFeedback("thumbs_up");
        }}
        disabled={loading}
        className={cn(
          "text-muted-foreground hover:text-foreground hover:bg-muted transition-colors",
          "rounded-md p-1.5 disabled:opacity-50",
          existingFeedback?.feedback_type === "thumbs_up" &&
            "text-green-600 dark:text-green-400",
          compact && "p-1",
        )}
        title="Thumbs up (right-click for details)"
        aria-label="Helpful response"
      >
        <ThumbsUpIcon className={cn("size-4", compact && "size-3.5")} />
      </button>

      {/* Thumbs Down */}
      <button
        onClick={() => handleQuickFeedback("thumbs_down")}
        onContextMenu={(e) => {
          e.preventDefault();
          openDetailedFeedback("thumbs_down");
        }}
        disabled={loading}
        className={cn(
          "text-muted-foreground hover:text-foreground hover:bg-muted transition-colors",
          "rounded-md p-1.5 disabled:opacity-50",
          existingFeedback?.feedback_type === "thumbs_down" &&
            "text-red-600 dark:text-red-400",
          compact && "p-1",
        )}
        title="Thumbs down (right-click for details)"
        aria-label="Unhelpful response"
      >
        <ThumbsDownIcon className={cn("size-4", compact && "size-3.5")} />
      </button>

      {/* Comment Dialog */}
      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogTrigger asChild>
          <button
            onClick={() => setShowDialog(true)}
            disabled={loading}
            className={cn(
              "text-muted-foreground hover:text-foreground hover:bg-muted transition-colors",
              "rounded-md p-1.5 disabled:opacity-50",
              existingFeedback?.comment && "text-blue-600 dark:text-blue-400",
              compact && "p-1",
            )}
            title="Add comment"
            aria-label="Add feedback comment"
          >
            <MessageCircleIcon className={cn("size-4", compact && "size-3.5")} />
          </button>
        </DialogTrigger>

        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Feedback Details</DialogTitle>
            <DialogDescription>
              Help us improve by providing specific feedback
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Tags */}
            {pendingFeedbackType && (
              <div className="space-y-2">
                <label className="text-sm font-medium">Tags (optional)</label>
                <div className="flex flex-wrap gap-1.5">
                  {availableTags.map((tag) => (
                    <button
                      key={tag}
                      onClick={() => toggleTag(tag)}
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-xs transition-colors",
                        selectedTags.includes(tag)
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-background hover:bg-muted border-border",
                      )}
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Comment */}
            <div className="space-y-2">
              <label className="text-sm font-medium">Comment (optional)</label>
              <Textarea
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                placeholder="What could be improved?"
                className="min-h-[100px] resize-none"
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowDialog(false);
                setPendingFeedbackType(null);
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleDetailedFeedback}
              disabled={loading || !pendingFeedbackType}
            >
              Submit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Existing feedback indicator */}
      {existingFeedback && (existingFeedback.tags.length > 0 || existingFeedback.comment) && (
        <span className="text-muted-foreground text-xs">
          {existingFeedback.tags.length > 0 && (
            <span className="inline-flex items-center gap-1">
              {existingFeedback.tags.slice(0, 2).join(", ")}
              {existingFeedback.tags.length > 2 && ` +${existingFeedback.tags.length - 2}`}
            </span>
          )}
        </span>
      )}
    </div>
  );
}
