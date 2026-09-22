import { useEffect, useState } from "react";
import { AdvancedMarker, Map } from "@vis.gl/react-google-maps";
import { CheckCircle2, MapPin } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import {
  BusMarkerGlyph,
  FitBounds,
  RoutePolyline,
  useNow,
  type LatLng,
} from "@/components/map/MapPrimitives";
import { MAP_ID, NAIROBI } from "@/lib/googleMaps";
import { freshnessLabel } from "@/lib/positionFreshness";
import { PARENT_NAV, useChildren, useTrack } from "@/features/parent/parentHooks";

// The bus colour on the parent map: the route line's green, so the dot reads
// as "the bus on this line" rather than a second kind of stop.
const BUS_COLOR = "#206F4A";

export function ParentTrackPage() {
  const { data: children = [] } = useChildren();
  const [studentId, setStudentId] = useState<string | null>(null);
  const { data: track } = useTrack(studentId);
  // A 5 s tick keeps "updated X ago" counting between polls: the parent's
  // payload carries no age, and an unchanged position would freeze the label.
  const now = useNow();

  useEffect(() => {
    if (!studentId && children.length > 0) setStudentId(children[0].id);
  }, [children, studentId]);

  const stops = track?.stops ?? [];
  const run = track?.run;
  const completed = run?.stops_completed ?? 0;
  // The bus is served only while the child's run is in progress (GPS plan
  // U8, R37): the position IS the live signal, so the badge keys off it as it
  // did off the bus coordinates before.
  const position = track?.bus_position ?? null;
  const busLive = position != null;
  const busName = track?.bus?.name ?? track?.student?.bus_name ?? "Bus";
  const freshness = freshnessLabel(position, now);
  const points: LatLng[] = stops
    .filter((s: any) => s.lat != null && s.lng != null)
    .map((s: any) => ({ lat: s.lat, lng: s.lng }));
  // Fit the stops and the bus once per (route, bus) — keyed on the bus id, not
  // its position, so the map never re-fits under a parent's fingers as the
  // dot moves.
  const fitPoints = position ? [...points, { lat: position.lat, lng: position.lng }] : points;
  const focusKey = `track:${points.map((p) => `${p.lat},${p.lng}`).join("|")}|bus:${track?.bus?.id ?? ""}`;

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="Track Bus">
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <h1 className="font-heading text-xl font-bold">Track</h1>
          {children.length > 1 && (
            <Select value={studentId ?? ""} onValueChange={setStudentId}>
              <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                {children.map((c: any) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
        </div>

        <Card className="overflow-hidden">
          <div className="h-56 w-full" data-testid="track-map">
            <Map
              mapId={MAP_ID}
              defaultCenter={points[0] ?? NAIROBI}
              defaultZoom={13}
              gestureHandling="greedy"
              className="h-full w-full"
            >
              <FitBounds points={fitPoints} focusKey={focusKey} />
              {points.length > 1 && <RoutePolyline path={points} color={BUS_COLOR} />}
              {points.map((p, i) => (
                <AdvancedMarker key={i} position={p}>
                  <span
                    className={`block h-3.5 w-3.5 rounded-full border-2 border-white shadow ${
                      i < completed ? "bg-emerald-600" : "bg-slate-500"
                    }`}
                  />
                </AdvancedMarker>
              ))}
              {/* The bus, plotted for the first time (R9): one marker, one
                  child; stale is a class toggle on the glyph, with the same
                  dimming the fleet map uses (R27). */}
              {position && (
                <AdvancedMarker
                  key="bus"
                  position={{ lat: position.lat, lng: position.lng }}
                  zIndex={20}
                >
                  <BusMarkerGlyph
                    color={BUS_COLOR}
                    busId={track?.bus?.id}
                    stale={position.stale}
                    title={`${busName} — ${freshness ?? "position time unknown"}`}
                  />
                </AdvancedMarker>
              )}
            </Map>
          </div>
          {/* The live badge as before, now with the position's freshness
              beside it: "updated X ago" while fresh, "last seen X ago" once
              the server says stale — the fleet map's wording (R27). */}
          {busLive && (
            <div className="flex flex-wrap items-center justify-center gap-2 py-2">
              <Badge variant="outline" className="animate-pulse-dot">
                {busName} is live
              </Badge>
              {freshness && (
                <span
                  data-testid="track-freshness"
                  data-stale={position.stale ? "true" : "false"}
                  className={`text-xs ${position.stale ? "text-amber-700" : "text-muted-foreground"}`}
                >
                  {freshness}
                </span>
              )}
            </div>
          )}
        </Card>

        <Card>
          <CardContent className="p-4">
            {stops.length === 0 ? (
              <p className="text-sm text-muted-foreground">No route assigned to your children yet.</p>
            ) : (
              <ol className="space-y-2">
                {stops.map((s: any) => {
                  const done = s.stop_order <= completed;
                  return (
                    <li key={s.stop_order} className="flex items-center gap-2 text-sm">
                      {done ? <CheckCircle2 className="h-4 w-4 text-success" /> : <MapPin className="h-4 w-4 text-muted-foreground" />}
                      <span className={done ? "text-muted-foreground" : ""}>{s.name}</span>
                      {s.is_school_gate && <Badge variant="outline" className="ml-auto">School</Badge>}
                      {s.is_own && <Badge variant="success" className="ml-auto">Your stop</Badge>}
                    </li>
                  );
                })}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>
    </RoleMobileLayout>
  );
}
