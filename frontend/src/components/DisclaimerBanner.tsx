"use client";

import { ShieldAlert } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Legal disclaimer banner (§6 of the Security & Privacy Spec).
 * Rendered at the top of trip-post pages and chat rooms.
 */
export default function DisclaimerBanner({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  const t = useT();

  return (
    <div
      role="note"
      className={cn(
        "flex items-start gap-2.5 rounded-lg border border-warning/40 bg-warning/10 px-3.5 py-2.5 text-xs leading-relaxed text-warning-foreground",
        className,
      )}
    >
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <p>
        <strong>{t("disclaimer.heading")}</strong>
        {t("disclaimer.body")}
        {!compact && t("disclaimer.noMoney")}
      </p>
    </div>
  );
}
