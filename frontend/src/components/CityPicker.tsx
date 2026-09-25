"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Loader2, MapPin, X } from "lucide-react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { CitySuggestion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** Below this the API answers with an empty list by design, so asking is wasted
 *  work. Kept in sync with `MIN_QUERY_LENGTH` in `backend/app/api/v1/cities.py`. */
const MIN_QUERY_LENGTH = 2;
/** Keystroke debounce. The picker fires per keystroke, and each request is a
 *  prefix scan; 250 ms is short enough to feel immediate and long enough that a
 *  normal typing burst produces one request rather than six. */
const DEBOUNCE_MS = 250;

export interface CitySelection {
  /** GeoNames `geonameid`, or null when nothing is selected. */
  id: number | null;
  /** Display name, stored alongside the id so the list can render without a join. */
  name: string;
  /** ISO-3166 alpha-2, used to pre-fill the country field. */
  countryCode: string;
  countryName: string;
}

interface CityPickerProps {
  /** Current selection, or null when the field is blank. */
  value: CitySelection | null;
  onChange: (next: CitySelection | null) => void;
  /** Pre-filters suggestions, e.g. to the country already chosen. */
  countryCode?: string;
  id?: string;
  placeholder?: string;
  /** Rendered under the field, e.g. the "filling this helps matching" hint. */
  hint?: string;
  /** Marks the control as required for form validation. */
  required?: boolean;
  disabled?: boolean;
}

/**
 * A **select-only** city combobox: the user picks from a known list, or picks
 * nothing.
 *
 * The product rule this enforces is that a city is one of a known set. Free
 * text was removed because a typed city cannot be verified — and an
 * unverifiable spelling silently fails to match, so the user sees a worse match
 * score with no way to discover why. The field is optional; leaving it blank is
 * a supported outcome, not an error state.
 *
 * Why not the existing `Select`: the reference table is 69,740 rows, so the
 * control has to be a *search* rather than an enumeration. Radix `Select` with
 * several thousand `SelectItem`s would render them all.
 *
 * The `open` state is controlled on purpose. Radix `Popover` does not close
 * when a button inside it is clicked, so an uncontrolled popover would stay on
 * screen after a selection and the user's next click would dismiss it instead of
 * doing what they intended.
 */
export function CityPicker({
  value,
  onChange,
  countryCode,
  id,
  placeholder,
  hint,
  required,
  disabled,
}: CityPickerProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [items, setItems] = useState<CitySuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  // The request counter is what makes out-of-order responses harmless. Without
  // it, a slow response for "os" can land after a fast one for "osaka" and
  // replace the correct list with a stale one.
  const requestId = useRef(0);
  const inputId = useId();

  useEffect(() => {
    if (!open) return;
    const trimmed = term.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      // The API would return an empty list anyway; not asking is both faster and
      // the honest representation of "nothing to suggest yet".
      setItems([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    const seq = ++requestId.current;
    const timer = setTimeout(async () => {
      try {
        const page = await api.suggestCities({ q: trimmed, country: countryCode, limit: 8 });
        if (seq !== requestId.current) return; // superseded by a later keystroke
        setItems(page.suggestions);
      } catch {
        // A failed suggestion lookup must not obstruct the form. The user can
        // still submit with the city blank, which is a valid state.
        if (seq === requestId.current) setItems([]);
      } finally {
        if (seq === requestId.current) setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
    // `t` is deliberately absent: it is only used inside the catch for a message
    // that is not shown here, and adding it would re-fire the debounce on every
    // locale change while the popover is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term, open, countryCode]);

  function select(item: CitySuggestion) {
    onChange({
      id: item.id,
      name: item.name,
      countryCode: item.country_code,
      countryName: item.country_name,
    });
    setTerm("");
    setOpen(false);
  }

  function clear() {
    onChange(null);
    setTerm("");
  }

  const label = value ? formatCity(value) : "";

  return (
    <div className="space-y-1">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            id={id ?? inputId}
            disabled={disabled}
            aria-expanded={open}
            aria-haspopup="listbox"
            className={cn(
              "flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm",
              "ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
              "disabled:cursor-not-allowed disabled:opacity-50",
              !value && "text-muted-foreground",
            )}
          >
            <span className="flex min-w-0 items-center gap-2">
              <MapPin className="h-4 w-4 shrink-0 opacity-60" aria-hidden />
              <span className="truncate">{label || placeholder || t("cityPicker.placeholder")}</span>
            </span>
            {value ? (
              <span
                role="button"
                tabIndex={0}
                aria-label={t("cityPicker.clear")}
                className="shrink-0 rounded-sm p-0.5 opacity-60 hover:opacity-100"
                onClick={(e) => {
                  // Without this the click would also toggle the popover open.
                  e.stopPropagation();
                  clear();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    e.preventDefault();
                    clear();
                  }
                }}
              >
                <X className="h-4 w-4" />
              </span>
            ) : null}
          </button>
        </PopoverTrigger>

        <PopoverContent
          align="start"
          className="w-[--radix-popover-trigger-width] min-w-[18rem] p-2"
          onOpenAutoFocus={(e) => {
            // Focus the search field, not the first suggestion: typing is the
            // expected first action, and focusing a suggestion would make Enter
            // select a city the user never chose.
            e.preventDefault();
            document.getElementById(`${inputId}-search`)?.focus();
          }}
        >
          <Input
            id={`${inputId}-search`}
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder={t("cityPicker.searchPlaceholder")}
            autoComplete="off"
            className="mb-2"
          />

          <ul role="listbox" aria-label={t("cityPicker.listLabel")} className="max-h-64 overflow-y-auto">
            {loading ? (
              <li className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {t("common.loading")}
              </li>
            ) : items.length === 0 ? (
              <li className="px-2 py-3 text-sm text-muted-foreground">
                {term.trim().length < MIN_QUERY_LENGTH
                  ? t("cityPicker.typeToSearch")
                  : t("cityPicker.noMatch")}
              </li>
            ) : (
              items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={value?.id === item.id}
                    onClick={() => select(item)}
                    className={cn(
                      "flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left text-sm",
                      "hover:bg-secondary focus:bg-secondary focus:outline-none",
                      value?.id === item.id && "bg-secondary",
                    )}
                  >
                    <span className="font-medium">{item.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {disambiguate(item)}
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
        </PopoverContent>
      </Popover>

      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {/* A hidden input carries `required` into native form validation: the
          visible control is a button, so the browser cannot enforce it itself. */}
      {required ? (
        <input
          type="text"
          tabIndex={-1}
          aria-hidden
          required
          value={value ? String(value.id) : ""}
          onChange={() => undefined}
          className="pointer-events-none absolute h-0 w-0 opacity-0"
        />
      ) : null}
    </div>
  );
}

/** `City, Region, Country`, omitting the parts that are absent. */
export function formatCity(city: CitySelection): string {
  return [city.name, city.countryName].filter(Boolean).join(", ");
}

/** The second line of a suggestion: everything that tells two candidates apart.
 *
 * `Santa Cruz` occurs 16 times in the real table, so a list of bare names is
 * unusable. Region and country are what the user actually recognises.
 */
function disambiguate(item: CitySuggestion): string {
  return [item.admin1_name, item.country_name].filter(Boolean).join(" · ");
}
