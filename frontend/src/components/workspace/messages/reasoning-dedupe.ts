type ReasoningDedupeEvent =
  | {
      kind: "thought";
      text: string;
    }
  | {
      kind: string;
      text?: string;
    };

export function normalizeReasoningTextForDedupe(value?: string | null): string {
  return (value ?? "")
    .replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, "")
    .replace(/\b(?:Thought|思考|想法)\s*[:：]\s*/gi, "")
    .replace(/[。！？；;.!?]+$/gm, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function shouldShowReasoningProcessText(
  reasoningContent: string | null | undefined,
  publicThinkingSummary: string | null | undefined,
  events: ReasoningDedupeEvent[],
): boolean {
  if (events.length === 0 || !reasoningContent?.trim()) return false;

  const summaryText = normalizeReasoningTextForDedupe(publicThinkingSummary);
  if (!summaryText) return true;

  if (events.some((event) => event.kind !== "thought")) return true;

  const thoughtTexts = events
    .filter(
      (event): event is Extract<ReasoningDedupeEvent, { kind: "thought" }> =>
        event.kind === "thought",
    )
    .map((event) => normalizeReasoningTextForDedupe(event.text))
    .filter(Boolean);

  if (
    thoughtTexts.length > 0 &&
    thoughtTexts.every(
      (thoughtText) =>
        summaryText.includes(thoughtText) || thoughtText.includes(summaryText),
    )
  ) {
    return false;
  }

  return normalizeReasoningTextForDedupe(reasoningContent) !== summaryText;
}
