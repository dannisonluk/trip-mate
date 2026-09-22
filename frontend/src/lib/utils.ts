import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, resolving Tailwind conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Option values for the multi-select fields, in display order.
 *
 * Only the *values* live here — the labels are in the translation dictionary
 * (`lib/i18n/dictionaries.ts`) and are reached through `useI18n().label()`, so
 * that adding a language cannot leave a hard-coded label behind.
 *
 * Date formatting also lives in the i18n module now: it is locale-dependent and
 * used to hard-code `zh-HK` at the call site.
 */

export const BUDGET_OPTIONS = ["BUDGET", "MODERATE", "LUXURY"];

/** Target-gender options for a trip post. Distinct from the profile gender
 *  list, which also has OTHER. */
export const TARGET_GENDER_OPTIONS = ["MALE", "FEMALE", "ANY"];

export const TRAVEL_STYLE_OPTIONS = [
  "BACKPACKER",
  "PHOTOGRAPHY",
  "FOOD",
  "CULTURE",
  "HIKING",
  "NATURE",
  "ADVENTURE",
  "CAFE",
  "NIGHTLIFE",
  "SHOPPING",
  "BEACH",
  "SKI",
];

export const REVIEW_TAG_OPTIONS = [
  "PUNCTUAL",
  "FRIENDLY",
  "GOOD_PLANNER",
  "FLEXIBLE",
  "TIDY",
  "FUN",
  "KNOWLEDGEABLE",
  "RESPECTFUL",
];
