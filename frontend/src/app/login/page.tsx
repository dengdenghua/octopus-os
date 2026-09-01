import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowRightIcon,
  FingerprintIcon,
  KeyRoundIcon,
  MailIcon,
  UserCircle2Icon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EchoMark } from "@/components/brand/echo-mark";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/ui/state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type AuthProviderInfo, getAuthProviderInfo } from "@/core/auth/api";
import {
  authReturnToFromSearch,
  registerPathWithReturnTo,
} from "@/core/auth/return-to";
import { octAuthApi, OctApiError, octErrorMessage } from "@/core/oct/api";
import { useI18n } from "@/core/i18n/hooks";
import { isEmbeddedWindow } from "@/components/workspace/embedded-window-bridge";
import { useAuth } from "@/providers/AuthProvider";
import { toast } from "sonner";

import "./login.css";
import {
  normalizeEmailVerificationCode,
  remainingCooldownSeconds,
} from "./login-utils";

const SMS_COOLDOWN_SECONDS = 60;
const AUTH_PROVIDER_RETRY_COUNT = 5; // 24 → 5
const AUTH_PROVIDER_BASE_DELAY_MS = 500;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isValidEmail(raw: string): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(raw.trim());
}

function EmailLoginForm({ returnTo }: { returnTo: string }) {
  const navigate = useNavigate();
  const { emailLogin } = useAuth();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const emailInputRef = useRef<HTMLInputElement | null>(null);
  const codeInputRef = useRef<HTMLInputElement | null>(null);
  const sendingRef = useRef(false);
  const submittingRef = useRef(false);
  const cooldownDeadlineRef = useRef(0);
  const [sendStatus, setSendStatus] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const updateCooldown = () => {
      setCooldown(remainingCooldownSeconds(cooldownDeadlineRef.current));
    };
    const id = window.setInterval(updateCooldown, 500);
    document.addEventListener("visibilitychange", updateCooldown);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", updateCooldown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cooldown > 0]);

  async function sendCode() {
    if (sendingRef.current || submittingRef.current || cooldown > 0) return;
    const addr = email.trim();
    if (!isValidEmail(addr)) {
      setEmailError(t.auth.errors.invalidEmail);
      setSendStatus(null);
      emailInputRef.current?.focus();
      toast.error(t.auth.errors.invalidEmail);
      return;
    }
    setEmailError(null);
    setSendStatus(null);
    setLoginError(null);
    sendingRef.current = true;
    setSending(true);
    try {
      const r = await octAuthApi.emailSend(addr);
      setSendStatus({
        kind: "success",
        message: `${t.auth.success.emailCodeSent} · ${addr}`,
      });
      toast.success(t.auth.success.emailCodeSent);
      if (r.dev_code) toast.message(t.auth.devCodeNotice(r.dev_code));
      cooldownDeadlineRef.current = Date.now() + SMS_COOLDOWN_SECONDS * 1000;
      setCooldown(SMS_COOLDOWN_SECONDS);
    } catch (err) {
      const message =
        err instanceof OctApiError && err.status === 503
          ? t.auth.errors.gatewayNotEnabled
          : octErrorMessage(err, t.auth.errors.sendFailed);
      setSendStatus({ kind: "error", message });
      if (err instanceof OctApiError && err.status === 503) {
        toast.error(message);
      } else {
        toast.error(message);
      }
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const addr = email.trim();
    const trimmedCode = code.trim();
    const nextEmailError = !addr
      ? t.auth.errors.emailRequired
      : !isValidEmail(addr)
        ? t.auth.errors.invalidEmail
        : null;
    const nextCodeError = !trimmedCode
      ? t.auth.errors.codeRequired
      : trimmedCode.length !== 6
        ? t.auth.errors.invalidCode
        : null;
    setEmailError(nextEmailError);
    setCodeError(nextCodeError);
    if (nextEmailError || nextCodeError) {
      toast.error(t.auth.errors.emailFillRequired);
      (nextEmailError ? emailInputRef : codeInputRef).current?.focus();
      return;
    }
    if (submittingRef.current || sendingRef.current) return;
    submittingRef.current = true;
    setLoginError(null);
    setSubmitting(true);
    let focusCodeAfterSubmit = false;
    try {
      await emailLogin(addr, trimmedCode);
      toast.success(t.auth.success.loginSuccess);
      navigate(returnTo, { replace: true });
    } catch (err) {
      const message = octErrorMessage(err, t.auth.errors.loginFailed);
      setLoginError(message);
      toast.error(message);
      focusCodeAfterSubmit = true;
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      if (focusCodeAfterSubmit) {
        window.setTimeout(() => codeInputRef.current?.focus(), 0);
      }
    }
  }

  return (
    <form
      aria-busy={submitting}
      onSubmit={onSubmit}
      className="echo-login-form space-y-5"
    >
      <div className="space-y-2.5">
        <Label htmlFor="email" className="text-sm font-medium">
          {t.auth.emailLabel}
        </Label>
        <div className="relative">
          <MailIcon className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground/50" />
          <Input
            aria-describedby={
              emailError
                ? "email-error"
                : sendStatus
                  ? "email-send-feedback"
                  : undefined
            }
            aria-invalid={Boolean(emailError)}
            id="email"
            type="email"
            placeholder="you@example.com"
            value={email}
            ref={emailInputRef}
            onChange={(e) => {
              setEmail(e.target.value);
              setEmailError(null);
              setSendStatus(null);
              setLoginError(null);
            }}
            disabled={sending || submitting || cooldown > 0}
            autoComplete="email"
            autoFocus
            className="echo-login-input h-12 rounded-xl border-border/60 bg-card/50 pl-11 text-base transition-colors focus:border-primary/40 focus:bg-card aria-invalid:border-destructive aria-invalid:focus:border-destructive aria-invalid:focus-visible:ring-destructive/25"
          />
        </div>
        {emailError && (
          <p
            aria-live="polite"
            className="px-1 text-xs text-destructive"
            id="email-error"
            role="alert"
          >
            {emailError}
          </p>
        )}
      </div>
      <div className="space-y-2.5">
        <Label htmlFor="email-code" className="text-sm font-medium">
          {t.auth.verificationCode}
        </Label>
        <div className="flex gap-3">
          <div className="relative flex-1">
            <KeyRoundIcon className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground/50" />
            <Input
              aria-describedby={
                [
                  codeError ? "email-code-error" : null,
                  loginError ? "email-login-error" : null,
                ]
                  .filter(Boolean)
                  .join(" ") || undefined
              }
              aria-invalid={Boolean(codeError)}
              id="email-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              enterKeyHint="done"
              maxLength={6}
              placeholder={t.auth.placeholders.code}
              value={code}
              ref={codeInputRef}
              onChange={(e) => {
                setCode(normalizeEmailVerificationCode(e.target.value));
                setCodeError(null);
                setLoginError(null);
              }}
              disabled={submitting}
              className="echo-login-input h-12 rounded-xl border-border/60 bg-card/50 pl-11 text-base transition-colors focus:border-primary/40 focus:bg-card aria-invalid:border-destructive aria-invalid:focus:border-destructive aria-invalid:focus-visible:ring-destructive/25"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={sendCode}
            disabled={sending || submitting || cooldown > 0}
            className="echo-login-code-button h-12 shrink-0 rounded-xl px-5"
          >
            {cooldown > 0
              ? `${cooldown}s`
              : sending
                ? t.auth.sending
                : t.auth.sendCode}
          </Button>
        </div>
        {codeError && (
          <p
            aria-live="polite"
            className="px-1 text-xs text-destructive"
            id="email-code-error"
            role="alert"
          >
            {codeError}
          </p>
        )}
        {sendStatus && (
          <p
            aria-live="polite"
            className={`px-1 text-xs ${
              sendStatus.kind === "error"
                ? "text-destructive"
                : "text-emerald-600 dark:text-emerald-400"
            }`}
            id="email-send-feedback"
            role={sendStatus.kind === "error" ? "alert" : "status"}
          >
            {sendStatus.message}
          </p>
        )}
      </div>
      {loginError && (
        <p
          aria-live="assertive"
          className="rounded-lg border border-destructive/30 bg-destructive/[0.08] px-3 py-2 text-sm text-destructive"
          id="email-login-error"
          role="alert"
        >
          {loginError}
        </p>
      )}
      <Button
        type="submit"
        className="echo-login-primary-button h-12 w-full rounded-xl text-base font-medium transition-all"
        disabled={submitting}
      >
        {submitting ? "正在进入 ECHO" : "进入 ECHO"}
        {!submitting && <ArrowRightIcon className="ml-1 size-4" />}
      </Button>
      <p className="px-1 text-center text-xs leading-relaxed text-muted-foreground/70">
        {t.auth.terms.emailAutoRegister}
        {t.auth.terms.agreeTo}{" "}
        <Link
          to="/terms"
          className="text-primary/80 underline-offset-2 transition-colors hover:text-primary hover:underline"
        >
          {t.auth.terms.userAgreement}
        </Link>{" "}
        {t.auth.terms.and}{" "}
        <Link
          to="/privacy"
          className="text-primary/80 underline-offset-2 transition-colors hover:text-primary hover:underline"
        >
          {t.auth.terms.privacyPolicy}
        </Link>
      </p>
    </form>
  );
}

function LocalLoginForm({
  passwordRequired,
  returnTo,
}: {
  passwordRequired: boolean;
  returnTo: string;
}) {
  const navigate = useNavigate();
  const { login } = useAuth();
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedUsername = username.trim();
    if (!trimmedUsername || (passwordRequired && !password)) {
      toast.error(t.auth.errors.fillRequired);
      return;
    }
    setSubmitting(true);
    try {
      await login({
        username: trimmedUsername,
        ...(password ? { password } : {}),
      });
      toast.success(t.auth.success.loginSuccess);
      navigate(returnTo, { replace: true });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t.auth.errors.loginFailed,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="echo-login-form space-y-5">
      <div className="rounded-xl border border-border/50 bg-muted/30 px-4 py-3 text-xs text-muted-foreground/80">
        {t.loginPage.localBanner}
      </div>
      <div className="space-y-2.5">
        <Label htmlFor="local-username" className="text-sm font-medium">
          {t.registerPage.usernameLabel}
        </Label>
        <div className="relative">
          <UserCircle2Icon className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground/50" />
          <Input
            id="local-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={t.registerPage.usernamePlaceholder}
            autoComplete="username"
            className="echo-login-input h-12 rounded-xl border-border/60 bg-card/50 pl-11 text-base transition-colors focus:border-primary/40 focus:bg-card"
          />
        </div>
      </div>
      {passwordRequired && (
        <div className="space-y-2.5">
          <Label htmlFor="local-password" className="text-sm font-medium">
            {t.registerPage.passwordLabel}
          </Label>
          <div className="relative">
            <KeyRoundIcon className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground/50" />
            <Input
              id="local-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t.registerPage.passwordPlaceholder}
              autoComplete="current-password"
              className="echo-login-input h-12 rounded-xl border-border/60 bg-card/50 pl-11 text-base transition-colors focus:border-primary/40 focus:bg-card"
            />
          </div>
        </div>
      )}
      <Button
        type="submit"
        className="echo-login-primary-button h-12 w-full rounded-xl text-base font-medium transition-all"
        disabled={submitting}
      >
        {submitting ? "正在进入 ECHO" : "进入 ECHO"}
        {!submitting && <ArrowRightIcon className="ml-1 size-4" />}
      </Button>
    </form>
  );
}

function EchoBrand() {
  return (
    <div className="echo-login-brand" aria-label="ECHO AGE 回响纪元">
      <span className="echo-login-brand-mark" aria-hidden="true">
        <EchoMark tone="light" />
      </span>
      <span className="echo-login-brand-copy">
        <strong>ECHO AGE</strong>
        <small>回响纪元</small>
      </span>
    </div>
  );
}

const ECHO_PARTICLES = Array.from({ length: 148 }, (_, index) => {
  const angle = index * 2.3999632297;
  const radius = 94 + ((index * 37) % 225);
  const wobble = Math.sin(index * 1.73) * 22;

  return {
    cx: 515 + Math.cos(angle) * (radius + wobble) * 1.16,
    cy: 416 + Math.sin(angle) * (radius - wobble) * 0.72,
    radius: 0.65 + (index % 5) * 0.28,
    opacity: 0.2 + (index % 7) * 0.1,
    delay: `${-((index * 0.17) % 6).toFixed(2)}s`,
  };
});

function EchoAgeField() {
  return (
    <svg
      className="echo-age-field"
      viewBox="0 0 1600 900"
      preserveAspectRatio="xMidYMid slice"
      role="presentation"
    >
      <defs>
        <linearGradient id="echo-wave-gradient" x1="0" x2="1">
          <stop offset="0" stopColor="#5b82ff" stopOpacity="0" />
          <stop offset="0.25" stopColor="#748fff" stopOpacity="0.9" />
          <stop offset="0.55" stopColor="#b08cff" />
          <stop offset="0.78" stopColor="#70dfff" stopOpacity="0.9" />
          <stop offset="1" stopColor="#5b82ff" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="echo-core-gradient" cx="38%" cy="30%">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.08" stopColor="#b9c9ff" />
          <stop offset="0.36" stopColor="#586bd5" />
          <stop offset="0.72" stopColor="#171b51" />
          <stop offset="1" stopColor="#070812" />
        </radialGradient>
        <filter
          id="echo-field-glow"
          x="-50%"
          y="-50%"
          width="200%"
          height="200%"
        >
          <feGaussianBlur stdDeviation="3.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter
          id="echo-field-soft-glow"
          x="-100%"
          y="-100%"
          width="300%"
          height="300%"
        >
          <feGaussianBlur stdDeviation="18" />
        </filter>
      </defs>

      <g className="echo-age-waves" fill="none">
        <path
          className="echo-age-wave echo-age-wave-1"
          d="M-80 463 C 125 292, 275 574, 475 423 S 795 278, 1015 432 S 1350 585, 1680 380"
        />
        <path
          className="echo-age-wave echo-age-wave-2"
          d="M-80 457 C 128 310, 282 554, 475 420 S 792 296, 1014 430 S 1358 566, 1680 392"
        />
        <path
          className="echo-age-wave echo-age-wave-3"
          d="M-60 489 C 158 340, 298 584, 492 445 S 808 324, 1020 452 S 1370 594, 1660 420"
        />
        <path
          className="echo-age-wave echo-age-wave-4"
          d="M-60 422 C 134 278, 296 520, 472 391 S 786 250, 1005 405 S 1356 530, 1660 360"
        />
      </g>

      <g className="echo-age-vortex" fill="none">
        <ellipse cx="515" cy="416" rx="306" ry="208" />
        <ellipse
          cx="515"
          cy="416"
          rx="280"
          ry="225"
          transform="rotate(28 515 416)"
        />
        <ellipse
          cx="515"
          cy="416"
          rx="246"
          ry="171"
          transform="rotate(-18 515 416)"
        />
        <ellipse
          cx="515"
          cy="416"
          rx="215"
          ry="145"
          transform="rotate(38 515 416)"
        />
      </g>

      <g className="echo-age-particles" filter="url(#echo-field-glow)">
        {ECHO_PARTICLES.map((particle, index) => (
          <circle
            className="echo-age-particle"
            key={index}
            cx={particle.cx}
            cy={particle.cy}
            r={particle.radius}
            opacity={particle.opacity}
            style={{ animationDelay: particle.delay }}
          />
        ))}
      </g>

      <g className="echo-age-core">
        <circle
          cx="515"
          cy="416"
          r="54"
          fill="#6e75ff"
          opacity="0.2"
          filter="url(#echo-field-soft-glow)"
        />
        <circle
          cx="515"
          cy="416"
          r="34"
          fill="url(#echo-core-gradient)"
          stroke="#93aaff"
          strokeOpacity="0.5"
        />
        <text
          x="515"
          y="425"
          textAnchor="middle"
          fill="#f5f7ff"
          fontSize="25"
          fontWeight="350"
        >
          E
        </text>
      </g>
    </svg>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = authReturnToFromSearch(location.search);
  const { authError, authStatus, isLoading, isAuthenticated, retryAuth } =
    useAuth();
  const { t } = useI18n();
  const [authProviders, setAuthProviders] = useState<AuthProviderInfo[] | null>(
    null,
  );
  const [providerReloadKey, setProviderReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadAuthProviders() {
      for (let attempt = 0; attempt < AUTH_PROVIDER_RETRY_COUNT; attempt += 1) {
        const providers = await getAuthProviderInfo();
        if (cancelled) return;
        if (providers.length > 0) {
          setAuthProviders(providers);
          return;
        }
        if (attempt < AUTH_PROVIDER_RETRY_COUNT - 1) {
          // 指数退避：500ms, 1s, 2s, 4s
          const backoffDelay =
            AUTH_PROVIDER_BASE_DELAY_MS * Math.pow(2, attempt);
          await delay(backoffDelay);
        }
      }
      // 5 次后仍为空，停止重试
      setAuthProviders([]);
    }

    void loadAuthProviders();
    return () => {
      cancelled = true;
    };
  }, [providerReloadKey]);

  const providersReady = authProviders !== null;
  const hasOct = authProviders?.some((p) => p.id === "oct") ?? false;
  const localProvider = authProviders?.find((p) => p.id === "local") ?? null;
  const backendUnavailable = authError !== null && authStatus === null;

  const retryBackend = () => {
    setAuthProviders(null);
    setProviderReloadKey((current) => current + 1);
    void retryAuth();
  };

  useEffect(() => {
    if (isLoading) return;
    if (isAuthenticated) {
      navigate(returnTo, { replace: true });
      return;
    }
    if (authStatus && !authStatus.enabled) {
      navigate(returnTo, { replace: true });
    }
  }, [isLoading, isAuthenticated, authStatus, navigate, returnTo]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="animate-pulse text-sm text-muted-foreground">
          {t.common.loading}
        </div>
      </div>
    );
  }

  if (isAuthenticated || (authStatus && !authStatus.enabled)) {
    return null;
  }

  const embeddedWindow = isEmbeddedWindow();

  return (
    <div
      className={`echo-login-shell relative min-h-screen overflow-hidden${embeddedWindow ? " echo-login-shell-embedded" : ""}`}
    >
      <div className="echo-login-cosmos" aria-hidden="true">
        <div className="echo-login-stars" />
        <div className="echo-login-aurora echo-login-aurora-blue" />
        <div className="echo-login-aurora echo-login-aurora-violet" />
        <EchoAgeField />
      </div>

      <header className="echo-login-header">
        <EchoBrand />
        <nav className="echo-login-header-nav" aria-hidden="true">
          <span>PRODUCT</span>
          <i />
          <span>MEMORY</span>
          <i />
          <span>AGENTS</span>
          <i />
          <span>VISION</span>
        </nav>
        <div className="echo-login-header-status">
          ENTER ECHO
          <span />
        </div>
      </header>

      <main className="echo-login-layout">
        <section className="echo-login-hero" aria-labelledby="echo-login-title">
          <div className="echo-login-eyebrow">THE ECHO ECOSYSTEM</div>
          <h1 id="echo-login-title" className="echo-login-title">
            <span>THE AGE OF</span>
            <strong>ECHO</strong>
          </h1>
          <div className="echo-login-subtitle">
            <span />
            <strong>回响纪元</strong>
            <span />
          </div>
          <p className="echo-login-lead">
            <span>Every interaction leaves an echo.</span>
            <small>每一次交互，都留下回响。</small>
          </p>

          <div className="echo-login-continuum" aria-label="ECHO 能力">
            <div>
              <span>01</span>
              <strong>MEMORY</strong>
              <small>它记住</small>
            </div>
            <i />
            <div>
              <span>02</span>
              <strong>UNDERSTAND</strong>
              <small>它理解</small>
            </div>
            <i />
            <div>
              <span>03</span>
              <strong>ACT</strong>
              <small>它行动</small>
            </div>
            <i />
            <div>
              <span>04</span>
              <strong>GROW</strong>
              <small>它与你共同成长</small>
            </div>
          </div>
        </section>

        <section className="echo-login-form-column" aria-label="ECHO 账号登录">
          <Card className="echo-login-card overflow-hidden rounded-[22px] border-border/50 bg-card/80 backdrop-blur-xl">
            <CardHeader className="space-y-2.5 px-8 pb-6 pt-8 text-center">
              <div className="echo-login-card-kicker">
                <span /> ENTER THE ECHO
              </div>
              <CardTitle className="text-2xl font-medium tracking-tight">
                进入 ECHO
              </CardTitle>
              <CardDescription className="text-[15px] text-muted-foreground/80">
                {hasOct
                  ? "用一封验证码，唤醒你的 ECHO 身份"
                  : "登录你的 ECHO 身份"}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-8 pb-7 pt-0">
              {backendUnavailable ? (
                <ErrorState
                  className="min-h-40 rounded-xl border border-destructive/20 bg-destructive/5"
                  title="暂时无法连接 EchoAI 服务"
                  detail="本地服务可能仍在启动或已停止。请确认服务运行后重试。"
                  actionLabel="重试连接"
                  onAction={retryBackend}
                />
              ) : !providersReady ? (
                <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
                  {t.common.loading}
                </div>
              ) : hasOct && localProvider ? (
                <Tabs defaultValue="email" className="w-full">
                  <TabsList className="mb-6 grid h-11 w-full grid-cols-2 rounded-xl bg-muted/50 p-1">
                    <TabsTrigger
                      value="email"
                      className="rounded-lg text-sm font-medium data-[state=active]:bg-card data-[state=active]:shadow-sm"
                    >
                      邮箱登录
                    </TabsTrigger>
                    <TabsTrigger
                      value="local"
                      className="rounded-lg text-sm font-medium data-[state=active]:bg-card data-[state=active]:shadow-sm"
                    >
                      {localProvider.label ?? "本地账户"}
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="email" className="mt-0">
                    <EmailLoginForm returnTo={returnTo} />
                  </TabsContent>
                  <TabsContent value="local" className="mt-0">
                    <LocalLoginForm
                      passwordRequired={
                        localProvider.password_required === true
                      }
                      returnTo={returnTo}
                    />
                  </TabsContent>
                </Tabs>
              ) : hasOct ? (
                <EmailLoginForm returnTo={returnTo} />
              ) : localProvider ? (
                <LocalLoginForm
                  passwordRequired={localProvider.password_required === true}
                  returnTo={returnTo}
                />
              ) : (
                <div className="rounded-xl border border-border/50 bg-muted/30 px-4 py-6 text-center text-sm text-muted-foreground">
                  {t.loginPage.errorServiceDisabled}
                </div>
              )}

              {authStatus?.allow_registration && (
                <div className="mt-6 text-center text-sm text-muted-foreground/80">
                  还没有账户？{" "}
                  <Link
                    to={registerPathWithReturnTo(returnTo)}
                    className="font-medium text-primary transition-colors hover:text-primary/80"
                  >
                    立即注册
                  </Link>
                </div>
              )}

              <div className="echo-login-security-note">
                <FingerprintIcon className="size-3.5" />
                验证码由 verify@echo-age.com 安全发送
              </div>
            </CardContent>
          </Card>

          <p className="echo-login-footer">
            AN AGE BEGINS · © {new Date().getFullYear()} ECHO AGE
          </p>
        </section>
      </main>
    </div>
  );
}
