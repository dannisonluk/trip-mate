import type { Metadata } from "next";

import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { DEFAULT_LOCALE, htmlLang, translate } from "@/lib/i18n/dictionaries";
import { LocaleProvider } from "@/lib/i18n";
import Footer from "@/components/Footer";
import NavBar from "@/components/NavBar";

/**
 * `metadata` is built from the **default locale** on purpose.
 *
 * This is a server component, so it cannot see the locale cookie — and reading
 * it here via `cookies()` would opt every route out of static rendering. Rather
 * than make that trade silently, the document metadata stays in the default
 * locale and the trade-off is recorded in `lib/i18n/dictionaries.ts`.
 *
 * Reading straight from the dictionary (rather than through a hook) is what
 * keeps this a server component.
 */
export const metadata: Metadata = {
  title: translate(DEFAULT_LOCALE, "meta.title"),
  description: translate(DEFAULT_LOCALE, "meta.description"),
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning` because LocaleProvider rewrites `lang` on the
    // client once it has read the stored preference — a deliberate, one-time
    // difference between the server markup and the client's.
    <html lang={htmlLang(DEFAULT_LOCALE)} suppressHydrationWarning>
      <body className="min-h-screen">
        <LocaleProvider>
          <AuthProvider>
            <NavBar />
            <main className="container py-6">{children}</main>
          </AuthProvider>
          <Footer />
        </LocaleProvider>
      </body>
    </html>
  );
}
