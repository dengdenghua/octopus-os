export function normalizeEmailVerificationCode(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, 6);
}

export function remainingCooldownSeconds(deadline: number, now = Date.now()) {
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}
