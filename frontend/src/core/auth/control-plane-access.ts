import type { AuthStatus, User } from "./types";

const GLOBAL_SCOPES = new Set([
  "evolution:cross_tenant",
  "tenant:cross_tenant",
  "global:admin",
  "*",
]);

/** Operator-only control surfaces remain available in local auth-off mode. */
export function canAccessOperatorControlPlane(
  authStatus: AuthStatus | null,
  user: User | null,
): boolean {
  if (authStatus?.enabled === false) return true;
  if (!authStatus?.enabled || !user) return false;
  const roles = new Set(user.roles ?? []);
  return roles.has("admin") || roles.has("operator");
}

/** Cross-tenant process-global controls require an admin and a durable scope. */
export function canAccessGlobalControlPlane(
  authStatus: AuthStatus | null,
  user: User | null,
): boolean {
  if (authStatus?.enabled === false) return true;
  if (!authStatus?.enabled || !user?.roles?.includes("admin")) return false;
  const permissions = user.permissions ?? [];
  // Older auth payloads omitted scopes for an otherwise valid admin. Let the
  // backend make the final decision in that compatibility case; explicit
  // permission lists are authoritative when present.
  return (
    permissions.length === 0 ||
    permissions.some((permission) => GLOBAL_SCOPES.has(permission))
  );
}
