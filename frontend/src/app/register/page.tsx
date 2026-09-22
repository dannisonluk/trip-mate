"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2 } from "lucide-react";

import { useAuth } from "@/lib/auth";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function RegisterPage() {
  const { register, refreshProfile } = useAuth();
  const t = useT();
  const router = useRouter();

  const [phone, setPhone] = useState("");
  const [nickname, setNickname] = useState("");
  const [password, setPassword] = useState("");
  const [consentPrivacy, setConsentPrivacy] = useState(false);
  const [consentTerms, setConsentTerms] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Step 2 — phone verification
  const [registered, setRegistered] = useState(false);
  const [otp, setOtp] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [otpNotice, setOtpNotice] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!consentPrivacy || !consentTerms) {
      setError(t("register.consentRequired"));
      return;
    }

    setBusy(true);
    try {
      await register({ phone_number: phone, password, nickname });
      setRegistered(true);
    } catch (err) {
      setError(errorMessage(err, t("register.failed")));
    } finally {
      setBusy(false);
    }
  }

  async function sendOtp() {
    setOtpNotice(null);
    try {
      const res = await api.requestOtp(phone);
      setDevCode(res.dev_code);
      setOtpNotice(
        res.dev_code
          ? t("register.otpDevNotice", { code: res.dev_code })
          : t("register.otpSent"),
      );
    } catch (err) {
      setOtpNotice(errorMessage(err, t("register.otpSendError")));
    }
  }

  async function verifyOtp() {
    setOtpNotice(null);
    try {
      await api.verifyOtp(phone, otp);
      await refreshProfile();
      setOtpNotice(t("register.otpVerified"));
      setTimeout(() => router.push("/trips"), 600);
    } catch (err) {
      setOtpNotice(errorMessage(err, t("register.otpVerifyError")));
    }
  }

  if (registered) {
    return (
      <div className="mx-auto max-w-md py-12">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-2xl">
              <CheckCircle2 className="h-5 w-5 text-primary" />
              {t("register.createdTitle")}
            </CardTitle>
            <CardDescription>{t("register.createdBody")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {otpNotice && (
              <div className="rounded-md border border-border bg-secondary px-3 py-2 text-sm">
                {otpNotice}
              </div>
            )}
            <Button variant="outline" className="w-full" onClick={sendOtp}>
              {t("register.sendOtp")}
            </Button>
            <div className="space-y-2">
              <Label htmlFor="otp">{t("register.otpLabel")}</Label>
              <Input
                id="otp"
                inputMode="numeric"
                placeholder={t("register.otpPlaceholder")}
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
            </div>
            <Button className="w-full" onClick={verifyOtp} disabled={otp.length < 4}>
              {t("register.verify")}
            </Button>
            <Button variant="ghost" className="w-full" onClick={() => router.push("/trips")}>
              {t("register.later")}
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md py-12">
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl">{t("register.title")}</CardTitle>
          <CardDescription>{t("register.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="nickname">{t("register.nicknameLabel")}</Label>
              <Input
                id="nickname"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                minLength={2}
                maxLength={60}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="phone">{t("register.phoneLabel")}</Label>
              <Input
                id="phone"
                inputMode="tel"
                placeholder="+85291234567"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t("register.passwordLabel")}</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            <div className="space-y-3 rounded-md border border-border p-3">
              <label className="flex cursor-pointer items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]"
                  checked={consentPrivacy}
                  onChange={(e) => setConsentPrivacy(e.target.checked)}
                />
                <span className="text-muted-foreground">{t("register.consentPrivacy")}</span>
              </label>
              <label className="flex cursor-pointer items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]"
                  checked={consentTerms}
                  onChange={(e) => setConsentTerms(e.target.checked)}
                />
                <span className="text-muted-foreground">{t("register.consentTerms")}</span>
              </label>
            </div>

            <Button type="submit" className="w-full" disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("register.submit")}
            </Button>
          </form>

          <p className="mt-5 text-center text-sm text-muted-foreground">
            {t("register.haveAccount")}{" "}
            <Link href="/login" className="font-medium text-primary hover:underline">
              {t("register.loginLink")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
