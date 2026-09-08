/** React Query keys for account management.
 *
 * Centralized query key definitions for cache invalidation.
 */

import { currentActorId } from "@/core/auth/api";

const actorSegment = () => currentActorId();

export const queryKeys = {
  // Profile
  profile: () => ["account", actorSegment(), "profile"] as const,

  // Linked accounts
  linkedAccounts: () => ["account", actorSegment(), "linked-accounts"] as const,

  // Privacy
  privacy: () => ["account", actorSegment(), "privacy"] as const,

  // Subscription
  subscription: () => ["account", actorSegment(), "subscription"] as const,
  plans: (includeInactive = false) =>
    ["account", "plans", { includeInactive }] as const,

  // Usage
  usage: () => ["account", actorSegment(), "usage"] as const,
  usageEvents: (params?: { limit?: number; event_type?: string }) =>
    ["account", actorSegment(), "usage", "events", params] as const,
  usageSummary: (period?: string) =>
    ["account", actorSegment(), "usage", "summary", period] as const,

  // Billing
  billingSummary: () =>
    ["account", actorSegment(), "billing", "summary"] as const,
  billingHistory: (limit?: number) =>
    ["account", actorSegment(), "billing", "history", { limit }] as const,

  // Overview
  overview: () => ["account", actorSegment(), "overview"] as const,
};
