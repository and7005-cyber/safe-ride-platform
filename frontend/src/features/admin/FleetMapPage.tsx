import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import { Plus, Route as RouteIcon, Trash2, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { NAIROBI } from "@/lib/leafletSetup";
import { api } from "@/lib/apiClient";
import { useBuses, useSchools } from "@/lib/queries";

// Distinct, high-contrast colours assigned deterministically per bus.
const PALETTE = [
  "#2f6f4f", "#2563eb", "#dc2626", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#475569", "#ea580c",
];

const BUS_PATH =
  "M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z";

function busIcon(color: string) {
  const svg = `<svg viewBox="0 0 24 24" width="18" height="18" fill="#ffffff" aria-hidden="true"><path d="${BUS_PATH}"/></svg>`;
  const html =
    `<div style="background:${color};width:30px;height:30px;border-radius:9999px;` +
    `display:flex;align-items:center;justify-content:center;border:2px solid #ffffff;` +
    `box-shadow:0 1px 4px rgba(0,0,0,.45)">${svg}</div>`;
  return L.divIcon({
    html,
    className: "saferide-bus-marker",
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    popupAnchor: [0, -18],
  });
}

// Fit the viewport to the live buses (or planned stops). Refits only when the
// focusKey changes — not on every GPS-free position tick — so it never fights
// the admin panning the map.
function FitBounds({ points, focusKey }: { points: [number, number][]; focusKey: string }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 14);
    else if (points.length > 1) map.fitBounds(points, { padding: [60, 60], maxZoom: 15 });
    else map.setView(NAIROBI, 12);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);
  return null;
}

interface PlanStop { address: string; pickup_time: string }
interface OptionStop { seq: number; label: string; lat: number; lng: number; pickup_time?: string | null; is_school?: boolean }
interface RouteOption { strategy: string; stops: OptionStop[] }

export function FleetMapPage() {
  // Live polling so the map keeps up as buses arrive at stops (#8).
  const { data: buses = [] } = useBuses({ poll: true });
  const { data: schools = [] } = useSchools();
  const { toast } = useToast();

  const located = (buses as any[]).filter((b) => b.current_lat != null && b.current_lng != null);

  // Stable colour per bus, by sorted id so it doesn't shuffle as data changes.
  const colorMap = useMemo(() => {
    const ids = (buses as any[]).map((b) => b.id).sort();
    const m: Record<string, string> = {};
    ids.forEach((id, i) => { m[id] = PALETTE[i % PALETTE.length]; });
    return m;
  }, [buses]);

  // Route planner state (#9).
  const [rows, setRows] = useState<PlanStop[]>([{ address: "", pickup_time: "" }]);
  const [type, setType] = useState("morning");
  const [schoolId, setSchoolId] = useState("none");
  const [options, setOptions] = useState<RouteOption[] | null>(null);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [provider, setProvider] = useState("");
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);

  const setRow = (i: number, patch: Partial<PlanStop>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { address: "", pickup_time: "" }]);
  const removeRow = (i: number) => setRows((rs) => rs.filter((_, idx) => idx !== i));

  const plan = async () => {
    const stops = rows.filter((r) => r.address.trim());
    if (stops.length < 2) {
      toast({ title: "Add at least two addresses", variant: "destructive" });
      return;
    }
    setLoading(true);
    try {
      const res = await api.post("/api/fleet/route-options", {
        type,
        school_id: schoolId === "none" ? null : schoolId,
        stops: stops.map((s) => ({ address: s.address, pickup_time: s.pickup_time || null })),
      });
      setOptions(res.options ?? []);
      setUnresolved(res.unresolved ?? []);
      setProvider(res.provider ?? "");
      setSelected(0);
      if ((res.unresolved ?? []).length) {
        toast({
          title: "Some addresses couldn't be located",
          description: (res.unresolved as string[]).join(", "),
          variant: "destructive",
        });
      }
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const activeOption = options?.[selected];
  const planned = activeOption?.stops ?? [];
  const plannedLine = planned.map((s) => [s.lat, s.lng]) as [number, number][];

  const focusPoints: [number, number][] = planned.length
    ? plannedLine
    : located.map((b) => [b.current_lat, b.current_lng] as [number, number]);
  const focusKey = planned.length
    ? `plan:${planned.map((s) => `${s.lat},${s.lng}`).join("|")}`
    : `buses:${located.map((b) => b.id).sort().join(",")}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-bold">Fleet Map</h1>
        <p className="text-sm text-muted-foreground">
          Live bus positions · {located.length} active{located.length === 1 ? " bus" : " buses"}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="overflow-hidden lg:col-span-2">
          <div className="h-[70vh] w-full">
            <MapContainer center={NAIROBI} zoom={12} className="h-full w-full" scrollWheelZoom>
              <TileLayer
                attribution="&copy; OpenStreetMap contributors"
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <FitBounds points={focusPoints} focusKey={focusKey} />
              {located.map((bus: any) => (
                <Marker
                  key={bus.id}
                  position={[bus.current_lat, bus.current_lng]}
                  icon={busIcon(colorMap[bus.id] ?? PALETTE[0])}
                >
                  <Popup>
                    <div className="space-y-1 text-sm">
                      <p className="font-semibold" style={{ color: colorMap[bus.id] }}>{bus.name}</p>
                      <p>{bus.position_label ?? "On the move"}</p>
                      <p>Driver: {bus.driver_name ?? "—"}</p>
                      <p>Plate: {bus.plate_number ?? "—"}</p>
                    </div>
                  </Popup>
                </Marker>
              ))}
              {plannedLine.length > 1 && <Polyline positions={plannedLine} color="#2f6f4f" />}
              {planned.map((s, i) => (
                <Marker key={`plan-${i}`} position={[s.lat, s.lng]}>
                  <Popup>
                    <div className="text-sm">
                      <p className="font-semibold">{s.seq}. {s.label}</p>
                      {s.pickup_time && <p>Pickup: {s.pickup_time}</p>}
                    </div>
                  </Popup>
                </Marker>
              ))}
            </MapContainer>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader className="pb-2">
              <p className="font-heading text-lg font-semibold">Buses</p>
            </CardHeader>
            <CardContent className="space-y-2">
              {located.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No buses are on an active run. A bus appears here once its driver starts a run —
                  its position updates as they arrive at each stop.
                </p>
              ) : (
                located.map((bus: any) => (
                  <div key={bus.id} className="flex items-center gap-2 text-sm">
                    <span
                      className="h-3 w-3 shrink-0 rounded-full border border-white shadow"
                      style={{ background: colorMap[bus.id] }}
                    />
                    <span className="font-medium">{bus.name}</span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {bus.position_label ?? "Live"}
                    </span>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center gap-2 space-y-0">
              <RouteIcon className="h-5 w-5 text-primary" />
              <p className="font-heading text-lg font-semibold">Route planner</p>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Enter addresses and pickup times to get suggested route orders.
              </p>

              <div className="space-y-2">
                {rows.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      placeholder="Address"
                      value={r.address}
                      onChange={(e) => setRow(i, { address: e.target.value })}
                    />
                    <Input
                      type="time"
                      className="w-28"
                      value={r.pickup_time}
                      onChange={(e) => setRow(i, { pickup_time: e.target.value })}
                    />
                    <Button variant="ghost" size="icon" onClick={() => removeRow(i)} disabled={rows.length === 1}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={addRow}><Plus className="h-4 w-4" /> Add stop</Button>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label>Direction</Label>
                  <Select value={type} onValueChange={setType}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="morning">Morning</SelectItem>
                      <SelectItem value="afternoon">Afternoon</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label>School</Label>
                  <Select value={schoolId} onValueChange={setSchoolId}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">— None —</SelectItem>
                      {schools.map((s: any) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button onClick={plan} disabled={loading} className="w-full">
                <Wand2 className="h-4 w-4" /> {loading ? "Planning…" : "Get route options"}
              </Button>

              {options && (
                <div className="space-y-3 pt-2">
                  {provider && <p className="text-xs text-muted-foreground">Ordered via {provider}</p>}
                  {unresolved.length > 0 && (
                    <p className="text-xs text-destructive">Could not locate: {unresolved.join(", ")}</p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {options.map((o, i) => (
                      <Button
                        key={o.strategy}
                        variant={i === selected ? "default" : "outline"}
                        size="sm"
                        onClick={() => setSelected(i)}
                      >
                        {o.strategy}
                      </Button>
                    ))}
                  </div>
                  {activeOption && (
                    <ol className="space-y-1 text-sm">
                      {activeOption.stops.map((s) => (
                        <li key={s.seq} className="flex items-center gap-2">
                          <span className="font-medium">{s.seq}.</span>
                          <span className="flex-1 truncate">{s.label}</span>
                          {s.is_school && <Badge variant="outline">School</Badge>}
                          {s.pickup_time && <span className="text-xs text-muted-foreground">{s.pickup_time}</span>}
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
