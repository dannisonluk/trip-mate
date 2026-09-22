"use client";

import { useT } from "@/lib/i18n";

/**
 * Site footer.
 *
 * A client component only because the disclaimer is translated; it holds no
 * state. The root layout is a server component and cannot use the translation
 * hook directly.
 */
export default function Footer() {
  const t = useT();

  return (
    <footer className="container pb-16 pt-10">
      <p className="text-xs text-muted-foreground">{t("footer.disclaimer")}</p>
    </footer>
  );
}
