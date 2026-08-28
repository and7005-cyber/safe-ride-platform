import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, Trash2, X } from "lucide-react";
import { format } from "date-fns";
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
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useAuth, useIsDirector } from "@/lib/auth";
import { emailError } from "@/lib/validation";
import { useSchoolKey, useStaff } from "@/lib/queries";

// U12 — the director's staff surface (R5, R6, R10, R12): members with their
// role and "password set on", the create/offer form, offered rows with cancel,
// remove (last-director 409 surfaced verbatim), and reset password. Temporary
// passwords are revealed EXACTLY ONCE in a copy dialog — the DriversPage
// pinReveal pattern — because the server hashes them and can never show them
// again.

const ROLE_LABEL: Record<string, string> = {
  director: "Director",
  coordinator: "Coordinator",
};

const EMPTY = { email: "", full_name: "", role: "coordinator" };

function dateText(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : format(date, "d MMM yyyy");
}

export function StaffPage() {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isDirector = useIsDirector();
  const { data: staff } = useStaff();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  // Reveal-once dialog for a freshly created or reset temporary password.
  const [reveal, setReveal] = useState<{ who: string; password: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Coordinators never see this page: the nav hides it and a direct URL
  // bounces (the server would refuse the writes anyway).
  if (!isDirector) return <Navigate to="/" replace />;

  const members: any[] = staff?.members ?? [];
  const offers: any[] = staff?.offers ?? [];

  const refetchStaff = () => qc.invalidateQueries({ queryKey: schoolKey("staff") });

  const copyPassword = async (password: string) => {
    try {
      await navigator.clipboard.writeText(password);
      toast({ title: "Password copied" });
    } catch {
      toast({
        title: "Copy failed",
        description: "Copy the password manually.",
        variant: "destructive",
      });
    }
  };

  const startCreate = () => {
    setForm({ ...EMPTY });
    setOpen(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.post("/api/staff", {
        email: form.email,
        fullName: form.full_name || null,
        role: form.role,
      });
      await refetchStaff();
      setOpen(false);
      if (res.status === "created") {
        // The one and only reveal (R6/AE23).
        setReveal({ who: res.fullName ?? res.email, password: res.temporaryPassword });
      } else {
        toast({
          title: "Role offered",
          description: `${res.email} already has a SafeRide account — they will be asked to accept the ${ROLE_LABEL[res.role] ?? res.role} role at their next sign-in.`,
        });
      }
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const removeMember = async (member: any) => {
    const self = member.userId === user?.id;
    if (
      !(await confirm({
        title: self ? "Leave this school?" : `Remove ${member.fullName ?? member.email}?`,
        description: self
          ? "You will lose access to this school's console immediately."
          : "They lose access to this school immediately. Their account is kept — any other schools or roles they hold stay untouched.",
        confirmLabel: self ? "Leave school" : "Remove",
      }))
    )
      return;
    setBusyId(member.userId);
    try {
      await api.del(`/api/staff/${member.userId}`);
      await refetchStaff();
      toast({ title: self ? "You left the school" : "Staff member removed" });
    } catch (err) {
      // The last-director lock (AE4) answers 409 with a friendly sentence —
      // surfaced verbatim.
      toast({ title: "Cannot remove", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyId(null);
    }
  };

  const cancelOffer = async (offer: any) => {
    if (
      !(await confirm({
        title: `Cancel the offer to ${offer.fullName ?? offer.email}?`,
        description: "They will no longer be asked to join this school.",
        confirmLabel: "Cancel offer",
      }))
    )
      return;
    setBusyId(offer.id);
    try {
      await api.del(`/api/staff/offers/${offer.id}`);
      await refetchStaff();
      toast({ title: "Offer cancelled" });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyId(null);
    }
  };

  const resetPassword = async (member: any) => {
    if (
      !(await confirm({
        title: `Reset ${member.fullName ?? member.email}'s password?`,
        description:
          "Their current password stops working and a new temporary password is shown to you once. They must change it at their next sign-in.",
        confirmLabel: "Reset password",
        destructive: false,
      }))
    )
      return;
    setBusyId(member.userId);
    try {
      const res = await api.post(`/api/staff/${member.userId}/reset-password`);
      await refetchStaff();
      setReveal({ who: member.fullName ?? member.email, password: res.temporaryPassword });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyId(null);
    }
  };

  const emailErr = emailError(form.email, true);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Staff"
        subtitle={`${members.length} member${members.length === 1 ? "" : "s"}${offers.length ? ` · ${offers.length} offered` : ""}`}
        action={
          <Button onClick={startCreate} data-testid="staff-add">
            <Plus className="h-4 w-4" /> Add Staff
          </Button>
        }
      />

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Password set on</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.length === 0 && offers.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  No staff yet.
                </TableCell>
              </TableRow>
            ) : (
              <>
                {members.map((m: any) => (
                  <TableRow key={m.membershipId} data-testid={`staff-member-${m.userId}`}>
                    <TableCell className="font-medium">{m.fullName ?? "—"}</TableCell>
                    <TableCell>{m.email}</TableCell>
                    <TableCell>
                      <Badge variant={m.role === "director" ? "default" : "secondary"}>
                        {ROLE_LABEL[m.role] ?? m.role}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="success">Active</Badge>
                    </TableCell>
                    {/* R12: when this person last set their own password — a
                        "—" means they still hold a temporary one. */}
                    <TableCell className="text-muted-foreground">
                      {dateText(m.passwordSetOn)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Reset password"
                        aria-label={`Reset password for ${m.email}`}
                        disabled={busyId !== null}
                        onClick={() => resetPassword(m)}
                      >
                        <KeyRound className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Remove from this school"
                        aria-label={`Remove ${m.email}`}
                        disabled={busyId !== null}
                        onClick={() => removeMember(m)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {offers.map((o: any) => (
                  <TableRow key={o.id} data-testid={`staff-offer-${o.id}`}>
                    <TableCell className="font-medium">{o.fullName ?? "—"}</TableCell>
                    <TableCell>{o.email}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{ROLE_LABEL[o.role] ?? o.role}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">Offered</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {o.offeredBy ? `by ${o.offeredBy} · ` : ""}
                      {dateText(o.offeredAt)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Cancel offer"
                        aria-label={`Cancel offer to ${o.email}`}
                        disabled={busyId !== null}
                        onClick={() => cancelOffer(o)}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </>
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Staff</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Email</Label>
              <Input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
              {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
            </div>
            <div className="space-y-2">
              <Label>Full name</Label>
              <Input
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Role</Label>
              <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                <SelectTrigger data-testid="staff-role">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="coordinator">Coordinator</SelectItem>
                  <SelectItem value="director">Director</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Coordinators run the day-to-day but cannot delete records or
                manage staff. Directors can do everything, including this page.
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              A new email gets an account with a temporary password shown to
              you once. An email that already has a SafeRide account is offered
              the role instead — they accept it at their next sign-in.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={saving || !form.email || !!emailErr}
              data-testid="staff-save"
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reveal-once dialog (AE23): mirrors the driver pinReveal — the server
          stores only a hash, so this is the single chance to share it. */}
      <Dialog open={!!reveal} onOpenChange={(o) => (o ? null : setReveal(null))}>
        <DialogContent className="max-w-sm" data-testid="temp-password-dialog">
          <DialogHeader>
            <DialogTitle>Temporary password</DialogTitle>
          </DialogHeader>
          {reveal && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Share this password with {reveal.who} now — they sign in with
                it and must set their own password immediately. For security it
                is stored encrypted and can't be shown again.
              </p>
              <div className="flex items-center justify-between gap-2 rounded-md border bg-muted px-4 py-3">
                <span
                  className="break-all font-mono text-lg font-semibold"
                  data-testid="temp-password"
                >
                  {reveal.password}
                </span>
                <Button variant="outline" size="sm" onClick={() => copyPassword(reveal.password)}>
                  <Copy className="h-4 w-4" /> Copy
                </Button>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setReveal(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
