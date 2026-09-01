import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { swallow } from "@/core/utils/log";
import {
  FolderKanbanIcon,
  type LucideIcon,
  MousePointerClickIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";

const DESKTOP_ORGANIZER_ENABLED_KEY = "echo:desktop-organizer-enabled";

export default function DesktopOrganizerPage() {
  const { t } = useI18n();
  const { confirm, confirmDialog } = useConfirmDialog();
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState<"install" | "remove" | null>(null);
  const [contextMenuMessage, setContextMenuMessage] = useState("");
  const isElectron =
    typeof window !== "undefined" && !!window.echo?.isElectron;

  useEffect(() => {
    try {
      setEnabled(
        localStorage.getItem(DESKTOP_ORGANIZER_ENABLED_KEY) === "true",
      );
    } catch (e) {
      swallow(e, "storage");
    }
  }, []);

  const updateEnabled = (value: boolean) => {
    setEnabled(value);
    try {
      localStorage.setItem(DESKTOP_ORGANIZER_ENABLED_KEY, String(value));
    } catch (e) {
      swallow(e, "storage");
    }
  };

  const installContextMenu = async () => {
    setBusy("install");
    setContextMenuMessage("");
    const result = await window.echo?.desktop?.installContextMenu?.();
    setBusy(null);
    setContextMenuMessage(
      result?.ok
        ? t.desktopOrganizerPage.installSuccess
        : result?.error || t.desktopOrganizerPage.installUnsupported,
    );
  };

  const removeContextMenu = async () => {
    if (
      !(await confirm({
        title: t.desktopOrganizerPage.confirmRemoveTitle,
        description: t.desktopOrganizerPage.confirmRemoveDescription,
        confirmLabel: t.desktopOrganizerPage.removeButton,
      }))
    )
      return;
    setBusy("remove");
    setContextMenuMessage("");
    const result = await window.echo?.desktop?.removeContextMenu?.();
    setBusy(null);
    setContextMenuMessage(
      result?.ok
        ? t.desktopOrganizerPage.removeSuccess
        : result?.error || t.desktopOrganizerPage.removeUnsupported,
    );
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody>
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 py-2">
          <section className="workspace-panel flex flex-col gap-5 p-4 md:p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-3">
                <div className="flex size-11 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <FolderKanbanIcon className="size-5" />
                </div>
                <div>
                  <h1 className="text-xl font-semibold tracking-tight">
                    {t.desktopOrganizerPage.title}
                  </h1>
                  <p className="text-sm text-muted-foreground">
                    {t.desktopOrganizerPage.description}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3 rounded-lg border border-border bg-background/70 px-4 py-3">
                <span className="text-sm font-medium">
                  {enabled
                    ? t.desktopOrganizerPage.enabledOn
                    : t.desktopOrganizerPage.enabledOff}
                </span>
                <Switch checked={enabled} onCheckedChange={updateEnabled} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
              <InfoTile
                icon={ShieldCheckIcon}
                title={t.desktopOrganizerPage.tileNotTakeoverTitle}
                body={t.desktopOrganizerPage.tileNotTakeoverBody}
              />
              <InfoTile
                icon={MousePointerClickIcon}
                title={t.desktopOrganizerPage.tileRightClickTitle}
                body={t.desktopOrganizerPage.tileRightClickBody}
              />
              <InfoTile
                icon={SparklesIcon}
                title={t.desktopOrganizerPage.tileSafePreviewTitle}
                body={t.desktopOrganizerPage.tileSafePreviewBody}
              />
            </div>

            {!isElectron && (
              <div className="rounded-lg border border-warning/20 bg-warning/50/[0.08] px-4 py-3 text-sm text-muted-foreground">
                {t.desktopOrganizerPage.webEnvNotice}
              </div>
            )}

            <div className="rounded-lg border border-border bg-background/70 p-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-sm font-semibold">
                    {t.desktopOrganizerPage.contextMenuTitle}
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    {t.desktopOrganizerPage.contextMenuDescription}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!enabled || busy === "install" || !isElectron}
                    onClick={installContextMenu}
                  >
                    {busy === "install"
                      ? t.desktopOrganizerPage.installingButton
                      : t.desktopOrganizerPage.installButton}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={busy === "remove" || !isElectron}
                    onClick={removeContextMenu}
                  >
                    {busy === "remove"
                      ? t.desktopOrganizerPage.removingButton
                      : t.desktopOrganizerPage.removeButton}
                  </Button>
                </div>
              </div>
              {contextMenuMessage && (
                <p className="mt-3 text-sm text-muted-foreground">
                  {contextMenuMessage}
                </p>
              )}
            </div>

            <div className="flex flex-wrap gap-3">
              {enabled ? (
                <Button asChild>
                  <Link to="/desktop">{t.desktopOrganizerPage.openAssistant}</Link>
                </Button>
              ) : (
                <Button disabled>{t.desktopOrganizerPage.openAssistant}</Button>
              )}
              <Button variant="outline" asChild>
                <Link to="/workspace/realtime/new">
                  {t.desktopOrganizerPage.backToWorkspace}
                </Link>
              </Button>
            </div>
          </section>
        </div>
      </WorkspaceBody>
      {confirmDialog}
    </WorkspaceContainer>
  );
}

function InfoTile({
  icon: Icon,
  title,
  body,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-background/70 p-4">
      <Icon className="mb-3 size-5 text-primary" />
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">{body}</p>
    </div>
  );
}
