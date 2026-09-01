/** Account management types.
 *
 * Type definitions for user profiles, subscriptions, usage tracking,
 * and billing.
 */

export type SubscriptionTier = "free" | "basic" | "pro" | "enterprise";

export type LoginMethod = "password" | "google" | "github" | "microsoft";

export type UsageEventType =
  | "chat"
  | "completion"
  | "embedding"
  | "image_generation"
  | "code_execution"
  | "agent_run"
  | "workflow_run";

export type BillingStatus =
  | "pending"
  | "paid"
  | "failed"
  | "refunded"
  | "cancelled";

export type InvoiceType = "subscription" | "usage" | "bonus" | "refund";

// Profile
export interface LinkedAccount {
  provider: LoginMethod;
  email?: string;
  linked_at: string;
  is_primary: boolean;
}

export interface UserProfile {
  user_id: string;
  username: string;
  display_name?: string;
  email?: string;
  avatar_url?: string;
  bio?: string;
  timezone: string;
  language: string;
  linked_accounts: LinkedAccount[];
  privacy_mode: boolean;
  data_collection_consent: boolean;
  marketing_emails: boolean;
  created_at: string;
  updated_at: string;
}

export interface UpdateProfileRequest {
  display_name?: string;
  bio?: string;
  timezone?: string;
  language?: string;
  avatar_url?: string;
}

// Privacy
export interface PrivacySettings {
  privacy_mode: boolean;
  data_collection_consent: boolean;
  share_usage_analytics: boolean;
  allow_community_features: boolean;
  public_profile: boolean;
}

// Subscription
export interface PlanQuota {
  monthly_requests: number;
  monthly_tokens?: number;
  max_agents: number;
  max_workflows: number;
  max_memory_items: number;
  max_file_storage_mb: number;
  max_team_members?: number;
  priority_support: boolean;
  advanced_features: string[];
}

export interface SubscriptionPlan {
  plan_id: string;
  name: string;
  tier: SubscriptionTier;
  description: string;
  price_monthly: string;
  price_yearly?: string;
  currency: string;
  quota: PlanQuota;
  features: string[];
  is_active: boolean;
  created_at: string;
}

export interface UserSubscription {
  subscription_id: string;
  user_id: string;
  plan_id: string;
  tier: SubscriptionTier;
  status: string;
  started_at: string;
  expires_at?: string;
  auto_renew: boolean;
  payment_method_id?: string;
  bonus_balance: string;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionUsage {
  user_id: string;
  plan_id: string;
  period_start: string;
  period_end: string;
  requests_used: number;
  requests_limit: number;
  tokens_used?: number;
  tokens_limit?: number;
  cost_incurred: string;
  bonus_used: string;
  requests_remaining: number;
  usage_percentage: number;
}

// Usage
export interface UsageEvent {
  event_id: string;
  user_id: string;
  event_type: UsageEventType;
  product?: string;
  model?: string;
  cost: string;
  tokens_input?: number;
  tokens_output?: number;
  metadata: Record<string, unknown>;
  source?: string;
  created_at: string;
}

export interface UsageSummary {
  user_id: string;
  period: string;
  total_cost: string;
  total_requests: number;
  total_tokens: number;
  by_model: Record<string, { count: number; cost: string }>;
  by_event_type: Record<string, number>;
  most_used_model?: string;
}

// Billing
export interface BillingRecord {
  record_id: string;
  user_id: string;
  invoice_type: InvoiceType;
  description: string;
  amount: string;
  currency: string;
  status: BillingStatus;
  period_start?: string;
  period_end?: string;
  payment_method?: string;
  transaction_id?: string;
  invoice_url?: string;
  metadata: Record<string, unknown>;
  created_at: string;
  paid_at?: string;
}

export interface BillingSummary {
  user_id: string;
  current_balance: string;
  bonus_balance: string;
  current_period_cost: string;
  last_invoice_date?: string;
  last_invoice_amount?: string;
  next_billing_date?: string;
}

export interface BillingHistoryResponse {
  data: BillingRecord[];
  pagination?: {
    total: number;
    page: number;
    page_size: number;
    total_pages: number;
  };
}

// Overview
export interface AccountOverview {
  profile: UserProfile;
  subscription: UserSubscription | null;
  usage: SubscriptionUsage | null;
  billing_summary: BillingSummary;
  recent_usage: UsageEvent[];
  recent_billing: BillingRecord[];
}
