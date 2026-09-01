export type QuickReplyDetail = {
  text?: unknown;
  threadId?: unknown;
};

export const QUICK_REPLY_EVENT = "echo:quick-reply";

export function dispatchQuickReply(detail: QuickReplyDetail): boolean {
  const event = new CustomEvent<QuickReplyDetail>(QUICK_REPLY_EVENT, {
    cancelable: true,
    detail,
  });
  return !window.dispatchEvent(event);
}

export function quickReplyTextForThread(
  detail: QuickReplyDetail | null | undefined,
  currentThreadId: string,
): string | null {
  if (
    typeof detail?.threadId === "string" &&
    detail.threadId !== currentThreadId
  ) {
    return null;
  }
  const text = typeof detail?.text === "string" ? detail.text.trim() : "";
  return text || null;
}
