import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRightIcon, SparklesIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/core/i18n/hooks";
import { useAuth } from "@/providers/AuthProvider";
import { toast } from "sonner";

export default function RegisterPage() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const { register, authStatus, isLoading } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      toast.error(t.registerPage.toastFillRequired);
      return;
    }
    if (password !== confirmPassword) {
      toast.error(t.registerPage.toastPasswordMismatch);
      return;
    }
    if (username.length < 3) {
      toast.error(t.registerPage.toastUsernameTooShort);
      return;
    }
    if (password.length < 6) {
      toast.error(t.registerPage.toastPasswordTooShort);
      return;
    }

    setIsSubmitting(true);
    try {
      await register({ username, password, email: email || undefined });
      toast.success(t.registerPage.toastSuccess);
      navigate("/login");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.registerPage.toastFailed,
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="animate-pulse">{t.registerPage.loadingText}</div>
      </div>
    );
  }

  if (authStatus && (!authStatus.enabled || !authStatus.allow_registration)) {
    navigate("/workspace");
    return null;
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#08080c]">
      <div className="absolute inset-0 css-starfield" />

      <div className="relative z-10 grid w-full max-w-5xl gap-10 px-4 md:grid-cols-[1.05fr_0.95fr] md:px-6">
        <div className="hidden flex-col justify-center md:flex">
          <div className="max-w-xl space-y-5">
            <div className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-white/65 backdrop-blur-sm">
              <SparklesIcon className="size-3.5 text-white/70" />
              {t.registerPage.badgeText}
            </div>
            <div className="space-y-3">
              <h1 className="text-5xl font-bold tracking-tight text-white">
                {t.registerPage.heroTitleLine1}
                <span className="block text-white/80">
                  {t.registerPage.heroTitleLine2}
                </span>
              </h1>
              <p className="max-w-lg text-base leading-7 text-white/45">
                {t.registerPage.heroDescription}
              </p>
            </div>
          </div>
        </div>

        <Card className="border-white/10 bg-white/[0.06] shadow-2xl shadow-black/25 backdrop-blur-xl">
          <CardHeader className="text-center">
            <div className="flex items-center justify-center gap-2 mb-4">
              <div className="flex size-11 items-center justify-center rounded-xl border border-white/12 bg-white/[0.04] text-white/80 shadow-sm">
                <svg
                  width="21"
                  height="21"
                  viewBox="0 0 512 512"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="M256 32C167.6 32 96 103.6 96 192c0 52.8 25.6 99.6 65.2 128.8C128 348 96 404 96 448c0 17.7 14.3 32 32 32s32-14.3 32-32c0-28 16-68 40-96 8 4 16.4 7.2 25.2 9.6-4 26.4-9.2 56-9.2 86.4 0 17.7 14.3 32 32 32s32-14.3 32-32c0-26.4 4-52 8-76 12-2.4 23.6-6 34.8-11.2C348 384 368 420 368 448c0 17.7 14.3 32 32 32s32-14.3 32-32c0-48-36-108-72-147.2C399.6 271.6 416 233.6 416 192c0-88.4-71.6-160-160-160zm0 64c53 0 96 43 96 96s-43 96-96 96-96-43-96-96 43-96 96-96z"
                    fill="currentColor"
                  />
                  <circle cx="224" cy="176" r="20" fill="currentColor" />
                  <circle cx="288" cy="176" r="20" fill="currentColor" />
                  <circle cx="228" cy="180" r="10" fill="#08080c" />
                  <circle cx="292" cy="180" r="10" fill="#08080c" />
                </svg>
              </div>
            </div>
            <CardTitle className="text-2xl">
              <span className="text-white">{t.registerPage.cardTitle}</span>
            </CardTitle>
            <CardDescription>{t.registerPage.cardDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username">{t.registerPage.usernameLabel}</Label>
                <Input
                  id="username"
                  type="text"
                  placeholder={t.registerPage.usernamePlaceholder}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">{t.registerPage.emailLabel}</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="your@email.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">{t.registerPage.passwordLabel}</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder={t.registerPage.passwordPlaceholder}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirmPassword">
                  {t.registerPage.confirmPasswordLabel}
                </Label>
                <Input
                  id="confirmPassword"
                  type="password"
                  placeholder={t.registerPage.confirmPasswordPlaceholder}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              <Button
                type="submit"
                className="w-full bg-white text-[#08080c] hover:bg-white/90"
                disabled={isSubmitting}
              >
                {isSubmitting
                  ? t.registerPage.submitting
                  : t.registerPage.submitButton}
                {!isSubmitting && <ArrowRightIcon className="size-4" />}
              </Button>
            </form>
            <div className="mt-4 text-center text-sm text-muted-foreground">
              {t.registerPage.alreadyHaveAccount}{" "}
              <Link to="/login" className="text-white/70 hover:text-white">
                {t.registerPage.loginLink}
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
