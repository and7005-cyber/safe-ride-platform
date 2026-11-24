import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { useIsDirector } from "@/lib/auth";
import { emailError, phoneError } from "@/lib/validation";
import { useDrivers, useSchoolKey, useSchoolSettings } from "@/lib/queries";
import { formatDriverCode } from "@/lib/driverSchool";
import { PIN_LENGTH, generatePin as generateNewPin, newPinProblem } from "@/lib/driverPin";
import { DriverCodeCard } from "@/features/admin/components/DriverCodeCard";
import { pinRevealCodeLine } from "@/features/admin/driverCodeCopy";

const EMPTY = { full_name: "", email: "", password: "", phone: "", pin: "" };

export function DriversPage() {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const isDirector = useIsDirector();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: drivers = [] } = useDrivers();
  // Hardening U10: the school's driver code (the settings row carries it).
  const { data: school } = useSchoolSettings();
  const driverCode = school?.driver_code ? formatDriverCode(school.driver_code) : null;
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  // After a PIN is set/reset, reveal it once so the admin can share it — the
  // backend hashes it and can never show it again.
  const [pinReveal, setPinReveal] = useState<{ name: string; pin: string } | null>(null);

  const copyPin = async (pin: string) => {
    try {
      await navigator.clipboard.writeText(pin);
      toast({ title: "PIN copied" });
    } catch {
      toast({ title: "Copy failed", description: "Copy the PIN manually.", variant: "destructive" });
    }
  };

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (d: any) => {
    setEditId(d.id);
    // PIN/password always start blank: blank PIN keeps the existing PIN.
    setForm({ full_name: d.full_name ?? "", email: d.email ?? "", password: "", phone: d.phone ?? "", pin: "" });
    setOpen(true);
  };

  // DL8: six digits from the browser's cryptographic generator, drawn again
  // until the same rules the server applies accept them.
  const generatePin = () => setForm({ ...form, pin: generateNewPin() });

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
      await qc.invalidateQueries({ queryKey: schoolKey("accounts-drivers") });
      await qc.invalidateQueries({ queryKey: schoolKey("buses") });
      const newPin = form.pin;
      const driverName = form.full_name;
      setOpen(false);
      // A non-empty PIN means it was just set or reset — reveal it once.
      if (newPin) setPinReveal({ name: driverName, pin: newPin });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this driver?",
      description: "This permanently removes the driver account and unassigns their bus.",
      confirmLabel: "Delete driver",
    }))) return;
    await api.del(`/api/accounts/drivers/${id}`);
    await qc.invalidateQueries({ queryKey: schoolKey("accounts-drivers") });
    await qc.invalidateQueries({ queryKey: schoolKey("buses") });
  };

  const emailErr = emailError(form.email, true);
  const phoneErr = phoneError(form.phone);
  // DL8: a new or re-issued PIN is six digits and not an easy guess; blank
  // means "no PIN yet" on create and "keep the current PIN" on edit.
  const pinErr = newPinProblem(form.pin);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Drivers"
        subtitle={`${drivers.length} driver${drivers.length === 1 ? "" : "s"}`}
        action={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Driver</Button>}
      />

      <DriverCodeCard school={school} isDirector={isDirector} />

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
                    {/* Director-only (U12/R8): the server 403s the backstop. */}
                    {isDirector && (
                      <Button variant="ghost" size="icon" title="Delete driver" onClick={() => remove(d.id)}><Trash2 className="h-4 w-4" /></Button>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editId ? "Edit Driver" : "Add Driver"}</DialogTitle>
            <DialogDescription className="sr-only">The driver's name, contact details and sign-in credentials.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2"><Label>Full name</Label><Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div className="space-y-2">
              <Label>Email</Label>
              <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
            </div>
            {!editId && (
              <div className="space-y-2"><Label>Password</Label><Input type="password" minLength={6} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
            )}
            <div className="space-y-2">
              <Label>Phone</Label>
              <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              {phoneErr && <p className="text-xs text-destructive">{phoneErr}</p>}
            </div>
            <div className="space-y-2">
              <Label>
                {editId ? "Reset PIN" : "Driver PIN"}
                {editId && <span className="ml-1 text-xs text-muted-foreground">(leave blank to keep the current PIN)</span>}
              </Label>
              <div className="flex gap-2">
                <Input
                  inputMode="numeric"
                  maxLength={PIN_LENGTH}
                  placeholder="6 digits"
                  aria-label={editId ? "Reset PIN" : "Driver PIN"}
                  aria-invalid={pinErr ? true : undefined}
                  value={form.pin}
                  onChange={(e) => setForm({ ...form, pin: e.target.value.replace(/\D/g, "").slice(0, PIN_LENGTH) })}
                />
                <Button type="button" variant="outline" onClick={generatePin}>Generate</Button>
              </div>
              {pinErr && <p className="text-xs text-destructive" data-testid="pin-error">{pinErr}</p>}
              <p className="text-xs text-muted-foreground">
                You'll see the PIN once after saving — it's stored encrypted and can't be shown again.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={saving || !form.full_name || !!emailErr || !!phoneErr || !!pinErr || (!editId && form.password.length < 6)}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!pinReveal} onOpenChange={(o) => (o ? null : setPinReveal(null))}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Driver PIN set</DialogTitle></DialogHeader>
          {pinReveal && (
            <div className="space-y-3">
              <DialogDescription>
                Share this PIN with {pinReveal.name || "the driver"} now — they sign in on the
                <span className="font-medium"> Driver PIN</span> tab. For security it's stored
                encrypted and can't be shown again.
              </DialogDescription>
              {driverCode && (
                <p className="text-sm text-muted-foreground" data-testid="pin-reveal-code">
                  {pinRevealCodeLine(driverCode)}
                </p>
              )}
              <div className="flex items-center justify-between rounded-md border bg-muted px-4 py-3">
                <span className="font-mono text-2xl font-semibold tracking-[0.3em]">{pinReveal.pin}</span>
                <Button variant="outline" size="sm" onClick={() => copyPin(pinReveal.pin)}>
                  <Copy className="h-4 w-4" /> Copy
                </Button>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setPinReveal(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
