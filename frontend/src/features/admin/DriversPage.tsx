import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useDrivers } from "@/lib/queries";

const EMPTY = { full_name: "", email: "", password: "", phone: "", pin: "" };

export function DriversPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: drivers = [] } = useDrivers();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (d: any) => {
    setEditId(d.id);
    // PIN/password always start blank: blank PIN keeps the existing PIN.
    setForm({ full_name: d.full_name ?? "", email: d.email ?? "", password: "", phone: d.phone ?? "", pin: "" });
    setOpen(true);
  };

  const generatePin = () => {
    let p = "";
    for (let i = 0; i < 4; i++) p += Math.floor(Math.random() * 10);
    setForm({ ...form, pin: p });
  };

  const save = async () => {
    setSaving(true);
    try {
      if (editId) {
        await api.put(`/api/accounts/drivers/${editId}`, {
          full_name: form.full_name, email: form.email, phone: form.phone || null,
          pin: form.pin || null,
        });
      } else {
        await api.post("/api/accounts/drivers", {
          full_name: form.full_name, email: form.email, password: form.password,
          phone: form.phone || null, pin: form.pin || null,
        });
      }
      await qc.invalidateQueries({ queryKey: ["accounts-drivers"] });
      await qc.invalidateQueries({ queryKey: ["buses"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    await api.del(`/api/accounts/drivers/${id}`);
    await qc.invalidateQueries({ queryKey: ["accounts-drivers"] });
    await qc.invalidateQueries({ queryKey: ["buses"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Drivers"
        subtitle={`${drivers.length} driver${drivers.length === 1 ? "" : "s"}`}
        action={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Driver</Button>}
      />

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>PIN</TableHead>
              <TableHead>Assigned bus</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {drivers.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">No drivers yet.</TableCell></TableRow>
            ) : (
              drivers.map((d: any) => (
                <TableRow key={d.id}>
                  <TableCell className="font-medium">{d.full_name}</TableCell>
                  <TableCell>{d.email}</TableCell>
                  <TableCell>{d.phone ?? "—"}</TableCell>
                  <TableCell>{d.has_pin ? <Badge variant="success">Set</Badge> : <Badge variant="secondary">None</Badge>}</TableCell>
                  <TableCell>{d.assigned_bus ?? "—"}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" onClick={() => startEdit(d)}><Pencil className="h-4 w-4" /></Button>
                    <Button variant="ghost" size="icon" onClick={() => remove(d.id)}><Trash2 className="h-4 w-4" /></Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editId ? "Edit Driver" : "Add Driver"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2"><Label>Full name</Label><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div className="space-y-2"><Label>Email</Label><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
            {!editId && (
              <div className="space-y-2"><Label>Password</Label><Input type="password" minLength={6} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
            )}
            <div className="space-y-2"><Label>Phone</Label><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
            <div className="space-y-2">
              <Label>Driver PIN {editId && <span className="text-xs text-muted-foreground">(leave blank to keep existing)</span>}</Label>
              <div className="flex gap-2">
                <Input inputMode="numeric" maxLength={4} placeholder="4 digits" value={form.pin} onChange={(e) => setForm({ ...form, pin: e.target.value.replace(/\D/g, "").slice(0, 4) })} />
                <Button type="button" variant="outline" onClick={generatePin}>Generate</Button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={saving || !form.full_name || !form.email || (!editId && form.password.length < 6)}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
