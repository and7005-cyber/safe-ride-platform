import { Fragment, useMemo } from "react";
// `Map` aliased so the ES `Map` keeps its meaning (RouteMapPreview precedent).
import { AdvancedMarker, Map as GoogleMap } from "@vis.gl/react-google-maps";
import { FitBounds, RoutePolyline, type LatLng } from "@/components/map/MapPrimitives";
import { MAP_ID, hasMapsKey } from "@/lib/googleMaps";

// U9 — the draft plan on one map: every bus's leg as a polyline through its
// stops (straight segments — a draft document carries no road geometry; the
// encoded polyline arrives only after apply's post-commit refresh) plus
// numbered stop markers in the bus's colour and a ★ marker on the school gate.
// Non-interactive beyond pan/zoom; key-less environments degrade to a
// placeholder like RouteMapPreview.

export interface PlanMapStop {
  key: string;
  name: string | null;
  lat: number | null;
  lng: number | null;
}

export interface PlanMapBus {
  bus_id: string;
  bus_name: string | null;
  color: string;
  stops: PlanMapStop[];
}

function StopGlyph({ seq, color }: { seq: number; color: string }) {
  return (
    <div
      style={{ background: color }}
      className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-white text-[11px] font-semibold text-white shadow"
    >
      {seq}
    </div>
  );
}

function SchoolGlyph() {
  return (
    <div className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-white bg-amber-600 text-[11px] font-semibold text-white shadow">
      ★
    </div>
  );
}

export function PlanDraftMap({
  buses,
  leg,
  school,
}: {
  buses: PlanMapBus[];
  /** Which leg's stop sequences are drawn ("morning" | "afternoon"). */
  leg: string;
  school: { lat: number; lng: number } | null;
}) {
  const located = useMemo(
    () =>
      buses.map((bus) => ({
        ...bus,
        stops: bus.stops.filter((s) => s.lat != null && s.lng != null),
      })),
    [buses],
  );

  const points: LatLng[] = useMemo(() => {
    const all: LatLng[] = located.flatMap((bus) =>
      bus.stops.map((s) => ({ lat: s.lat as number, lng: s.lng as number })),
    );
    if (school) all.push(school);
    return all;
  }, [located, school]);

  if (!hasMapsKey) {
    return (
      <div
        data-testid="plan-map-placeholder"
        className="flex h-72 w-full items-center justify-center rounded-md border bg-muted/30 text-sm text-muted-foreground"
      >
        Map unavailable
      </div>
    );
  }
  if (points.length === 0) {
    return (
      <div
        data-testid="plan-map-placeholder"
        className="flex h-72 w-full items-center justify-center rounded-md border bg-muted/30 text-sm text-muted-foreground"
      >
        No stops to show
      </div>
    );
  }

  const focusKey = `${leg}:${points.map((p) => `${p.lat},${p.lng}`).join("|")}`;

  return (
    <div className="h-[28rem] w-full overflow-hidden rounded-md border" data-testid="plan-map">
      <GoogleMap
        mapId={MAP_ID}
        defaultCenter={points[0]}
        defaultZoom={13}
        gestureHandling="greedy"
        disableDefaultUI
        className="h-full w-full"
      >
        <FitBounds points={points} focusKey={focusKey} padding={40} />
        {located.map((bus) => {
          const path: LatLng[] = bus.stops.map((s) => ({
            lat: s.lat as number,
            lng: s.lng as number,
          }));
          // Morning legs run home → gate; afternoon gate → homes. Append or
          // prepend the school so the drawn leg ends where the bus does.
          const withSchool: LatLng[] = school
            ? leg === "afternoon"
              ? [school, ...path]
              : [...path, school]
            : path;
          return (
            <Fragment key={bus.bus_id}>
              {withSchool.length > 1 && <RoutePolyline path={withSchool} color={bus.color} />}
              {bus.stops.map((s, i) => (
                <AdvancedMarker
                  key={`${bus.bus_id}-${s.key}`}
                  position={{ lat: s.lat as number, lng: s.lng as number }}
                  title={s.name ?? undefined}
                >
                  <StopGlyph seq={i + 1} color={bus.color} />
                </AdvancedMarker>
              ))}
            </Fragment>
          );
        })}
        {school && (
          <AdvancedMarker position={school} zIndex={10} title="School gate">
            <SchoolGlyph />
          </AdvancedMarker>
        )}
      </GoogleMap>
    </div>
  );
}
