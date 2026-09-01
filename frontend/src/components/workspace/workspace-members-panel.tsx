/**
 * Workspace Members Panel · list members of a remote workspace and
 * manage their roles.
 *
 * For each member we render an avatar + name + role badge + a small
 * "currently editing" hint derived from the workspace's open file
 * leases (``GET /api/workspaces/{id}/leases``). The owner can change
 * another member's role through a per-row Select and remove members
 * via a small ghost button. An "Add member" action opens a tiny
 * inline dialog that takes a free-form member id.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";
import {
  addMember,
  listLeases,
  listMembers,
  removeMember,
} from "@/core/workspace/api";
import type {
  FileLease,
  MemberRole,
  WorkspaceMember,
} from "@/core/workspace/types";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface WorkspaceMembersPanelProps {
  workspaceId: string | null;
  /** The actor viewing the panel — used to gate owner-only controls. */
  currentMemberId?: string | null;
  className?: string;
}

const ROLE_OPTIONS: MemberRole[] = [
  "owner",
  "editor",
  "reviewer",
  "viewer",
];

function roleLabelKey(role: MemberRole) {
  switch (role) {
    case "owner":
      return "roleOwner" as const;
    case "editor":
      return "roleEditor" as const;
    case "reviewer":
      return "roleReviewer" as const;
    case "viewer":
      return "roleViewer" as const;
  }
}

function roleBadgeVariant(
  role: MemberRole,
): "default" | "secondary" | "outline" {
  switch (role) {
    case "owner":
      return "default";
    case "editor":
      return "secondary";
    default:
      return "outline";
  }
}

function avatarLetter(id: string): string {
  return (id[0] || "?").toUpperCase();
}

function leaseForMember(
  leases: FileLease[],
  memberId: string,
): FileLease | null {
  return leases.find((lease) => lease.holder_id === memberId) ?? null;
}

export function WorkspaceMembersPanel({
  workspaceId,
  currentMemberId,
  className,
}: WorkspaceMembersPanelProps) {
  const { t } = useI18n();
  const tr = t.remoteWorkspace.members;

  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [leases, setLeases] = useState<FileLease[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [newMemberId, setNewMemberId] = useState("");
  const [newMemberRole, setNewMemberRole] = useState<MemberRole>("viewer");
  const [adding, setAdding] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!workspaceId) {
      setMembers([]);
      setLeases([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [memberList, leaseList] = await Promise.all([
        listMembers(workspaceId),
        listLeases(workspaceId).catch(() => [] as FileLease[]),
      ]);
      setMembers(memberList);
      setLeases(leaseList);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const ownerMember = useMemo(
    () => members.find((m) => m.role === "owner") ?? null,
    [members],
  );
  // Only the current owner can manage roles / remove members. We
  // intentionally accept the owner's id from the membership list (not
  // the current actor) so the UI degrades gracefully if the backend
  // reports a different actor id.
  const canManage =
    Boolean(currentMemberId) &&
    (ownerMember?.member_id === currentMemberId ||
      members.some(
        (m) => m.member_id === currentMemberId && m.role === "owner",
      ));

  const handleAddMember = async () => {
    if (!workspaceId || !newMemberId.trim() || adding) return;
    setAdding(true);
    try {
      await addMember(workspaceId, newMemberId.trim(), newMemberRole);
      toast.success(tr.addMember);
      setNewMemberId("");
      setNewMemberRole("viewer");
      setAddDialogOpen(false);
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(tr.addFailed(message));
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveMember = async (memberId: string) => {
    if (!workspaceId || removingId) return;
    setRemovingId(memberId);
    try {
      await removeMember(workspaceId, memberId);
      toast.success(tr.title);
      await reload();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(tr.removeFailed(message));
    } finally {
      setRemovingId(null);
    }
  };

  const handleChangeRole = async (
    member: WorkspaceMember,
    nextRole: MemberRole,
  ) => {
    if (!workspaceId || member.role === nextRole) return;
    // Optimistic update — roll back on error.
    const previous = members;
    setMembers((prev) =>
      prev.map((m) =>
        m.member_id === member.member_id ? { ...m, role: nextRole } : m,
      ),
    );
    try {
      // The backend treats add as upsert, so re-adding with the new
      // role is the simplest path that avoids a dedicated PATCH
      // endpoint.
      await addMember(workspaceId, member.member_id, nextRole);
      toast.success(tr.title);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setMembers(previous);
      toast.error(tr.roleChangeFailed(message));
    }
  };

  return (
    <Card className={cn("gap-3 py-4", className)}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 px-4">
        <CardTitle className="text-sm">{tr.title}</CardTitle>
        {canManage && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setAddDialogOpen(true)}
          >
            {tr.addMember}
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-2 px-4">
        {error && (
          <div role="alert" className="text-xs text-destructive">
            {tr.addFailed(error)}
          </div>
        )}
        {loading && members.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            {tr.loading}
          </div>
        ) : members.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            {tr.empty}
          </div>
        ) : (
          <ul className="space-y-1">
            {members.map((member) => {
              const lease = leaseForMember(leases, member.member_id);
              const editingFile = lease?.file_path ?? null;
              const isRemoving = removingId === member.member_id;
              return (
                <li
                  key={member.member_id}
                  className="flex items-center gap-3 rounded-lg border border-border-subtle px-2 py-2"
                >
                  <Avatar className="size-7 rounded-full">
                    <AvatarFallback className="rounded-full bg-muted text-xs font-semibold text-muted-foreground">
                      {avatarLetter(member.member_id)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-xs font-medium text-foreground">
                        {member.member_id}
                      </span>
                      {canManage ? (
                        <Select
                          value={member.role}
                          onValueChange={(value) =>
                            handleChangeRole(member, value as MemberRole)
                          }
                        >
                          <SelectTrigger
                            size="sm"
                            aria-label={tr.changeRoleAria(member.member_id)}
                            className="h-6 w-[88px] border-transparent bg-transparent px-1 text-xs shadow-none hover:bg-muted/40"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLE_OPTIONS.map((role) => (
                              <SelectItem
                                key={role}
                                value={role}
                                className="text-xs"
                              >
                                {tr[roleLabelKey(role)]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge
                          variant={roleBadgeVariant(member.role)}
                          className="px-1.5 py-0 text-xs font-medium"
                        >
                          {tr[roleLabelKey(member.role)]}
                        </Badge>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {editingFile
                        ? tr.editingFile(editingFile)
                        : tr.editingNone}
                    </div>
                  </div>
                  {canManage && member.role !== "owner" && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={isRemoving}
                      onClick={() => handleRemoveMember(member.member_id)}
                      aria-label={tr.removeMemberAria(member.member_id)}
                      className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                    >
                      {isRemoving ? tr.loading : t.common.delete}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent
          showCloseButton
          className="w-[min(380px,calc(100vw-2rem))] gap-3 rounded-lg p-4 sm:max-w-[380px]"
        >
          <DialogHeader className="gap-1 text-left">
            <DialogTitle className="text-sm">
              {tr.addMember}
            </DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              {tr.addMemberPlaceholder}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <div className="flex flex-col gap-1.5">
              <Label
                htmlFor="new-member-id"
                className="text-xs text-muted-foreground"
              >
                {tr.addMemberPlaceholder}
              </Label>
              <Input
                id="new-member-id"
                value={newMemberId}
                onChange={(event) => setNewMemberId(event.target.value)}
                placeholder={tr.addMemberPlaceholder}
                autoComplete="off"
                className="h-8 text-xs"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label
                htmlFor="new-member-role"
                className="text-xs text-muted-foreground"
              >
                {t.remoteWorkspace.members.roleViewer}
              </Label>
              <Select
                value={newMemberRole}
                onValueChange={(value) =>
                  setNewMemberRole(value as MemberRole)
                }
              >
                <SelectTrigger
                  id="new-member-role"
                  size="sm"
                  className="h-8 text-xs"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((role) => (
                    <SelectItem
                      key={role}
                      value={role}
                      className="text-xs"
                    >
                      {tr[roleLabelKey(role)]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setAddDialogOpen(false)}
              disabled={adding}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleAddMember}
              disabled={!newMemberId.trim() || adding}
            >
              {adding ? tr.loading : tr.addMember}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
