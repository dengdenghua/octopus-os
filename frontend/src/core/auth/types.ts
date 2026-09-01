export interface AuthStatus {
  enabled: boolean;
  jwt_available: boolean;
  allow_registration: boolean;
  exempt_paths: string[];
}

export interface LoginRequest {
  username: string;
  password?: string;
}

export interface LoginResponse {
  success: boolean;
  access_token?: string;
  token_type: string;
  expires_in: number;
  user?: User;
}

export interface RegisterRequest {
  username: string;
  password: string;
  email?: string;
}

export interface User {
  user_id: string;
  username: string;
  email?: string;
  roles?: string[];
  permissions?: string[];
  is_active?: boolean;
  created_at?: string;
  last_login?: string;
  is_guest?: boolean;
  actor_id?: string;
  mobile?: string;
  provider?: string;
}

export interface APIKey {
  key_id: string;
  name: string;
  key?: string;
  scopes: string[];
  rate_limit: number;
  expires_at?: string;
  created_at: string;
  last_used?: string;
  is_active: boolean;
}

export interface Session {
  session_id: string;
  user_id: string;
  auth_method: string;
  created_at: string;
  expires_at: string;
  last_activity: string;
  ip_address?: string;
}
