/* Implementation note. */

export {
  CollabProvider,
  useCollab,
  useOptionalCollab,
  type TeamTaskProgressEvent,
} from "./collab-provider";
export { PresenceAvatars } from "./presence-avatars";
export { InviteDialog, type InviteDialogProps } from "./invite-dialog";
export {
  GroupHumanInviteButton,
  type GroupHumanInviteButtonProps,
} from "./group-human-invite-button";
export { TeamMembersDialog } from "./team-members-dialog";
export { CreateTaskDialog } from "./create-task-dialog";
export { TeamTasksPanel } from "./team-tasks-panel";
export {
  CoworkRoomMessageActions,
  buildCoworkMessageProjectActionInput,
  type CoworkMilestoneOption,
  type CoworkRoomMessageActionsProps,
} from "./cowork-room-message-actions";
export {
  CoworkRoomSystemCard,
  getCoworkRoomSystemCard,
  isCoworkRoomSystemMessage,
  type CoworkRoomSystemCardProps,
} from "./cowork-room-system-card";
export {
  CoworkRoomTimeline,
  CoworkRoomTimelineEntry,
  dedupeCoworkRoomMessages,
  type CoworkRoomMessageActionOptions,
  type CoworkRoomTimelineEntryProps,
  type CoworkRoomTimelineProps,
} from "./cowork-room-timeline";
export {
  TeamWorkbenchPanel,
  type TeamWorkbenchTabId,
} from "./team-workbench-panel";
