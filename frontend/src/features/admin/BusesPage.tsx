import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Bus, Plus, Trash2, Pencil } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ListToolbar } from "@/features/admin/components/ListToolbar";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { phoneError } from "@/lib/validation";
import { useBuses, useDrivers } from "@/lib/queries";

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "idle", label: "Idle" },
  { value: "delayed", label: "Delayed" },
  { value: "offline", label: "Offline" },
];

const STATUS_VARIANT: Record<string, "success" | "warning" | "secondary" | "destructive"> = {
  active: "success",
  delayed: "warning",
  idle: "secondary",
  offline: "destructive",
};

const EMPTY = {
  name: "",
  plate_number: "",
  driver_id: "none",
  driver_name: "",
  driver_phone: "",
  capacity: 45,
  status: "idle",
};

export function BusesPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: buses = [], isLoading } = useBuses();
  const { data: drivers = [] } = useDrivers();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const filtered = useMemo(
    () =>
      (buses as any[]).filter((b) => {
        const matchesStatus = statusFilter === "all" || b.status === statusFilter;
        const q = search.toLowerCase();
        const matchesSearch =
          !q ||
          [b.name, b.plate_number, b.driver_name].some((v: string) =>
            (v ?? "").toLowerCase().includes(q),
          );
        return matchesStatus && matchesSearch;
      }),
    [buses, search, statusFilter],
  );

  const startCreate = () => {
    setEditId(null);
    setForm({ ...EMPTY });
    setOpen(true);
  };

  const startEdit = (bus: any) => {
    setEditId(bus.id);
    setForm({
      name: bus.name ?? "",
      plate_number: bus.plate_number ?? "",
      driver_id: bus.driver_id ?? "none",
      driver_name: bus.driver_name ?? "",
      driver_phone: bus.driver_phone ?? "",
      capacity: bus.capacity ?? 45,
      status: bus.status ?? "idle",
    });
    setOpen(true);
  };

  const pickDriver = (id: string) => {
    if (id === "none") {
      setForm((f) => ({ ...f, driver_id: "none", driver_name: "", driver_phone: "" }));
      return;
    }
    const d = drivers.find((x: any) => x.id === id);
    setForm((f) => ({
      ...f,
      driver_id: id,
      driver_name: d?.full_name ?? "",
      driver_phone: d?.phone ?? "",
    }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = {
        ...form,
        driver_id: form.driver_id === "none" ? null : form.driver_id,
        capacity: Number(form.capacity),
      };
      if (editId) await api.put(`/api/fleet/buses/${editId}`, payload);
      else await api.post("/api/fleet/buses", payload);
      await qc.invalidateQueries({ queryKey: ["buses"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this bus?",
      description: "This removes the bus from the fleet.",
      confirmLabel: "Delete bus",
    }))) return;
    try {
      await api.del(`/api/fleet/buses/${id}`);
      await qc.invalidateQueries({ queryKey: ["buses"] });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const driverSelected = form.driver_id !== "none";
  // Only the manually-typed driver phone needs validating; a synced one is
  // already normalised on the driver's own record.
  const driverPhoneErr = driverSelected ? null : phoneError(form.driver_phone);

  return (
    <div className="space-y-6">
      <PageHeader title="Buses" subtitle="Manage your fleet" />

      <ListToolbar
        search={search}
        onSearch={setSearch}
        placeholder="Search buses, plates, drivers…"
        filters={[{ value: statusFilter, onChange: setStatusFilter, options: STATUS_FILTERS }]}
        actions={
          <Button onClick={startCreate}>
            <Plus className="h-4 w-4" /> Add Bus
          </Button>
        }
      />

      <p className="text-sm text-muted-foreground">
        {filtered.length} of {buses.length} buses
      </p>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Bus Name</TableHead>
              <TableHead>Plate Number</TableHead>
              <TableHead>Driver</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Capacity</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground">Loading…</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground">No buses match your filters.</TableCell></TableRow>
            ) : (
              filtered.map((bus: any) => (
                <TableRow key={bus.id}>
                  <TableCell className="flex items-center gap-2 font-medium">
                    <Bus className="h-4 w-4 text-primary" /> {bus.name}
                  </TableCell>
                  <TableCell>{bus.plate_number ?? "—"}</TableCell>
                  <TableCell>{bus.driver_name ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{bus.driver_phone ?? "—"}</TableCell>
                  <TableCell>{bus.capacity} seats</TableCell>
                  <TableCell><Badge variant={STATUS_VARIANT[bus.status] ?? "secondary"}>{bus.status}</Badge></TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" onClick={() => startEdit(bus)}><Pencil className="h-4 w-4" /></Button>
                    <Button variant="ghost" size="icon" onClick={() => remove(bus.id)}><Trash2 className="h-4 w-4" /></Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editId ? "Edit Bus" : "Add Bus"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Plate number</Label>
              <Input value={form.plate_number} onChange={(e) => setForm({ ...form, plate_number: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Assign Driver</Label>
              <Select value={form.driver_id} onValueChange={pickDriver}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— No driver —</SelectItem>
                  {drivers.map((d: any) => (
                    <SelectItem key={d.id} value={d.id}>{d.full_name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Driver name</Label>
                <Input value={form.driver_name} readOnly={driverSelected} className={driverSelected ? "bg-muted" : ""} onChange={(e) => setForm({ ...form, driver_name: e.target.value })} />
                {driverSelected && <p className="text-xs text-muted-foreground">Synced from the selected driver's profile.</p>}
              </div>
              <div className="space-y-2">
                <Label>Driver phone</Label>
                <Input value={form.driver_phone} readOnly={driverSelected} className={driverSelected ? "bg-muted" : ""} onChange={(e) => setForm({ ...form, driver_phone: e.target.value })} />
                {driverPhoneErr && <p className="text-xs text-destructive">{driverPhoneErr}</p>}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Capacity</Label>
                <Input type="number" min={1} max={100} value={form.capacity} onChange={(e) => setForm({ ...form, capacity: Number(e.target.value) })} />
              </div>
              <div className="space-y-2">
                <Label>Status</Label>
                <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="idle">Idle</SelectItem>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="delayed">Delayed</SelectItem>
                    <SelectItem value="offline">Offline</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={saving || !form.name || !!driverPhoneErr}>{saving ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
