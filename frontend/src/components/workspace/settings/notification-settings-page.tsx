import { AlertTriangleIcon, BellIcon, CheckCircle2Icon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings } from "@/core/settings";

import { SettingsSection } from "./settings-section";

export default function NotificationSettingsPage() {
  const { t, locale } = useI18n();
  const zh = locale.toLowerCase().startsWith("zh");
  const {
    permission,
    isSupported,
    isReady,
    requestPermission,
    showNotification,
  } = useNotification();

  const [settings, setSettings] = useLocalSettings();

  const handleRequestPermission = async () => {
    try {
      const result = await requestPermission();
      if (result === "granted") {
        setSettings("notification", { enabled: true });
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.settings.notification.requestFailed,
      );
    }
  };

  const handleTestNotification = () => {
    const sent = showNotification(t.settings.notification.testTitle, {
      body: t.settings.notification.testBody,
    });
    if (sent) toast.success(t.settings.notification.testSent);
  };

  const handleEnableNotification = async (enabled: boolean) => {
    setSettings("notification", {
      enabled,
    });
  };

  if (!isReady) {
    return (
      <SettingsSection
        title={t.settings.notification.title}
        description={t.settings.notification.description}
      >
        <div className="space-y-3" role="status" aria-live="polite">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-9 w-44 rounded-lg" />
        </div>
      </SettingsSection>
    );
  }

  if (!isSupported) {
    return (
      <SettingsSection
        title={t.settings.notification.title}
        description={t.settings.notification.description}
      >
        <p
          className="text-muted-foreground rounded-lg border bg-muted/30 p-4 text-sm"
          role="status"
        >
          {t.settings.notification.notSupported}
        </p>
      </SettingsSection>
    );
  }

  return (
    <SettingsSection
      title={t.settings.notification.title}
      description={t.settings.notification.description}
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4 rounded-lg border bg-card p-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
              {permission === "granted" ? (
                <CheckCircle2Icon className="size-4 text-success" />
              ) : (
                <BellIcon className="size-4 text-muted-foreground" />
              )}
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {t.settings.notification.enableNotification}
              </p>
              <Badge
                variant={permission === "granted" ? "secondary" : "outline"}
                className="mt-1 text-xs"
              >
                {permission === "granted"
                  ? t.settings.notification.permissionGranted
                  : permission === "denied"
                    ? t.settings.notification.permissionDenied
                    : t.settings.notification.permissionPrompt}
              </Badge>
            </div>
          </div>
          <Switch
            aria-label={t.settings.notification.enableNotification}
            aria-describedby={
              permission === "denied" ? "notification-denied-hint" : undefined
            }
            disabled={permission !== "granted"}
            checked={permission === "granted" && settings.notification.enabled}
            onCheckedChange={handleEnableNotification}
          />
        </div>

        {permission === "default" && (
          <Button
            onClick={handleRequestPermission}
            variant="default"
            className="self-start"
          >
            <BellIcon className="mr-2 size-4" />
            {t.settings.notification.requestPermission}
          </Button>
        )}

        {permission === "denied" && (
          <div className="space-y-2">
            <div
              id="notification-denied-hint"
              role="alert"
              className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning"
            >
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
              <p>{t.settings.notification.deniedHint}</p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              onClick={handleRequestPermission}
            >
              <BellIcon className="mr-2 size-4" />
              {zh ? "重新检测通知权限" : "Check notification permission again"}
            </Button>
          </div>
        )}

        {permission === "granted" && settings.notification.enabled && (
          <div className="flex flex-col gap-4">
            <Button
              onClick={handleTestNotification}
              variant="outline"
              className="self-start"
            >
              <BellIcon className="mr-2 size-4" />
              {t.settings.notification.testButton}
            </Button>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
