"use client";

import { useRef, useState } from "react";
import { Camera, Loader2, Trash2 } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

/** Mirrors the server's `MAX_UPLOAD_BYTES` (5 MB) and `sniff_mime` allow-list. */
const MAX_BYTES = 5 * 1024 * 1024;
const ACCEPTED = ["image/jpeg", "image/png", "image/webp"];

export default function AvatarUploader({ className }: { className?: string }) {
  const { profile, refreshProfile } = useAuth();
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function validate(file: File): string | null {
    // Check client-side too: it gives an instant, specific message and avoids
    // burning an upload on a file the server will reject. The server still
    // validates — this is UX, not the security boundary.
    if (!ACCEPTED.includes(file.type)) {
      return t("avatar.wrongType");
    }
    if (file.size > MAX_BYTES) {
      const mb = (file.size / 1024 / 1024).toFixed(1);
      return t("avatar.tooLarge", { mb });
    }
    return null;
  }

  async function handleFile(file: File) {
    setError("");
    setNotice("");

    const problem = validate(file);
    if (problem) {
      setError(problem);
      return;
    }

    setBusy(true);
    try {
      const { url } = await api.uploadImage(file);
      await api.updateProfile({ avatar_url: url });
      await refreshProfile();
      setNotice(t("avatar.updated"));
    } catch (err) {
      setError(errorMessage(err, t("avatar.uploadError")));
    } finally {
      setBusy(false);
      // Reset so picking the same file again still fires onChange.
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function removeAvatar() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api.updateProfile({ avatar_url: "" });
      await refreshProfile();
      setNotice(t("avatar.removed"));
    } catch (err) {
      setError(errorMessage(err, t("avatar.removeError")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cn("flex flex-col items-center gap-2", className)}>
      <div className="group relative">
        <Avatar className="h-20 w-20">
          {profile?.avatar_url && <AvatarImage src={profile.avatar_url} alt="" />}
          <AvatarFallback className="text-2xl">{profile?.nickname?.slice(0, 1)}</AvatarFallback>
        </Avatar>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          aria-label={t("avatar.change")}
          className={cn(
            "absolute inset-0 grid place-items-center rounded-full bg-black/55 text-white",
            "opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
            busy && "opacity-100",
          )}
        >
          {busy ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <Camera className="h-5 w-5" />
          )}
        </button>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(",")}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
      />

      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {profile?.avatar_url ? t("avatar.replace") : t("avatar.upload")}
        </Button>
        {profile?.avatar_url && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("avatar.remove")}
            disabled={busy}
            onClick={removeAvatar}
            className="h-8 w-8 text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      {(error || notice) && (
        <p
          className={cn(
            "max-w-[16rem] text-center text-[11px] leading-snug",
            error ? "text-destructive" : "text-emerald-600 dark:text-emerald-400",
          )}
          role={error ? "alert" : "status"}
        >
          {error || notice}
        </p>
      )}
      <p className="max-w-[16rem] text-center text-[10px] leading-snug text-muted-foreground">
        {t("avatar.exifNotice")}
      </p>
    </div>
  );
}
