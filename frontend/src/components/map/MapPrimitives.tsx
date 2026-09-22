import { useEffect, useState } from "react";
import { useMap, useMapsLibrary } from "@vis.gl/react-google-maps";
import { cn } from "@/lib/utils";

export type LatLng = { lat: number; lng: number };

/** Re-render on a fixed tick so "updated X ago" keeps counting between polls
 * (a parent's payload carries no age, and an unchanged position would
 * otherwise freeze the label until the next data change). */
export function useNow(intervalMs = 5_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

const BUS_GLYPH_PATH =
  "M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z";

/**
 * The bus dot on the fleet map and the parent Track map (GPS plan U8).
 *
 * ONE element with class toggles, never a conditional child: an
 * AdvancedMarker whose child count changes between polls is torn down and
 * recreated, which flickers and resets the info window. Stale dims the glyph
 * (R27, the "last seen" state); GPS-off swaps the solid ring for a dashed red
 * one (F5, AE2). Both states also ride data attributes so a test — or a
 * screen reader through `title` — can read them without inspecting styles.
 */
export function BusMarkerGlyph({
  color,
  stale = false,
  gpsOff = false,
  busId,
  title,
}: {
  color: string;
  stale?: boolean;
  gpsOff?: boolean;
  busId?: string;
  title?: string;
}) {
  return (
    <div
      data-testid="bus-marker"
      data-bus-id={busId}
      data-stale={stale ? "true" : "false"}
      data-gps-off={gpsOff ? "true" : "false"}
      title={title}
      style={{ background: color }}
      className={cn(
        "flex h-[30px] w-[30px] items-center justify-center rounded-full border-2 shadow-[0_1px_4px_rgba(0,0,0,.45)] transition-opacity",
        gpsOff ? "border-dashed border-red-600 ring-2 ring-red-600/50" : "border-white",
        stale && "opacity-50 saturate-50",
      )}
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="#fff" aria-hidden="true">
        <path d={BUS_GLYPH_PATH} />
      </svg>
    </div>
  );
}

/**
 * A fix's accuracy radius around the bus (staff surfaces only). The caller
 * caps the radius (`accuracyRadius`); the circle is not clickable so it never
 * steals the marker's tap.
 */
export function AccuracyCircle({
  center,
  radiusM,
  color = "#2f6f4f",
}: {
  center: LatLng;
  radiusM: number;
  color?: string;
}) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const circle = new google.maps.Circle({
      map,
      center,
      radius: radiusM,
      clickable: false,
      strokeColor: color,
      strokeOpacity: 0.5,
      strokeWeight: 1,
      fillColor: color,
      fillOpacity: 0.12,
    });
    return () => circle.setMap(null);
  }, [map, center.lat, center.lng, radiusM, color]);
  return null;
}

/**
 * Fit the map to a set of points. Refits only when `focusKey` changes — not on
 * every position tick — so it never fights the admin panning the map.
 */
export function FitBounds({
  points,
  focusKey,
  padding = 64,
}: {
  points: LatLng[];
  focusKey: string;
  /** Pixel padding around the bounds; compact panes want less than the 64px default. */
  padding?: number;
}) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    if (points.length === 1) {
      map.setCenter(points[0]);
      map.setZoom(14);
    } else if (points.length > 1) {
      const bounds = new google.maps.LatLngBounds();
      points.forEach((p) => bounds.extend(p));
      map.fitBounds(bounds, padding);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, focusKey]);
  return null;
}

/**
 * Draw a route line on the map. Prefers a Google-encoded polyline (the real
 * road geometry from the Routes API); falls back to straight segments through
 * `path` when there's no encoded line (offline mode).
 */
export function RoutePolyline({
  encoded,
  path,
  color = "#2f6f4f",
}: {
  encoded?: string | null;
  path?: LatLng[];
  color?: string;
}) {
  const map = useMap();
  const geometry = useMapsLibrary("geometry");
  useEffect(() => {
    if (!map) return;
    let coords: LatLng[] | undefined = path;
    if (encoded && geometry) {
      coords = geometry.encoding.decodePath(encoded).map((p) => ({ lat: p.lat(), lng: p.lng() }));
    }
    if (!coords || coords.length < 2) return;
    const line = new google.maps.Polyline({
      path: coords,
      strokeColor: color,
      strokeOpacity: 0.9,
      strokeWeight: 5,
    });
    line.setMap(map);
    return () => line.setMap(null);
  }, [map, geometry, encoded, color, JSON.stringify(path)]);
  return null;
}
