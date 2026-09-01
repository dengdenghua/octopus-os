import { EyeIcon, EyeOffIcon, XIcon } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { swallow } from "@/core/utils/log";
import { getBackendBaseURL } from "@/core/config";
import { authHeaders, jsonAuthHeaders } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";

import { Button } from "@/components/ui/button";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  platform: string;
  displayName: string;
  helpUrl?: string;
  onSaved: () => void;
  onDeleted: () => void;
  hasExisting: boolean;
}

interface FieldDef {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  required?: boolean;
  hint?: string;
}

function getPlatformFields(t: Translations): Record<string, FieldDef[]> {
  return {
    slack: [
      {
        key: "bot_token",
        label: "Bot User OAuth Token",
        placeholder: "xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-...",
        secret: true,
        required: true,
        hint: t.channelCredential.slackBotTokenHint,
      },
      {
        key: "signing_secret",
        label: "Signing Secret",
        placeholder: "3f...(32 hex)",
        secret: true,
        required: true,
        hint: t.channelCredential.slackSigningSecretHint,
      },
    ],
    dingtalk: [
      {
        key: "webhook_url",
        label: "Webhook URL",
        placeholder: "https://oapi.dingtalk.com/robot/send?access_token=...",
        required: true,
        hint: t.channelCredential.dingtalkWebhookUrlHint,
      },
      {
        key: "secret",
        label: t.channelCredential.dingtalkSecretLabel,
        placeholder: "SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: false,
        hint: t.channelCredential.dingtalkSecretHint,
      },
    ],
    feishu: [
      {
        key: "app_id",
        label: "App ID",
        placeholder: "cli_xxxxxxxxxxxx",
        required: true,
        hint: t.channelCredential.feishuAppIdHint,
      },
      {
        key: "app_secret",
        label: "App Secret",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "verification_token",
        label: "Verification Token",
        placeholder: t.channelCredential.feishuVerificationTokenPlaceholder,
        secret: true,
        required: true,
        hint: t.channelCredential.feishuVerificationTokenHint,
      },
    ],
    telegram: [
      {
        key: "bot_token",
        label: "Bot Token",
        placeholder: "123456789:ABCdef...",
        secret: true,
        required: true,
        hint: t.channelCredential.telegramBotTokenHint,
      },
      {
        key: "webhook_secret",
        label: t.channelCredential.telegramWebhookSecretLabel,
        placeholder: t.channelCredential.telegramWebhookSecretPlaceholder,
        secret: true,
        required: false,
        hint: t.channelCredential.telegramWebhookSecretHint,
      },
    ],
    discord: [
      {
        key: "bot_token",
        label: "Bot Token",
        placeholder: t.channelCredential.discordBotTokenPlaceholder,
        secret: true,
        required: true,
        hint: t.channelCredential.discordBotTokenHint,
      },
      {
        key: "public_key",
        label: "Application Public Key",
        placeholder: t.channelCredential.discordPublicKeyPlaceholder,
        required: true,
        hint: t.channelCredential.discordPublicKeyHint,
      },
    ],
    signal: [
      {
        key: "phone_number",
        label: "Phone Number",
        placeholder: "+1234567890",
        required: true,
      },
      {
        key: "api_base_url",
        label: "Signal CLI REST API URL",
        placeholder: "http://localhost:8080",
        required: true,
      },
      {
        key: "webhook_secret",
        label: "Webhook Secret",
        placeholder: "optional shared secret",
        secret: true,
        required: false,
      },
    ],
    whatsapp: [
      {
        key: "phone_number_id",
        label: "Phone Number ID",
        placeholder: "123456789012345",
        required: true,
      },
      {
        key: "access_token",
        label: "Access Token",
        placeholder: "EAAx...",
        secret: true,
        required: true,
      },
      {
        key: "verify_token",
        label: "Verify Token",
        placeholder: "my-verify-token",
        secret: true,
        required: true,
      },
      {
        key: "app_secret",
        label: "App Secret",
        placeholder: "abc123...",
        secret: true,
        required: false,
      },
    ],
    email: [
      {
        key: "smtp_host",
        label: "SMTP Host",
        placeholder: "smtp.gmail.com",
        required: true,
      },
      {
        key: "smtp_port",
        label: "SMTP Port",
        placeholder: "587",
        required: true,
      },
      {
        key: "imap_host",
        label: "IMAP Host",
        placeholder: "imap.gmail.com",
        required: true,
      },
      {
        key: "username",
        label: "Username",
        placeholder: "bot@example.com",
        required: true,
      },
      {
        key: "password",
        label: "Password",
        placeholder: "app-specific password",
        secret: true,
        required: true,
      },
      {
        key: "from_address",
        label: "From Address",
        placeholder: "bot@example.com",
        required: true,
      },
    ],
    sms: [
      {
        key: "account_sid",
        label: "Account SID",
        placeholder: "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        key: "auth_token",
        label: "Auth Token",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "from_number",
        label: "From Number",
        placeholder: "+1234567890",
        required: true,
      },
    ],
    mattermost: [
      {
        key: "bot_token",
        label: "Bot Token",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "server_url",
        label: "Server URL",
        placeholder: "https://mattermost.example.com",
        required: true,
      },
    ],
    matrix: [
      {
        key: "homeserver_url",
        label: "Homeserver URL",
        placeholder: "https://matrix.org",
        required: true,
      },
      {
        key: "access_token",
        label: "Access Token",
        placeholder: "syt_...",
        secret: true,
        required: true,
      },
    ],
    wecom: [
      {
        key: "corp_id",
        label: "Corp ID",
        placeholder: "wwxxxxxxxxxxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        key: "agent_id",
        label: "Agent ID",
        placeholder: "1000002",
        required: true,
      },
      {
        key: "secret",
        label: "Secret",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "token",
        label: "Callback Token",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "encoding_aes_key",
        label: "Encoding AES Key",
        placeholder: "43 chars base64",
        secret: true,
        required: true,
      },
    ],
    qqbot: [
      {
        key: "app_id",
        label: "App ID",
        placeholder: "xxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        key: "app_secret",
        label: "App Secret",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
    ],
    teams: [
      {
        key: "app_id",
        label: "App ID (Microsoft App ID)",
        placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        required: true,
      },
      {
        key: "app_password",
        label: "App Password",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
    ],
    line: [
      {
        key: "channel_access_token",
        label: "Channel Access Token",
        placeholder: "xxx...",
        secret: true,
        required: true,
      },
      {
        key: "channel_secret",
        label: "Channel Secret",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
    ],
    homeassistant: [
      {
        key: "ha_url",
        label: "Home Assistant URL",
        placeholder: "http://homeassistant.local:8123",
        required: true,
      },
      {
        key: "long_lived_token",
        label: "Long-Lived Access Token",
        placeholder: "eyJ...",
        secret: true,
        required: true,
      },
    ],
    bluebubbles: [
      {
        key: "server_url",
        label: "BlueBubbles Server URL",
        placeholder: "http://localhost:1234",
        required: true,
      },
      {
        key: "api_key",
        label: "API Key",
        placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        secret: true,
        required: true,
      },
      {
        key: "password",
        label: "Password",
        placeholder: "optional",
        secret: true,
        required: false,
      },
    ],
    ntfy: [
      {
        key: "server_url",
        label: "ntfy Server URL",
        placeholder: "https://ntfy.sh",
        required: true,
      },
      {
        key: "topic",
        label: "Topic",
        placeholder: "my-agent-topic",
        required: true,
      },
    ],
    webhooks: [
      {
        key: "webhook_secret",
        label: "Webhook Secret",
        placeholder: "shared HMAC secret",
        secret: true,
        required: true,
      },
      {
        key: "outbound_url",
        label: "Outbound URL",
        placeholder: "https://example.com/webhook",
        required: true,
      },
    ],
    google_chat: [
      {
        key: "service_account_key",
        label: "Service Account Key (JSON)",
        placeholder: '{"type":"service_account",...}',
        secret: true,
        required: true,
      },
    ],
    simplex: [
      {
        key: "api_base_url",
        label: "SimpleX Chat API URL",
        placeholder: "http://localhost:5225",
        required: true,
      },
    ],
    open_webui: [
      {
        key: "base_url",
        label: "Open WebUI URL",
        placeholder: "http://localhost:3000",
        required: true,
      },
      {
        key: "api_key",
        label: "API Key",
        placeholder: "sk-...",
        secret: true,
        required: true,
      },
    ],
    yuanbao: [
      {
        key: "bot_id",
        label: "Bot ID",
        placeholder: "xxxxxxxxxxxxxxxx",
        required: true,
      },
      {
        key: "bot_token",
        label: "Bot Token",
        placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        secret: true,
        required: true,
      },
    ],
  };
}

export function ChannelCredentialDialog({
  open,
  onOpenChange,
  platform,
  displayName,
  helpUrl,
  onSaved,
  onDeleted,
  hasExisting,
}: Props) {
  const { t } = useI18n();
  const fields = getPlatformFields(t)[platform] ?? [];
  const isWeChat = platform === "wechat";
  const supported = fields.length > 0 || isWeChat;

  const [values, setValues] = useState<Record<string, string>>({});
  const [showSecret, setShowSecret] = useState<Record<string, boolean>>({});
  const [masked, setMasked] = useState<Record<string, string> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { confirm: confirmAction, confirmDialog } = useConfirmDialog();

  useEffect(() => {
    if (!open) return;
    setValues({});
    setShowSecret({});
    if (!hasExisting) {
      setMasked(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(
          `${getBackendBaseURL()}/api/channels/credentials`,
          {
            headers: authHeaders(),
          },
        );
        if (!r.ok) throw new Error(r.statusText);
        const data = (await r.json()) as {
          credentials: Record<string, Record<string, string>>;
        };
        if (!cancelled) setMasked(data.credentials[platform] ?? null);
      } catch (e) {
        swallow(e);
        if (!cancelled) setMasked(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, platform, hasExisting]);

  async function handleSubmit() {
    if (!supported) return;
    for (const f of fields) {
      if (f.required && !values[f.key]?.trim()) {
        toast.error(`${f.label} ${t.channelCredential.cannotBeEmpty}`);
        return;
      }
    }
    setSubmitting(true);
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/channels/credentials/${platform}`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify(values),
        },
      );
      if (!r.ok) {
        const detail = await r.text();
        throw new Error(detail || r.statusText);
      }
      toast.success(`${displayName} ${t.channelCredential.connected}`);
      onSaved();
      onOpenChange(false);
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : t.channelCredential.saveFailed,
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    const ok = await confirmAction({
      title: t.channelCredential.disconnect,
      description: t.channelCredential.confirmDisconnect(displayName),
      confirmLabel: t.channelCredential.disconnect,
    });
    if (!ok) return;
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/channels/credentials/${platform}`,
        {
          method: "DELETE",
          headers: authHeaders(),
        },
      );
      if (!r.ok) throw new Error(r.statusText);
      toast.success(`${displayName} ${t.channelCredential.disconnected}`);
      onDeleted();
      onOpenChange(false);
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : t.channelCredential.deleteFailed,
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base">
            {hasExisting
              ? t.channelCredential.editCredential
              : t.channelCredential.setCredential}{" "}
            {displayName} {t.channelCredential.botSuffix}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t.channelCredential.credentialLocalHint}
            {helpUrl && (
              <>
                {" "}
                <RoutedWebLink
                  href={helpUrl}
                  openTargetSource="channel-credential-help"
                  className="text-primary underline underline-offset-2"
                >
                  {t.channelCredential.howToConnect}
                </RoutedWebLink>
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        {!supported ? (
          <div className="rounded-lg border border-dashed border-border-default px-4 py-8 text-center text-xs text-muted-foreground">
            <div className="mb-2 font-medium">
              {t.channelCredential.comingSoon}
            </div>
            <div>
              {displayName} {t.channelCredential.unsupportedPlatformDesc1}
              <code className="mx-1 rounded bg-muted/60 px-1 py-0.5 text-xs">
                config.yaml
              </code>
              {t.channelCredential.unsupportedPlatformDesc2}
            </div>
          </div>
        ) : isWeChat ? (
          <WeChatQRForm
            displayName={displayName}
            onConfirmed={() => {
              onSaved();
              onOpenChange(false);
            }}
          />
        ) : (
          <>
            {hasExisting && masked && (
              <div className="rounded-lg border border-border-subtle bg-muted/30 px-3 py-2 text-xs text-muted-foreground space-y-0.5">
                <div className="mb-1 font-medium text-foreground">
                  {t.channelCredential.currentConfigured}
                </div>
                {fields.map((f) => (
                  <div key={f.key} className="flex justify-between gap-2">
                    <span>{f.label}</span>
                    <span className="font-mono truncate max-w-[var(--text-truncate-xl)]">
                      {masked[f.key] ?? "—"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="space-y-3 mt-1">
              {fields.map((f) => {
                const isSecret = !!f.secret;
                const shown = showSecret[f.key];
                return (
                  <div key={f.key} className="space-y-1">
                    <label className="text-xs font-medium">
                      {f.label}
                      {f.required && (
                        <span className="ml-1 text-destructive">*</span>
                      )}
                    </label>
                    <div className="relative">
                      <input
                        type={isSecret && !shown ? "password" : "text"}
                        value={values[f.key] ?? ""}
                        onChange={(e) =>
                          setValues((v) => ({
                            ...v,
                            [f.key]: e.target.value,
                          }))
                        }
                        placeholder={f.placeholder}
                        autoComplete="off"
                        spellCheck={false}
                        className={cn(
                          "w-full rounded-md border border-border-default bg-background/70",
                          "px-2.5 py-1.5 text-xs font-mono outline-none",
                          "placeholder:text-muted-foreground/40",
                          "focus:border-primary/50 focus:ring-2 focus:ring-primary/10",
                          isSecret && "pr-9",
                        )}
                      />
                      {isSecret && (
                        <button
                          type="button"
                          onClick={() =>
                            setShowSecret((s) => ({
                              ...s,
                              [f.key]: !s[f.key],
                            }))
                          }
                          className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
                          title={
                            shown
                              ? t.channelCredential.hide
                              : t.channelCredential.show
                          }
                        >
                          {shown ? (
                            <EyeOffIcon className="size-3.5" />
                          ) : (
                            <EyeIcon className="size-3.5" />
                          )}
                        </button>
                      )}
                    </div>
                    {f.hint && (
                      <div className="text-xs text-muted-foreground">
                        {f.hint}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}

        <div className="mt-4 flex items-center justify-between gap-2">
          <div>
            {hasExisting && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleDelete}
                className="text-xs text-muted-foreground hover:text-destructive"
              >
                <XIcon className="mr-1.5 size-3" />
                {t.channelCredential.disconnect}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="h-8 text-xs"
            >
              {t.common.cancel}
            </Button>
            {supported && !isWeChat && (
              <Button
                type="button"
                size="sm"
                onClick={handleSubmit}
                disabled={submitting}
                className="h-8 text-xs"
              >
                {submitting
                  ? t.channelCredential.saving
                  : t.channelCredential.saveAndConnect}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
      {confirmDialog}
    </Dialog>
  );
}

function WeChatQRForm({
  displayName,
  onConfirmed,
}: {
  displayName: string;
  onConfirmed: () => void;
}) {
  const { t } = useI18n();
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [qrImg, setQrImg] = useState<string | null>(null);
  const [status, setStatus] = useState<
    | "idle"
    | "pending"
    | "scanned"
    | "confirmed"
    | "expired"
    | "rejected"
    | "error"
  >("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  async function startQr() {
    setStarting(true);
    setErrMsg(null);
    try {
      const r = await fetch(
        `${getBackendBaseURL()}/api/channels/wechat/qr/start`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
        },
      );
      if (!r.ok) throw new Error(await r.text());
      const data = (await r.json()) as {
        qrcode: string;
        qrcode_img_content: string;
      };
      setQrCode(data.qrcode);
      setQrImg(data.qrcode_img_content);
      setStatus("pending");
    } catch (e) {
      swallow(e);
      setErrMsg(
        e instanceof Error ? e.message : t.channelCredential.qrCodeFailed,
      );
      setStatus("error");
    } finally {
      setStarting(false);
    }
  }

  useEffect(() => {
    if (!qrCode) return;
    if (["confirmed", "expired", "rejected", "error"].includes(status)) return;

    let cancelled = false;
    let errorCount = 0;
    const tick = async () => {
      try {
        const r = await fetch(
          `${getBackendBaseURL()}/api/channels/wechat/qr/poll`,
          {
            method: "POST",
            headers: jsonAuthHeaders(),
            body: JSON.stringify({ qrcode: qrCode }),
          },
        );
        if (!r.ok) throw new Error(await r.text());
        const data = (await r.json()) as {
          status: string;
          confirmed: boolean;
        };
        if (cancelled) return;
        errorCount = 0;
        const s = data.status as typeof status;
        setStatus(s);
        if (data.confirmed) {
          toast.success(`${displayName} ${t.channelCredential.scanConfirmed}`);
          onConfirmed();
        }
      } catch (e) {
        swallow(e);
        if (cancelled) return;
        errorCount++;
        if (errorCount >= 3) {
          setErrMsg(
            e instanceof Error ? e.message : t.channelCredential.pollFailed,
          );
          setStatus("error");
        }
      }
    };

    const id = setInterval(() => void tick(), 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [qrCode, status, displayName, onConfirmed, t]);

  if (!qrCode) {
    return (
      <div className="rounded-lg border border-dashed border-border-default px-4 py-8 text-center">
        <p className="mb-3 text-xs text-muted-foreground">
          {t.channelCredential.wechatScanInstruction}
        </p>
        <Button
          type="button"
          size="sm"
          onClick={startQr}
          disabled={starting}
          className="h-8 text-xs"
        >
          {starting
            ? t.channelCredential.requesting
            : t.channelCredential.getQrCode}
        </Button>
        {errMsg && <p className="mt-3 text-xs text-destructive">{errMsg}</p>}
      </div>
    );
  }

  const statusLabel: Record<typeof status, string> = {
    idle: "",
    pending: t.channelCredential.qrPending,
    scanned: t.channelCredential.qrScanned,
    confirmed: t.channelCredential.qrConfirmed,
    expired: t.channelCredential.qrExpired,
    rejected: t.channelCredential.qrRejected,
    error: t.channelCredential.qrError,
  };

  return (
    <div className="flex flex-col items-center py-4">
      {qrImg && (
        <div className="rounded-lg border border-border-subtle bg-background p-2">
          {qrImg.startsWith("http") ? (
            <QRCodeSVG value={qrImg} size={192} level="M" marginSize={2} />
          ) : (
            <img
              src={
                qrImg.startsWith("data:")
                  ? qrImg
                  : `data:image/png;base64,${qrImg}`
              }
              alt="WeChat QR"
              className="size-48 object-contain"
            />
          )}
        </div>
      )}
      <div
        className={cn(
          "mt-3 text-xs tabular-nums",
          status === "confirmed" && "text-success",
          (status === "expired" ||
            status === "rejected" ||
            status === "error") &&
            "text-destructive",
        )}
      >
        {statusLabel[status]}
      </div>
      {(status === "expired" ||
        status === "rejected" ||
        status === "error") && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setQrCode(null);
            setQrImg(null);
            setStatus("idle");
            setErrMsg(null);
          }}
          className="mt-3 h-8 text-xs"
        >
          {t.channelCredential.refreshQr}
        </Button>
      )}
      {errMsg && <p className="mt-2 text-xs text-destructive">{errMsg}</p>}
    </div>
  );
}
