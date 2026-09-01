/**
 * Annotations -- add comments/annotations to specific agent messages,
 * similar to Google Docs comment threads.
 *
 * Sub-components:
 *  - AnnotationThread:  renders a single annotation with replies
 *  - AnnotationSidebar: lists all annotations for the current thread
 *  - AddAnnotationButton: inline button placed next to a message to start a comment
 */

import { useCallback, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  MessageSquarePlus,
  RotateCcw,
  Send,
  Trash2,
  Undo2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n";
import { cn } from "@/lib/utils";

import {
  useCollab,
  type Annotation,
  type AnnotationReply,
} from "./collab-provider";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0]![0]! + parts[1]![0]!).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function timeAgo(ts: number, t: Translations["annotations"]): string {
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return t.justNow;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t.minutesAgo(minutes);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t.hoursAgo(hours);
  const days = Math.floor(hours / 24);
  return t.daysAgo(days);
}

// ---------------------------------------------------------------------------
// AnnotationThread
// ---------------------------------------------------------------------------

interface AnnotationThreadProps {
  annotation: Annotation;
  className?: string;
}

export function AnnotationThread({
  annotation,
  className,
}: AnnotationThreadProps) {
  const { t } = useI18n();
  const {
    resolveAnnotation,
    unresolveAnnotation,
    deleteAnnotation,
    replyToAnnotation,
  } = useCollab();

  const [replyText, setReplyText] = useState("");
  const [replying, setReplying] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleReply = useCallback(async () => {
    const text = replyText.trim();
    if (!text) return;
    setReplying(true);
    await replyToAnnotation(annotation.annotation_id, text);
    setReplyText("");
    setReplying(false);
  }, [replyText, annotation.annotation_id, replyToAnnotation]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void handleReply();
      }
    },
    [handleReply],
  );

  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3 text-sm shadow-[var(--shadow-xs)] transition-colors",
        annotation.resolved && "opacity-60",
        className,
      )}
    >
      {/* Header */}
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {annotation.author && (
            <div
              className="flex size-6 shrink-0 items-center justify-center rounded-lg text-xs font-semibold text-white"
              style={{ backgroundColor: annotation.author.avatar_color }}
            >
              {initials(annotation.author.display_name)}
            </div>
          )}
          <div className="flex flex-col">
            <span className="text-xs font-medium">
              {annotation.author?.display_name ?? t.annotations.anonymous}
            </span>
            <span className="text-xs text-muted-foreground">
              {timeAgo(annotation.created_at, t.annotations)}
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5">
          {annotation.resolved ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() =>
                    void unresolveAnnotation(annotation.annotation_id)
                  }
                  aria-label={t.annotations.unresolve}
                >
                  <Undo2 size={12} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{t.annotations.unresolve}</TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() =>
                    void resolveAnnotation(annotation.annotation_id)
                  }
                  aria-label={t.annotations.resolve}
                >
                  <CheckCircle2 size={12} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{t.annotations.resolve}</TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 text-destructive hover:text-destructive"
                onClick={() => void deleteAnnotation(annotation.annotation_id)}
                aria-label={t.annotations.delete}
              >
                <Trash2 size={12} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t.annotations.delete}</TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Body */}
      <p className="mb-2 whitespace-pre-wrap text-sm leading-relaxed">
        {annotation.body}
      </p>

      {/* Resolved badge */}
      {annotation.resolved && (
        <div className="mb-2 flex items-center gap-1 text-xs text-success">
          <Check size={12} />
          <span>{t.annotations.resolved}</span>
        </div>
      )}

      {/* Replies */}
      {annotation.replies.length > 0 && (
        <div className="mt-2 flex flex-col gap-2 border-t pt-2">
          {annotation.replies.map((reply) => (
            <ReplyBubble key={reply.reply_id} reply={reply} />
          ))}
        </div>
      )}

      {/* Reply input */}
      {!annotation.resolved && (
        <div className="mt-2 flex items-center gap-1.5">
          <input
            ref={inputRef}
            type="text"
            className="h-7 flex-1 rounded-lg border bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
            placeholder={t.annotations.replyPlaceholder}
            value={replyText}
            onChange={(e) => setReplyText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={replying}
          />
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            disabled={!replyText.trim() || replying}
            onClick={() => void handleReply()}
            aria-label={t.annotations.sendReply}
          >
            <Send size={12} />
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReplyBubble
// ---------------------------------------------------------------------------

function ReplyBubble({ reply }: { reply: AnnotationReply }) {
  const { t } = useI18n();
  return (
    <div className="flex items-start gap-2">
      {reply.author && (
        <div
          className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-lg text-xs font-semibold text-white"
          style={{ backgroundColor: reply.author.avatar_color }}
        >
          {initials(reply.author.display_name)}
        </div>
      )}
      <div className="flex flex-col">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xs font-medium">
            {reply.author?.display_name ?? t.annotations.anonymous}
          </span>
          <span className="text-xs text-muted-foreground">
            {timeAgo(reply.created_at, t.annotations)}
          </span>
        </div>
        <p className="text-xs leading-relaxed">{reply.body}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AnnotationSidebar
// ---------------------------------------------------------------------------

interface AnnotationSidebarProps {
  className?: string;
  /** If true, only show unresolved annotations */
  hideResolved?: boolean;
}

export function AnnotationSidebar({
  className,
  hideResolved = false,
}: AnnotationSidebarProps) {
  const { t } = useI18n();
  const { annotations } = useCollab();
  const [showResolved, setShowResolved] = useState(!hideResolved);

  const filtered = showResolved
    ? annotations
    : annotations.filter((a) => !a.resolved);

  const resolvedCount = annotations.filter((a) => a.resolved).length;

  if (annotations.length === 0) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-2 p-6 text-center text-muted-foreground",
          className,
        )}
      >
        <MessageSquarePlus size={32} strokeWidth={1.5} />
        <p className="text-sm">{t.annotations.noAnnotations}</p>
        <p className="text-xs">{t.annotations.noAnnotationsHint}</p>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* Header with filter toggle */}
      <div className="flex items-center justify-between px-1">
        <span className="text-xs font-medium text-muted-foreground">
          {t.annotations.annotation(filtered.length)}
        </span>
        {resolvedCount > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={() => setShowResolved((v) => !v)}
          >
            <RotateCcw size={12} className="mr-1" />
            {showResolved
              ? t.annotations.hideResolved(resolvedCount)
              : t.annotations.showResolved(resolvedCount)}
          </Button>
        )}
      </div>

      {/* Annotation list */}
      <div className="flex flex-col gap-2">
        {filtered.map((ann) => (
          <AnnotationThread key={ann.annotation_id} annotation={ann} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddAnnotationButton (inline, next to a message)
// ---------------------------------------------------------------------------

interface AddAnnotationButtonProps {
  messageId: string;
  className?: string;
}

export function AddAnnotationButton({
  messageId,
  className,
}: AddAnnotationButtonProps) {
  const { t } = useI18n();
  const { addAnnotation } = useCollab();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(async () => {
    const body = text.trim();
    if (!body) return;
    setSubmitting(true);
    await addAnnotation(messageId, body);
    setText("");
    setOpen(false);
    setSubmitting(false);
  }, [text, messageId, addAnnotation]);

  if (!open) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "size-6 opacity-0 transition-opacity group-hover/msg:opacity-100",
              className,
            )}
            onClick={() => {
              setOpen(true);
              setTimeout(() => inputRef.current?.focus(), 50);
            }}
            aria-label={t.annotations.addComment}
          >
            <MessageSquarePlus size={14} />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">{t.annotations.addComment}</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div
      className={cn(
        "mt-1 flex flex-col gap-1.5 rounded-lg border bg-card p-2 shadow-[var(--shadow-xs)]",
        className,
      )}
    >
      <textarea
        ref={inputRef}
        className="min-h-[60px] w-full resize-none rounded-lg border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-ring"
        placeholder={t.annotations.addCommentPlaceholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void handleSubmit();
          }
          if (e.key === "Escape") {
            setOpen(false);
            setText("");
          }
        }}
        disabled={submitting}
      />
      <div className="flex items-center justify-end gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          className="h-6 text-xs"
          onClick={() => {
            setOpen(false);
            setText("");
          }}
        >
          {t.annotations.cancel}
        </Button>
        <Button
          variant="default"
          size="sm"
          className="h-6 text-xs"
          disabled={!text.trim() || submitting}
          onClick={() => void handleSubmit()}
        >
          {t.annotations.comment}
        </Button>
      </div>
    </div>
  );
}
