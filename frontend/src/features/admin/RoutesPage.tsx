import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Clock,
  MapPin,
  Megaphone,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
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
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { RouteMapPreview } from "@/components/map/RouteMapPreview";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useBuses, useRoutes, useSchools } from "@/lib/queries";

const EMPTY = {
  name: "",
  type: "morning",
  bus_id: "none",
  school_id: "none",
  // Ordinal of this trip within the bus's period (R19): 1 = first wave. A bus
  // may hold several trips per period, each a distinct (bus, type, trip_index).
  trip_index: 1,
  // Route-level gate-anchor override (R3, HH:MM); "" inherits the school bell.
  gate_anchor: "",
};

interface StopGroup {
  order: number;
  name: string;
  scheduled_time: string | null;
  /** The student's own pickup_time attribute (the INPUT the time editor
   * edits), as opposed to scheduled_time (often a computed ETA). Null on
   * the school gate and planner stops. */
  student_pickup_time: string | null;
  is_school_gate: boolean;
  /** Server-issued location-group key; null on the school gate and planner stops. */
  group_key: string | null;
  studentIds: string[];
}

// --- U11 pure pieces (exported for tests/unit/routeOrdering.test.ts) --------

/** Durable R10 degradation signal — the card badge and the transient toast
 * raised on any `stops_recalculated: false` mutation share this text. */
export const DEGRADED_MESSAGE =
  "Order/times not recalculated — check addresses/maps key";

/** Client mirror of the server's broadcast body cap (R23). */
export const BROADCAST_MAX_CHARS = 500;

/** The broadcast dialog's Send-disabled predicate (R23 client edge): blocked
 * for an empty/whitespace-only body or one past the 500-char cap — exactly
 * the two shapes the server 400s. */
export function broadcastSendDisabled(body: string): boolean {
  return !body.trim() || body.length > BROADCAST_MAX_CHARS;
}

export interface ReorderableStop {
  group_key: string | null;
}

/**
 * Full-order payload for PUT /routes/:id/stop-order: every student-stop group
 * key of the route in display order, deduped (siblings share a key), with the
 * group at `fromIdx` shifted into `toIdx`'s slot. Returns null when the move
 * is illegal or a no-op (gate/planner rows, out of bounds, same slot) — the
 * arrows reuse it as their disabled predicate, so the UI never fires a
 * request that isn't a real move.
 */
export function buildReorderPayload(
  stops: ReorderableStop[],
  fromIdx: number,
  toIdx: number,
): string[] | null {
  if (fromIdx === toIdx || !stops[fromIdx]?.group_key || !stops[toIdx]?.group_key) return null;

  // Deduped key list in display order, plus each row's slot within it.
  const keys: string[] = [];
  const slotOf = stops.map((s) => {
    if (!s.group_key) return -1;
    const seen = keys.indexOf(s.group_key);
    if (seen !== -1) return seen;
    keys.push(s.group_key);
    return keys.length - 1;
  });

  const fromSlot = slotOf[fromIdx];
  const toSlot = slotOf[toIdx];
  if (fromSlot === toSlot) return null;
  const order = [...keys];
  const [moved] = order.splice(fromSlot, 1);
  order.splice(toSlot, 0, moved);
  return order;
}

interface RouteModeFlags {
  custom_stops?: boolean;
  manual_stop_order?: boolean;
  last_recalc_degraded?: boolean;
}

/** One ordering authority per route: custom (planner) > manual > auto. */
export function routeModeLabel(route: RouteModeFlags): "Planner" | "Manual order" | "Auto" {
  if (route.custom_stops) return "Planner";
  return route.manual_stop_order ? "Manual order" : "Auto";
}

/** Recalculate is offered when there is an order to recompute back to auto
 * (manual mode) or a degraded pass to retry — never on planner routes, where
 * the server 409s and neither flag can be set (008 CHECK / planner save). */
export function showRecalculate(route: RouteModeFlags): boolean {
  return !route.custom_stops && Boolean(route.manual_stop_order || route.last_recalc_degraded);
}

/** The warning badge renders purely from the persisted route flag so the R10
 * signal survives reloads; the next successful recalculation clears it. */
export function showDegradedBadge(route: RouteModeFlags): boolean {
  return Boolean(route.last_recalc_degraded);
}

export function RoutesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: routes = [] } = useRoutes();
  const { data: buses = [] } = useBuses();
  const { data: schools = [] } = useSchools();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });

  // Inline stop-time editor.
  const [stopEdit, setStopEdit] = useState<
    { routeId: string; studentIds: string[]; name: string; time: string } | null
  >(null);

  // Route whose reorder/recalculate request is in flight: every arrow and
  // stop control on that card disables (FleetMapPage `busy` pattern) —
  // a double-fired arrow would PUT a stale full-order payload into a 400.
  const [busyRouteId, setBusyRouteId] = useState<string | null>(null);

  // Broadcast dialog ("Message parents", R20).
  const [broadcast, setBroadcast] = useState<{ routeId: string; routeName: string } | null>(null);
  const [broadcastBody, setBroadcastBody] = useState("");
  const [sending, setSending] = useState(false);

  const busName = (id: string | null) => buses.find((b: any) => b.id === id)?.name ?? "Unassigned";

  // Transient counterpart of the durable card badge: any mutation response on
  // this page that reports stops_recalculated: false degraded instead of
  // recomputing (U6/R10). Responses without the field are ignored.
  const notifyIfDegraded = (...responses: any[]) => {
    if (responses.some((r) => r && r.stops_recalculated === false)) {
      toast({ title: DEGRADED_MESSAGE });
    }
  };

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (r: any) => {
    setEditId(r.id);
    setForm({
      name: r.name,
      type: r.type,
      bus_id: r.bus_id ?? "none",
      school_id: r.school_id ?? "none",
      // Prefill both from the saved route (deferred from U10, which only did the
      // create planner) — a bare PUT would otherwise reset trip_index to 1 and
      // wipe the gate anchor server-side (both default when omitted).
      trip_index: r.trip_index ?? 1,
      gate_anchor: r.gate_anchor ?? "",
    });
    setOpen(true);
  };

  const save = async () => {
    try {
      const payload = {
        name: form.name,
        type: form.type,
        bus_id: form.bus_id === "none" ? null : form.bus_id,
        school_id: form.school_id === "none" ? null : form.school_id,
        // A blank/NaN trip number falls back to 1 (single-trip). Same (bus,
        // type, trip_index) 409s; the catch below surfaces the API detail.
        trip_index: Number(form.trip_index) || 1,
        gate_anchor: form.gate_anchor || null,
      };
      const res = editId
        ? await api.put(`/api/fleet/routes/${editId}`, payload)
        : await api.post("/api/fleet/routes", payload);
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setOpen(false);
      notifyIfDegraded(res);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this route?",
      description: "This removes the route and all of its stops.",
      confirmLabel: "Delete route",
    }))) return;
    await api.del(`/api/fleet/routes/${id}`);
    await qc.invalidateQueries({ queryKey: ["routes"] });
  };

  // Collapse per-student stop rows into one entry per stop_order.
  const groupStops = (stops: any[]): StopGroup[] => {
    const byOrder = new Map<number, StopGroup>();
    for (const s of stops) {
      const g: StopGroup = byOrder.get(s.stop_order) ?? {
        order: s.stop_order,
        name: s.name,
        scheduled_time: s.scheduled_time,
        student_pickup_time: s.student_pickup_time ?? null,
        is_school_gate: s.is_school_gate,
        group_key: s.group_key ?? null,
        studentIds: [],
      };
      if (s.student_id) g.studentIds.push(s.student_id);
      byOrder.set(s.stop_order, g);
    }
    return [...byOrder.values()].sort((a, b) => a.order - b.order);
  };

  // Manual reorder (R11): echo the full deduped group_key order back with one
  // group shifted; the server flips the route to manual mode.
  const reorderStop = async (routeId: string, stops: StopGroup[], fromIdx: number, toIdx: number) => {
    const order = buildReorderPayload(stops, fromIdx, toIdx);
    if (!order) return;
    setBusyRouteId(routeId);
    try {
      await api.put(`/api/fleet/routes/${routeId}/stop-order`, { order });
      // Await the refetch so the arrows stay disabled until the display order
      // (the next payload's source) is fresh.
      await qc.invalidateQueries({ queryKey: ["routes"] });
    } catch (err) {
      // 400 "refresh and try again" (stale order) and the planner 409 verbatim.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyRouteId(null);
    }
  };

  // Explicit return to auto ordering / retry of a degraded pass (R11).
  const recalculate = async (routeId: string) => {
    setBusyRouteId(routeId);
    try {
      const res = await api.post(`/api/fleet/routes/${routeId}/recalculate`);
      await qc.invalidateQueries({ queryKey: ["routes"] });
      notifyIfDegraded(res);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyRouteId(null);
    }
  };

  const openBroadcast = (route: any) => {
    setBroadcastBody("");
    setBroadcast({ routeId: route.id, routeName: route.name });
  };

  const sendBroadcast = async () => {
    if (!broadcast) return;
    setSending(true);
    try {
      const res = await api.post(`/api/fleet/routes/${broadcast.routeId}/broadcast`, {
        body: broadcastBody,
      });
      toast({ title: `Sent to ${res.recipients} parents` });
      setBroadcast(null);
    } catch (err) {
      // 400/409/429 server detail verbatim; the dialog stays open so the
      // admin keeps the drafted message.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSending(false);
    }
  };

  // Both stop-level mutations sit behind the same busyRouteId gate as
  // reorder/recalculate: a double-fired delete or time write would race the
  // in-flight rebuild the first request already triggered.
  const cancelStop = async (routeId: string, g: StopGroup) => {
    if (!(await confirm({
      title: `Cancel the ${g.name} stop?`,
      description: "The student(s) at this stop will be removed from the route.",
      confirmLabel: "Cancel stop",
    }))) return;
    setBusyRouteId(routeId);
    try {
      const responses = [];
      for (const sid of g.studentIds) {
        responses.push(await api.del(`/api/fleet/routes/${routeId}/stops/${sid}`));
      }
      await qc.invalidateQueries({ queryKey: ["routes"] });
      await qc.invalidateQueries({ queryKey: ["students"] });
      notifyIfDegraded(...responses);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyRouteId(null);
    }
  };

  const saveStopTime = async () => {
    if (!stopEdit) return;
    setBusyRouteId(stopEdit.routeId);
    try {
      const responses = [];
      for (const sid of stopEdit.studentIds) {
        responses.push(
          await api.put(`/api/fleet/routes/${stopEdit.routeId}/stops/${sid}`, {
            pickup_time: stopEdit.time || null,
          }),
        );
      }
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setStopEdit(null);
      notifyIfDegraded(...responses);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyRouteId(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Routes"
        subtitle="Manage bus routes and stops"
        action={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Route</Button>}
      />

      <p className="text-sm text-muted-foreground">{routes.length} routes configured</p>

      <div className="grid gap-4 lg:grid-cols-2">
        {routes.map((route: any) => {
          const stops = groupStops(route.route_stops ?? []);
          const busy = busyRouteId === route.id;
          const mode = routeModeLabel(route);
          return (
            <Card key={route.id}>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div>
                  <p className="font-heading text-lg font-semibold">{route.name}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <Badge variant={route.type === "morning" ? "secondary" : "warning"}>{route.type}</Badge>
                    <Badge
                      variant={mode === "Manual order" ? "default" : "outline"}
                      data-testid="route-mode-chip"
                    >
                      {mode}
                    </Badge>
                    {/* Trip ordinal (R19): makes two trips of one bus/period
                        legible on the card — a bus can hold e.g. a morning
                        trip 1 and a morning trip 2. */}
                    <Badge variant="outline" data-testid="route-trip-index">
                      Trip {route.trip_index ?? 1}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{busName(route.bus_id)}</span>
                  </div>
                </div>
                <div className="flex shrink-0">
                  <Button
                    variant="ghost"
                    size="icon"
                    title="Message parents"
                    aria-label="Message parents"
                    data-testid="message-parents"
                    onClick={() => openBroadcast(route)}
                  >
                    <Megaphone className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" onClick={() => startEdit(route)}><Pencil className="h-4 w-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => remove(route.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {(showDegradedBadge(route) || showRecalculate(route)) && (
                  <div className="flex flex-wrap items-center gap-2">
                    {showDegradedBadge(route) && (
                      <Badge variant="warning" data-testid="degraded-badge">
                        {DEGRADED_MESSAGE}
                      </Badge>
                    )}
                    {showRecalculate(route) && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        data-testid="recalculate-order"
                        onClick={() => recalculate(route.id)}
                      >
                        <RefreshCw className="h-3.5 w-3.5" /> Recalculate order & times
                      </Button>
                    )}
                  </div>
                )}
                <RouteMapPreview stops={route.route_stops ?? []} polyline={route.polyline} />
                {stops.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No students assigned to this route yet. Add students with home addresses to see pickups here.
                  </p>
                ) : (
                  <ol className="space-y-1.5">
                    {stops.map((s, i) => (
                      <li key={s.order} className="flex items-center gap-2 text-sm">
                        <MapPin className="h-3.5 w-3.5 shrink-0 text-primary" />
                        <span className="font-medium">{s.order}.</span>
                        <span className="truncate">{s.name}</span>
                        {s.scheduled_time && (
                          <span className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Clock className="h-3 w-3" /> {s.scheduled_time}
                          </span>
                        )}
                        {s.is_school_gate ? (
                          <Badge variant="outline" className="ml-auto">School gate</Badge>
                        ) : (
                          <span className="ml-auto flex items-center gap-0.5">
                            {!route.custom_stops && (
                              <span className="mr-1 flex flex-col">
                                <button
                                  type="button"
                                  aria-label="Move up"
                                  disabled={busy || buildReorderPayload(stops, i, i - 1) === null}
                                  onClick={() => reorderStop(route.id, stops, i, i - 1)}
                                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                                >
                                  <ArrowUp className="h-3 w-3" />
                                </button>
                                <button
                                  type="button"
                                  aria-label="Move down"
                                  disabled={busy || buildReorderPayload(stops, i, i + 1) === null}
                                  onClick={() => reorderStop(route.id, stops, i, i + 1)}
                                  className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                                >
                                  <ArrowDown className="h-3 w-3" />
                                </button>
                              </span>
                            )}
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7"
                              title="Edit pickup time"
                              disabled={busy}
                              onClick={() =>
                                setStopEdit({
                                  routeId: route.id,
                                  studentIds: s.studentIds,
                                  name: s.name,
                                  // Prefill the INPUT (the student's own
                                  // pickup_time), never scheduled_time — on
                                  // computed routes that's a derived ETA and
                                  // re-saving it as the pickup time would
                                  // corrupt the anchor.
                                  time: s.student_pickup_time ?? "",
                                })
                              }
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7"
                              title="Cancel this stop"
                              disabled={busy}
                              onClick={() => cancelStop(route.id, s)}
                            >
                              <X className="h-3.5 w-3.5" />
                            </Button>
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editId ? "Edit Route" : "Add Route"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="space-y-2">
              <Label>Type</Label>
              <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="morning">Morning</SelectItem>
                  <SelectItem value="afternoon">Afternoon</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Morning routes end at the school; afternoon routes start at the school (reverse order).
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Bus</Label>
                <Select value={form.bus_id} onValueChange={(v) => setForm({ ...form, bus_id: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Unassigned —</SelectItem>
                    {buses.map((b: any) => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {/* Trip number (R19): a bus can hold more than one trip per period
                  as long as each has a distinct number. Saving a duplicate
                  (same bus, type, trip) 409s with the API's friendly detail. */}
              <div className="space-y-2">
                <Label>Trip number</Label>
                <Input
                  type="number"
                  min={1}
                  step={1}
                  value={form.trip_index}
                  data-testid="route-trip-index-input"
                  onChange={(e) => setForm({ ...form, trip_index: Number(e.target.value) })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>School</Label>
              <Select value={form.school_id} onValueChange={(v) => setForm({ ...form, school_id: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— None —</SelectItem>
                  {schools.map((s: any) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {/* Gate anchor (R3): the bell time the schedule solves backwards
                from — morning = arrival, afternoon = departure. Prefilled from
                the saved route; empty inherits the school bell. */}
            <div className="space-y-2">
              <Label>
                {form.type === "afternoon" ? "Departure from school gate" : "Arrival at school gate"}
              </Label>
              <Input
                type="time"
                className="w-32"
                value={form.gate_anchor}
                data-testid="route-gate-anchor"
                onChange={(e) => setForm({ ...form, gate_anchor: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Leave empty to use the school bell time.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={!form.name}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!stopEdit} onOpenChange={(o) => (o ? null : setStopEdit(null))}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Edit pickup time</DialogTitle></DialogHeader>
          {stopEdit && (
            <div className="space-y-2">
              <Label>{stopEdit.name}</Label>
              <Input
                type="time"
                value={stopEdit.time}
                onChange={(e) => setStopEdit({ ...stopEdit, time: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Reorders the route by pickup time across all of this student's routes.
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopEdit(null)}>Cancel</Button>
            <Button
              onClick={saveStopTime}
              disabled={!!stopEdit && busyRouteId === stopEdit.routeId}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!broadcast}
        onOpenChange={(o) => {
          if (!o && !sending) setBroadcast(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Message parents — {broadcast?.routeName}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label>Message</Label>
            <Textarea
              rows={4}
              value={broadcastBody}
              onChange={(e) => setBroadcastBody(e.target.value)}
              placeholder="Type the notice for this route's parents…"
              disabled={sending}
              data-testid="broadcast-body"
            />
            <p
              className={`text-xs ${
                broadcastBody.length > BROADCAST_MAX_CHARS
                  ? "font-medium text-destructive"
                  : "text-muted-foreground"
              }`}
              data-testid="broadcast-counter"
            >
              {broadcastBody.length}/{BROADCAST_MAX_CHARS}
            </p>
            <p className="text-xs text-muted-foreground">
              Sent once to every parent with a child assigned to this route.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={sending} onClick={() => setBroadcast(null)}>
              Cancel
            </Button>
            <Button
              onClick={sendBroadcast}
              disabled={sending || broadcastSendDisabled(broadcastBody)}
              data-testid="broadcast-send"
            >
              Send
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
