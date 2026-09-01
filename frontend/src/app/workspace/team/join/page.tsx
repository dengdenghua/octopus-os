import {
  Clock3Icon,
  Loader2Icon,
  ShieldCheckIcon,
  UsersIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState, LoadingState } from "@/components/ui/state";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import {
  dispatchTeamUpdated,
  getOwnTeamJoinRequest,
  inspectTeamInvite,
  joinTeamInvite,
  readOrCreateTeamParticipantId,
  writePreferredTeam,
  type TeamInvitePreview,
  type JoinedTeamInviteResult,
  type TeamJoinRequest,
  withdrawOwnTeamJoinRequest,
} from "@/core/teams";

function teamRealtimeTarget(threadId?: string | null): string {
  const cleanId = threadId?.trim();
  if (!cleanId || cleanId === "new") return "/workspace/realtime/new";
  return `/workspace/realtime/${encodeURIComponent(cleanId)}`;
}

export default function TeamJoinPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = useMemo(() => params.get("token")?.trim() ?? "", [params]);
  const [preview, setPreview] = useState<TeamInvitePreview | null>(null);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [joining, setJoining] = useState(false);
  const [joinRequest, setJoinRequest] = useState<TeamJoinRequest | null>(null);
  const [checkingRequest, setCheckingRequest] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const restoredRequestTokenRef = useRef("");

  useEffect(() => {
    restoredRequestTokenRef.current = "";
    setPreview(null);
    setJoinRequest(null);
    setName("");
    setError(null);
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setLoading(false);
      setError(t.teamJoin.missingToken);
      return;
    }
    setLoading(true);
    void inspectTeamInvite(token)
      .then((nextPreview) => {
        if (cancelled) return;
        setPreview(nextPreview);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError(t.teamJoin.invalidInvite);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t.teamJoin.invalidInvite, t.teamJoin.missingToken, token]);

  const enterJoinedTeam = useCallback(
    (result: JoinedTeamInviteResult) => {
      writePreferredTeam(result.team);
      dispatchTeamUpdated(result.team);
      window.dispatchEvent(new Event("echo:teams-refresh"));
      toast.success(t.teamJoin.joinSuccess(result.team.name));
      const canonicalThreadId = result.thread_id?.trim();
      if (!canonicalThreadId || canonicalThreadId === "new") {
        toast.error(t.teamJoin.missingDestination);
        return;
      }
      navigate(teamRealtimeTarget(canonicalThreadId), { replace: true });
    },
    [navigate, t.teamJoin],
  );

  const checkJoinRequest = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setCheckingRequest(true);
      try {
        const result = await getOwnTeamJoinRequest(token);
        if (!result) {
          setJoinRequest(null);
          return;
        }
        if (result.outcome === "joined") {
          enterJoinedTeam(result);
          return;
        }
        setJoinRequest(result.join_request);
        setPreview((current) =>
          current ? { ...current, join_policy: result.join_policy } : current,
        );
      } catch {
        if (!silent) toast.error(t.teamJoin.statusCheckFailed);
      } finally {
        if (!silent) setCheckingRequest(false);
      }
    },
    [enterJoinedTeam, t.teamJoin.statusCheckFailed, token],
  );

  useEffect(() => {
    if (
      !token ||
      !preview ||
      preview.join_policy !== "apply_then_join" ||
      restoredRequestTokenRef.current === token
    ) {
      return;
    }
    restoredRequestTokenRef.current = token;
    void checkJoinRequest(true);
  }, [checkJoinRequest, preview, token]);

  useEffect(() => {
    if (joinRequest?.status !== "pending") return;
    const interval = window.setInterval(() => {
      void checkJoinRequest(true);
    }, 4000);
    return () => window.clearInterval(interval);
  }, [checkJoinRequest, joinRequest?.status]);

  const handleJoin = async () => {
    if (!token || !preview || preview.invite.status !== "active") return;
    setJoining(true);
    try {
      const result = await joinTeamInvite(token, {
        display_name: name.trim() || t.teamJoin.guestName,
        participant_id: readOrCreateTeamParticipantId(),
      });
      if (result.outcome === "joined") {
        enterJoinedTeam({
          ...result,
          thread_id: result.thread_id ?? preview.thread_id,
        });
      } else {
        setJoinRequest(result.join_request);
        setPreview((current) =>
          current ? { ...current, join_policy: result.join_policy } : current,
        );
        toast.success(t.teamJoin.requestSubmitted);
      }
    } catch {
      toast.error(t.teamJoin.joinFailed);
    } finally {
      setJoining(false);
    }
  };

  const handleWithdraw = async () => {
    if (!token || joinRequest?.status !== "pending") return;
    setWithdrawing(true);
    try {
      const result = await withdrawOwnTeamJoinRequest(token);
      setJoinRequest(result.join_request);
    } catch {
      toast.error(t.teamJoin.withdrawFailed);
    } finally {
      setWithdrawing(false);
    }
  };

  const requestStatusText = (status: TeamJoinRequest["status"]) => {
    if (status === "approved") return t.teamJoin.requestApprovedButUnavailable;
    if (status === "rejected") return t.teamJoin.requestRejected;
    if (status === "withdrawn") return t.teamJoin.requestWithdrawn;
    if (status === "expired") return t.teamJoin.requestExpired;
    if (status === "cancelled") return t.teamJoin.requestCancelled;
    return t.teamJoin.requestPendingDescription;
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="flex items-center justify-center px-6 py-10">
        <div className="workspace-panel w-full max-w-md p-6 shadow-[var(--shadow-xs)]">
          <div className="mb-5 flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <UsersIcon className="size-5" />
            </span>
            <div>
              <h1 className="text-base font-semibold">{t.teamJoin.title}</h1>
              <p className="text-sm text-muted-foreground">
                {t.teamJoin.description}
              </p>
            </div>
          </div>

          {loading ? (
            <LoadingState
              title={t.teamJoin.loadingInvite}
              className="min-h-[180px] border-0 bg-transparent p-4"
            />
          ) : error ? (
            <ErrorState
              title={t.teamJoin.invalidInvite}
              detail={error}
              className="min-h-[180px] border-0 bg-transparent p-4"
            />
          ) : (
            <div className="space-y-4">
              <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium">
                    {preview?.team.name}
                  </div>
                  <Badge variant="secondary">
                    {preview?.invite.role === "viewer"
                      ? t.collab.inviteAgents.roleViewer
                      : t.collab.inviteAgents.roleMember}
                  </Badge>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {t.teamJoin.membersAndParticipants(
                    preview?.team.member_count ?? 0,
                    preview?.team.participant_count ?? 0,
                  )}
                </div>
                {preview?.invite.expires_at ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {t.collab.humanInvite.expiresAt(
                      new Intl.DateTimeFormat(undefined, {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(preview.invite.expires_at)),
                    )}
                  </div>
                ) : null}
                {preview?.join_policy === "apply_then_join" ? (
                  <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/8 px-2.5 py-2 text-xs">
                    <ShieldCheckIcon className="mt-0.5 size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
                    <span>
                      <span className="block font-medium text-foreground">
                        {t.teamJoin.approvalRequired}
                      </span>
                      <span className="mt-0.5 block leading-5 text-muted-foreground">
                        {t.teamJoin.approvalRequiredDescription}
                      </span>
                    </span>
                  </div>
                ) : null}
              </div>
              {joinRequest ? (
                <div className="space-y-3 rounded-lg border border-border-default p-4 text-center">
                  <span className="mx-auto grid size-10 place-items-center rounded-full bg-primary/10 text-primary">
                    {joinRequest.status === "pending" ? (
                      <Clock3Icon className="size-5" />
                    ) : (
                      <ShieldCheckIcon className="size-5" />
                    )}
                  </span>
                  <div>
                    <div className="text-sm font-medium">
                      {joinRequest.status === "pending"
                        ? t.teamJoin.requestPendingTitle
                        : requestStatusText(joinRequest.status)}
                    </div>
                    {joinRequest.status === "pending" ? (
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {t.teamJoin.requestPendingDescription}
                      </p>
                    ) : null}
                  </div>
                  {joinRequest.status === "pending" ? (
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        className="flex-1"
                        disabled={withdrawing}
                        onClick={() => void handleWithdraw()}
                      >
                        {withdrawing ? (
                          <Loader2Icon className="size-4 animate-spin" />
                        ) : null}
                        {t.teamJoin.withdrawRequest}
                      </Button>
                      <Button
                        type="button"
                        className="flex-1"
                        disabled={checkingRequest || joining}
                        onClick={() =>
                          preview?.join_policy === "direct_join"
                            ? void handleJoin()
                            : void checkJoinRequest()
                        }
                      >
                        {checkingRequest || joining ? (
                          <Loader2Icon className="size-4 animate-spin" />
                        ) : null}
                        {preview?.join_policy === "direct_join"
                          ? t.teamJoin.joinButton
                          : t.teamJoin.refreshStatus}
                      </Button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <>
                  <Input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={t.teamJoin.displayNamePlaceholder}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void handleJoin();
                      }
                    }}
                  />
                  <Button
                    className="w-full"
                    onClick={() => void handleJoin()}
                    disabled={joining || preview?.invite.status !== "active"}
                  >
                    {joining ? (
                      <>
                        <Loader2Icon className="mr-2 size-4 animate-spin" />
                        {preview?.join_policy === "apply_then_join"
                          ? t.teamJoin.applying
                          : t.teamJoin.joining}
                      </>
                    ) : preview?.join_policy === "apply_then_join" ? (
                      t.teamJoin.applyButton
                    ) : (
                      t.teamJoin.joinButton
                    )}
                  </Button>
                </>
              )}
            </div>
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
