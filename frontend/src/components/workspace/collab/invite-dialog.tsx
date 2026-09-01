import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckIcon,
  CopyIcon,
  EyeIcon,
  LinkIcon,
  Loader2Icon,
  ShieldCheckIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { copyTextToClipboard } from "@/core/clipboard";
import { coworkQueryKeys } from "@/core/cowork";
import { useI18n } from "@/core/i18n/hooks";
import {
  createTeamInvite,
  approveTeamJoinRequest,
  getTeamJoinPolicy,
  listTeamJoinRequests,
  listTeamInvites,
  rejectTeamJoinRequest,
  revokeTeamInvite,
  updateTeamJoinPolicy,
  type TeamInvite,
  type TeamInviteRecord,
  type TeamInviteRole,
  type TeamInviteStatus,
  type TeamJoinPolicy,
  type TeamJoinPolicyInfo,
  type TeamJoinRequest,
} from "@/core/teams";
import { cn } from "@/lib/utils";

export interface InviteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roomId: string;
  threadId?: string | null;
}

const EXPIRATION_OPTIONS = [
  { seconds: 60 * 60, translationKey: "expiresHour" },
  { seconds: 24 * 60 * 60, translationKey: "expiresDay" },
  { seconds: 7 * 24 * 60 * 60, translationKey: "expiresWeek" },
  { seconds: 30 * 24 * 60 * 60, translationKey: "expiresMonth" },
] as const;

export function inviteLinkFromResponse(
  invite: Pick<TeamInvite, "invite_hash_path" | "invite_path">,
  origin: string,
) {
  const path = invite.invite_hash_path || invite.invite_path;
  return new URL(path, origin).toString();
}

export function InviteDialog({
  open,
  onOpenChange,
  roomId,
  threadId,
}: InviteDialogProps) {
  const { t, locale } = useI18n();
  const copy = t.collab.humanInvite;
  const queryClient = useQueryClient();
  const { confirm, confirmDialog } = useConfirmDialog();
  const loadGenerationRef = useRef(0);
  const [role, setRole] = useState<TeamInviteRole>("member");
  const [expiration, setExpiration] = useState(
    String(EXPIRATION_OPTIONS[2].seconds),
  );
  const [inviteLink, setInviteLink] = useState("");
  const [createdInviteId, setCreatedInviteId] = useState<string | null>(null);
  const [invites, setInvites] = useState<TeamInviteRecord[]>([]);
  const [loadingInvites, setLoadingInvites] = useState(false);
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [policy, setPolicy] = useState<TeamJoinPolicyInfo | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [joinRequests, setJoinRequests] = useState<TeamJoinRequest[]>([]);
  const [loadingRequests, setLoadingRequests] = useState(false);
  const [actingRequestId, setActingRequestId] = useState<string | null>(null);

  const statusLabels = useMemo<Record<TeamInviteStatus, string>>(
    () => ({
      active: copy.statusActive,
      expired: copy.statusExpired,
      exhausted: copy.statusExhausted,
      revoked: copy.statusRevoked,
    }),
    [copy],
  );

  const loadInvites = useCallback(
    async (
      options: {
        generation?: number;
        roomId?: string;
        silent?: boolean;
      } = {},
    ) => {
      const targetRoomId = options.roomId ?? roomId;
      const generation = options.generation ?? loadGenerationRef.current;
      if (!targetRoomId) return;
      if (!options.silent && generation === loadGenerationRef.current) {
        setLoadingInvites(true);
      }
      try {
        const nextInvites = await listTeamInvites(targetRoomId);
        if (generation === loadGenerationRef.current) {
          setInvites(nextInvites);
        }
      } catch (error) {
        if (generation === loadGenerationRef.current && !options.silent) {
          toast.error(error instanceof Error ? error.message : copy.loadFailed);
        }
      } finally {
        if (generation === loadGenerationRef.current && !options.silent) {
          setLoadingInvites(false);
        }
      }
    },
    [copy.loadFailed, roomId],
  );

  const loadGovernance = useCallback(
    async (
      options: {
        generation?: number;
        roomId?: string;
        silent?: boolean;
      } = {},
    ) => {
      const targetRoomId = options.roomId ?? roomId;
      const generation = options.generation ?? loadGenerationRef.current;
      if (!targetRoomId) return;
      if (!options.silent && generation === loadGenerationRef.current) {
        setLoadingRequests(true);
      }
      try {
        const nextPolicy = await getTeamJoinPolicy(targetRoomId);
        const nextRequests = nextPolicy.is_project_group
          ? await listTeamJoinRequests(targetRoomId, "pending")
          : [];
        if (generation === loadGenerationRef.current) {
          setPolicy(nextPolicy);
          setJoinRequests(nextRequests);
        }
      } catch (error) {
        if (generation === loadGenerationRef.current && !options.silent) {
          toast.error(
            error instanceof Error ? error.message : copy.requestsLoadFailed,
          );
        }
      } finally {
        if (generation === loadGenerationRef.current && !options.silent) {
          setLoadingRequests(false);
        }
      }
    },
    [copy.requestsLoadFailed, roomId],
  );

  useEffect(() => {
    const generation = loadGenerationRef.current + 1;
    loadGenerationRef.current = generation;
    setInviteLink("");
    setCreatedInviteId(null);
    setCopied(false);
    setInvites([]);
    setPolicy(null);
    setJoinRequests([]);
    setLoadingInvites(open);
    setLoadingRequests(open);
    if (open && roomId) {
      void loadInvites({ generation, roomId });
      void loadGovernance({ generation, roomId });
    }
    return () => {
      if (loadGenerationRef.current === generation) {
        loadGenerationRef.current += 1;
      }
    };
  }, [loadGovernance, loadInvites, open, roomId]);

  useEffect(() => {
    if (!open || !policy?.is_project_group) return;
    const timer = window.setInterval(() => {
      void loadGovernance({ silent: true });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadGovernance, open, policy?.is_project_group]);

  const handlePolicyChange = async (nextPolicy: TeamJoinPolicy) => {
    if (
      !policy ||
      policy.team_id !== roomId ||
      nextPolicy === policy.join_policy ||
      savingPolicy
    ) {
      return;
    }
    const previous = policy;
    setPolicy({ ...policy, join_policy: nextPolicy, overridden: true });
    setSavingPolicy(true);
    try {
      setPolicy(await updateTeamJoinPolicy(roomId, nextPolicy));
    } catch (error) {
      setPolicy(previous);
      toast.error(
        error instanceof Error ? error.message : copy.policySaveFailed,
      );
    } finally {
      setSavingPolicy(false);
    }
  };

  const handlePolicySelection = async (nextPolicy: TeamJoinPolicy) => {
    if (nextPolicy === "direct_join" && policy?.join_policy !== "direct_join") {
      const accepted = await confirm({
        title: copy.directJoinConfirmTitle,
        description: copy.directJoinConfirmDescription,
        confirmLabel: copy.directJoinConfirmAction,
        cancelLabel: copy.directJoinConfirmCancel,
        destructive: false,
      });
      if (!accepted) return;
    }
    await handlePolicyChange(nextPolicy);
  };

  const handleJoinRequest = async (
    requestId: string,
    action: "approve" | "reject",
  ) => {
    setActingRequestId(requestId);
    try {
      if (action === "approve") {
        await approveTeamJoinRequest(roomId, requestId);
        toast.success(copy.approveSuccess);
      } else {
        await rejectTeamJoinRequest(roomId, requestId);
        toast.success(copy.rejectSuccess);
      }
      setJoinRequests((current) =>
        current.filter((request) => request.id !== requestId),
      );
      if (threadId) {
        void queryClient.invalidateQueries({
          queryKey: coworkQueryKeys.session(threadId),
        });
        void queryClient.invalidateQueries({
          queryKey: coworkQueryKeys.group(threadId),
        });
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : copy.requestActionFailed,
      );
    } finally {
      setActingRequestId(null);
    }
  };

  const handleCreate = async () => {
    if (!roomId) return;
    setCreating(true);
    try {
      const invite = await createTeamInvite(roomId, {
        role,
        expires_in_seconds: Number(expiration),
      });
      setInviteLink(inviteLinkFromResponse(invite, window.location.origin));
      setCreatedInviteId(invite.invite_id);
      setCopied(false);
      await loadInvites();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : copy.createFailed);
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!inviteLink) return;
    try {
      await copyTextToClipboard(inviteLink);
      setCopied(true);
      toast.success(t.collab.linkCopied);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t.collab.copyFailed);
    }
  };

  const handleRevoke = async (inviteId: string) => {
    setRevokingId(inviteId);
    try {
      const revoked = await revokeTeamInvite(roomId, inviteId);
      setInvites((current) =>
        current.map((invite) => (invite.id === inviteId ? revoked : invite)),
      );
      if (createdInviteId === inviteId) {
        setInviteLink("");
        setCreatedInviteId(null);
      }
      toast.success(copy.revokeSuccess);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : copy.revokeFailed);
    } finally {
      setRevokingId(null);
    }
  };

  const formatDate = (value?: string | null) => {
    if (!value) return copy.neverExpires;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[min(82vh,720px)] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{copy.dialogTitle}</DialogTitle>
            <DialogDescription>{copy.dialogDescription}</DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            {policy?.is_project_group ? (
              <section
                aria-labelledby="human-invite-policy-heading"
                className="space-y-2 rounded-lg border border-border-default bg-muted/20 p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <Label id="human-invite-policy-heading">
                    {copy.joinPolicyLabel}
                  </Label>
                  {savingPolicy ? (
                    <Loader2Icon
                      className="size-4 animate-spin text-muted-foreground"
                      aria-hidden="true"
                    />
                  ) : null}
                </div>
                <Select
                  value={policy.join_policy}
                  onValueChange={(value) =>
                    void handlePolicySelection(value as TeamJoinPolicy)
                  }
                  disabled={savingPolicy}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="apply_then_join">
                      {copy.joinPolicyApply}
                    </SelectItem>
                    <SelectItem value="direct_join">
                      {copy.joinPolicyDirect}
                    </SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs leading-5 text-muted-foreground">
                  {policy.join_policy === "apply_then_join"
                    ? copy.joinPolicyApplyDesc
                    : copy.joinPolicyDirectDesc}
                </p>
              </section>
            ) : null}

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">{copy.roleLabel}</legend>
              <div className="grid grid-cols-2 gap-2">
                {(["member", "viewer"] as const).map((nextRole) => {
                  const isMember = nextRole === "member";
                  const selected = role === nextRole;
                  return (
                    <button
                      key={nextRole}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setRole(nextRole)}
                      className={cn(
                        "rounded-lg border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        selected
                          ? "border-primary/40 bg-primary/10"
                          : "border-border-default hover:bg-muted/40",
                      )}
                    >
                      <span className="flex items-center gap-2 text-sm font-medium">
                        {isMember ? (
                          <ShieldCheckIcon className="size-4" />
                        ) : (
                          <EyeIcon className="size-4" />
                        )}
                        {isMember
                          ? t.collab.inviteAgents.roleMember
                          : t.collab.inviteAgents.roleViewer}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {isMember
                          ? t.collab.inviteAgents.roleMemberDesc
                          : t.collab.inviteAgents.roleViewerDesc}
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>

            <div className="space-y-2">
              <Label htmlFor="human-invite-expiration">
                {copy.expiresLabel}
              </Label>
              <Select value={expiration} onValueChange={setExpiration}>
                <SelectTrigger
                  id="human-invite-expiration"
                  className="w-full focus-visible:border-muted-foreground/40 focus-visible:ring-muted-foreground/15"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRATION_OPTIONS.map((option) => (
                    <SelectItem
                      key={option.seconds}
                      value={String(option.seconds)}
                    >
                      {copy[option.translationKey]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button
              type="button"
              className="w-full"
              onClick={() => void handleCreate()}
              disabled={creating || !roomId}
            >
              {creating ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <LinkIcon className="size-4" />
              )}
              {creating ? copy.creatingLink : copy.createLink}
            </Button>

            {inviteLink ? (
              <div className="space-y-2" aria-live="polite">
                <Label htmlFor="human-invite-link">{copy.currentLink}</Label>
                <div className="flex gap-2">
                  <Input
                    id="human-invite-link"
                    value={inviteLink}
                    readOnly
                    className="min-w-0 font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void handleCopy()}
                    aria-label={t.collab.copyLink}
                  >
                    {copied ? (
                      <CheckIcon className="size-4" />
                    ) : (
                      <CopyIcon className="size-4" />
                    )}
                    <span className="hidden sm:inline">
                      {copied ? t.collab.copied : t.collab.copy}
                    </span>
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {copy.linkVisibleOnce}
                </p>
              </div>
            ) : null}

            {policy?.is_project_group ? (
              <section aria-labelledby="human-join-requests-heading">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3
                    id="human-join-requests-heading"
                    className="text-sm font-medium"
                  >
                    {copy.pendingRequestsTitle}
                  </h3>
                  <div className="flex items-center gap-1">
                    <Badge variant="secondary">{joinRequests.length}</Badge>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => void loadGovernance()}
                      disabled={loadingRequests}
                    >
                      {copy.refresh}
                    </Button>
                  </div>
                </div>
                {loadingRequests ? (
                  <div className="flex items-center justify-center gap-2 rounded-lg border border-border-default py-6 text-sm text-muted-foreground">
                    <Loader2Icon className="size-4 animate-spin" />
                    {copy.loadingRecords}
                  </div>
                ) : joinRequests.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border-default py-6 text-center text-sm text-muted-foreground">
                    {copy.pendingRequestsEmpty}
                  </div>
                ) : (
                  <div className="max-h-52 space-y-2 overflow-y-auto pr-1">
                    {joinRequests.map((request) => (
                      <div
                        key={request.id}
                        className="flex items-center gap-3 rounded-lg border border-border-default px-3 py-2"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">
                            {request.display_name}
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {request.role === "viewer"
                              ? t.collab.inviteAgents.roleViewer
                              : t.collab.inviteAgents.roleMember}
                            {request.created_at
                              ? ` · ${formatDate(request.created_at)}`
                              : ""}
                          </div>
                          {request.actor_id ? (
                            <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground/80">
                              {request.actor_id}
                            </div>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={actingRequestId === request.id}
                            onClick={() =>
                              void handleJoinRequest(request.id, "reject")
                            }
                          >
                            {copy.rejectRequest}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            disabled={actingRequestId === request.id}
                            onClick={() =>
                              void handleJoinRequest(request.id, "approve")
                            }
                          >
                            {actingRequestId === request.id ? (
                              <Loader2Icon className="size-4 animate-spin" />
                            ) : null}
                            {copy.approveRequest}
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            ) : null}

            <section aria-labelledby="human-invite-records-heading">
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3
                  id="human-invite-records-heading"
                  className="text-sm font-medium"
                >
                  {copy.recordsTitle}
                </h3>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => void loadInvites()}
                  disabled={loadingInvites}
                >
                  {copy.refresh}
                </Button>
              </div>

              {loadingInvites ? (
                <div className="flex items-center justify-center gap-2 rounded-lg border border-border-default py-8 text-sm text-muted-foreground">
                  <Loader2Icon className="size-4 animate-spin" />
                  {copy.loadingRecords}
                </div>
              ) : invites.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border-default py-8 text-center text-sm text-muted-foreground">
                  {copy.emptyRecords}
                </div>
              ) : (
                <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                  {invites.map((invite) => (
                    <div
                      key={invite.id}
                      className="flex items-center gap-3 rounded-lg border border-border-default px-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">
                            {invite.role === "viewer"
                              ? t.collab.inviteAgents.roleViewer
                              : t.collab.inviteAgents.roleMember}
                          </span>
                          <Badge
                            variant={
                              invite.status === "active"
                                ? "secondary"
                                : "outline"
                            }
                          >
                            {statusLabels[invite.status]}
                          </Badge>
                        </div>
                        <div className="mt-1 truncate text-xs text-muted-foreground">
                          {copy.expiresAt(formatDate(invite.expires_at))} ·{" "}
                          {copy.usage(
                            invite.use_count,
                            invite.max_uses ?? null,
                          )}
                        </div>
                      </div>
                      {invite.status === "active" ? (
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={copy.revoke}
                          disabled={revokingId === invite.id}
                          onClick={() => void handleRevoke(invite.id)}
                        >
                          {revokingId === invite.id ? (
                            <Loader2Icon className="size-4 animate-spin" />
                          ) : (
                            <Trash2Icon className="size-4" />
                          )}
                        </Button>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        </DialogContent>
      </Dialog>
      {confirmDialog}
    </>
  );
}
