/** Privacy and security controls backed by live runtime policy endpoints. */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangleIcon,
  PlusIcon,
  RefreshCwIcon,
  TrashIcon,
} from "lucide-react";

import type { components } from "@/core/api/openapi-types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import { getBackendBaseURL } from "@/core/config";
import { jsonAuthHeaders } from "@/core/auth/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { ReachControl } from "@/components/workspace/reach-control";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { formatAiModeDevice, type AiModeDevice } from "./settings-resilience";
import { getSettingsUxCopy } from "./settings-ux-copy";
import { PersonalSpaceFolderSettings } from "./personal-space-settings-page";

// ─── Local API types · narrow to what the UI actually uses ──────
//
// These mirror the backend wire shape (see ``runtime/web/ai_mode_router.py``
// and ``runtime/web/path_denylist_router.py``). They're intentionally
// hand-rolled rather than codegen — the endpoints are recent and
// the openapi-ts pipeline hasn't picked them up yet.
type AiModeId = "efficiency" | "privacy";

interface AiModeOption {
  id: AiModeId;
  label: string;
  description: string;
  recommended_default?: boolean;
}

interface AiModeStatus {
  mode: AiModeId;
  recommended: AiModeId;
  device?: AiModeDevice;
  modes: AiModeOption[];
}

interface PathDenylistStatus {
  paths: string[];
}

interface FactoryResetResult {
  ok: boolean;
}

// Pulled from the auto-generated OpenAPI types so backend changes
// to the ``IdentityLockResponse`` pydantic model propagate here
// without a hand-edit. See docs/adr/004-openapi-ts-codegen.md.
// The ``source`` field is typed as ``string`` in the generated
// file (FastAPI can't express the ``"runtime" | "env" | "default"``
// union through pydantic without a Literal); we re-narrow it for
// UI-side compile-time safety where we branch on it.
type LockStatus = components["schemas"]["IdentityLockResponse"] & {
  source: "runtime" | "env" | "default";
};

type ConstitutionProfile = "strict" | "normal" | "lax";

type ConstitutionProfileStatus = {
  profile: ConstitutionProfile;
  available: ConstitutionProfile[];
};

// LLM 语义安全 judge:enabled=当前是否接了真 judge;available=有无模型路由可接。
type JudgeStatus = {
  enabled: boolean;
  available: boolean;
};

type LoadState = "loading" | "ready" | "error";

export default function PrivacySettingsPage() {
  const { t, locale } = useI18n();
  const copy = getSettingsUxCopy(locale).privacy;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<LockStatus | null>(null);
  const [statusLoadState, setStatusLoadState] = useState<LoadState>("loading");
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<ConstitutionProfileStatus | null>(
    null,
  );
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileLoadState, setProfileLoadState] =
    useState<LoadState>("loading");
  const [judge, setJudge] = useState<JudgeStatus | null>(null);
  const [judgeBusy, setJudgeBusy] = useState(false);
  const [judgeLoadState, setJudgeLoadState] = useState<LoadState>("loading");
  const [showFactoryResetDialog, setShowFactoryResetDialog] = useState(false);
  const [factoryResetConfirmText, setFactoryResetConfirmText] = useState("");
  const [factoryResetPending, setFactoryResetPending] = useState(false);

  // ── AI mode (efficiency / privacy) ──
  const [aiMode, setAiMode] = useState<AiModeStatus | null>(null);
  const [aiModeBusy, setAiModeBusy] = useState(false);
  const [aiModeLoadState, setAiModeLoadState] = useState<LoadState>("loading");

  // ── Path denylist ──
  const [denylist, setDenylist] = useState<PathDenylistStatus | null>(null);
  const [denylistLoadState, setDenylistLoadState] =
    useState<LoadState>("loading");
  const [showAddPathDialog, setShowAddPathDialog] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [denylistBusy, setDenylistBusy] = useState(false);
  const [pathToRemove, setPathToRemove] = useState<string | null>(null);

  const fetchIdentityLock = useCallback(async () => {
    setStatusLoadState("loading");
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/identity-lock`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = (await res.json()) as LockStatus;
      if (
        typeof next?.locked !== "boolean" ||
        !(["runtime", "env", "default"] as string[]).includes(next?.source)
      ) {
        throw new Error("invalid data");
      }
      setStatus(next);
      setStatusLoadState("ready");
    } catch {
      setStatus(null);
      setStatusLoadState("error");
    }
  }, []);

  const fetchProfile = useCallback(async () => {
    setProfileLoadState("loading");
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/safety/constitution-profile`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = (await res.json()) as ConstitutionProfileStatus;
      if (!isConstitutionProfileStatus(next)) throw new Error("invalid data");
      setProfile(next);
      setProfileLoadState("ready");
    } catch {
      setProfile(null);
      setProfileLoadState("error");
    }
  }, []);

  const fetchJudge = useCallback(async () => {
    setJudgeLoadState("loading");
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/safety/llm-judge`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = (await res.json()) as JudgeStatus;
      if (
        typeof next?.enabled !== "boolean" ||
        typeof next?.available !== "boolean"
      ) {
        throw new Error("invalid data");
      }
      setJudge(next);
      setJudgeLoadState("ready");
    } catch {
      setJudge(null);
      setJudgeLoadState("error");
    }
  }, []);

  const fetchAiMode = useCallback(async () => {
    setAiModeLoadState("loading");
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/ai-mode`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as AiModeStatus;
      if (
        (data?.mode !== "efficiency" && data?.mode !== "privacy") ||
        (data?.recommended !== "efficiency" &&
          data?.recommended !== "privacy") ||
        !Array.isArray(data.modes) ||
        !data.modes.some((mode) => mode?.id === "efficiency") ||
        !data.modes.some((mode) => mode?.id === "privacy")
      ) {
        throw new Error("invalid data");
      }
      setAiMode(data);
      setAiModeLoadState("ready");
    } catch {
      setAiMode(null);
      setAiModeLoadState("error");
    }
  }, []);

  const fetchDenylist = useCallback(async () => {
    setDenylistLoadState("loading");
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/path-denylist`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as PathDenylistStatus;
      if (!isPathList(data?.paths)) throw new Error("invalid data");
      setDenylist({ paths: data.paths });
      setDenylistLoadState("ready");
    } catch {
      setDenylist(null);
      setDenylistLoadState("error");
    }
  }, []);

  useEffect(() => {
    void fetchIdentityLock();
    void fetchProfile();
    void fetchJudge();
    void fetchAiMode();
    void fetchDenylist();
  }, [fetchAiMode, fetchDenylist, fetchIdentityLock, fetchJudge, fetchProfile]);

  async function selectAiMode(mode: AiModeId) {
    if (aiModeBusy || !aiMode) return;
    if (aiMode.mode === mode) return;
    // Optimistic update — rollback on failure.
    const prev = aiMode;
    setAiMode({ ...aiMode, mode });
    setAiModeBusy(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/ai-mode`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ mode }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = (await res.json()) as Partial<AiModeStatus>;
      const resolvedMode =
        payload.mode === "efficiency" || payload.mode === "privacy"
          ? payload.mode
          : mode;
      const next: AiModeStatus = {
        ...prev,
        ...payload,
        mode: resolvedMode,
        recommended: payload.recommended ?? prev.recommended,
        device: payload.device ?? prev.device,
        modes: Array.isArray(payload.modes) ? payload.modes : prev.modes,
      };
      setAiMode(next);
      const label =
        mode === "efficiency"
          ? t.privacySettings.efficiencyMode
          : t.privacySettings.privacyMode;
      toast.success(t.privacySettings.toastAiModeSwitched(label));
    } catch {
      setAiMode(prev);
      toast.error(copy.restoreFailed);
    } finally {
      setAiModeBusy(false);
    }
  }

  async function addDenylistPath() {
    const path = newPath.trim();
    if (!isAbsoluteLikePath(path)) {
      toast.error(t.privacySettings.toastInvalidPath);
      return;
    }
    setDenylistBusy(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/path-denylist`, {
        method: "POST",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ path }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = (await res.json()) as Partial<PathDenylistStatus>;
      if (isPathList(payload.paths)) {
        setDenylist({ paths: payload.paths });
      } else {
        await fetchDenylist();
      }
      toast.success(t.privacySettings.toastPathAdded(path));
      setNewPath("");
      setShowAddPathDialog(false);
    } catch {
      toast.error(copy.restoreFailed);
    } finally {
      setDenylistBusy(false);
    }
  }

  async function removeDenylistPath(path: string): Promise<boolean> {
    setDenylistBusy(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/path-denylist`, {
        method: "DELETE",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ path }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = (await res.json()) as Partial<PathDenylistStatus>;
      if (isPathList(payload.paths)) {
        setDenylist({ paths: payload.paths });
      } else {
        await fetchDenylist();
      }
      toast.success(t.privacySettings.toastPathRemoved(path));
      return true;
    } catch {
      toast.error(copy.restoreFailed);
      return false;
    } finally {
      setDenylistBusy(false);
    }
  }

  async function setConstitutionProfile(name: ConstitutionProfile) {
    setProfileBusy(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/safety/constitution-profile`,
        {
          method: "PUT",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({ profile: name }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next: ConstitutionProfileStatus = await res.json();
      if (!isConstitutionProfileStatus(next)) throw new Error("invalid data");
      setProfile(next);
      setProfileLoadState("ready");
      toast.success(t.privacySettings.toastProfileSwitched(name));
    } catch {
      toast.error(copy.restoreFailed);
    } finally {
      setProfileBusy(false);
    }
  }

  async function setJudgeEnabled(enabled: boolean) {
    if (judgeBusy) return;
    setJudgeBusy(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/safety/llm-judge`, {
        method: "PUT",
        headers: jsonAuthHeaders(),
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next: JudgeStatus = await res.json();
      if (
        typeof next?.enabled !== "boolean" ||
        typeof next?.available !== "boolean"
      ) {
        throw new Error("invalid data");
      }
      setJudge(next);
      setJudgeLoadState("ready");
      toast.success(
        next.enabled
          ? t.privacySettings.toastJudgeEnabled
          : t.privacySettings.toastJudgeDisabled,
      );
    } catch {
      toast.error(copy.restoreFailed);
    } finally {
      setJudgeBusy(false);
    }
  }

  async function toggle(newLocked: boolean | null) {
    setBusy(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/config/identity-lock`,
        {
          method: "PUT",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({ locked: newLocked }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next: LockStatus = await res.json();
      if (
        typeof next?.locked !== "boolean" ||
        !(["runtime", "env", "default"] as string[]).includes(next?.source)
      ) {
        throw new Error("invalid data");
      }
      setStatus(next);
      setStatusLoadState("ready");
      toast.success(
        newLocked === null
          ? t.privacySettings.toastRestoreDefault
          : newLocked
            ? t.privacySettings.toastLockOn
            : t.privacySettings.toastLockOff,
      );
    } catch {
      toast.error(copy.restoreFailed);
    } finally {
      setBusy(false);
    }
  }

  async function handleFactoryReset() {
    if (factoryResetConfirmText !== "RESET ECHO") {
      toast.error(t.accountSettings.factoryResetTypeMismatch);
      return;
    }
    setFactoryResetPending(true);
    try {
      const res = await fetch(
        `${getBackendBaseURL()}/api/system/factory-reset`,
        {
          method: "POST",
          headers: jsonAuthHeaders(),
          body: JSON.stringify({
            confirm: "RESET ECHO",
            clear_user_install_state: true,
          }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = (await res.json()) as FactoryResetResult;
      if (result?.ok !== true) throw new Error("factory reset incomplete");
      clearEchoBrowserState();
      queryClient.removeQueries({ queryKey: ["threads"] });
      queryClient.removeQueries({ queryKey: ["projects"] });
      toast.success(t.accountSettings.factoryResetSuccess);
      setShowFactoryResetDialog(false);
      setFactoryResetConfirmText("");
      navigate("/workspace/realtime/new", { replace: true });
    } catch {
      toast.error(t.accountSettings.factoryResetFailed);
    } finally {
      setFactoryResetPending(false);
    }
  }

  const locked = status?.locked ?? true;
  const source = status?.source ?? "default";

  return (
    <div className="flex flex-col gap-4 text-sm sm:gap-6">
      <PersonalSpaceFolderSettings />

      {/* ─── Identity Lock toggle ─── */}
      <div
        data-testid="identity-protection-section"
        className="rounded-lg border border-border-default bg-card/50 p-4 sm:p-5"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h3 className="text-base font-semibold text-foreground">
              {copy.identityTitle}
            </h3>
            <p className="mt-1 text-xs leading-4 text-muted-foreground sm:leading-normal">
              {copy.identityDescription}
            </p>
          </div>
          {statusLoadState === "ready" && status ? (
            <button
              type="button"
              onClick={() => toggle(!locked)}
              disabled={busy}
              aria-pressed={locked}
              aria-label={locked ? copy.disableIdentity : copy.enableIdentity}
              className={cn(
                "shrink-0 relative inline-flex h-7 w-12 items-center rounded-full transition-colors",
                locked ? "bg-primary" : "bg-muted",
                busy && "opacity-60 cursor-not-allowed",
              )}
            >
              <span
                className={cn(
                  "inline-block h-5 w-5 rounded-full bg-background shadow transition-transform",
                  locked ? "translate-x-6" : "translate-x-1",
                )}
              />
            </button>
          ) : null}
        </div>

        {statusLoadState === "ready" && status ? (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs sm:mt-3">
            <span
              className={cn(
                "rounded px-1.5 py-0.5 font-medium",
                locked
                  ? "bg-success/10 text-success"
                  : "bg-warning/10 text-warning",
              )}
            >
              {locked ? copy.identityOn : copy.identityOff}
            </span>
            <span className="text-muted-foreground/80">
              {copy.sourceLabel}：{copy.sources[source]}
            </span>
            {source === "runtime" && (
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2"
                onClick={() => toggle(null)}
                disabled={busy}
              >
                {t.privacySettings.restoreDefault}
              </button>
            )}
          </div>
        ) : (
          <SettingsStateNotice
            state={statusLoadState}
            copy={copy}
            onRetry={fetchIdentityLock}
          />
        )}
      </div>

      {/* ─── AI mode (efficiency / privacy) ─── */}
      <div
        data-testid="ai-mode-section"
        className="rounded-lg border border-border-default bg-card/50 p-4 sm:p-5"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h3 className="text-base font-semibold text-foreground">
              {t.privacySettings.aiModeTitle}
            </h3>
            <p className="mt-1 text-xs leading-4 text-muted-foreground sm:leading-normal">
              {(() => {
                if (!aiMode) return t.privacySettings.aiModeDescScanning;
                const recLabel =
                  aiMode.recommended === "efficiency"
                    ? t.privacySettings.efficiencyMode
                    : t.privacySettings.privacyMode;
                return t.privacySettings.aiModeRecommended(recLabel);
              })()}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={fetchAiMode}
            disabled={aiModeBusy || aiModeLoadState === "loading"}
          >
            <RefreshCwIcon
              className={cn(
                "mr-1 h-3 w-3",
                aiModeLoadState === "loading" && "animate-spin",
              )}
            />{" "}
            {aiModeLoadState === "error"
              ? copy.retry
              : t.privacySettings.detectButton}
          </Button>
        </div>

        {aiModeLoadState === "ready" && aiMode ? (
          <>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:mt-4 sm:gap-3">
              {aiMode.modes
                .filter(
                  (opt) => opt.id === "efficiency" || opt.id === "privacy",
                )
                .map((opt) => {
                  const active = aiMode?.mode === opt.id;
                  const recommended =
                    !!opt.recommended_default || aiMode?.recommended === opt.id;
                  const label =
                    opt.id === "efficiency"
                      ? t.privacySettings.efficiencyMode
                      : t.privacySettings.privacyMode;
                  const description =
                    opt.id === "efficiency"
                      ? t.privacySettings.efficiencyModeDesc
                      : t.privacySettings.privacyModeDesc;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => selectAiMode(opt.id)}
                      disabled={aiModeBusy}
                      aria-pressed={active}
                      className={cn(
                        "flex flex-col gap-1.5 rounded-lg border p-3 text-left transition sm:gap-2 sm:p-4",
                        active
                          ? "border-primary bg-primary/5 ring-1 ring-primary/40"
                          : "border-border-default hover:border-primary/40",
                        aiModeBusy && "opacity-60 cursor-not-allowed",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium">{label}</span>
                        <div className="flex items-center gap-1.5">
                          {recommended && (
                            <span className="rounded bg-success/10 px-1.5 py-0.5 text-xs font-medium text-success">
                              {t.privacySettings.recommendedTag}
                            </span>
                          )}
                          {active && (
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                              {t.privacySettings.enabledTag}
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="line-clamp-2 text-xs leading-snug text-muted-foreground sm:line-clamp-none">
                        {description}
                      </p>
                    </button>
                  );
                })}
            </div>
            {formatAiModeDevice(aiMode.device, locale) && (
              <div className="mt-3 text-xs text-muted-foreground/80">
                {t.privacySettings.deviceLabel}{" "}
                <code>{formatAiModeDevice(aiMode.device, locale)}</code>
              </div>
            )}
          </>
        ) : (
          <SettingsStateNotice
            state={aiModeLoadState}
            copy={copy}
            onRetry={fetchAiMode}
            compact
          />
        )}
      </div>

      <details className="group rounded-lg border border-border-default bg-card/35">
        <summary className="cursor-pointer list-none rounded-lg px-5 py-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
          <span className="flex items-center justify-between gap-4">
            <span>
              <span className="block text-sm font-semibold text-foreground">
                {locale.startsWith("zh")
                  ? "高级隐私与安全"
                  : locale.startsWith("ja")
                    ? "高度なプライバシーと安全性"
                    : locale.startsWith("ko")
                      ? "고급 개인정보 보호 및 보안"
                      : "Advanced privacy & safety"}
              </span>
              <span className="mt-1 block text-xs font-normal text-muted-foreground">
                {locale.startsWith("zh")
                  ? "目录隔离、外发规则、语义审查与私有搜索。"
                  : locale.startsWith("ja")
                    ? "フォルダー分離、送信ルール、意味審査、プライベート検索。"
                    : locale.startsWith("ko")
                      ? "폴더 격리, 전송 규칙, 의미 검사 및 비공개 검색."
                      : "Folder isolation, outbound rules, semantic review, and private search."}
              </span>
            </span>
            <span className="text-muted-foreground transition-transform group-open:rotate-90">
              ›
            </span>
          </span>
        </summary>
        <div className="space-y-4 border-t border-border-subtle p-4">
          {/* ─── Path denylist (folders the agent can't read) ─── */}
          <div
            data-testid="path-denylist-section"
            className="rounded-lg border border-border-default bg-card/50 p-5"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <h3 className="text-base font-semibold text-foreground">
                  {t.privacySettings.pathDenyTitle}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t.privacySettings.pathDenyDesc}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs"
                onClick={() => {
                  setNewPath("");
                  setShowAddPathDialog(true);
                }}
                disabled={denylistLoadState !== "ready"}
              >
                <PlusIcon className="mr-1 h-3 w-3" />{" "}
                {t.privacySettings.addPathButton}
              </Button>
            </div>

            {denylistLoadState === "ready" && denylist ? (
              <div className="mt-4 rounded-lg border border-border-subtle divide-y divide-border/40">
                {denylist.paths.length === 0 ? (
                  <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                    {t.privacySettings.pathDenyEmpty}
                  </div>
                ) : (
                  denylist.paths.map((p) => (
                    <div
                      key={p}
                      className="flex items-center justify-between gap-3 px-4 py-3"
                    >
                      <code className="truncate font-mono text-xs text-foreground">
                        {p}
                      </code>
                      <button
                        type="button"
                        aria-label={`${copy.removePathTitle}: ${p}`}
                        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        onClick={() => setPathToRemove(p)}
                        disabled={denylistBusy}
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            ) : (
              <SettingsStateNotice
                state={denylistLoadState}
                copy={copy}
                onRetry={fetchDenylist}
              />
            )}
          </div>

          {/* ─── Constitution profile ─── */}
          <div
            data-testid="outbound-safety-section"
            className="rounded-lg border border-border-default bg-card/50 p-5"
          >
            <h3 className="text-base font-semibold text-foreground">
              {copy.profileTitle}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {copy.profileDescription}{" "}
              <RoutedWebLink
                href="https://github.com/dengdenghua/echo-os/blob/main/docs/constitution.md"
                className="underline underline-offset-2"
                openTargetSource="privacy-documentation"
              >
                {copy.profileDocLabel}
              </RoutedWebLink>
            </p>

            {profileLoadState === "ready" && profile ? (
              <>
                <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {profile.available.map((name) => {
                    const active = profile.profile === name;
                    return (
                      <button
                        key={name}
                        type="button"
                        onClick={() => setConstitutionProfile(name)}
                        disabled={profileBusy || active}
                        aria-pressed={active}
                        className={cn(
                          "flex flex-col gap-1 rounded-lg border p-3 text-left transition",
                          active
                            ? "border-primary bg-primary/5 ring-1 ring-primary/40"
                            : "border-border-default hover:border-primary/40",
                          profileBusy && "opacity-60 cursor-not-allowed",
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium">
                            {copy.profiles[name].label}
                          </span>
                          {active && (
                            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                              {copy.active}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-muted-foreground leading-snug">
                          {copy.profiles[name].description}
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-border-default p-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{copy.judgeTitle}</div>
                    <div className="text-xs text-muted-foreground leading-snug">
                      {copy.judgeDescription}
                      {judgeLoadState === "ready" &&
                        judge &&
                        !judge.available && (
                          <span className="mt-1 block text-warning">
                            {copy.judgeUnavailable}
                          </span>
                        )}
                    </div>
                    {judgeLoadState !== "ready" && (
                      <SettingsStateNotice
                        state={judgeLoadState}
                        copy={copy}
                        onRetry={fetchJudge}
                        compact
                      />
                    )}
                  </div>
                  <Switch
                    aria-label={copy.judgeSwitchLabel}
                    checked={!!judge?.enabled}
                    disabled={
                      judgeBusy ||
                      judgeLoadState !== "ready" ||
                      !judge?.available
                    }
                    onCheckedChange={(v) => setJudgeEnabled(v)}
                  />
                </div>
              </>
            ) : (
              <SettingsStateNotice
                state={profileLoadState}
                copy={copy}
                onRetry={fetchProfile}
              />
            )}
          </div>

          <ReachControl />

          {/* ─── Alternative unlock paths ─── */}
          <details className="group rounded-lg border border-border-subtle bg-muted/20 p-4">
            <summary className="cursor-pointer list-none rounded text-xs font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
              <span className="flex items-center justify-between gap-3">
                <span>
                  <span className="block font-semibold">
                    {copy.advancedTitle}
                  </span>
                  <span className="mt-0.5 block font-normal text-muted-foreground">
                    {copy.advancedSummary}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  className="text-muted-foreground transition-transform group-open:rotate-90"
                >
                  ›
                </span>
              </span>
            </summary>
            <div className="mt-3 border-t border-border-subtle pt-3">
              <p className="text-xs leading-relaxed text-muted-foreground">
                {copy.advancedDescription}
              </p>
              <ul className="mt-2 space-y-1.5 text-xs text-muted-foreground/90">
                <li>{copy.advancedEnvironment}</li>
                <li>{copy.advancedTurn}</li>
                <li>{copy.advancedApi}</li>
              </ul>
            </div>
          </details>
        </div>
      </details>

      <details className="group rounded-lg border border-destructive/20 bg-destructive/5">
        <summary className="cursor-pointer list-none rounded-lg px-5 py-4 outline-none focus-visible:ring-2 focus-visible:ring-destructive/35">
          <span className="flex items-center justify-between gap-4">
            <span>
              <span className="block text-sm font-semibold text-destructive">
                {t.accountSettings.factoryResetTitle}
              </span>
              <span className="mt-1 block text-xs font-normal text-muted-foreground">
                {t.accountSettings.factoryResetDescription}
              </span>
            </span>
            <span className="text-destructive/70 transition-transform group-open:rotate-90">
              ›
            </span>
          </span>
        </summary>
        <div className="border-t border-destructive/15 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <h3 className="text-base font-semibold text-destructive">
                {t.accountSettings.factoryResetTitle}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {t.accountSettings.factoryResetDescription}
              </p>
            </div>
            <Button
              variant="destructive"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setShowFactoryResetDialog(true)}
            >
              {t.accountSettings.factoryResetTitle}
            </Button>
          </div>
        </div>
      </details>

      <Dialog
        open={showAddPathDialog}
        onOpenChange={(open) => {
          if (!denylistBusy) setShowAddPathDialog(open);
        }}
      >
        <DialogContent closeLabel={t.common.close} className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>{t.privacySettings.addPathDialogTitle}</DialogTitle>
            <DialogDescription>
              {t.privacySettings.addPathDialogDesc}
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-3 py-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!denylistBusy && isAbsoluteLikePath(newPath.trim())) {
                void addDenylistPath();
              }
            }}
          >
            <Label htmlFor="denylist-path-input" className="text-sm">
              {t.privacySettings.pathLabel}
            </Label>
            <Input
              id="denylist-path-input"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              placeholder="C:\\Users\\you\\secrets"
              className="h-9 font-mono text-xs"
              autoFocus
              required
              aria-invalid={
                newPath.trim().length > 0 && !isAbsoluteLikePath(newPath.trim())
              }
            />
            <DialogFooter className="gap-2 pt-1 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowAddPathDialog(false)}
                disabled={denylistBusy}
              >
                {t.common.cancel}
              </Button>
              <Button
                type="submit"
                disabled={denylistBusy || !isAbsoluteLikePath(newPath.trim())}
              >
                {denylistBusy ? t.common.loading : t.common.confirm}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pathToRemove !== null}
        onOpenChange={(open) => {
          if (!open && !denylistBusy) setPathToRemove(null);
        }}
      >
        <DialogContent closeLabel={t.common.close} className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle className="text-destructive">
              {copy.removePathTitle}
            </DialogTitle>
            <DialogDescription>
              {pathToRemove ? copy.removePathDescription(pathToRemove) : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setPathToRemove(null)}
              disabled={denylistBusy}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={async () => {
                if (!pathToRemove) return;
                const removed = await removeDenylistPath(pathToRemove);
                if (removed) setPathToRemove(null);
              }}
              disabled={denylistBusy}
            >
              {denylistBusy ? t.common.loading : copy.removePathConfirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={showFactoryResetDialog}
        onOpenChange={(open) => {
          if (!factoryResetPending) setShowFactoryResetDialog(open);
        }}
      >
        <DialogContent closeLabel={t.common.close} className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertTriangleIcon className="size-5" />
              {t.accountSettings.factoryResetTitle}
            </DialogTitle>
            <DialogDescription>
              {t.accountSettings.factoryResetDialogDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-4">
            <Label htmlFor="factory-reset-confirm" className="text-sm">
              {t.accountSettings.factoryResetTypeToConfirm}
            </Label>
            <Input
              id="factory-reset-confirm"
              value={factoryResetConfirmText}
              onChange={(e) => setFactoryResetConfirmText(e.target.value)}
              placeholder="RESET ECHO"
              className="h-9"
            />
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowFactoryResetDialog(false)}
              disabled={factoryResetPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleFactoryReset}
              disabled={
                factoryResetConfirmText !== "RESET ECHO" ||
                factoryResetPending
              }
            >
              {factoryResetPending
                ? t.accountSettings.factoryResetPending
                : t.accountSettings.factoryResetConfirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SettingsStateNotice({
  state,
  copy,
  onRetry,
  compact = false,
}: {
  state: Exclude<LoadState, "ready"> | "ready";
  copy: Pick<
    ReturnType<typeof getSettingsUxCopy>["privacy"],
    "loading" | "loadFailed" | "retry"
  >;
  onRetry: () => void | Promise<void>;
  compact?: boolean;
}) {
  if (state === "ready") return null;
  const failed = state === "error";
  if (!failed) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label={copy.loading}
        className={cn("mt-3 space-y-2", compact && "mt-2")}
      >
        <span className="block h-2.5 w-2/3 animate-pulse rounded-full bg-muted" />
        {!compact ? (
          <span className="block h-2.5 w-2/5 animate-pulse rounded-full bg-muted/70" />
        ) : null}
      </div>
    );
  }
  return (
    <div
      role="alert"
      aria-live="polite"
      className={cn(
        "mt-3 flex items-center justify-between gap-3 rounded-lg border px-3 text-xs",
        compact ? "border-transparent bg-transparent px-0 py-1" : "py-3",
        "border-destructive/25 bg-destructive/5 text-destructive",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span>{copy.loadFailed}</span>
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 shrink-0 px-2 text-xs"
        onClick={() => void onRetry()}
      >
        {copy.retry}
      </Button>
    </div>
  );
}

function clearEchoBrowserState(): void {
  if (typeof window === "undefined") return;
  const prefixes = ["echo", "code:", "team:", "realtime:"];
  const exactKeys = new Set([
    "token",
    "echo_auth_token",
    "echo_user",
    "echo_auth_ts",
  ]);
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (let i = store.length - 1; i >= 0; i -= 1) {
      const key = store.key(i);
      if (!key) continue;
      if (
        exactKeys.has(key) ||
        prefixes.some((prefix) => key.startsWith(prefix))
      ) {
        store.removeItem(key);
      }
    }
  }
  window.dispatchEvent(new Event("storage"));
}

function isPathList(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((path) => typeof path === "string")
  );
}

function isConstitutionProfileStatus(
  value: ConstitutionProfileStatus,
): boolean {
  const allowed = new Set<ConstitutionProfile>(["strict", "normal", "lax"]);
  return (
    allowed.has(value?.profile) &&
    Array.isArray(value.available) &&
    value.available.length > 0 &&
    value.available.every((profile) => allowed.has(profile)) &&
    value.available.includes(value.profile)
  );
}

function isAbsoluteLikePath(path: string): boolean {
  if (!path) return false;
  return (
    path.startsWith("/") ||
    path.startsWith("~/") ||
    path.startsWith("$HOME/") ||
    path.startsWith("%USERPROFILE%") ||
    path.startsWith("%APPDATA%") ||
    /^[a-zA-Z]:[\\/]/.test(path) ||
    path.startsWith("\\\\")
  );
}
