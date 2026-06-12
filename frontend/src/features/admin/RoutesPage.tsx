import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MapPin, Pencil, Plus, Trash2 } from "lucide-react";
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
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useBuses, useRoutes, useSchools } from "@/lib/queries";

const EMPTY = { name: "", type: "morning", bus_id: "none", school_id: "none" };

export function RoutesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: routes = [] } = useRoutes();
  const { data: buses = [] } = useBuses();
  const { data: schools = [] } = useSchools();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });

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
    await api.del(`/api/fleet/routes/${id}`);
    await qc.invalidateQueries({ queryKey: ["routes"] });
  };

  // Distinct stops (per-student rows share an order).
  const distinctStops = (stops: any[]) => {
    const seen = new Set<number>();
    return stops.filter((s) => (seen.has(s.stop_order) ? false : seen.add(s.stop_order)));
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Routes"
        subtitle="Manage bus routes and stops"
        action={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Route</Button>}
      />

      <p className="text-sm text-muted-foreground">{routes.length} routes configured</p>

      {/* No dedicated zero-routes empty state: the grid simply renders empty. */}
      <div className="grid gap-4 lg:grid-cols-2">
        {routes.map((route: any) => {
          const stops = distinctStops(route.route_stops ?? []);
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
              <CardContent>
                {stops.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No students assigned to this route yet. Add students with home addresses to see pickups here.
                  </p>
                ) : (
                  <ol className="space-y-1.5">
                    {stops.map((s: any) => (
                      <li key={s.stop_order} className="flex items-center gap-2 text-sm">
                        <MapPin className="h-3.5 w-3.5 text-primary" />
                        <span className="font-medium">{s.stop_order}.</span>
                        <span>{s.name}</span>
                        {s.is_school_gate && <Badge variant="outline" className="ml-auto">School gate</Badge>}
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
    </div>
  );
}
