/** Account management React hooks.
 *
 * Provides React Query hooks for account data fetching and mutations.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useI18n } from "@/core/i18n/hooks";

import { accountApi } from "./api";
import { queryKeys } from "./query-keys";

import type {
  AccountOverview,
  BillingRecord,
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

// Profile hooks
export function useProfile() {
  return useQuery<UserProfile, Error>({
    queryKey: queryKeys.profile(),
    queryFn: () => accountApi.getProfile(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    // Local/community deployments may intentionally omit the optional
    // profile service. Fail fast so Settings can render the authenticated
    // account fallback instead of showing a retrying skeleton for seconds.
    retry: false,
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation<UserProfile, Error, UpdateProfileRequest>({
    mutationFn: (data) => accountApi.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profile() });
      queryClient.invalidateQueries({ queryKey: queryKeys.overview() });
      toast.success(t.accountSettings.profileUpdated);
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

export function useUploadAvatar() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation<{ success: boolean; avatar_url: string }, Error, File>({
    mutationFn: (file) => accountApi.uploadAvatar(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profile() });
      toast.success(t.accountSettings.avatarUploaded);
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

// Linked accounts hooks
export function useLinkedAccounts() {
  return useQuery<LinkedAccount[], Error>({
    queryKey: queryKeys.linkedAccounts(),
    queryFn: () => accountApi.getLinkedAccounts(),
  });
}

export function useLinkAccount() {
  const queryClient = useQueryClient();

  return useMutation<
    UserProfile,
    Error,
    { provider: string; email?: string; is_primary?: boolean }
  >({
    mutationFn: (data) => accountApi.linkAccount(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.linkedAccounts() });
      queryClient.invalidateQueries({ queryKey: queryKeys.profile() });
      toast.success("Account linked successfully");
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

export function useUnlinkAccount() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation<UserProfile, Error, string>({
    mutationFn: (provider) => accountApi.unlinkAccount(provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.linkedAccounts() });
      queryClient.invalidateQueries({ queryKey: queryKeys.profile() });
      toast.success(t.accountSettings.accountUnlinked);
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

// Privacy settings hooks
export function usePrivacySettings() {
  return useQuery<PrivacySettings, Error>({
    queryKey: queryKeys.privacy(),
    queryFn: () => accountApi.getPrivacySettings(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    retry: false,
  });
}

export function useUpdatePrivacySettings() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation<PrivacySettings, Error, Partial<PrivacySettings>>({
    mutationFn: (data) => accountApi.updatePrivacySettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.privacy() });
      toast.success(t.accountSettings.privacyUpdated);
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

// Subscription hooks
export function useSubscription() {
  return useQuery<UserSubscription | null, Error>({
    queryKey: queryKeys.subscription(),
    queryFn: () => accountApi.getSubscription(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    retry: false,
  });
}

export function usePlans(includeInactive = false) {
  return useQuery<SubscriptionPlan[], Error>({
    queryKey: queryKeys.plans(includeInactive),
    queryFn: () => accountApi.getPlans(includeInactive),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useSubscribe() {
  const queryClient = useQueryClient();

  return useMutation<
    UserSubscription,
    Error,
    { planId: string; autoRenew?: boolean }
  >({
    mutationFn: ({ planId, autoRenew }) =>
      accountApi.subscribe(planId, autoRenew),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.subscription() });
      queryClient.invalidateQueries({ queryKey: queryKeys.usage() });
      queryClient.invalidateQueries({ queryKey: queryKeys.overview() });
      toast.success("Subscription updated successfully");
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

export function useCancelSubscription() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

  return useMutation<UserSubscription, Error>({
    mutationFn: () => accountApi.cancelSubscription(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.subscription() });
      toast.success(t.subscriptionSettings.cancelled);
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });
}

// Usage hooks
export function useUsage() {
  return useQuery<SubscriptionUsage | null, Error>({
    queryKey: queryKeys.usage(),
    queryFn: () => accountApi.getUsage(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useUsageEvents(params?: {
  limit?: number;
  event_type?: string;
}) {
  return useQuery<UsageEvent[], Error>({
    queryKey: queryKeys.usageEvents(params),
    queryFn: async () => {
      const response = await accountApi.getUsageEvents(params);
      return response.data ?? [];
    },
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useUsageSummary(period?: string) {
  return useQuery<UsageSummary, Error>({
    queryKey: queryKeys.usageSummary(period),
    queryFn: () => accountApi.getUsageSummary(period),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

// Billing hooks
export function useBillingSummary() {
  return useQuery<BillingSummary, Error>({
    queryKey: queryKeys.billingSummary(),
    queryFn: () => accountApi.getBillingSummary(),
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

export function useBillingHistory(limit = 20) {
  return useQuery<BillingRecord[], Error>({
    queryKey: queryKeys.billingHistory(limit),
    queryFn: async () => {
      const response = await accountApi.getBillingHistory(limit);
      return response.data;
    },
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

// Overview hook
export function useAccountOverview() {
  return useQuery<AccountOverview, Error>({
    queryKey: queryKeys.overview(),
    queryFn: () => accountApi.getOverview(),
  });
}
