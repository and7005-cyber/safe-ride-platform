import "leaflet/dist/leaflet.css";
import { useState } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer } from "react-leaflet";
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

interface PlanStop {
  address: string;
  pickup_time: string;
}

interface OptionStop {
  seq: number;
  label: string;
  lat: number;
  lng: number;
  pickup_time?: string | null;
  is_school?: boolean;
}

interface RouteOption {
  strategy: string;
  stops: OptionStop[];
}

export function FleetMapPage() {
  // Live polling so the map shows every active bus's latest position (#8).
  const { data: buses = [] } = useBuses({ poll: true });
  const { data: schools = [] } = useSchools();
  const { toast } = useToast();
  const located = buses.filter((b: any) => b.current_lat != null && b.current_lng != null);

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
  const polyline = planned.map((s) => [s.lat, s.lng]) as [number, number][];

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
              {located.map((bus: any) => (
                <Marker key={bus.id} position={[bus.current_lat, bus.current_lng]}>
                  <Popup>
                    <div className="space-y-1 text-sm">
                      <p className="font-semibold">{bus.name}</p>
                      <p>Plate: {bus.plate_number ?? "—"}</p>
                      <p>Driver: {bus.driver_name ?? "—"}</p>
                      <p>Phone: {bus.driver_phone ?? "—"}</p>
                      <p>Status: {bus.status}</p>
                    </div>
                  </Popup>
                </Marker>
              ))}
              {polyline.length > 1 && <Polyline positions={polyline} color="#2f6f4f" />}
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
                {provider && (
                  <p className="text-xs text-muted-foreground">Ordered via {provider}</p>
                )}
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
  );
}
