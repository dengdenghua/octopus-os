import { useMemo, useState } from "react";
import {
  BotIcon,
  CrownIcon,
  EyeIcon,
  Loader2Icon,
  MicIcon,
  MicOffIcon,
  ShieldCheckIcon,
  Trash2Icon,
  UserCogIcon,
  UserIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  removeTeamParticipant,
  updateDelegation,
  updateSpeakerPolicy,
  updateTeamParticipant,
  type SpeakerPolicy,
  type SpeakMode,
  type Team,
  type TeamParticipant,
  type TeamParticipantRole,
} from "@/core/teams";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const SPEAKER_POLICIES: {
  value: SpeakerPolicy;
  labelKey:
    | "policyFree"
    | "policyAdminOnly"
    | "policyRoundRobin"
    | "policyRollCall"
    | "policyModerated";
}[] = [
  { value: "free", labelKey: "policyFree" },
  { value: "admin_only", labelKey: "policyAdminOnly" },
  { value: "round_robin", labelKey: "policyRoundRobin" },
  { value: "roll_call", labelKey: "policyRollCall" },
  { value: "moderated", labelKey: "policyModerated" },
];

interface TeamMembersDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: Team | null;
  currentParticipantId: string;
  onTeamChange: (team: Team) => void;
}

const ROLE_META: Record<
  TeamParticipantRole,
  {
    label: string;
    descriptionKey: "ownerDesc" | "memberDesc" | "viewerDesc";
    icon: typeof CrownIcon;
  }
> = {
  owner: {
    label: "Owner",
    descriptionKey: "ownerDesc",
    icon: CrownIcon,
  },
  member: {
    label: "Member",
    descriptionKey: "memberDesc",
    icon: ShieldCheckIcon,
  },
  viewer: {
    label: "Viewer",
    descriptionKey: "viewerDesc",
    icon: EyeIcon,
  },
};

export function TeamMembersDialog({
  open,
  onOpenChange,
  team,
  currentParticipantId,
  onTeamChange,
}: TeamMembersDialogProps) {
  const { t } = useI18n();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [policyBusy, setPolicyBusy] = useState(false);
  const participants = useMemo(
    () => (team?.participants ?? []).filter((p) => p.status !== "removed"),
    [team?.participants],
  );
  const ownerCount = participants.filter((p) => p.role === "owner").length;
  // Owner-only controls (policy, mute). The caller is the owner when their
  // participant carries the owner role or matches the room's owner_id.
  const isOwner = useMemo(() => {
    const self = participants.find((p) => p.id === currentParticipantId);
    return (
      self?.role === "owner" ||
      (!!team?.owner_id && self?.actor_id === team.owner_id)
    );
  }, [participants, currentParticipantId, team?.owner_id]);
  const speakerPolicy: SpeakerPolicy = team?.speaker_policy ?? "free";

  const handlePolicyChange = async (policy: SpeakerPolicy) => {
    if (!team || policy === speakerPolicy) return;
    setPolicyBusy(true);
    try {
      const result = await updateSpeakerPolicy(team.id, policy);
      onTeamChange(result.team);
      toast.success(t.teamMembers.policyUpdated);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamMembers.updatePolicyFailed,
      );
    } finally {
      setPolicyBusy(false);
    }
  };

  const handleToggleMute = async (participant: TeamParticipant) => {
    if (!team) return;
    setBusyId(participant.id);
    try {
      const result = await updateTeamParticipant(team.id, participant.id, {
        muted: !participant.muted,
      });
      onTeamChange(result.team);
      toast.success(t.teamMembers.permissionsUpdated);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamMembers.updatePermissionsFailed,
      );
    } finally {
      setBusyId(null);
    }
  };

  // Self-service delegation (the participant's OWN opt-in). The backend
  // rejects setting this on anyone else, so this is wired only for the
  // current user's own row.
  const handleDelegationChange = async (
    participant: TeamParticipant,
    mode: SpeakMode,
    binding?: { twin_agent_id?: string; host_id?: string },
  ) => {
    if (!team) return;
    setBusyId(participant.id);
    try {
      const result = await updateDelegation(team.id, participant.id, {
        speak_mode: mode,
        twin_agent_id: binding?.twin_agent_id ?? null,
        host_id: binding?.host_id ?? null,
      });
      onTeamChange(result.team);
      toast.success(t.teamMembers.delegationUpdated);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamMembers.updateDelegationFailed,
      );
    } finally {
      setBusyId(null);
    }
  };

  const handleRoleChange = async (
    participant: TeamParticipant,
    role: TeamParticipantRole,
  ) => {
    if (!team || role === participant.role) return;
    setBusyId(participant.id);
    try {
      const result = await updateTeamParticipant(team.id, participant.id, {
        role,
      });
      onTeamChange(result.team);
      toast.success(t.teamMembers.permissionsUpdated);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamMembers.updatePermissionsFailed,
      );
    } finally {
      setBusyId(null);
    }
  };

  const handleRemove = async (participant: TeamParticipant) => {
    if (!team) return;
    setBusyId(participant.id);
    try {
      const result = await removeTeamParticipant(team.id, participant.id);
      onTeamChange(result.team);
      toast.success(t.teamMembers.memberRemoved);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t.teamMembers.removeMemberFailed,
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UserCogIcon className="size-5" />
            {t.teamMembers.title}
          </DialogTitle>
          <DialogDescription>{t.teamMembers.description}</DialogDescription>
        </DialogHeader>

        {isOwner && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border-default bg-muted/15 px-3 py-2">
            <div className="min-w-0">
              <div className="text-sm font-medium">
                {t.teamMembers.speakerPolicy}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {t.teamMembers.speakerPolicyHint}
              </div>
            </div>
            <Select
              value={speakerPolicy}
              disabled={policyBusy}
              onValueChange={(value) =>
                void handlePolicyChange(value as SpeakerPolicy)
              }
            >
              <SelectTrigger size="sm" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SPEAKER_POLICIES.map((policy) => (
                  <SelectItem key={policy.value} value={policy.value}>
                    {t.teamMembers[policy.labelKey]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="max-h-[58vh] overflow-y-auto pr-1">
          <div className="space-y-2">
            {participants.map((participant) => {
              const meta = ROLE_META[participant.role] ?? ROLE_META.viewer;
              const Icon = meta.icon;
              const isSelf = participant.id === currentParticipantId;
              const isLastOwner =
                participant.role === "owner" && ownerCount <= 1;
              const isBusy = busyId === participant.id;
              const speakMode: SpeakMode = participant.speak_mode ?? "manual";
              const otherParticipants = participants.filter(
                (p) => p.id !== participant.id,
              );
              return (
                <div
                  key={participant.id}
                  className="flex items-center gap-3 rounded-lg border border-border-default bg-muted/15 px-3 py-2"
                >
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    <Icon className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium">
                        {participant.display_name}
                      </span>
                      {isSelf && <Badge variant="outline">You</Badge>}
                      {participant.muted && (
                        <Badge
                          variant="outline"
                          className="border-warning/40 text-warning"
                        >
                          {t.teamMembers.mutedBadge}
                        </Badge>
                      )}
                      <span
                        className={cn(
                          "size-2 rounded-full",
                          participant.status === "active"
                            ? "bg-success"
                            : "bg-muted-foreground/35",
                        )}
                      />
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {t.teamMembers[meta.descriptionKey]}
                    </div>
                  </div>

                  {isSelf && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-8 gap-1.5 text-xs"
                          disabled={isBusy}
                          title={t.teamMembers.speakingAs}
                        >
                          {speakMode === "twin" ? (
                            <BotIcon className="size-3.5" />
                          ) : speakMode === "hosted" ? (
                            <UserIcon className="size-3.5" />
                          ) : (
                            <MicIcon className="size-3.5" />
                          )}
                          {speakMode === "twin"
                            ? t.teamMembers.speakViaTwin
                            : speakMode === "hosted"
                              ? t.teamMembers.hostedBy
                              : t.teamMembers.speakManual}
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-56">
                        <DropdownMenuLabel className="text-xs">
                          {t.teamMembers.speakingAs}
                        </DropdownMenuLabel>
                        <DropdownMenuCheckboxItem
                          checked={speakMode === "manual"}
                          onCheckedChange={() =>
                            void handleDelegationChange(participant, "manual")
                          }
                        >
                          {t.teamMembers.speakManual}
                        </DropdownMenuCheckboxItem>
                        {(team?.members.length ?? 0) > 0 && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuLabel className="text-xs text-muted-foreground">
                              {t.teamMembers.speakViaTwin}
                            </DropdownMenuLabel>
                            {team?.members.map((agent) => (
                              <DropdownMenuCheckboxItem
                                key={agent.name}
                                checked={
                                  speakMode === "twin" &&
                                  participant.twin_agent_id === agent.name
                                }
                                onCheckedChange={() =>
                                  void handleDelegationChange(
                                    participant,
                                    "twin",
                                    {
                                      twin_agent_id: agent.name,
                                    },
                                  )
                                }
                              >
                                {agent.display_name ?? agent.name}
                              </DropdownMenuCheckboxItem>
                            ))}
                          </>
                        )}
                        {otherParticipants.length > 0 && (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuLabel className="text-xs text-muted-foreground">
                              {t.teamMembers.hostedBy}
                            </DropdownMenuLabel>
                            {otherParticipants.map((p) => (
                              <DropdownMenuCheckboxItem
                                key={p.id}
                                checked={
                                  speakMode === "hosted" &&
                                  participant.host_id === p.id
                                }
                                onCheckedChange={() =>
                                  void handleDelegationChange(
                                    participant,
                                    "hosted",
                                    {
                                      host_id: p.id,
                                    },
                                  )
                                }
                              >
                                {p.display_name}
                              </DropdownMenuCheckboxItem>
                            ))}
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}

                  <Select
                    value={participant.role}
                    disabled={isBusy || isLastOwner}
                    onValueChange={(value) =>
                      void handleRoleChange(
                        participant,
                        value as TeamParticipantRole,
                      )
                    }
                  >
                    <SelectTrigger size="sm" className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(ROLE_META).map(([role, item]) => (
                        <SelectItem key={role} value={role}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {isOwner && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className={cn(
                        "size-8 rounded-lg",
                        participant.muted
                          ? "text-warning hover:bg-warning/10 dark:text-warning"
                          : "text-muted-foreground hover:bg-muted",
                      )}
                      disabled={isBusy}
                      onClick={() => void handleToggleMute(participant)}
                      title={
                        participant.muted
                          ? t.teamMembers.unmute
                          : t.teamMembers.mute
                      }
                    >
                      {participant.muted ? (
                        <MicOffIcon className="size-4" />
                      ) : (
                        <MicIcon className="size-4" />
                      )}
                    </Button>
                  )}

                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    disabled={isBusy || isSelf || isLastOwner}
                    onClick={() => void handleRemove(participant)}
                    title={t.teamMembers.removeMember}
                  >
                    {isBusy ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <Trash2Icon className="size-4" />
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
