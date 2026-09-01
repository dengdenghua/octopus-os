import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  getAuthStatus,
  getMe,
  getToken,
  getUser as getStoredUser,
  login as loginApi,
  logout as logoutApi,
  refreshToken,
  register as registerApi,
  _writeToken,
  _clearTokens,
} from "@/core/auth/api";
import { octAuthApi } from "@/core/oct/api";
import { AUTH_EXPIRED_EVENT } from "@/core/auth/fetch-interceptor";

import { swallow } from "@/core/utils/log";
import type {
  AuthStatus,
  LoginRequest,
  RegisterRequest,
  User,
} from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

const GUEST_USER_ID = "__guest__";
const ANONYMOUS_USER_ID = "__anonymous__";

function isPlaceholderUsername(username?: string | null): boolean {
  const value = username?.trim().toLowerCase();
  return !value || value === "anonymous" || value === ANONYMOUS_USER_ID;
}

function isPlaceholderUserId(userId?: string | null): boolean {
  const value = userId?.trim().toLowerCase();
  return !value || value === "anonymous" || value === ANONYMOUS_USER_ID;
}

function userFromJwt(token: string | null): Partial<User> | null {
  if (!token || token === GUEST_USER_ID || token.split(".").length !== 3) {
    return null;
  }
  try {
    const tokenPayload = token.split(".")[1];
    if (!tokenPayload) return null;
    const rawPayload = tokenPayload.replace(/-/g, "+").replace(/_/g, "/");
    const payload = rawPayload.padEnd(
      Math.ceil(rawPayload.length / 4) * 4,
      "=",
    );
    const json = JSON.parse(window.atob(payload)) as {
      sub?: string;
      mobile?: string;
      provider?: string;
    };
    const actorId = json.sub;
    const mobile = json.mobile;
    if (!actorId && !mobile) return null;
    return {
      user_id: actorId || mobile,
      actor_id: actorId,
      username: mobile || actorId || "EchoAI",
      mobile,
      provider: json.provider,
    };
  } catch (e) {
    swallow(e);
    return null;
  }
}

function normalizeUserIdentity(
  incoming: User,
  fallback?: Partial<User> | null,
  fallbackMobile?: string,
): User {
  const mobile = incoming.mobile || fallback?.mobile || fallbackMobile;
  const actorId = incoming.actor_id || fallback?.actor_id;
  const incomingUserId = !isPlaceholderUserId(incoming.user_id)
    ? incoming.user_id
    : undefined;
  const fallbackUserId = !isPlaceholderUserId(fallback?.user_id)
    ? fallback?.user_id
    : undefined;
  const userId =
    incomingUserId || actorId || fallbackUserId || mobile || ANONYMOUS_USER_ID;
  const fallbackUsername =
    fallback?.username && !isPlaceholderUsername(fallback.username)
      ? fallback.username
      : undefined;
  const username = isPlaceholderUsername(incoming.username)
    ? mobile ||
      incoming.email ||
      fallbackUsername ||
      incoming.username ||
      userId
    : incoming.username;

  return {
    ...fallback,
    ...incoming,
    user_id: userId,
    username,
    ...(actorId ? { actor_id: actorId } : {}),
    ...(mobile ? { mobile } : {}),
  };
}

interface AuthContextType {
  isLoading: boolean;
  authError: Error | null;
  authStatus: AuthStatus | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (request: LoginRequest) => Promise<void>;
  emailLogin: (email: string, code: string) => Promise<void>;
  /** Deprecated: guest mode is disabled when auth is enabled. */
  guestLogin: () => Promise<void>;
  register: (request: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  retryAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const [isLoading, setIsLoading] = useState(true);
  const [authError, setAuthError] = useState<Error | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const initializedRef = useRef(false);

  const isAuthenticated =
    !!user && !isPlaceholderUserId(user.user_id) && !user.is_guest;

  const initAuth = useCallback(async () => {
    setIsLoading(true);
    setAuthError(null);
    const token = getToken();
    const storedUser = getStoredUser();
    const tokenUser = userFromJwt(token);
    const localUser = storedUser || tokenUser;
    try {
      if (token === GUEST_USER_ID || storedUser?.is_guest) {
        _clearTokens();
        setUser(null);
      } else if (token && localUser && !localUser.is_guest) {
        setUser(normalizeUserIdentity(localUser as User, tokenUser));
      } else {
        setUser(null);
      }
      let status: AuthStatus;
      try {
        status = await getAuthStatus();
        setAuthStatus(status);
      } catch (error) {
        const unavailable =
          error instanceof Error
            ? error
            : new Error("Authentication service is unavailable");
        swallow(unavailable);
        setAuthStatus(null);
        setAuthError(unavailable);
        return;
      }
      // A browser restart intentionally has no sessionStorage JWT.  The
      // HttpOnly cookie is the durable credential, so always ask the backend
      // for the current actor when authentication is enabled.
      const current = status.enabled
        ? await getMe()
            .then((currentUser) => ({ currentUser, error: null }))
            .catch((error: unknown) => ({ currentUser: null, error }))
        : { currentUser: null, error: null };
      if (current.currentUser) {
        setUser(
          normalizeUserIdentity(current.currentUser, storedUser || tokenUser),
        );
      } else if (current.error) {
        swallow(current.error);
        const msg = current.error instanceof Error ? current.error.message : "";
        if (/401|Unauthorized/i.test(msg)) {
          _clearTokens();
          setUser(null);
        }
      }
    } catch (e) {
      swallow(e);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    initAuth();
  }, [initAuth]);

  useEffect(() => {
    const expire = () => {
      _clearTokens();
      setUser(null);
      // HttpOnly cookies cannot be cleared from JavaScript.  The logout route
      // is deliberately callable even when the old JWT has expired.
      void fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      }).catch(() => undefined);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, expire);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expire);
  }, []);

  const login = useCallback(async (request: LoginRequest) => {
    const response = await loginApi(request);
    if (response.user) {
      const normalized = normalizeUserIdentity(response.user);
      if (response.access_token) _writeToken(response.access_token, normalized);
      setUser(normalized);
    }
  }, []);

  const emailLogin = useCallback(async (email: string, code: string) => {
    // oct 账号网关:邮箱验证码登录 → agent 自有会话 JWT
    const response = await octAuthApi.emailLogin(email, code);
    if (response.user) {
      const normalized = normalizeUserIdentity(
        response.user as unknown as User,
        null,
        email,
      );
      if (response.access_token) _writeToken(response.access_token, normalized);
      setUser(normalized);
    }
  }, []);

  const guestLogin = useCallback(async () => {
    _clearTokens();
    setUser(null);
    throw new Error(t.auth.notLoggedIn);
  }, [t.auth.notLoggedIn]);

  const register = useCallback(async (request: RegisterRequest) => {
    const newUser = await registerApi(request);
    setUser(newUser);
  }, []);

  const logout = useCallback(async () => {
    await logoutApi();
    _clearTokens();
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    const response = await refreshToken();
    if (response.user) {
      setUser((previous) => {
        const normalized = normalizeUserIdentity(response.user!, previous);
        if (response.access_token)
          _writeToken(response.access_token, normalized);
        return normalized;
      });
    }
  }, []);

  const value = useMemo(
    () => ({
      isLoading,
      authError,
      authStatus,
      user,
      isAuthenticated,
      login,
      emailLogin,
      guestLogin,
      register,
      logout,
      refresh,
      retryAuth: initAuth,
    }),
    [
      isLoading,
      authError,
      authStatus,
      user,
      isAuthenticated,
      login,
      emailLogin,
      guestLogin,
      register,
      logout,
      refresh,
      initAuth,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

/**
 * Read authentication when a reusable leaf component may render outside the
 * application shell (tests, stories, previews). Missing context means no
 * control-plane privileges; it must never grant guest access implicitly.
 */
export function useOptionalAuth() {
  return useContext(AuthContext);
}
