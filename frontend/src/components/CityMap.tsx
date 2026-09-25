"use client";

import { useEffect, useRef } from "react";
import type { Map as LeafletMap } from "leaflet";

/** OpenStreetMap tile endpoint. */
const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
/** Level 10 is roughly "a city and its suburbs" — the map is context, not a tool
 *  for finding an address, and a closer zoom would imply an accuracy we do not
 *  claim (see `docs/SECURITY.md` §2.3: the coordinate is a city centre only). */
const CITY_ZOOM = 10;

interface CityMapProps {
  latitude: number;
  longitude: number;
  /** Used for the accessible description, e.g. "Tokyo, Japan". */
  label: string;
}

/**
 * The map itself. **Must be loaded with `next/dynamic` and `ssr: false`** — see
 * `CityMapLoader.tsx`.
 *
 * Leaflet reads `window`/`document` at module scope, so importing it during
 * server rendering throws. That constraint is why this file is separate from the
 * loader: the loader can be a server-safe module that decides *whether* a map is
 * worth rendering, and only pulls this in when it is.
 */
export default function CityMap({ latitude, longitude, label }: CityMapProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<LeafletMap | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Imported inside the effect as well as dynamically loaded by the parent:
    // the parent guarantees this never runs on the server, and this guarantees
    // the `window` access happens after mount even in a test environment that
    // ignores the dynamic import.
    void (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !container.current || map.current) return;

      map.current = L.map(container.current, {
        center: [latitude, longitude],
        zoom: CITY_ZOOM,
        // The coordinate marks a city, not a point of interest. Panning is
        // allowed so the user can orient themselves, but the map is not a
        // navigation surface — and `scrollWheelZoom` off avoids hijacking the
        // page scroll, which is the usual reason embedded maps feel broken.
        scrollWheelZoom: false,
        attributionControl: true,
      });

      L.tileLayer(TILE_URL, {
        maxZoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(map.current);

      L.circleMarker([latitude, longitude], {
        radius: 8,
        weight: 2,
        color: "#ffffff",
        fillColor: "#2563eb",
        fillOpacity: 1,
      }).addTo(map.current);
    })();

    return () => {
      cancelled = true;
      // `remove()` both detaches listeners and clears the container. Leaving it
      // out leaks a map instance per mount, and Leaflet throws
      // "Map container is already initialized" on remount.
      map.current?.remove();
      map.current = null;
    };
  }, [latitude, longitude]);

  return (
    <div
      ref={container}
      // Leaflet's own CSS needs a positioned, sized box. The height is fixed
      // rather than aspect-based so the map cannot push the rest of the page
      // around while tiles load.
      className="h-56 w-full overflow-hidden rounded-md border"
      role="img"
      aria-label={label}
    />
  );
}
