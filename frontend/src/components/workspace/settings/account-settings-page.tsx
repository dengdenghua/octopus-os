import { useEffect, useRef, useState } from "react";
import {
  CameraIcon,
  MailIcon,
  TrashIcon,
  SaveIcon,
  Link2Icon,
  CoinsIcon,
  RefreshCwIcon,
  AlertTriangleIcon,
  UserIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useProfile,
  useUpdateProfile,
  useUploadAvatar,
  usePrivacySettings,
  useUpdatePrivacySettings,
  useUnlinkAccount,
} from "@/core/account";
import { useI18n } from "@/core/i18n/hooks";
import { useOctLink, useRefreshOctCredits } from "@/core/oct/hooks";
import { useAuth } from "@/providers/AuthProvider";

function formatCredits(
  n: number | undefined | null,
  t: { numberFormat: { yi: string; wan: string } },
): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  if (n >= 100_000_000)
    return `${(n / 100_000_000).toFixed(1)}${t.numberFormat.yi}`;
  if (n >= 10_000)
    return `${(n / 10_000).toFixed(n >= 100_000 ? 0 : 1)}${t.numberFormat.wan}`;
  if (n >= 1_000) return n.toLocaleString();
  return String(n);
}

export default function AccountSettingsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const {
    data: profile,
    isLoading: profileLoading,
    isError: profileError,
    refetch: refetchProfile,
  } = useProfile();
  const {
    data: privacy,
    isLoading: privacyLoading,
    isError: privacyError,
    refetch: refetchPrivacy,
  } = usePrivacySettings();
  const updateProfile = useUpdateProfile();
  const uploadAvatar = useUploadAvatar();
  const updatePrivacy = useUpdatePrivacySettings();
  const unlinkLinkedAccount = useUnlinkAccount();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const profileDisplayName = profile?.display_name || "";
  const profileBio = profile?.bio || "";

  const [displayName, setDisplayName] = useState(profileDisplayName);
  const [bio, setBio] = useState(profileBio);
  const [unlinkAccount, setUnlinkAccount] = useState<{
    provider: string;
    email?: string;
  } | null>(null);
  const normalizedDisplayName = displayName.trim();
  const normalizedBio = bio.trim();
  const profileDirty =
    normalizedDisplayName !== profileDisplayName.trim() ||
    normalizedBio !== profileBio.trim();
  const accountName =
    profile?.display_name?.trim() ||
    profile?.username?.trim() ||
    user?.username?.trim() ||
    user?.email?.trim() ||
    t.auth.currentAccount;
  const accountUsername = profile?.username?.trim() || user?.username?.trim();
  const accountEmail = profile?.email?.trim() || user?.email?.trim();

  useEffect(() => {
    setDisplayName(profileDisplayName);
    setBio(profileBio);
  }, [profileBio, profileDisplayName]);

  const handleAvatarClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      toast.error(t.settings.account.profile.avatar);
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t.accountSettings.avatarTooLarge);
      return;
    }
    uploadAvatar.mutate(file);
  };

  const handleSaveProfile = () => {
    if (!profileDirty) return;
    updateProfile.mutate({
      display_name: normalizedDisplayName || undefined,
      bio: normalizedBio || undefined,
    });
  };

  const handleUnlink = () => {
    if (unlinkAccount) {
      unlinkLinkedAccount.mutate(unlinkAccount.provider, {
        onSuccess: () => setUnlinkAccount(null),
      });
    }
  };

  const isLoading = profileLoading || privacyLoading;

  if (isLoading && !profile && !privacy) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-96" />
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {(profileError || privacyError) && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning/70 bg-warning/5 px-4 py-2.5 text-xs text-warning dark:border-warning/50"
        >
          <span>{t.accountSettings.dataUnavailable}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              if (profileError) void refetchProfile();
              if (privacyError) void refetchPrivacy();
            }}
          >
            <RefreshCwIcon className="mr-1.5 size-3.5" />
            {t.accountSettings.retry}
          </Button>
        </div>
      )}
      <OfficialCreditsCard />

      {/* Profile */}
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-medium">
            {t.settings.account.profile.title}
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t.settings.account.profile.description}
          </p>
        </div>
        <div className="rounded-lg border bg-card p-5 space-y-5">
          <div className="flex items-center gap-4">
            <div className="relative group flex-shrink-0">
              <Avatar className="size-12 border border-border-default bg-background">
                <AvatarImage src={profile?.avatar_url} />
                <AvatarFallback className="bg-muted/50 text-sm font-medium text-foreground">
                  {accountName[0] || (
                    <UserIcon className="size-4 text-muted-foreground" />
                  )}
                </AvatarFallback>
              </Avatar>
              <button
                type="button"
                onClick={handleAvatarClick}
                disabled={uploadAvatar.isPending}
                aria-busy={uploadAvatar.isPending}
                aria-label={t.accountSettings.clickToChangeAvatar}
                className="absolute inset-0 flex items-center justify-center rounded-full bg-black/0 text-white opacity-0 transition group-hover:bg-black/40 group-hover:opacity-100 focus-visible:bg-black/40 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
              >
                <CameraIcon className="size-3.5" />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileChange}
              />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-medium">{accountName}</p>
                {accountUsername && (
                  <span className="text-xs text-muted-foreground">
                    @{accountUsername}
                  </span>
                )}
              </div>
              {accountEmail && (
                <p className="text-xs text-muted-foreground flex items-center gap-1.5 mt-0.5">
                  <MailIcon className="size-3" />
                  {accountEmail}
                </p>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="displayName" className="text-xs">
                {t.settings.account.profile.displayName}
              </Label>
              <Input
                id="displayName"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={t.settings.account.profile.displayNamePlaceholder}
                maxLength={80}
                disabled={updateProfile.isPending}
                className="h-9 text-sm"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="bio" className="text-xs">
                {t.settings.account.profile.bio}
              </Label>
              <textarea
                id="bio"
                value={bio}
                onChange={(e) => setBio(e.target.value)}
                placeholder={t.settings.account.profile.bioPlaceholder}
                maxLength={500}
                disabled={updateProfile.isPending}
                className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex min-h-[64px] w-full rounded-lg border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none"
              />
              <p className="text-xs text-muted-foreground text-right">
                {bio.length}/500
              </p>
            </div>
          </div>

          <div className="flex items-center justify-end">
            <Button
              size="sm"
              onClick={handleSaveProfile}
              disabled={!profileDirty || updateProfile.isPending}
              aria-busy={updateProfile.isPending}
            >
              <SaveIcon className="mr-1.5 size-3.5" />
              {updateProfile.isPending
                ? t.settings.account.profile.saving
                : t.settings.account.profile.saveChanges}
            </Button>
          </div>
        </div>
      </section>

      {/* Linked Accounts */}
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-medium">
            {t.settings.account.linkedAccounts.title}
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t.settings.account.linkedAccounts.description}
          </p>
        </div>
        <div className="rounded-lg border bg-card">
          <ul className="divide-y">
            {profile?.linked_accounts?.map((account) => (
              <li
                key={account.provider}
                className="flex items-center justify-between px-5 py-3"
              >
                <div className="flex items-center gap-3">
                  <div className="flex size-8 items-center justify-center rounded-lg bg-muted">
                    {account.provider === "google" && (
                      <svg className="size-4" viewBox="0 0 24 24">
                        <path
                          fill="currentColor"
                          d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                        />
                        <path
                          fill="currentColor"
                          d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                        />
                        <path
                          fill="currentColor"
                          d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                        />
                        <path
                          fill="currentColor"
                          d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                        />
                      </svg>
                    )}
                    {account.provider === "github" && (
                      <svg className="size-4" viewBox="0 0 24 24">
                        <path
                          fill="currentColor"
                          d="M12 1C5.92 1 1 5.92 1 12c0 4.87 3.15 8.99 7.52 10.44.55.1.75-.24.75-.53v-1.86c-3.06.66-3.7-1.47-3.7-1.47-.5-1.27-1.22-1.61-1.22-1.61-.99-.68.08-.66.08-.66 1.1.08 1.68 1.13 1.68 1.13.97 1.67 2.55 1.19 3.17.91.1-.71.38-1.19.69-1.46-2.44-.28-5.01-1.22-5.01-5.44 0-1.2.43-2.19 1.13-2.96-.11-.28-.49-1.4.11-2.92 0 0 .92-.3 3.02 1.13a10.5 10.5 0 0 1 5.51 0c2.1-1.43 3.02-1.13 3.02-1.13.6 1.52.22 2.64.11 2.92.7.77 1.13 1.76 1.13 2.96 0 4.23-2.57 5.16-5.02 5.43.39.34.74 1.01.74 2.03v3.01c0 .29.2.64.75.53C19.85 20.99 23 16.87 23 12c0-6.08-4.92-11-11-11z"
                        />
                      </svg>
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-medium capitalize">
                      {account.provider}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {account.email}
                    </p>
                  </div>
                  {account.is_primary && (
                    <span className="bg-primary/10 text-primary rounded-full px-2 py-0.5 text-xs font-medium">
                      {t.accountSettings.primaryAccount}
                    </span>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-muted-foreground hover:text-destructive"
                  aria-label={`${t.common.unlink}: ${account.provider}`}
                  onClick={() =>
                    setUnlinkAccount({
                      provider: account.provider,
                      email: account.email,
                    })
                  }
                >
                  <TrashIcon className="size-3.5" />
                </Button>
              </li>
            ))}

            {(!profile?.linked_accounts ||
              profile.linked_accounts.length === 0) && (
              <li className="text-muted-foreground p-6 text-center">
                <Link2Icon className="mx-auto mb-2 size-5 opacity-40" />
                <p className="text-sm">
                  {t.settings.account.linkedAccounts.notConnected}
                </p>
              </li>
            )}
          </ul>
          <p className="border-t px-5 py-3 text-xs text-muted-foreground">
            {t.accountSettings.thirdPartyLinkUnavailable}
          </p>
        </div>
      </section>

      {/* Privacy */}
      <section className="space-y-3">
        <div>
          <h3 className="text-sm font-medium">
            {t.settings.account.privacy.title}
          </h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t.settings.account.privacy.description}
          </p>
        </div>
        <div className="rounded-lg border bg-card divide-y">
          <div className="flex items-center justify-between px-5 py-4">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm">
                {t.settings.account.privacy.shareUsageData}
              </Label>
              <p className="text-xs text-muted-foreground">
                {t.settings.account.privacy.shareUsageDataDescription}
              </p>
            </div>
            <Switch
              checked={privacy?.share_usage_analytics ?? false}
              aria-label={t.settings.account.privacy.shareUsageData}
              disabled={updatePrivacy.isPending}
              onCheckedChange={(checked) =>
                updatePrivacy.mutate({ share_usage_analytics: checked })
              }
            />
          </div>
          <div className="flex items-center justify-between px-5 py-4">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm">
                {t.settings.account.privacy.allowAnalytics}
              </Label>
              <p className="text-xs text-muted-foreground">
                {t.settings.account.privacy.allowAnalyticsDescription}
              </p>
            </div>
            <Switch
              checked={privacy?.data_collection_consent !== false}
              aria-label={t.settings.account.privacy.allowAnalytics}
              disabled={updatePrivacy.isPending}
              onCheckedChange={(checked) =>
                updatePrivacy.mutate({ data_collection_consent: checked })
              }
            />
          </div>
        </div>
      </section>

      {/* Unlink Confirm Dialog */}
      <Dialog
        open={!!unlinkAccount}
        onOpenChange={() => setUnlinkAccount(null)}
      >
        <DialogContent className="sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle>{t.common.unlink}</DialogTitle>
            <DialogDescription>
              {t.accountSettings.unlinkConfirm}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setUnlinkAccount(null)}>
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleUnlink}
              disabled={unlinkLinkedAccount.isPending}
            >
              {t.common.confirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function OfficialCreditsCard() {
  const { t } = useI18n();
  const link = useOctLink();
  const refresh = useRefreshOctCredits();

  if (link.isLoading) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Skeleton className="h-5 w-5 rounded" />
          <Skeleton className="h-5 w-24" />
        </div>
        <Skeleton className="h-20 w-full rounded-lg" />
      </div>
    );
  }

  if (!link.data) return null;

  const tokenInvalid = Boolean(link.data.token_invalid);
  const tokenInvalidReason = link.data.token_invalid_reason;

  const credits = link.data.credits ?? {};
  const remaining = credits.surplusCredits;
  const summary = credits.creditsSummary;
  const naive = Object.values(summary?.by_type ?? {}).reduce(
    (acc, b) => acc + (b?.remaining ?? 0),
    0,
  );
  const expired =
    typeof remaining === "number" ? Math.max(0, naive - remaining) : 0;

  const onRefresh = async () => {
    try {
      await refresh.mutateAsync();
      toast.success(t.accountSettings.creditsRefreshed);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t.accountSettings.refreshFailed,
      );
    }
  };

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-medium flex items-center gap-2">
          <CoinsIcon className="size-4" />
          {t.accountSettings.creditsBalance}
        </h3>
      </div>
      <div className="rounded-lg border bg-card">
        {tokenInvalid && (
          <div
            role="alert"
            className="flex items-start gap-2 border-b border-warning/60 bg-warning/5 px-5 py-3 text-xs text-warning dark:border-warning/40"
          >
            <AlertTriangleIcon className="size-3.5 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-medium">
                {t.accountSettings.sessionExpired(
                  tokenInvalidReason ||
                    t.accountSettings.sessionExpiredDefaultReason,
                )}
              </p>
              <p className="mt-0.5 opacity-80">
                {t.accountSettings.sessionCacheHint}
              </p>
            </div>
          </div>
        )}
        <div className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-baseline gap-2">
                <span
                  className={
                    "text-xl font-semibold tabular-nums tracking-tight" +
                    (tokenInvalid ? " text-muted-foreground" : "")
                  }
                >
                  {formatCredits(remaining, t)}
                </span>
                {typeof remaining === "number" && remaining > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {tokenInvalid
                      ? t.accountSettings.cachedSuffix
                      : t.accountSettings.available}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {credits.plan ? credits.plan : t.accountSettings.octAccount}
                {credits.modelDisplayName
                  ? ` · ${credits.modelDisplayName}`
                  : ""}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={onRefresh}
              disabled={refresh.isPending}
              className="size-8"
              aria-label={t.accountSettings.refresh}
            >
              <RefreshCwIcon
                className={refresh.isPending ? "animate-spin size-4" : "size-4"}
              />
            </Button>
          </div>

          {summary?.by_type && Object.keys(summary.by_type).length > 0 && (
            <div className="space-y-2.5">
              {Object.entries(summary.by_type)
                .filter(([, v]) => v.granted > 0)
                .map(([type, v]) => {
                  const percentage =
                    v.granted > 0 ? (v.remaining / v.granted) * 100 : 0;
                  return (
                    <div key={type} className="space-y-1">
                      <div className="flex items-center justify-between text-xs">
                        <span className="capitalize text-muted-foreground">
                          {type}
                        </span>
                        <span className="tabular-nums">
                          {v.remaining.toLocaleString()}
                          <span className="text-muted-foreground">
                            {" "}
                            / {v.granted.toLocaleString()}
                          </span>
                        </span>
                      </div>
                      <div className="h-1 w-full rounded-full bg-muted overflow-hidden">
                        <div
                          role="progressbar"
                          aria-label={type}
                          aria-valuemin={0}
                          aria-valuemax={v.granted}
                          aria-valuenow={Math.min(
                            v.granted,
                            Math.max(0, v.remaining),
                          )}
                          className="h-full rounded-full bg-primary transition-all"
                          style={{
                            width: `${Math.min(100, Math.max(0, percentage))}%`,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              {expired > 0 && (
                <div className="flex items-center justify-between pt-1.5 border-t text-xs">
                  <span className="text-muted-foreground">
                    {t.accountSettings.expiredOrFrozen}
                  </span>
                  <span className="tabular-nums text-chart-7">
                    −{expired.toLocaleString()}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
        <p className="border-t px-5 py-3 text-xs text-muted-foreground">
          {t.accountSettings.creditsDescription}
        </p>
      </div>
    </section>
  );
}
