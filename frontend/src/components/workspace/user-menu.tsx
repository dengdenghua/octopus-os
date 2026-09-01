import { Coins, LogOut, RefreshCw, User } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import type { OctBalance } from "@/core/oct/api";
import { useOctLink, useRefreshOctCredits } from "@/core/oct/hooks";
import { useAuth } from "@/providers/AuthProvider";
import { toast } from "sonner";
import { useI18n } from "@/core/i18n/hooks";

/* Implementation note. */
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

function isPlaceholderUsername(username?: string | null): boolean {
  const value = username?.trim().toLowerCase();
  return !value || value === "anonymous" || value === "__anonymous__";
}

function getAccountDisplayName(user: {
  mobile?: string;
  email?: string;
  username?: string;
  actor_id?: string;
}): string {
  return (
    user.mobile ||
    user.email ||
    (!isPlaceholderUsername(user.username) ? user.username : "") ||
    user.actor_id ||
    "EchoAI"
  );
}

export function UserMenu() {
  const navigate = useNavigate();
  const { user, logout, authStatus, isLoading } = useAuth();
  const octQuery = useOctLink();
  const refresh = useRefreshOctCredits();
  const { t } = useI18n();

  const handleLogout = async () => {
    try {
      await logout();
      toast.success(t.auth.logoutSuccess);
      navigate("/");
    } catch (_error) {
      toast.error(t.auth.logoutFailed);
    }
  };

  const handleRefresh = async (e: React.MouseEvent) => {
    // Don't let the dropdown close just because the refresh button was clicked.
    e.preventDefault();
    e.stopPropagation();
    try {
      await refresh.mutateAsync();
      toast.success(t.credits.refreshed);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t.credits.refreshFailed);
    }
  };

  // Hook must come BEFORE early returns (Rules of Hooks). Pre-fix
  // ``useMemo`` sat at L92 · below both ``if (isLoading)`` and
  // ``if (!user)`` · meaning a guest → logged-in transition triggered
  // "Rendered more hooks than during the previous render." Reads
  // account data which is always defined (the hook itself
  // runs earlier) · defensively handles ``undefined`` inside.
  const credits: OctBalance | undefined = octQuery.data?.credits;
  const remaining = credits?.surplusCredits;
  const summary = credits?.creditsSummary;
  const accountName = user ? getAccountDisplayName(user) : "";

  // "Expired" = the gap between historical balance per the by-type
  // breakdown and the authoritative live balance. Surface this so users
  // who see "I had 40w, why is my balance 30w" get an answer.
  const expired = useMemo(() => {
    if (!summary || remaining === undefined) return 0;
    const naiveByType = Object.values(summary.by_type ?? {}).reduce(
      (acc, b) => acc + (b?.remaining ?? 0),
      0,
    );
    return Math.max(0, naiveByType - remaining);
  }, [summary, remaining]);

  if (isLoading || !authStatus?.enabled) {
    return null;
  }

  if (!user) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton onClick={() => navigate("/login")}>
            <User className="size-4" />
            <span>{t.auth.login}</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    );
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton>
              <div className="flex size-6 items-center justify-center rounded-lg border border-border-default bg-background text-muted-foreground shadow-[var(--shadow-xs)]">
                <User className="size-3" />
              </div>
              <span className="truncate">{accountName}</span>
              {remaining !== undefined && (
                <span
                  className="ml-auto inline-flex items-center gap-1 rounded-lg bg-warning/15 px-1.5 py-0.5 text-xs font-medium text-warning"
                  title={t.credits.remaining(remaining)}
                >
                  <Coins className="size-3" />
                  {formatCredits(remaining, t)}
                </span>
              )}
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" className="w-72">
            <DropdownMenuLabel>
              <div className="flex flex-col gap-0.5">
                <span className="font-medium">{accountName}</span>
                {credits?.plan && (
                  <span className="text-xs text-muted-foreground">
                    {credits.plan}
                    {credits.modelDisplayName
                      ? ` · ${credits.modelDisplayName}`
                      : ""}
                  </span>
                )}
                {user.email && user.email !== accountName && !credits?.plan && (
                  <span className="text-xs text-muted-foreground">
                    {user.email}
                  </span>
                )}
              </div>
            </DropdownMenuLabel>

            {octQuery.data && (
              <>
                <DropdownMenuSeparator />
                <div className="px-2 pb-2 pt-1.5 text-xs">
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-muted-foreground">
                      {t.credits.credits}
                    </span>
                    <button
                      type="button"
                      onClick={handleRefresh}
                      disabled={refresh.isPending}
                      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
                    >
                      <RefreshCw
                        className={
                          "size-3 " + (refresh.isPending ? "animate-spin" : "")
                        }
                      />
                      {refresh.isPending
                        ? t.accountSettings.refreshing
                        : t.accountSettings.refresh}
                    </button>
                  </div>
                  <div className="mb-2 flex items-baseline justify-between">
                    <span className="text-2xl font-semibold tabular-nums">
                      {formatCredits(remaining, t)}
                    </span>
                    {remaining !== undefined && remaining >= 1000 && (
                      <span className="text-xs text-muted-foreground">
                        {remaining.toLocaleString()}
                      </span>
                    )}
                  </div>
                  {summary?.by_type &&
                    Object.entries(summary.by_type).length > 0 && (
                      <div className="space-y-0.5">
                        {Object.entries(summary.by_type)
                          .filter(([, v]) => v.granted > 0)
                          .map(([type, v]) => (
                            <div
                              key={type}
                              className="flex items-center justify-between text-xs"
                            >
                              <span className="capitalize text-muted-foreground">
                                {type}
                              </span>
                              <span className="tabular-nums">
                                {v.remaining.toLocaleString()}
                                <span className="text-muted-foreground">
                                  {" / "}
                                  {v.granted.toLocaleString()}
                                </span>
                              </span>
                            </div>
                          ))}
                        {expired > 0 && (
                          <div
                            className="mt-1 flex items-center justify-between border-t border-border-subtle pt-1 text-xs text-muted-foreground"
                            title={t.accountSettings.expiredTooltip}
                          >
                            <span>{t.accountSettings.expired}</span>
                            <span className="tabular-nums">
                              −{expired.toLocaleString()}
                            </span>
                          </div>
                        )}
                      </div>
                    )}
                </div>
              </>
            )}

            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={handleLogout}>
              <LogOut className="mr-2 size-4" />
              <span>{t.auth.logout}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
