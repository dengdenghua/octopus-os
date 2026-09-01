/** Account management API client.
 *
 * Provides methods for managing user profiles, subscriptions, usage tracking,
 * and billing history.
 */

import { getBackendBaseURL } from "@/core/config";
import { apiClient } from "@/core/api";
import { authHeaders } from "@/core/auth/api";

import type {
  AccountOverview,
  BillingHistoryResponse,
  BillingSummary,
  LinkedAccount,
  PrivacySettings,
  SubscriptionPlan,
  SubscriptionUsage,
  UpdateProfileRequest,
  UsageEvent,
  UsageSummary,
  UserProfile,
  UserSubscription,
} from "./types";

export const accountApi = {
  // Profile
  getProfile: () => apiClient.get<UserProfile>("/api/account/profile"),

  updateProfile: (data: UpdateProfileRequest) =>
    apiClient.patch<UserProfile>("/api/account/profile", data),

  uploadAvatar: async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const resp = await fetch(`${getBackendBaseURL()}/api/account/avatar`, {
      method: "POST",
      // Auth owns the token storage contract. In particular, current sessions
      // live in sessionStorage under `echo_auth_token`; never manufacture an
      // empty Authorization header because that also prevents the global fetch
      // interceptor from attaching a valid session token.
      headers: authHeaders(),
      body: formData,
    });
    if (!resp.ok) throw new Error(`Upload avatar failed: ${resp.status}`);
    return resp.json() as Promise<{ success: boolean; avatar_url: string }>;
  },

  // Linked Accounts
  getLinkedAccounts: () =>
    apiClient.get<LinkedAccount[]>("/api/account/linked-accounts"),

  linkAccount: (data: {
    provider: string;
    email?: string;
    is_primary?: boolean;
  }) => apiClient.post<UserProfile>("/api/account/linked-accounts", data),

  unlinkAccount: (provider: string) =>
    apiClient.delete<UserProfile>(`/api/account/linked-accounts/${provider}`),

  // Privacy Settings
  getPrivacySettings: () =>
    apiClient.get<PrivacySettings>("/api/account/privacy"),

  updatePrivacySettings: (data: Partial<PrivacySettings>) =>
    apiClient.patch<PrivacySettings>("/api/account/privacy", data),

  // Subscription
  getSubscription: () =>
    apiClient.get<UserSubscription | null>("/api/account/subscription"),

  getPlans: (includeInactive = false) => {
    const params = new URLSearchParams();
    if (includeInactive) params.append("include_inactive", "true");
    const query = params.toString();
    return apiClient.get<SubscriptionPlan[]>(
      `/api/account/plans${query ? `?${query}` : ""}`,
    );
  },

  subscribe: (planId: string, autoRenew = true) =>
    apiClient.post<UserSubscription>("/api/account/subscribe", {
      plan_id: planId,
      auto_renew: autoRenew,
    }),

  cancelSubscription: () =>
    apiClient.post<UserSubscription>("/api/account/cancel"),

  // Usage
  getUsage: () => apiClient.get<SubscriptionUsage | null>("/api/account/usage"),

  getUsageEvents: (params?: { limit?: number; event_type?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.append("limit", String(params.limit));
    if (params?.event_type)
      searchParams.append("event_type", params.event_type);
    const query = searchParams.toString();
    return apiClient.get<{
      data: UsageEvent[];
      pagination?: { total: number };
    }>(`/api/account/usage/events${query ? `?${query}` : ""}`);
  },

  getUsageSummary: (period?: string) => {
    const query = period ? `?period=${encodeURIComponent(period)}` : "";
    return apiClient.get<UsageSummary>(`/api/account/usage/summary${query}`);
  },

  // Billing
  getBillingSummary: () =>
    apiClient.get<BillingSummary>("/api/account/billing"),

  getBillingHistory: (limit = 20) =>
    apiClient.get<BillingHistoryResponse>(
      `/api/account/billing/history?limit=${limit}`,
    ),

  getInvoice: (invoiceId: string) =>
    apiClient.get<BillingHistoryResponse["data"][0]>(
      `/api/account/billing/invoices/${invoiceId}`,
    ),

  // Overview
  getOverview: () => apiClient.get<AccountOverview>("/api/account/overview"),
};
