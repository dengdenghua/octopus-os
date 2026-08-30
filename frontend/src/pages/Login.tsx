import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, auth } from "@/lib/api";
import { Card, ErrorHint, Spinner } from "@/components/Card";
import { swallow } from "@/core/utils/log";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n";
import { classNames, short } from "@/lib/format";
import type { AuthProvidersResponse } from "@/types/api";

type Providers = AuthProvidersResponse["providers"];

export default function Login() {
  const nav = useNavigate();
  const { t } = useI18n();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"molili" | "local">("molili");

  useEffect(() => {
    if (auth.isAuthed()) nav("/", { replace: true });
  }, [nav]);

  useEffect(() => {
    api.auth
      .providers()
      .then((r) => {
        setProviders(r.providers);
        // Implementation note.
        if (r.providers.length === 1) {
          setTab(r.providers[0].id);
        } else if (r.providers.some((p) => p.id === "molili")) {
          setTab("molili");
        } else if (r.providers.some((p) => p.id === "local")) {
          setTab("local");
        }
      })
      .catch((e) => setErr((e as Error).message));
  }, []);

  const hasMolili = providers?.some((p) => p.id === "molili") ?? false;
  const hasLocal = providers?.some((p) => p.id === "local") ?? false;
  const molili = providers?.find((p) => p.id === "molili") as
    | { id: "molili"; mock_mode: boolean }
    | undefined;

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-900 p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="text-6xl mb-3">🐙</div>
          <h1 className="text-2xl font-semibold text-white">Echo Agent</h1>
          <p className="text-sm text-mute mt-1">{t.loginPage.appSubtitle}</p>
        </div>

        {!providers && !err && (
          <Spinner label={t.loginPage.checkingProviders} />
        )}
        {err && <ErrorHint error={err} />}
        {providers && providers.length === 0 && (
          <Card>
            <div className="text-sm text-warn">
              {t.loginPage.noProvidersTitle}
              <br />
              <span className="text-mute mt-2 block text-xs">
                {t.loginPage.noProvidersHint}
              </span>
            </div>
          </Card>
        )}

        {providers && providers.length > 0 && (
          <Card>
            {providers.length > 1 && (
              <div className="flex gap-1 mb-4 p-1 bg-ink-900 rounded border border-ink-700">
                {hasMolili && (
                  <button
                    className={classNames(
                      "flex-1 py-1.5 rounded text-sm transition-colors",
                      tab === "molili"
                        ? "bg-cephalo-700/40 text-white"
                        : "text-mute hover:text-slate-300",
                    )}
                    onClick={() => setTab("molili")}
                  >
                    {t.loginPage.tabPhone}
                  </button>
                )}
                {hasLocal && (
                  <button
                    className={classNames(
                      "flex-1 py-1.5 rounded text-sm transition-colors",
                      tab === "local"
                        ? "bg-cephalo-700/40 text-white"
                        : "text-mute hover:text-slate-300",
                    )}
                    onClick={() => setTab("local")}
                  >
                    {t.loginPage.tabLocal}
                  </button>
                )}
              </div>
            )}

            {tab === "molili" && hasMolili && (
              <SmsLoginForm mockMode={molili?.mock_mode ?? false} />
            )}
            {tab === "local" && hasLocal && <LocalLoginForm />}
          </Card>
        )}

        <p className="text-[11px] text-mute text-center mt-6 leading-relaxed">
          {t.loginPage.termsPrefix}{" "}
          <a href="#" className="text-cephalo-300 hover:underline">
            {t.loginPage.termsLink}
          </a>
          <br />
          {t.loginPage.jwtNote}
        </p>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Implementation note.
// ═══════════════════════════════════════════════════════════

function SmsLoginForm({ mockMode }: { mockMode: boolean }) {
  const nav = useNavigate();
  const { t } = useI18n();
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [devHint, setDevHint] = useState<string | null>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const clean = phone.replace(/\D/g, "");
  const phoneOk = clean.length >= 11;
  const codeOk = code.trim().length >= 4;

  const send = async () => {
    if (!phoneOk || cooldown > 0) return;
    setErr(null);
    setDevHint(null);
    setLoading(true);
    try {
      const r = await api.molili.smsSend(clean);
      setStep("code");
      setCooldown(60);
      // Implementation note.
      if (mockMode && r.upstream?.code_for_dev) {
        setDevHint(t.loginPage.mockCodeLabel(r.upstream.code_for_dev));
      } else if (mockMode) {
        setDevHint(t.loginPage.mockServerLog);
      }
    } catch (e) {
      swallow(e);
      setErr(explain(e, t));
    } finally {
      setLoading(false);
    }
  };

  const verify = async () => {
    if (!codeOk) return;
    setErr(null);
    setLoading(true);
    try {
      const resp = await api.molili.smsVerify(clean, code.trim());
      if (!resp.success || !resp.access_token) {
        throw new Error("login failed: no token returned");
      }
      auth.set({
        jwt: resp.access_token,
        actor_id: resp.actor_id,
        provider: "molili",
        mobile: resp.user?.mobile ?? phone,
        display: resp.user?.mobile ?? phone,
      });
      nav("/", { replace: true });
    } catch (e) {
      swallow(e);
      setErr(explain(e, t));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {mockMode && (
        <div className="mb-3 p-2 rounded bg-warn/10 border border-warn/30 text-warn text-[11px]">
          {t.loginPage.mockModeBanner}
        </div>
      )}
      {step === "phone" && (
        <>
          <label className="block text-xs text-slate-300 mb-1">
            {t.loginPage.phoneLabel}
          </label>
          <input
            className="input w-full text-base"
            placeholder="13800001234"
            value={phone}
            autoFocus
            onChange={(e) => setPhone(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && phoneOk && send()}
            inputMode="tel"
            autoComplete="tel"
          />
          <button
            className="btn btn-primary w-full mt-4 justify-center text-base py-2.5"
            disabled={!phoneOk || loading || cooldown > 0}
            onClick={send}
          >
            {loading
              ? t.loginPage.sending
              : cooldown > 0
                ? t.loginPage.resendInSec(cooldown)
                : t.loginPage.getCode}
          </button>
        </>
      )}

      {step === "code" && (
        <>
          <div className="text-xs text-mute mb-3">
            {t.loginPage.sentToPrefix}{" "}
            <span className="text-slate-300 font-mono">{mask(clean)}</span> ·{" "}
            <button
              className="text-cephalo-300 hover:underline disabled:text-mute disabled:no-underline"
              onClick={send}
              disabled={cooldown > 0}
            >
              {cooldown > 0
                ? t.loginPage.resendInSec(cooldown)
                : t.loginPage.resend}
            </button>
          </div>
          {devHint && (
            <div className="mb-3 p-2 rounded bg-cephalo-700/20 border border-cephalo-600/40 text-[11px] font-mono text-cephalo-200">
              {devHint}
            </div>
          )}
          <label className="block text-xs text-slate-300 mb-1">
            {t.loginPage.codeLabel}
          </label>
          <input
            className="input w-full text-lg font-mono tracking-widest text-center"
            placeholder="••••••"
            value={code}
            autoFocus
            maxLength={8}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && codeOk && verify()}
            inputMode="numeric"
            autoComplete="one-time-code"
          />
          <div className="flex gap-2 mt-4">
            <button
              className="btn flex-1 justify-center"
              disabled={loading}
              onClick={() => {
                setCode("");
                setStep("phone");
                setDevHint(null);
              }}
            >
              {t.loginPage.backButton}
            </button>
            <button
              className="btn btn-primary flex-1 justify-center text-base py-2.5"
              disabled={!codeOk || loading}
              onClick={verify}
            >
              {loading ? t.loginPage.verifying : t.loginPage.loginButton}
            </button>
          </div>
        </>
      )}

      {err && (
        <div className="mt-3">
          <ErrorHint error={err} />
        </div>
      )}
      {loading && !err && !devHint && (
        <Spinner label={t.loginPage.spinnerWait} />
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════
// Implementation note.
// ═══════════════════════════════════════════════════════════

function LocalLoginForm() {
  const nav = useNavigate();
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [display, setDisplay] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const ok = /^[A-Za-z0-9._@-]{1,64}$/.test(username.trim());

  const submit = async () => {
    if (!ok) return;
    setErr(null);
    setLoading(true);
    try {
      const r = await api.auth.localLogin(
        username.trim(),
        display.trim() || undefined,
      );
      if (!r.success || !r.access_token) {
        throw new Error("login failed: no token");
      }
      auth.set({
        jwt: r.access_token,
        actor_id: r.actor_id,
        provider: "local",
        display: r.user?.display_name || r.user?.username || username.trim(),
      });
      nav("/", { replace: true });
    } catch (e) {
      swallow(e);
      setErr(explain(e, t));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="mb-3 p-2 rounded bg-warn/10 border border-warn/30 text-warn text-[11px]">
        {t.loginPage.localBanner}
      </div>
      <label className="block text-xs text-slate-300 mb-1">
        {t.loginPage.usernameLabel}
      </label>
      <input
        className="input w-full text-base"
        placeholder="alice"
        value={username}
        autoFocus
        maxLength={64}
        onChange={(e) => setUsername(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && ok && submit()}
      />
      <label className="block text-xs text-slate-300 mb-1 mt-3">
        {t.loginPage.displayNameLabel}
      </label>
      <input
        className="input w-full"
        placeholder="Alice"
        value={display}
        maxLength={128}
        onChange={(e) => setDisplay(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && ok && submit()}
      />
      <button
        className="btn btn-primary w-full mt-4 justify-center text-base py-2.5"
        disabled={!ok || loading}
        onClick={submit}
      >
        {loading ? t.loginPage.loggingIn : t.loginPage.loginButton}
      </button>
      {err && (
        <div className="mt-3">
          <ErrorHint error={err} />
        </div>
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════
// Utils
// ═══════════════════════════════════════════════════════════

function mask(phone: string): string {
  if (phone.length < 7) return phone;
  return phone.slice(0, 3) + "****" + phone.slice(-4);
}

function explain(e: unknown, t: Translations): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return t.loginPage.errorServiceDisabled;
    if (e.status === 502) return t.loginPage.errorUpstream;
    if (e.status === 401) return t.loginPage.errorCodeInvalid;
    if (e.status === 403) return t.loginPage.errorNotInWhitelist;
    if (e.status === 400) return short(e.body || e.message, 140);
    return `${e.status}: ${short(e.body || e.message, 120)}`;
  }
  return (e as Error).message || String(e);
}
