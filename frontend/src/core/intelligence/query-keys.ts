import { currentActorId } from "@/core/auth/api";

/** Canonical React Query keys for account-owned intelligence data. */
export function intelligenceSubscriptionsQueryKey(actor = currentActorId()) {
  return ["intelligence", actor, "subscriptions"] as const;
}

export function intelligenceReportsQueryKey(actor = currentActorId()) {
  return ["intelligence", actor, "reports"] as const;
}
