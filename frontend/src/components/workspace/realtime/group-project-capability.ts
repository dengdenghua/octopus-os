export type GroupProjectCapabilityAction = "create" | "open" | null;
export type GroupProjectDetachOutcome =
  | "detached"
  | "cancelled"
  | "binding-changed"
  | "failed";

/** Resolve the single project action shown in the shared group composer.
 * Project planning is durable thread state, never a per-turn task strategy. */
export function resolveGroupProjectCapabilityAction(input: {
  isNewThread: boolean;
  isGroupConversation: boolean;
  hasBoundProject: boolean;
  canManageGroup: boolean;
}): GroupProjectCapabilityAction {
  if (input.isNewThread || !input.isGroupConversation) return null;
  if (input.hasBoundProject) return "open";
  return input.canManageGroup ? "create" : null;
}

function projectBindingErrorCode(error: unknown): string | null {
  if (!error || typeof error !== "object") return null;
  const status = "status" in error ? error.status : null;
  const code = "code" in error ? error.code : null;
  return status === 409 && typeof code === "string" ? code : null;
}

/** Execute the guarded two-stage detach lifecycle.
 *
 * Both attempts use the same expected project id, so an old UI can never
 * detach a newer concurrent binding. Active work requires a second, explicit
 * force confirmation; any other failure stops immediately.
 */
export async function detachGroupProjectCapability(input: {
  expectedProjectId: string;
  requestDetach: (options: {
    expectedProjectId: string;
    force: boolean;
  }) => Promise<unknown>;
  confirmForce: () => Promise<boolean>;
}): Promise<GroupProjectDetachOutcome> {
  const request = (force: boolean) =>
    input.requestDetach({
      expectedProjectId: input.expectedProjectId,
      force,
    });

  try {
    await request(false);
    return "detached";
  } catch (error) {
    const code = projectBindingErrorCode(error);
    if (code === "PROJECT_BINDING_CHANGED") return "binding-changed";
    if (code !== "PROJECT_ACTIVE") return "failed";
  }

  if (!(await input.confirmForce())) return "cancelled";

  try {
    await request(true);
    return "detached";
  } catch (error) {
    return projectBindingErrorCode(error) === "PROJECT_BINDING_CHANGED"
      ? "binding-changed"
      : "failed";
  }
}
