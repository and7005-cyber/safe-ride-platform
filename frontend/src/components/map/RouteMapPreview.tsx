import { useMemo } from "react";
// `Map` aliased so the ES `Map` used for stop dedupe keeps its meaning.
import { AdvancedMarker, Map as GoogleMap } from "@vis.gl/react-google-maps";
import { FitBounds, RoutePolyline, type LatLng } from "@/components/map/MapPrimitives";
import { MAP_ID, hasMapsKey } from "@/lib/googleMaps";

export interface RouteStop {
  stop_order: number;
  name: string;
  lat: number | null;
  lng: number | null;
  is_school_gate: boolean;
}

/** Marker matching FleetMapPage's planner glyphs: numbered dot, ★ for the school gate. */
function StopGlyph({ seq, school }: { seq: number; school?: boolean }) {
  return (
    <div
      className={`flex h-6 w-6 items-center justify-center rounded-full border-2 border-white text-[11px] font-semibold text-white shadow ${
        school ? "bg-amber-600" : "bg-primary"
      }`}
    >
      {school ? "★" : seq}
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return (
    <div
      data-testid="route-map-placeholder"
      className="flex h-40 w-full items-center justify-center rounded-md border bg-muted/30 text-sm text-muted-foreground"
    >
      {text}
    </div>
  );
}

/**
 * Compact, non-interactive map preview of a route (R22): one marker per stop
 * order (siblings sharing a stop collapse to the first row), the stored road
 * polyline when present or straight segments otherwise, auto-fit to bounds.
 * No routing API calls. Key-less environments and stop-less routes degrade to
 * a placeholder instead of a broken map pane (R23).
 */
export function RouteMapPreview({
  stops,
  polyline,
}: {
  stops: RouteStop[];
  polyline?: string | null;
}) {
  // One marker per stop_order (first row wins — the backend orders rows by
  // stop_order, name), keeping only stops that actually have coordinates.
  const located = useMemo(() => {
    const byOrder = new Map<number, RouteStop>();
    for (const s of stops ?? []) {
      if (!byOrder.has(s.stop_order)) byOrder.set(s.stop_order, s);
    }
    return [...byOrder.values()]
      .filter((s) => s.lat != null && s.lng != null)
      .sort((a, b) => a.stop_order - b.stop_order);
  }, [stops]);

  if (!hasMapsKey) return <Placeholder text="Map unavailable" />;
  if (located.length < 1) return <Placeholder text="No map preview" />;

  const points: LatLng[] = located.map((s) => ({ lat: s.lat as number, lng: s.lng as number }));
  const focusKey = points.map((p) => `${p.lat},${p.lng}`).join("|");

  return (
    <div className="h-40 w-full overflow-hidden rounded-md border" data-testid="route-map-preview">
      <GoogleMap
        mapId={MAP_ID}
        defaultCenter={points[0]}
        defaultZoom={13}
        gestureHandling="none"
        disableDefaultUI
        className="h-full w-full"
      >
        <FitBounds points={points} focusKey={focusKey} padding={28} />
        {points.length > 1 && <RoutePolyline encoded={polyline} path={points} />}
        {located.map((s, i) => (
          <AdvancedMarker
            key={s.stop_order}
            position={points[i]}
            zIndex={s.is_school_gate ? 10 : 1}
          >
            <StopGlyph seq={s.stop_order} school={s.is_school_gate} />
          </AdvancedMarker>
        ))}
      </GoogleMap>
    </div>
  );
}
