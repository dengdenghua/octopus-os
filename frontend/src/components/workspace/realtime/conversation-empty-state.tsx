import { useI18n } from "@/core/i18n/hooks";

export function ConversationEmptyState({
  isGroupConversation,
  hasError,
  onRetry,
}: {
  isGroupConversation: boolean;
  hasError: boolean;
  onRetry: () => void;
}) {
  const { t } = useI18n();

  return (
    <div
      className="mx-auto flex min-h-[clamp(12rem,38vh,22rem)] w-full max-w-sm flex-col items-center justify-center gap-1.5 px-4 text-center"
      role="status"
      data-testid="conversation-empty-state"
    >
      <span className="text-sm font-medium text-foreground/80">
        {t.conversation.noMessages}
      </span>
      <span className="text-xs leading-5 text-muted-foreground">
        {isGroupConversation
          ? t.teamInput.placeholder
          : t.conversation.startConversation}
      </span>
      {hasError ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 text-xs font-medium text-foreground/75 underline-offset-4 hover:text-foreground hover:underline"
        >
          {t.conversation.retry}
        </button>
      ) : null}
    </div>
  );
}
