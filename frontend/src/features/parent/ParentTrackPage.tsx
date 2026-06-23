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
import { FitBounds, RoutePolyline, type LatLng } from "@/components/map/MapPrimitives";
import { MAP_ID, NAIROBI } from "@/lib/googleMaps";
import { PARENT_NAV, useChildren, useTrack } from "@/features/parent/parentHooks";

export function ParentTrackPage() {
  const { data: children = [] } = useChildren();
  const [studentId, setStudentId] = useState<string | null>(null);
  const { data: track } = useTrack(studentId);

  useEffect(() => {
    if (!studentId && children.length > 0) setStudentId(children[0].id);
  }, [children, studentId]);

  const stops = track?.stops ?? [];
  const run = track?.run;
  const completed = run?.stops_completed ?? 0;
  const busLive = track?.student?.bus_current_lat != null;
  const points: LatLng[] = stops
    .filter((s: any) => s.lat != null && s.lng != null)
    .map((s: any) => ({ lat: s.lat, lng: s.lng }));

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
              <FitBounds
                points={points}
                focusKey={`track:${points.map((p) => `${p.lat},${p.lng}`).join("|")}`}
              />
              {points.length > 1 && <RoutePolyline path={points} color="#206F4A" />}
              {points.map((p, i) => (
                <AdvancedMarker key={i} position={p}>
                  <span
                    className={`block h-3.5 w-3.5 rounded-full border-2 border-white shadow ${
                      i < completed ? "bg-emerald-600" : "bg-slate-500"
                    }`}
                  />
                </AdvancedMarker>
              ))}
            </Map>
          </div>
          {/* Live badge only — the bus position is NOT plotted on the map (live parity). */}
          {busLive && (
            <div className="flex justify-center py-2">
              <Badge variant="outline" className="animate-pulse-dot">
                {track?.student?.bus_name ?? "Bus"} is live
              </Badge>
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
