import { useEffect } from "react";
import { useMap, useMapsLibrary } from "@vis.gl/react-google-maps";

export type LatLng = { lat: number; lng: number };

/**
 * Fit the map to a set of points. Refits only when `focusKey` changes — not on
 * every position tick — so it never fights the admin panning the map.
 */
export function FitBounds({ points, focusKey }: { points: LatLng[]; focusKey: string }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    if (points.length === 1) {
      map.setCenter(points[0]);
      map.setZoom(14);
    } else if (points.length > 1) {
      const bounds = new google.maps.LatLngBounds();
      points.forEach((p) => bounds.extend(p));
      map.fitBounds(bounds, 64);
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
