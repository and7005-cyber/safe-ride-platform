import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Clock, MapPin, Pencil, Plus, Trash2, X } from "lucide-react";
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
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { RouteMapPreview } from "@/components/map/RouteMapPreview";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useBuses, useRoutes, useSchools } from "@/lib/queries";

const EMPTY = { name: "", type: "morning", bus_id: "none", school_id: "none" };

interface StopGroup {
  order: number;
  name: string;
  scheduled_time: string | null;
  is_school_gate: boolean;
  studentIds: string[];
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

  const busName = (id: string | null) => buses.find((b: any) => b.id === id)?.name ?? "Unassigned";

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (r: any) => {
    setEditId(r.id);
    setForm({ name: r.name, type: r.type, bus_id: r.bus_id ?? "none", school_id: r.school_id ?? "none" });
    setOpen(true);
  };

  const save = async () => {
    try {
      const payload = {
        name: form.name,
        type: form.type,
        bus_id: form.bus_id === "none" ? null : form.bus_id,
        school_id: form.school_id === "none" ? null : form.school_id,
      };
      if (editId) await api.put(`/api/fleet/routes/${editId}`, payload);
      else await api.post("/api/fleet/routes", payload);
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setOpen(false);
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
        is_school_gate: s.is_school_gate,
        studentIds: [],
      };
      if (s.student_id) g.studentIds.push(s.student_id);
      byOrder.set(s.stop_order, g);
    }
    return [...byOrder.values()].sort((a, b) => a.order - b.order);
  };

  const cancelStop = async (routeId: string, g: StopGroup) => {
    if (!(await confirm({
      title: `Cancel the ${g.name} stop?`,
      description: "The student(s) at this stop will be removed from the route.",
      confirmLabel: "Cancel stop",
    }))) return;
    try {
      for (const sid of g.studentIds) {
        await api.del(`/api/fleet/routes/${routeId}/stops/${sid}`);
      }
      await qc.invalidateQueries({ queryKey: ["routes"] });
      await qc.invalidateQueries({ queryKey: ["students"] });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const saveStopTime = async () => {
    if (!stopEdit) return;
    try {
      for (const sid of stopEdit.studentIds) {
        await api.put(`/api/fleet/routes/${stopEdit.routeId}/stops/${sid}`, {
          pickup_time: stopEdit.time || null,
        });
      }
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setStopEdit(null);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
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
          return (
            <Card key={route.id}>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div>
                  <p className="font-heading text-lg font-semibold">{route.name}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <Badge variant={route.type === "morning" ? "secondary" : "warning"}>{route.type}</Badge>
                    <span className="text-xs text-muted-foreground">{busName(route.bus_id)}</span>
                  </div>
                </div>
                <div>
                  <Button variant="ghost" size="icon" onClick={() => startEdit(route)}><Pencil className="h-4 w-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => remove(route.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <RouteMapPreview stops={route.route_stops ?? []} polyline={route.polyline} />
                {stops.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No students assigned to this route yet. Add students with home addresses to see pickups here.
                  </p>
                ) : (
                  <ol className="space-y-1.5">
                    {stops.map((s) => (
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
                          <span className="ml-auto flex items-center">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7"
                              title="Edit pickup time"
                              onClick={() =>
                                setStopEdit({
                                  routeId: route.id,
                                  studentIds: s.studentIds,
                                  name: s.name,
                                  time: s.scheduled_time ?? "",
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
            <Button onClick={saveStopTime}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
