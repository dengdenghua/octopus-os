import { useRef, useState, type ComponentProps } from "react";
import { toast } from "sonner";

import { downloadArtifactFile } from "@/core/artifacts/loader";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";

import { artifactReadErrorMessage } from "./artifact-load-error";

export function ArtifactDownloadLink({
  filepath,
  threadId,
  children,
  ...props
}: Omit<ComponentProps<"a">, "href" | "onClick"> & {
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  return (
    <a
      {...props}
      href={urlOfArtifact({ filepath, threadId, download: true })}
      aria-disabled={busy}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        void downloadArtifactFile({ filepath, threadId })
          .catch((error: unknown) =>
            toast.error(artifactReadErrorMessage(error, t.livePreview)),
          )
          .finally(() => {
            pending.current = false;
            setBusy(false);
          });
      }}
    >
      {children}
    </a>
  );
}
