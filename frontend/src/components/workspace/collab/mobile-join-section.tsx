import { useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon, SmartphoneIcon } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { authHeaders } from "@/core/auth/api";
import { copyTextToClipboard } from "@/core/clipboard";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

interface JoinInfo {
  lan_ip: string;
  ws_port: number;
  ws_url: string;
  token: string;
  connect_string: string;
}

/** "拉手机进群" — shows a scan-to-join QR + a paste-able 口令 so a phone running
 * echo-mobile can connect to this gateway without typing an IP. */
export function MobileJoinSection() {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const { data } = useQuery({
    queryKey: ["tentacle-join-info"],
    queryFn: async ({ signal }): Promise<JoinInfo | null> => {
      try {
        const res = await fetch(
          `${getBackendBaseURL()}/api/tentacle/join-info`,
          {
            headers: authHeaders(),
            signal,
          },
        );
        if (!res.ok) return null;
        return (await res.json()) as JoinInfo;
      } catch {
        return null;
      }
    },
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  // Bridge offline / not mounted → don't render the section at all.
  if (!data) return null;

  const handleCopy = async () => {
    try {
      await copyTextToClipboard(data.connect_string);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  return (
    <section className="min-w-0 rounded-lg border border-border-default bg-muted/10 p-3">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <SmartphoneIcon className="size-4 text-primary" />
        {t.collab.mobileJoin.title}
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        {t.collab.mobileJoin.description}
      </div>

      <div className="mt-3 flex items-start gap-3">
        <div className="shrink-0 rounded-lg bg-white p-2">
          <QRCodeSVG value={data.connect_string} size={104} />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t.collab.mobileJoin.connectCodeLabel}
            </div>
            <div className="flex gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md border border-border-default bg-background px-2 py-1.5 text-xs">
                {data.connect_string}
              </code>
              <Button
                onClick={handleCopy}
                variant="outline"
                size="icon"
                className="shrink-0"
              >
                {copied ? (
                  <CheckIcon className="size-4" />
                ) : (
                  <CopyIcon className="size-4" />
                )}
              </Button>
            </div>
          </div>
          <div className="text-xs leading-relaxed text-muted-foreground">
            {t.collab.mobileJoin.manualFillPrefix}{" "}
            <code className="text-foreground">{data.ws_url}</code>
            {data.token ? (
              <>
                {" "}
                {t.collab.mobileJoin.manualFillCode}{" "}
                <code className="text-foreground">{data.token}</code>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
