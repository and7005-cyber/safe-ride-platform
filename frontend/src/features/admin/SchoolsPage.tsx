import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { MapPicker } from "@/features/admin/components/MapPicker";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useSchools } from "@/lib/queries";

const EMPTY = { name: "", address: "", phone: "", lat: null as number | null, lng: null as number | null };

export function SchoolsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: schools = [] } = useSchools();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (s: any) => {
    setEditId(s.id);
    setForm({ name: s.name, address: s.address ?? "", phone: s.phone ?? "", lat: s.lat, lng: s.lng });
    setOpen(true);
  };

  const save = async () => {
    if (!form.address || form.lat == null || form.lng == null) {
      toast({ title: "Address and a map location are required", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      if (editId) await api.put(`/api/fleet/schools/${editId}`, form);
      else await api.post("/api/fleet/schools", form);
      await qc.invalidateQueries({ queryKey: ["schools"] });
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    await api.del(`/api/fleet/schools/${id}`);
    await qc.invalidateQueries({ queryKey: ["schools"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Schools"
        subtitle={`${schools.length} school${schools.length === 1 ? "" : "s"}`}
        action={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add School</Button>}
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {schools.map((s: any) => (
          <Card key={s.id}>
            <CardContent className="space-y-2 p-5">
              <div className="flex items-start justify-between">
                <p className="font-heading text-lg font-semibold">{s.name}</p>
                <div>
                  <Button variant="ghost" size="icon" onClick={() => startEdit(s)}><Pencil className="h-4 w-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => remove(s.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              <p className="text-sm text-muted-foreground">{s.address ?? "—"}</p>
              <p className="text-sm text-muted-foreground">{s.phone ?? ""}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editId ? "Edit School" : "Add School"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="space-y-2"><Label>Address</Label><Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></div>
            <div className="space-y-2"><Label>Phone</Label><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
            <div className="space-y-2">
              <Label>Location {form.lat != null && <span className="text-xs text-muted-foreground">({form.lat}, {form.lng})</span>}</Label>
              <MapPicker lat={form.lat} lng={form.lng} onPick={(lat, lng) => setForm({ ...form, lat, lng })} />
              <p className="text-xs text-muted-foreground">Click the map to set the school gate location.</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={saving || !form.name}>{saving ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
