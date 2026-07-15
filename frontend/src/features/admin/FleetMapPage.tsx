import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AdvancedMarker, InfoWindow, Map } from "@vis.gl/react-google-maps";
import {
  ArrowDown, ArrowUp, GripVertical, MapPinOff, Pencil, Plus, RotateCcw,
  Route as RouteIcon, Save, Trash2, Upload, Wand2, Warehouse,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToastAction } from "@/components/ui/toast";
import { useToast } from "@/components/ui/use-toast";
import { FitBounds, RoutePolyline, type LatLng } from "@/components/map/MapPrimitives";
import { PlacePicker, type Provenance, type ResolvedPlace } from "@/features/admin/components/PlacePicker";
import { PlannerCsvDialog } from "@/features/admin/components/PlannerCsvDialog";
import { MAP_ID, NAIROBI } from "@/lib/googleMaps";
import { api } from "@/lib/apiClient";
import { useBuses, useSchools } from "@/lib/queries";

// Distinct, high-contrast colours assigned deterministically per bus.
const PALETTE = [
  "#2f6f4f", "#2563eb", "#dc2626", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#475569", "#ea580c",
];

const BUS_PATH =
  "M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm1.5-6H6V6h12v5z";

function BusGlyph({ color }: { color: string }) {
  return (
    <div
      style={{ background: color }}
      className="flex h-[30px] w-[30px] items-center justify-center rounded-full border-2 border-white shadow-[0_1px_4px_rgba(0,0,0,.45)]"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="#fff" aria-hidden="true">
        <path d={BUS_PATH} />
      </svg>
    </div>
  );
}

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

interface PlanRow {
  address: string;
  pickup_time: string;
  lat?: number | null;
  lng?: number | null;
  // Coordinate provenance for the PlacePicker control (R8/R11). Carried only so
  // the picker stays a controlled value — the planner persists label+lat/lng
  // per stop, not provenance, so it is not sent to /route-options or /routes.
  provenance?: Provenance;
}
interface OptionStop {
  seq: number; label: string; lat: number; lng: number;
  pickup_time?: string | null; is_school?: boolean;
  eta?: string | null; leg_distance_m?: number | null; leg_duration_s?: number | null;
}
interface RouteOption {
  strategy: string; stops: OptionStop[];
  polyline?: string | null; provider?: string;
  total_distance_m?: number; total_duration_s?: number;
}

const fmtKm = (m?: number) => (m == null ? "—" : `${(m / 1000).toFixed(1)} km`);
const fmtMin = (s?: number) => (s == null ? "—" : `${Math.max(1, Math.round(s / 60))} min`);

export function FleetMapPage() {
  // Live polling so the map keeps up as buses arrive at stops.
  const { data: buses = [] } = useBuses({ poll: true });
  const { data: schools = [] } = useSchools();
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const located = (buses as any[]).filter((b) => b.current_lat != null && b.current_lng != null);
  const [selectedBus, setSelectedBus] = useState<string | null>(null);

  // Stable colour per bus, by sorted id so it doesn't shuffle as data changes.
  const colorMap = useMemo(() => {
    const ids = (buses as any[]).map((b) => b.id).sort();
    const m: Record<string, string> = {};
    ids.forEach((id, i) => { m[id] = PALETTE[i % PALETTE.length]; });
    return m;
  }, [buses]);

  // Route planner state.
  const [rows, setRows] = useState<PlanRow[]>([{ address: "", pickup_time: "" }]);
  const [type, setType] = useState("morning");
  const [schoolId, setSchoolId] = useState("none");
  // Route gate anchor (HH:MM, R3-UI/U4): the bell time the schedule is solved
  // backwards from. Empty = inherit the school bell (then the system default).
  // Sent as `gate_anchor` on /route-options (so the preview is bell-anchored)
  // and on the route save (so the saved route persists it).
  const [gateAnchor, setGateAnchor] = useState("");
  const [options, setOptions] = useState<RouteOption[] | null>(null);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [provider, setProvider] = useState("");
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  // Save-to-Routes dialog (R17/R19) and CSV import (R21).
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveSchoolId, setSaveSchoolId] = useState("none");
  const [saveBusId, setSaveBusId] = useState("none");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [csvOpen, setCsvOpen] = useState(false);

  // Overnight depot editor (R12/U13). The bus editor proper lives on the Buses
  // page, but this unit is scoped to FleetMapPage + RoutesPage, so the depot —
  // a per-bus map location — is set here, on the fleet's own map surface, with
  // the shared PlacePicker. `depotBus` is a snapshot of the row being edited.
  const [depotBus, setDepotBus] = useState<any | null>(null);
  const [depotValue, setDepotValue] = useState<ResolvedPlace>({
    address: "", lat: null, lng: null, provenance: "typed",
  });
  const [savingDepot, setSavingDepot] = useState(false);

  const openDepot = (bus: any) => {
    setDepotBus(bus);
    setDepotValue({
      address: bus.depot_address ?? "",
      lat: bus.depot_lat ?? null,
      lng: bus.depot_lng ?? null,
      provenance: (bus.depot_provenance as Provenance) ?? "typed",
    });
  };

  // BusPayload requires `name` and the server overwrites every column from the
  // payload, so a depot-only PUT would 422 and null the rest — resend the bus's
  // existing fields alongside the new depot. Setting/moving the depot
  // regenerates the bus's boundary trips server-side; clearing it (empty place)
  // persists no depot (a bus may have none).
  const persistDepot = async (place: ResolvedPlace) => {
    if (!depotBus) return;
    setSavingDepot(true);
    try {
      await api.put(`/api/fleet/buses/${depotBus.id}`, {
        name: depotBus.name,
        plate_number: depotBus.plate_number ?? null,
        driver_id: depotBus.driver_id ?? null,
        driver_name: depotBus.driver_name ?? null,
        driver_phone: depotBus.driver_phone ?? null,
        capacity: depotBus.capacity ?? 45,
        status: depotBus.status ?? "idle",
        depot_lat: place.lat,
        depot_lng: place.lng,
        depot_address: place.address || null,
        depot_provenance: place.provenance,
      });
      await queryClient.invalidateQueries({ queryKey: ["buses"] });
      setDepotBus(null);
      toast({ title: place.lat != null || place.address ? "Depot saved" : "Depot removed" });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSavingDepot(false);
    }
  };
  const saveDepot = () => persistDepot(depotValue);
  const removeDepot = () => persistDepot({ address: "", lat: null, lng: null, provenance: "typed" });

  // Request generation (R20): reset and every plan()/reorder() call bump the
  // counter; a response only applies if its generation is still current, so
  // anything in flight at reset time is discarded.
  const requestGeneration = useRef(0);

  const setRow = (i: number, patch: Partial<PlanRow>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { address: "", pickup_time: "" }]);
  const removeRow = (i: number) => setRows((rs) => rs.filter((_, idx) => idx !== i));

  const resetPlanner = () => {
    requestGeneration.current += 1;
    setRows([{ address: "", pickup_time: "" }]);
    setType("morning");
    setSchoolId("none");
    setGateAnchor("");
    setOptions(null);
    setUnresolved([]);
    setProvider("");
    setSelected(0);
    setDragIndex(null);
    setLoading(false);
    setBusy(false);
  };

  const plan = async () => {
    const stops = rows.filter((r) => r.address.trim());
    if (stops.length < 2) {
      toast({ title: "Add at least two addresses", variant: "destructive" });
      return;
    }
    const generation = ++requestGeneration.current;
    setLoading(true);
    try {
      const res = await api.post("/api/fleet/route-options", {
        type,
        school_id: schoolId === "none" ? null : schoolId,
        gate_anchor: gateAnchor || null,
        stops: stops.map((s) => ({
          address: s.address,
          pickup_time: s.pickup_time || null,
          lat: s.lat ?? null,
          lng: s.lng ?? null,
        })),
      });
      if (generation !== requestGeneration.current) return; // stale (reset/newer request)
      setOptions(res.options ?? []);
      setUnresolved(res.unresolved ?? []);
      setProvider(res.provider ?? "");
      setSelected(0);
      if ((res.unresolved ?? []).length) {
        toast({
          title: "Some addresses couldn't be located",
          description:
            "Fix them in the repair panel below — set each stop on the map, then recalculate.",
          variant: "destructive",
        });
      }
    } catch (err) {
      if (generation !== requestGeneration.current) return;
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      // Spinners clear unconditionally — the generation guard only protects
      // response-derived state. A plan superseded by a reorder (or vice
      // versa) must never leave its own busy flag stuck on.
      setLoading(false);
    }
  };

  const activeOption = options?.[selected];

  // CSV/address repair surface (U12 / spec 4, R16-R17). The planner flow reports
  // geocode failures as a flat `unresolved` list of stop labels. The planner
  // sends no explicit label, so each entry is exactly the row's `address`
  // (backend fleet.py route-options: `label = st.label or st.address`). Map those
  // failures back to the planner rows that still lack coordinates, so each gets
  // an inline PlacePicker to pin it on the map. A row drops out of the table the
  // moment it gains lat/lng — picking a suggestion or dropping a pin resolves it,
  // giving live feedback before the operator re-calculates (which then clears
  // `unresolved` for good and re-enables Save).
  //
  // The route-options flow is deliberately binary (resolved / unresolved): it
  // exposes no low-confidence "ambiguous" tier, so there is no accept-as-is path
  // here — a failed stop has no coordinates and cannot be routed until one is
  // set. (The ambiguous/accept tier belongs to the student-CSV flow, U8.)
  const repairRows = useMemo(() => {
    if (unresolved.length === 0) return [];
    const failed = new Set(unresolved);
    return rows
      .map((row, index) => ({ row, index }))
      .filter(
        ({ row }) =>
          row.address.trim() !== "" &&
          failed.has(row.address) &&
          (row.lat == null || row.lng == null),
      );
  }, [rows, unresolved]);

  // Drag-to-reorder: recompute road geometry + ETAs for the new fixed order.
  const reorder = async (from: number, to: number) => {
    if (!activeOption) return;
    const stops = [...activeOption.stops];
    if (to < 0 || to >= stops.length || from === to) return;
    const [moved] = stops.splice(from, 1);
    stops.splice(to, 0, moved);
    const generation = ++requestGeneration.current;
    setBusy(true);
    try {
      const res = await api.post("/api/fleet/route-options", {
        type,
        preserve_order: true,
        gate_anchor: gateAnchor || null,
        stops: stops.map((s) => ({
          label: s.label, lat: s.lat, lng: s.lng,
          pickup_time: s.pickup_time ?? null, is_school: !!s.is_school,
        })),
      });
      if (generation !== requestGeneration.current) return; // stale (reset/newer request)
      const opt = (res.options ?? [])[0];
      if (opt) {
        setProvider(res.provider ?? provider);
        setOptions((prev) => (prev ? prev.map((o, i) => (i === selected ? opt : o)) : prev));
      }
    } catch (err) {
      if (generation !== requestGeneration.current) return;
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      // Unconditional for the same reason as plan()'s finally above.
      setBusy(false);
      setDragIndex(null);
    }
  };

  const openSaveDialog = () => {
    setSaveName(`Planned route ${new Date().toISOString().slice(0, 10)}`);
    setSaveSchoolId(schoolId); // planner's school preselected; "none" must be changed
    setSaveBusId("none");
    setSaveError("");
    setSaveOpen(true);
  };

  const saveRoute = async () => {
    // Snapshot at click time — never re-read planner state after the await.
    const option = activeOption;
    if (!option) return;
    if (saveSchoolId === "none") {
      setSaveError("Please choose a school — every route belongs to one.");
      return;
    }
    const name = saveName.trim();
    if (!name) {
      setSaveError("Please give the route a name.");
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await api.post("/api/fleet/routes", {
        name,
        type,
        school_id: saveSchoolId,
        bus_id: saveBusId === "none" ? null : saveBusId,
        gate_anchor: gateAnchor || null,
        stops: option.stops.map((s) => ({
          label: s.label,
          lat: s.lat,
          lng: s.lng,
          pickup_time: s.pickup_time ?? null,
          is_school: !!s.is_school,
        })),
        polyline: option.polyline ?? null,
        total_distance_m: option.total_distance_m ?? null,
        total_duration_s: option.total_duration_s ?? null,
      });
      setSaveOpen(false);
      toast({
        title: "Route saved",
        description: name,
        action: (
          <ToastAction altText="View routes" onClick={() => navigate("/routes")}>
            View routes
          </ToastAction>
        ),
      });
      queryClient.invalidateQueries({ queryKey: ["routes"] });
      resetPlanner();
    } catch (err) {
      // Bus conflicts (409) and other failures keep the dialog open with the
      // server's message so the admin can pick another bus or rename.
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const planned = activeOption?.stops ?? [];
  const plannedPath: LatLng[] = planned.map((s) => ({ lat: s.lat, lng: s.lng }));

  const focusPoints: LatLng[] = planned.length
    ? plannedPath
    : located.map((b) => ({ lat: b.current_lat, lng: b.current_lng }));
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
          <div className="h-[70vh] w-full" data-testid="fleet-map">
            <Map
              mapId={MAP_ID}
              defaultCenter={NAIROBI}
              defaultZoom={12}
              gestureHandling="greedy"
              disableDefaultUI={false}
              className="h-full w-full"
            >
              <FitBounds points={focusPoints} focusKey={focusKey} />

              {located.map((bus: any) => (
                <AdvancedMarker
                  key={bus.id}
                  position={{ lat: bus.current_lat, lng: bus.current_lng }}
                  onClick={() => setSelectedBus(bus.id)}
                >
                  <BusGlyph color={colorMap[bus.id] ?? PALETTE[0]} />
                </AdvancedMarker>
              ))}

              {selectedBus &&
                (() => {
                  const bus = located.find((b: any) => b.id === selectedBus);
                  if (!bus) return null;
                  return (
                    <InfoWindow
                      position={{ lat: bus.current_lat, lng: bus.current_lng }}
                      onCloseClick={() => setSelectedBus(null)}
                    >
                      <div className="space-y-1 text-sm">
                        <p className="font-semibold" style={{ color: colorMap[bus.id] }}>{bus.name}</p>
                        <p>{bus.position_label ?? "On the move"}</p>
                        <p>Driver: {bus.driver_name ?? "—"}</p>
                        <p>Plate: {bus.plate_number ?? "—"}</p>
                      </div>
                    </InfoWindow>
                  );
                })()}

              {planned.length > 1 && (
                <RoutePolyline encoded={activeOption?.polyline} path={plannedPath} color="#2f6f4f" />
              )}
              {planned.map((s, i) => (
                <AdvancedMarker key={`plan-${i}`} position={{ lat: s.lat, lng: s.lng }} zIndex={10}>
                  <StopGlyph seq={s.seq} school={s.is_school} />
                </AdvancedMarker>
              ))}
            </Map>
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

          {/* Bus depots (R12/U13): every bus's optional overnight base. Unlike
              the Buses card above (active runs only), this lists the whole fleet
              so any bus's depot can be set, whether or not it is on a run now. */}
          <Card>
            <CardHeader className="flex-row items-center gap-2 space-y-0">
              <Warehouse className="h-5 w-5 text-primary" />
              <p className="font-heading text-lg font-semibold">Bus depots</p>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-sm text-muted-foreground">
                Overnight base each bus starts its first morning trip and ends its
                last afternoon trip at. Optional.
              </p>
              {(buses as any[]).length === 0 ? (
                <p className="text-sm text-muted-foreground">No buses in the fleet yet.</p>
              ) : (
                (buses as any[]).map((bus) => (
                  <div
                    key={bus.id}
                    className="flex items-center gap-2 text-sm"
                    data-testid={`depot-row-${bus.id}`}
                  >
                    <span className="font-medium">{bus.name}</span>
                    <span
                      className="ml-auto max-w-[55%] truncate text-xs text-muted-foreground"
                      title={bus.depot_address ?? undefined}
                    >
                      {bus.depot_address ?? "No depot set"}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0"
                      aria-label={`Set depot for ${bus.name}`}
                      data-testid={`edit-depot-${bus.id}`}
                      onClick={() => openDepot(bus)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
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
                Enter addresses and pickup times to get optimised, traffic-aware route options.
              </p>

              <div className="space-y-2">
                {rows.map((r, i) => (
                  <div key={i} className="space-y-2 rounded-md border p-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-muted-foreground">Stop {i + 1}</span>
                      <Input
                        type="time"
                        className="ml-auto w-28"
                        value={r.pickup_time}
                        onChange={(e) => setRow(i, { pickup_time: e.target.value })}
                      />
                      <Button variant="ghost" size="icon" onClick={() => removeRow(i)} disabled={rows.length === 1}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                    {/* One place-picking control per stop: add a stop by autocomplete
                        OR by dropping a map pin, interchangeably (R9). The resolved
                        {label, lat, lng} is what the planner pushes downstream. */}
                    <PlacePicker
                      placeholder="Address"
                      testId={`address-input-${i}`}
                      value={{
                        address: r.address,
                        lat: r.lat ?? null,
                        lng: r.lng ?? null,
                        provenance: r.provenance ?? "typed",
                      }}
                      onChange={(next) =>
                        setRow(i, {
                          address: next.address,
                          lat: next.lat,
                          lng: next.lng,
                          provenance: next.provenance,
                        })
                      }
                    />
                  </div>
                ))}
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={addRow}><Plus className="h-4 w-4" /> Add stop</Button>
                  <Button variant="outline" size="sm" onClick={() => setCsvOpen(true)}>
                    <Upload className="h-4 w-4" /> Upload CSV
                  </Button>
                </div>
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

              {/* Gate anchor (R3-UI/U4): the bell time the schedule is solved
                  backwards from. Direction picks the wording — morning = arrival,
                  afternoon = departure. Empty inherits the school bell. */}
              <div className="space-y-1">
                <Label>
                  {type === "afternoon" ? "Departure from school gate" : "Arrival at school gate"}
                </Label>
                <Input
                  type="time"
                  className="w-32"
                  value={gateAnchor}
                  data-testid="gate-anchor"
                  onChange={(e) => setGateAnchor(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Leave empty to use the school bell time.
                </p>
              </div>

              <div className="flex gap-2">
                <Button onClick={plan} disabled={loading} className="flex-1" data-testid="get-route-options">
                  <Wand2 className="h-4 w-4" /> {loading ? "Planning…" : "Get route options"}
                </Button>
                <Button variant="outline" onClick={resetPlanner} data-testid="reset-planner">
                  <RotateCcw className="h-4 w-4" /> Reset
                </Button>
              </div>

              {/* Persistent repair table (U12 / spec 4, R16-R17): unresolved
                  addresses surface here as a durable panel (not a fleeting
                  toast), each with an inline PlacePicker so the operator can fix
                  it by searching or dropping a pin. A row leaves the list the
                  instant it gains coordinates; a re-calculate then clears the
                  gate on Save. */}
              {repairRows.length > 0 && (
                <div
                  className="space-y-3 rounded-md border border-destructive/40 bg-destructive/5 p-3"
                  data-testid="csv-repair-table"
                >
                  <div className="flex items-center gap-2">
                    <MapPinOff className="h-4 w-4 shrink-0 text-destructive" />
                    <p className="text-sm font-semibold text-destructive">
                      {repairRows.length} address{repairRows.length === 1 ? "" : "es"} need a location
                    </p>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    These stops couldn't be found on the map. Set each one below —
                    search an address or drop a pin — then recalculate. Fixed stops
                    leave this list automatically.
                  </p>
                  {repairRows.map(({ row, index }) => (
                    <div
                      key={index}
                      data-testid={`csv-repair-row-${index}`}
                      className="space-y-2 rounded-md border bg-background p-2"
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="shrink-0 text-xs font-medium text-muted-foreground">
                          Stop {index + 1}
                        </span>
                        <span className="flex-1 truncate text-sm font-medium" title={row.address}>
                          {row.address}
                        </span>
                      </div>
                      <p className="text-xs text-destructive">
                        Address not found — set the location on the map.
                      </p>
                      <PlacePicker
                        placeholder="Search an address or drop a pin"
                        testId={`csv-repair-input-${index}`}
                        value={{
                          address: row.address,
                          lat: row.lat ?? null,
                          lng: row.lng ?? null,
                          provenance: row.provenance ?? "imported",
                        }}
                        onChange={(next) =>
                          setRow(index, {
                            address: next.address,
                            lat: next.lat,
                            lng: next.lng,
                            provenance: next.provenance,
                          })
                        }
                      />
                    </div>
                  ))}
                </div>
              )}

              {options && (
                <div className="space-y-3 pt-2" data-testid="route-result">
                  {/* Unresolved addresses are handled by the persistent repair
                      table above (data-testid="csv-repair-table"), not a weak
                      inline note here. */}
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
                    <>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                        <span className="font-medium" data-testid="route-distance">
                          {fmtKm(activeOption.total_distance_m)}
                        </span>
                        <span className="text-muted-foreground">·</span>
                        <span className="font-medium" data-testid="route-duration">
                          {fmtMin(activeOption.total_duration_s)}
                          {activeOption.provider === "google-routes" && " in traffic"}
                        </span>
                        {(activeOption.provider ?? provider) && (
                          <Badge variant="outline" className="ml-auto text-[10px]">
                            {activeOption.provider === "google-routes"
                              ? "Google Routes"
                              : (activeOption.provider ?? provider)}
                          </Badge>
                        )}
                      </div>

                      {/* Degraded signal (R10): when the schedule was solved
                          without live traffic (provider not google-routes) the
                          ETAs are approximate. Non-blocking — saving stays allowed. */}
                      {activeOption.provider && activeOption.provider !== "google-routes" && (
                        <p className="text-xs text-amber-600" data-testid="degraded-warning">
                          Schedule couldn't be fully optimised — times are approximate.
                        </p>
                      )}

                      <ol className="space-y-1 text-sm" data-testid="route-stops">
                        {activeOption.stops.map((s, i) => (
                          <li
                            key={`${s.seq}-${s.lat}-${s.lng}`}
                            className={`flex items-center gap-2 rounded px-1 py-0.5 ${
                              dragIndex === i ? "bg-accent" : ""
                            }`}
                            draggable={!busy}
                            onDragStart={() => setDragIndex(i)}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={() => dragIndex != null && reorder(dragIndex, i)}
                          >
                            <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground" />
                            <span className="font-medium">{s.seq}.</span>
                            <span className="flex-1 truncate">{s.label}</span>
                            {s.is_school && <Badge variant="outline">School</Badge>}
                            {s.eta && (
                              <span className="text-xs text-muted-foreground" title="Estimated arrival">
                                {s.eta}
                              </span>
                            )}
                            <span className="flex flex-col">
                              <button
                                type="button"
                                aria-label="Move up"
                                disabled={i === 0 || busy}
                                onClick={() => reorder(i, i - 1)}
                                className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                              >
                                <ArrowUp className="h-3 w-3" />
                              </button>
                              <button
                                type="button"
                                aria-label="Move down"
                                disabled={i === activeOption.stops.length - 1 || busy}
                                onClick={() => reorder(i, i + 1)}
                                className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                              >
                                <ArrowDown className="h-3 w-3" />
                              </button>
                            </span>
                          </li>
                        ))}
                      </ol>

                      <Button
                        className="w-full"
                        onClick={openSaveDialog}
                        disabled={busy || loading || unresolved.length > 0}
                        data-testid="save-to-routes"
                      >
                        <Save className="h-4 w-4" /> Save to Routes
                      </Button>
                      {unresolved.length > 0 && (
                        <p className="text-xs text-muted-foreground">
                          Some addresses still need a location — fix them in the
                          repair panel above, then recalculate before saving.
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Save-to-Routes dialog (R17): persists the selected option as a custom-stops route. */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Save to Routes</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1">
              <Label>Route name</Label>
              <Input value={saveName} onChange={(e) => setSaveName(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>School</Label>
              <Select value={saveSchoolId} onValueChange={(v) => { setSaveSchoolId(v); setSaveError(""); }}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— Choose a school —</SelectItem>
                  {schools.map((s: any) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>Bus</Label>
              <Select value={saveBusId} onValueChange={setSaveBusId}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— Unassigned —</SelectItem>
                  {(buses as any[]).map((b) => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {saveError && <p className="text-sm text-destructive" data-testid="save-route-error">{saveError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={saveRoute} disabled={saving} data-testid="confirm-save-route">
              {saving ? "Saving…" : "Save route"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Depot editor (R12): one PlacePicker per bus, prefilled when the bus
          already has a depot. Empty saves as no depot. */}
      <Dialog
        open={!!depotBus}
        onOpenChange={(o) => {
          if (!o && !savingDepot) setDepotBus(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Overnight depot — {depotBus?.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label>Depot location</Label>
            <PlacePicker
              placeholder="Depot address"
              testId="depot-address"
              value={depotValue}
              onChange={setDepotValue}
            />
            <p className="text-xs text-muted-foreground">
              The bus begins its first morning trip and ends its last afternoon
              trip here. It never appears as a passenger stop. Leave empty for none.
            </p>
          </div>
          <DialogFooter>
            {depotBus && (depotBus.depot_lat != null || depotBus.depot_address) && (
              <Button
                variant="outline"
                onClick={removeDepot}
                disabled={savingDepot}
                data-testid="remove-depot"
              >
                Remove depot
              </Button>
            )}
            <Button variant="outline" onClick={() => setDepotBus(null)} disabled={savingDepot}>
              Cancel
            </Button>
            <Button onClick={saveDepot} disabled={savingDepot} data-testid="save-depot">
              {savingDepot ? "Saving…" : "Save depot"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* CSV import (R21): appends parsed stop rows to the planner. */}
      <PlannerCsvDialog
        open={csvOpen}
        onOpenChange={setCsvOpen}
        existingRows={rows}
        onImport={(next) => setRows(next)}
      />
    </div>
  );
}
