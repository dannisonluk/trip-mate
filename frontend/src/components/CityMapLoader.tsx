"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2, MapPin } from "lucide-react";

import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { CitySuggestion } from "@/lib/types";

/** Leaflet cannot be server-rendered; see `CityMap.tsx`. */
const CityMap = dynamic(() => import("@/components/CityMap"), {
  ssr: false,
  loading: () => <div className="h-56 w-full animate-pulse rounded-md border bg-secondary" />,
});

interface CityMapLoaderProps {
  /** GeoNames id stored on the trip. `null` means the trip has no city. */
  cityId: number | null;
}

/**
 * Resolves a trip's `city_id` to coordinates and renders the map, or degrades to
 * plain text.
 *
 * The degradation contract is written down in `docs/SECURITY.md` §2.3 and is the
 * reason this component is not simply `<CityMap>`: **the map is presentation
 * only, and nothing about it may break the page.** Three ways it can fail, all
 * of them handled here without surfacing an error to the user:
 *
 * 1. the trip has no `city_id` (the field is optional);
 * 2. the id no longer resolves — a GeoNames re-import can replace the reference
 *    rows, and the FK is deliberately `ON DELETE SET NULL`, so a trip can lose
 *    its city. A `404` is the *expected* answer here, not an exception;
 * 3. the request fails for any other reason, or the tiles never load.
 *
 * In every case the caller keeps its own text rendering of the city and country,
 * so the page still says *where the trip goes*. That text is the primary
 * presentation; this component is an addition to it.
 */
export function CityMapLoader({ cityId }: CityMapLoaderProps) {
  const { t } = useI18n();
  const [city, setCity] = useState<CitySuggestion | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    if (cityId === null) {
      // No city on this trip: nothing to resolve, and nothing to say about it.
      setCity(null);
      setFailed(false);
      return;
    }

    setFailed(false);
    void (async () => {
      try {
        const resolved = await api.getCity(cityId);
        if (!cancelled) setCity(resolved);
      } catch {
        // Includes the 404 from a removed reference row. Not an error state for
        // the user — the trip still has `destination_city` as text.
        if (!cancelled) {
          setCity(null);
          setFailed(true);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [cityId]);

  if (cityId === null) return null;

  if (failed) {
    return (
      <p className="text-xs text-muted-foreground">{t("tripDetail.mapUnavailable")}</p>
    );
  }

  if (city === null) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {t("common.loading")}
      </div>
    );
  }

  const label = `${city.name}, ${city.country_name}`;

  return (
    <div className="space-y-1.5">
      {/* `label` is the map's accessible name, so the destination is announced
          once. An extra visually-hidden copy of the header's "city country" used
          to sit in the paragraph below; it duplicated what the header already
          says and made every `getByText("Tokyo")` ambiguous. */}
      <CityMap latitude={city.latitude} longitude={city.longitude} label={label} />
      <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
        <MapPin className="h-3.5 w-3.5" aria-hidden />
        {/* The exact stored value. The coordinate is a GeoNames city centre, and
            showing it at full precision is what the trip actually holds — see
            `docs/SECURITY.md` §2.3 for why that carries no extra privacy cost. */}
        <span className="font-mono">
          {city.latitude}, {city.longitude}
        </span>
      </p>
    </div>
  );
}
