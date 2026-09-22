"use client";

/**
 * Locale provider + hooks.
 *
 * The active locale lives in a cookie and is applied on the client. That keeps
 * every existing route and link untouched (no `[locale]` segment), at the cost
 * of a brief default-language flash on the very first paint for a non-default
 * locale — see the trade-off note in `dictionaries.ts`.
 *
 * The provider starts from `DEFAULT_LOCALE` and switches in an effect rather
 * than reading the cookie in a lazy initialiser. Reading it during render would
 * make the server and client disagree about the markup (the server cannot see
 * `document.cookie`), which is a hydration error rather than a cosmetic flash.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  DEFAULT_LOCALE,
  LOCALES,
  LOCALE_COOKIE,
  htmlLang,
  intlLocale,
  labelKey,
  translate,
  type LabelGroup,
  type Locale,
  type MessageKey,
} from "./dictionaries";

interface I18nState {
  locale: Locale;
  setLocale: (next: Locale) => void;
  /** Translate a key, interpolating `{name}` placeholders. */
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
  /** Display label for a server-supplied enum value; falls back to the raw value. */
  label: (group: LabelGroup, value: string | null | undefined) => string;
  formatDate: (value?: string | null, opts?: Intl.DateTimeFormatOptions) => string | null;
  formatDateTime: (value?: string | null) => string;
}

const I18nContext = createContext<I18nState | null>(null);

function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

function readCookie(): Locale | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${LOCALE_COOKIE}=([^;]*)`),
  );
  const value = match ? decodeURIComponent(match[1]) : null;
  return isLocale(value) ? value : null;
}

function writeCookie(locale: Locale): void {
  if (typeof document === "undefined") return;
  // A UI preference, not a credential, so it is deliberately readable by script
  // and long-lived. SameSite=Lax is enough: nothing here is security-relevant.
  document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(locale)};path=/;max-age=31536000;SameSite=Lax`;
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  // Restore the stored preference after mount. `document.documentElement.lang`
  // is set here too, so screen readers and the browser's font/locale handling
  // follow the choice rather than staying on the server-rendered default.
  useEffect(() => {
    const stored = readCookie();
    if (stored) setLocaleState(stored);
    document.documentElement.lang = htmlLang(stored ?? DEFAULT_LOCALE);
  }, []);

  const setLocale = useCallback((next: Locale) => {
    writeCookie(next);
    document.documentElement.lang = htmlLang(next);
    setLocaleState(next);
  }, []);

  const value = useMemo<I18nState>(() => {
    const tag = intlLocale(locale);
    return {
      locale,
      setLocale,
      t: (key, vars) => translate(locale, key, vars),
      label: (group, raw) => {
        const key = labelKey(group, raw);
        return key ? translate(locale, key) : (raw ?? "");
      },
      formatDate: (input, opts) =>
        input
          ? new Date(input).toLocaleDateString(
              tag,
              opts ?? { month: "short", day: "numeric" },
            )
          : null,
      formatDateTime: (input) =>
        input
          ? new Date(input).toLocaleString(tag, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "",
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nState {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <LocaleProvider>");
  return ctx;
}

/** Convenience for components that only need the translator. */
export function useT() {
  return useI18n().t;
}
