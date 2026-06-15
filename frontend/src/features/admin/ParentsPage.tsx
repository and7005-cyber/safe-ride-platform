import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
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
import { useConfirm } from "@/components/ui/confirm-dialog";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { emailError, phoneError } from "@/lib/validation";
import { useParents } from "@/lib/queries";

export function ParentsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: parents = [] } = useParents();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ full_name: "", email: "", phone: "" });

  const startEdit = (p: any) => {
    setEditId(p.id);
    setForm({ full_name: p.full_name ?? "", email: p.email ?? "", phone: p.phone ?? "" });
    setOpen(true);
  };

  const save = async () => {
    if (!editId) return;
    try {
      await api.put(`/api/accounts/parents/${editId}`, form);
      await qc.invalidateQueries({ queryKey: ["accounts-parents"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this parent?",
      description: "This removes the parent account and its student links.",
      confirmLabel: "Delete parent",
    }))) return;
    await api.del(`/api/accounts/parents/${id}`);
    await qc.invalidateQueries({ queryKey: ["accounts-parents"] });
  };

  const emailErr = emailError(form.email, true);
  const phoneErr = phoneError(form.phone);

  return (
    <div className="space-y-6">
      <PageHeader title="Parents" subtitle={`${parents.length} parent record${parents.length === 1 ? "" : "s"}`} />

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Students</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {parents.map((p: any, idx: number) => (
              <TableRow key={p.id ?? `pending-${idx}`}>
                <TableCell className="font-medium">{p.full_name ?? "—"}</TableCell>
                <TableCell>{p.email}</TableCell>
                <TableCell>{p.students.join(", ") || "—"}</TableCell>
                <TableCell>
                  {p.status === "registered" ? (
                    <Badge variant="success">Registered</Badge>
                  ) : (
                    <Badge variant="secondary">Awaiting signup</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {p.status === "registered" ? (
                    <>
                      <Button variant="ghost" size="icon" onClick={() => startEdit(p)}><Pencil className="h-4 w-4" /></Button>
                      <Button variant="ghost" size="icon" onClick={() => remove(p.id)}><Trash2 className="h-4 w-4" /></Button>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Edit Parent</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2"><Label>Full name</Label><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div className="space-y-2">
              <Label>Email</Label>
              <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
            </div>
            <div className="space-y-2">
              <Label>Phone</Label>
              <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              {phoneErr && <p className="text-xs text-destructive">{phoneErr}</p>}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={!!emailErr || !!phoneErr}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
