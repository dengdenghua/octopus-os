import { Button } from "@/components/ui/button";
import { ArtifactLoadError } from "@/core/artifacts/loader";
import { useI18n } from "@/core/i18n/hooks";

export function artifactReadErrorMessage(
  error: unknown,
  messages: {
    fileAccessDenied: string;
    fileMissing: string;
    fileUnavailable: string;
    fileTooLarge: string;
  },
) {
  const status = error instanceof ArtifactLoadError ? error.status : null;
  return status === 401 || status === 403
    ? messages.fileAccessDenied
    : status === 404
      ? messages.fileMissing
      : status === 413
        ? messages.fileTooLarge
        : messages.fileUnavailable;
}

export function ArtifactReadError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const message = artifactReadErrorMessage(error, t.livePreview);
  return (
    <div
      role="alert"
      className="flex size-full flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground"
    >
      <span>{message}</span>
      {onRetry && (
        <Button onClick={onRetry} size="sm" type="button" variant="outline">
          {t.livePreview.previewRetry}
        </Button>
      )}
    </div>
  );
}
