import { useEffect, useState } from "react";

import { ArtifactLoadError } from "@/core/artifacts/loader";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { authHeaders } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";

import { ArtifactReadError } from "./artifact-load-error";

/** Fetch with the same credentials as text/Office reads; an iframe cannot set
 * the bearer header. Revoke each file's blob and ignore obsolete responses. */
export function BinaryArtifactPreview({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  const { t } = useI18n();
  const [revision, setRevision] = useState(0);
  const requestUrl = urlOfArtifact({ filepath, threadId, isMock });
  const [result, setResult] = useState<{
    requestUrl: string;
    src?: string;
    error?: unknown;
  } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setResult(null);
    void fetch(requestUrl, {
      headers: authHeaders(),
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok)
          throw new ArtifactLoadError(response.status, "file read failed");
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setResult({ requestUrl, src: objectUrl });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ requestUrl, error });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [requestUrl, revision]);
  const current = result?.requestUrl === requestUrl ? result : null;
  if (current?.error) {
    return (
      <ArtifactReadError
        error={current.error}
        onRetry={() => setRevision((value) => value + 1)}
      />
    );
  }
  if (!current?.src) return <div role="status">{t.common.loading}…</div>;
  return (
    <iframe
      className="size-full"
      sandbox=""
      title={t.common.preview}
      src={current.src}
    />
  );
}
