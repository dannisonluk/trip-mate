/**
 * i18n dictionaries — a thin composition layer over per-feature namespaces.
 *
 * ## Why this file is small now (D5)
 *
 * It used to hold both locales inline: 1038 lines, growing linearly, with every
 * feature's strings in one diff. The strings now live in
 * `./namespaces/<feature>.ts`, one file per feature, so a change to the chat
 * copy touches `chat.ts` and nothing else.
 *
 * ## What must NOT change
 *
 * The **type-level guarantee** is the whole reason this module is not just
 * JSON:
 *
 * * `zh-HK` is the single source of the key set. `MessageKey` is derived from
 *   *all* the namespaces merged — so a key that exists in no namespace is a
 *   type error at every `t("…")` call site.
 * * every other locale is declared `Record<MessageKey, string>`, so **a missing
 *   translation is a compile error**, not a silent fallback to the key.
 *
 * The merge below is spread-based rather than `Object.assign`-based on purpose:
 * spreads are checked for duplicate keys at the type level when the objects are
 * literally known (as they are here), so a key defined in two namespaces is
 * caught by `tsc` rather than silently resolved by last-write-wins.
 *
 * If you add a namespace, add it to *both* merges and to `NAMESPACES` below —
 * `check:i18n` asserts the counts agree, so forgetting one is a test failure.
 */

import * as admin from "./namespaces/admin";
import * as auth from "./namespaces/auth";
import * as chat from "./namespaces/chat";
import * as city from "./namespaces/city";
import * as common from "./namespaces/common";
import * as enums from "./namespaces/enums";
import * as home from "./namespaces/home";
import * as notifications from "./namespaces/notifications";
import * as profile from "./namespaces/profile";
import * as reviews from "./namespaces/reviews";
import * as server from "./namespaces/server";
import * as tripDetail from "./namespaces/tripDetail";
import * as trips from "./namespaces/trips";

export const LOCALES = ["zh-HK", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "zh-HK";

/** Shown in the language switcher. Endonyms — never translated. */
export const LOCALE_LABEL: Record<Locale, string> = {
  "zh-HK": "繁體中文",
  en: "English",
};

export const LOCALE_COOKIE = "tripmate_locale";

/**
 * The namespace modules, for tooling. `check:i18n` walks this to report per-
 * feature key counts and to catch a namespace that exists on disk but was never
 * wired into the merge.
 */
export const NAMESPACES = {
  admin,
  auth,
  chat,
  city,
  common,
  enums,
  home,
  notifications,
  profile,
  reviews,
  server,
  tripDetail,
  trips,
} as const;

// ---------------------------------------------------------------------------
// zh-HK — the key set is defined here
// ---------------------------------------------------------------------------

const zhHK = {
  ...admin.zhHK,
  ...auth.zhHK,
  ...chat.zhHK,
  ...city.zhHK,
  ...common.zhHK,
  ...enums.zhHK,
  ...home.zhHK,
  ...notifications.zhHK,
  ...profile.zhHK,
  ...reviews.zhHK,
  ...server.zhHK,
  ...tripDetail.zhHK,
  ...trips.zhHK,
};

/** Every valid message key, derived from the zh-HK key set. */
export type MessageKey = keyof typeof zhHK;

// ---------------------------------------------------------------------------
// en — typed against the key set, so a missing key will not compile
// ---------------------------------------------------------------------------

const en: Record<MessageKey, string> = {
  ...admin.en,
  ...auth.en,
  ...chat.en,
  ...city.en,
  ...common.en,
  ...enums.en,
  ...home.en,
  ...notifications.en,
  ...profile.en,
  ...reviews.en,
  ...server.en,
  ...tripDetail.en,
  ...trips.en,
};

export const DICTIONARIES: Record<Locale, Record<MessageKey, string>> = {
  "zh-HK": zhHK,
  en,
};

// ---------------------------------------------------------------------------
// Interpolation and lookup
// ---------------------------------------------------------------------------

/**
 * Replace `{name}` placeholders. A placeholder with no matching variable is
 * left **intact** rather than blanked: a visible `{count}` in the UI is a bug
 * report, whereas an empty string is a silent wrong sentence.
 */
export function interpolate(
  template: string,
  vars?: Record<string, string | number>,
): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/**
 * Resolve a key in a locale, returning the key itself when there is no entry.
 *
 * Returning the key (rather than throwing) is what makes an unknown
 * server-supplied code detectable: `serverCodeKey` returns `null` for codes it
 * does not know, and callers compare the result to the input.
 */
export function translate(
  locale: Locale,
  key: MessageKey,
  vars?: Record<string, string | number>,
): string {
  const table = DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE];
  const template = table[key];
  if (template === undefined) return key;
  return interpolate(template, vars);
}

// ---------------------------------------------------------------------------
// Enum labels — an explicit mapping, never `t(`${group}.${value}`)`
// ---------------------------------------------------------------------------

export type LabelGroup =
  | "gender"
  | "budget"
  | "applicationStatus"
  | "tripStatus"
  | "notificationType"
  | "travelStyle"
  | "reviewTag";

const LABEL_KEYS: Record<LabelGroup, Record<string, MessageKey>> = {
  budget: {
    BUDGET: "budget.BUDGET",
    MODERATE: "budget.MODERATE",
    LUXURY: "budget.LUXURY",
  },
  tripStatus: {
    OPEN: "tripStatus.OPEN",
    CLOSED: "tripStatus.CLOSED",
    CANCELLED: "tripStatus.CANCELLED",
  },
  applicationStatus: {
    PENDING: "applicationStatus.PENDING",
    ACCEPTED: "applicationStatus.ACCEPTED",
    REJECTED: "applicationStatus.REJECTED",
  },
  gender: {
    MALE: "gender.MALE",
    FEMALE: "gender.FEMALE",
    OTHER: "gender.OTHER",
    ANY: "gender.ANY",
  },
  travelStyle: {
    BACKPACKER: "travelStyle.BACKPACKER",
    PHOTOGRAPHY: "travelStyle.PHOTOGRAPHY",
    FOOD: "travelStyle.FOOD",
    CULTURE: "travelStyle.CULTURE",
    HIKING: "travelStyle.HIKING",
    NATURE: "travelStyle.NATURE",
    ADVENTURE: "travelStyle.ADVENTURE",
    CAFE: "travelStyle.CAFE",
    NIGHTLIFE: "travelStyle.NIGHTLIFE",
    SHOPPING: "travelStyle.SHOPPING",
    BEACH: "travelStyle.BEACH",
    SKI: "travelStyle.SKI",
  },
  notificationType: {
    APPLICATION_RECEIVED: "notificationType.APPLICATION_RECEIVED",
    APPLICATION_ACCEPTED: "notificationType.APPLICATION_ACCEPTED",
    APPLICATION_REJECTED: "notificationType.APPLICATION_REJECTED",
    NEW_MESSAGE: "notificationType.NEW_MESSAGE",
    REVIEW_RECEIVED: "notificationType.REVIEW_RECEIVED",
  },
  reviewTag: {
    PUNCTUAL: "reviewTag.PUNCTUAL",
    FRIENDLY: "reviewTag.FRIENDLY",
    GOOD_PLANNER: "reviewTag.GOOD_PLANNER",
    FLEXIBLE: "reviewTag.FLEXIBLE",
    TIDY: "reviewTag.TIDY",
    FUN: "reviewTag.FUN",
    KNOWLEDGEABLE: "reviewTag.KNOWLEDGEABLE",
    RESPECTFUL: "reviewTag.RESPECTFUL",
  },
};

/**
 * Map an enum value to a dictionary key.
 *
 * Deliberately an explicit table, not a template literal: `t(`${group}.${value}`)`
 * needs a cast, which widens `t()` to accept any string and gives up the
 * compile-time guarantee that every key exists.
 */
export function labelKey(group: LabelGroup, value: string | null | undefined): MessageKey | null {
  if (!value) return null;
  return LABEL_KEYS[group]?.[value] ?? null;
}

// ---------------------------------------------------------------------------
// Server-supplied codes — the same discipline, applied to dynamic strings
// ---------------------------------------------------------------------------

const SERVER_CODES: Record<string, MessageKey> = {
  "notif.application_received": "notif.application_received",
  "notif.application_accepted": "notif.application_accepted",
  "notif.application_rejected": "notif.application_rejected",
  "notif.review_received": "notif.review_received",
  "notif.new_message": "notif.new_message",
  "notif.new_message_in_room": "notif.new_message_in_room",
  "notif.legacy": "notif.legacy",
  "contentRejected.content_rejected": "contentRejected.content_rejected",
  "contentRejected.content_filter_unavailable": "contentRejected.content_filter_unavailable",
  "content_field.title": "content_field.title",
  "content_field.description": "content_field.description",
  "content_field.nickname": "content_field.nickname",
  "content_field.bio": "content_field.bio",
  "content_field.comment": "content_field.comment",
  "content_field.message": "content_field.message",
  "content_field.summary": "content_field.summary",
  "ws.safetyHint.possible_phone": "ws.safetyHint.possible_phone",
};

/**
 * Resolve a `<prefix>.<code>` string sent by the server.
 *
 * The backend sends a code, never a rendered sentence (B6): a row that outlives
 * the request must not freeze a language. An unknown code returns `null` so the
 * caller can fall back deliberately rather than render a raw key.
 */
export function serverCodeKey(code: string | null | undefined): MessageKey | null {
  if (!code) return null;
  return SERVER_CODES[code] ?? null;
}

// ---------------------------------------------------------------------------
// Locale formatting helpers
// ---------------------------------------------------------------------------

/** `Intl` wants `zh-HK`; `<html lang>` wants `zh-Hant-HK`. Not the same value. */
export function intlLocale(locale: Locale): string {
  return locale;
}

export function htmlLang(locale: Locale): string {
  return locale === "zh-HK" ? "zh-Hant-HK" : "en";
}
