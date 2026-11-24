import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { KeyRound, Pencil, Trash2, UserCheck } from "lucide-react";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { TemporaryPasswordDialog } from "@/features/admin/components/TemporaryPasswordDialog";
import {
  CONFIRM_AND_RESET_LABEL,
  CONFIRM_SLOT_TEXT,
  CONFIRM_SLOT_WARNING,
  LINK_NOT_ACCEPTED_TOOLTIP,
  NOTHING_TO_CONFIRM_TEXT,
  NOTHING_TO_CONFIRM_TITLE,
  NOT_EDITABLE_HINT,
  NOT_EDITABLE_TITLE,
  NOT_RESETTABLE_HINT,
  NOT_RESETTABLE_TITLE,
  RESET_CONFIRM_LEAD,
  RESET_PASSWORD_LABEL,
  SHARED_EDIT_TOOLTIP,
  SHARED_RESET_TOOLTIP,
  TEMPORARY_PASSWORD_EXPIRED_CHIP,
  resetConfirmText,
  temporaryPasswordChip,
} from "@/features/auth/authCopy";
import { ApiError, api } from "@/lib/apiClient";
import { useIsDirector } from "@/lib/auth";
import { useMailEnabled } from "@/lib/publicConfig";
import { emailError, phoneError } from "@/lib/validation";
import { useParents, useSchoolKey } from "@/lib/queries";

/** The row's temporary-password chip (DL12), or null when it holds none. */
export function temporaryPasswordState(
  parent: { mustChangePassword?: boolean; temporaryPasswordExpiresAt?: string | null },
  now: number = Date.now(),
): { expired: boolean; label: string } | null {
  if (!parent.mustChangePassword || !parent.temporaryPasswordExpiresAt) return null;
  const expires = new Date(parent.temporaryPasswordExpiresAt);
  if (Number.isNaN(expires.getTime())) return null;
  if (expires.getTime() <= now) return { expired: true, label: TEMPORARY_PASSWORD_EXPIRED_CHIP };
  return { expired: false, label: temporaryPasswordChip(format(expires, "d MMM, HH:mm")) };
}

// Icon-only row buttons: 44px targets with the email in the label (DL17).
const ROW_ICON = "h-11 w-11";

export function ParentsPage() {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const isDirector = useIsDirector();
  const mailEnabled = useMailEnabled();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: parents = [] } = useParents();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ full_name: "", email: "", phone: "" });
  // Hardening U9 (DL12): one action at a time across the table (StaffPage's
  // busyId pattern) and the reveal-once dialog for a temporary password.
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ who: string; password: string } | null>(null);

  const refetchParents = () => qc.invalidateQueries({ queryKey: schoolKey("accounts-parents") });

  const startEdit = (p: any) => {
    setEditId(p.id);
    setForm({ full_name: p.full_name ?? "", email: p.email ?? "", phone: p.phone ?? "" });
    setOpen(true);
  };

  const save = async () => {
    if (!editId) return;
    try {
      await api.put(`/api/accounts/parents/${editId}`, form);
      await refetchParents();
      setOpen(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === "not-editable-here") {
        // The reset's sibling refusal: neutral, never the school or role.
        toast({
          title: NOT_EDITABLE_TITLE,
          description: `${err.message}. ${NOT_EDITABLE_HINT}`,
          variant: "destructive",
        });
      } else {
        toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      }
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this parent?",
      description: "This removes the parent account and its student links.",
      confirmLabel: "Delete parent",
    }))) return;
    await api.del(`/api/accounts/parents/${id}`);
    await refetchParents();
  };

  const resetPassword = async (p: any) => {
    const who = p.full_name ?? p.email;
    if (
      !(await confirm({
        title: `Reset ${who}'s password?`,
        // Mail off (DL1): no promise of a notice that is never sent.
        description: `${RESET_CONFIRM_LEAD} ${resetConfirmText(mailEnabled !== false)}`,
        confirmLabel: RESET_PASSWORD_LABEL,
        destructive: false,
      }))
    )
      return;
    setBusyId(p.id);
    try {
      const res = await api.post(`/api/accounts/parents/${p.id}/reset-password`);
      // Reveal first: the server keeps only a hash, so a refetch that stalls
      // or a page left mid-refetch must not lose the one-time password.
      setReveal({ who, password: res.temporaryPassword });
      void refetchParents();
    } catch (err) {
      if (err instanceof ApiError && err.code === "not-resettable-here") {
        toast({
          title: NOT_RESETTABLE_TITLE,
          description: `${err.message}. ${NOT_RESETTABLE_HINT}`,
          variant: "destructive",
        });
      } else {
        toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      }
    } finally {
      setBusyId(null);
    }
  };

  const confirmSlot = async (p: any) => {
    if (
      !(await confirm({
        title: `${CONFIRM_AND_RESET_LABEL}: ${p.email}?`,
        description: `${CONFIRM_SLOT_WARNING} ${CONFIRM_SLOT_TEXT}`,
        confirmLabel: CONFIRM_AND_RESET_LABEL,
        destructive: false,
      }))
    )
      return;
    setBusyId(`slot:${p.email}`);
    try {
      const res = await api.post("/api/accounts/parents/slots/confirm", { email: p.email });
      setReveal({ who: p.full_name ?? p.email, password: res.temporaryPassword });
      void refetchParents();
    } catch (err) {
      if (err instanceof ApiError && err.code === "nothing-to-confirm") {
        // The neutral no-op (DL12): never says whether an account exists.
        toast({ title: NOTHING_TO_CONFIRM_TITLE, description: NOTHING_TO_CONFIRM_TEXT });
      } else {
        toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      }
    } finally {
      setBusyId(null);
    }
  };

  const emailErr = emailError(form.email, true);
  const phoneErr = phoneError(form.phone);

  const resetButton = (p: any) => {
    const button = (
      <Button
        variant="ghost"
        size="icon"
        className={ROW_ICON}
        aria-label={`${RESET_PASSWORD_LABEL} for ${p.email}`}
        disabled={busyId !== null || !p.linkAccepted}
        onClick={() => resetPassword(p)}
        data-testid={`parent-reset-${p.id}`}
      >
        <KeyRound className="h-4 w-4" />
      </Button>
    );
    const tip = !p.linkAccepted
      ? LINK_NOT_ACCEPTED_TOOLTIP
      : p.shared
        ? SHARED_RESET_TOOLTIP
        : RESET_PASSWORD_LABEL;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          {/* span wrapper: disabled buttons swallow the hover */}
          <span className="inline-flex">{button}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{tip}</TooltipContent>
      </Tooltip>
    );
  };

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
            {parents.map((p: any, idx: number) => {
              const temporary = p.status === "registered" ? temporaryPasswordState(p) : null;
              return (
                <TableRow key={p.id ?? `pending-${idx}`} data-testid={p.id ? `parent-row-${p.id}` : undefined}>
                  <TableCell className="font-medium">{p.full_name ?? "—"}</TableCell>
                  <TableCell>{p.email}</TableCell>
                  <TableCell>{p.students.join(", ") || "—"}</TableCell>
                  <TableCell>
                    {p.status === "registered" ? (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="success">Registered</Badge>
                        {/* U6/R33: a parent with a child at another school is
                            frozen for this school — marked, never named. */}
                        {p.shared && (
                          <Badge variant="outline" data-testid={`parent-shared-${p.id}`}>Shared</Badge>
                        )}
                        {temporary && (
                          <Badge
                            variant={temporary.expired ? "destructive" : "warning"}
                            className="rounded-md"
                            data-testid={`parent-temporary-${p.id}`}
                          >
                            {temporary.label}
                          </Badge>
                        )}
                      </span>
                    ) : (
                      <Badge variant="secondary">Awaiting signup</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {p.status === "registered" ? (
                      <span className="inline-flex items-center justify-end">
                        {p.shared ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="inline-flex">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className={ROW_ICON}
                                  disabled
                                  aria-label={`Edit ${p.email} (shared)`}
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                              </span>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs">{SHARED_EDIT_TOOLTIP}</TooltipContent>
                          </Tooltip>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            className={ROW_ICON}
                            aria-label={`Edit ${p.email}`}
                            disabled={busyId !== null}
                            onClick={() => startEdit(p)}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                        )}
                        {resetButton(p)}
                        {/* Director-only (U12/R8): the server 403s the backstop. */}
                        {isDirector && !p.shared && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className={ROW_ICON}
                            title="Delete parent"
                            aria-label={`Delete ${p.email}`}
                            disabled={busyId !== null}
                            onClick={() => remove(p.id)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </span>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-11"
                        aria-label={`${CONFIRM_AND_RESET_LABEL} for ${p.email}`}
                        disabled={busyId !== null}
                        onClick={() => confirmSlot(p)}
                        data-testid={`parent-confirm-${p.email}`}
                      >
                        <UserCheck className="h-4 w-4" /> {CONFIRM_AND_RESET_LABEL}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Parent</DialogTitle>
            <DialogDescription className="sr-only">Update this parent's name, email and phone.</DialogDescription>
          </DialogHeader>
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

      <TemporaryPasswordDialog reveal={reveal} onClose={() => setReveal(null)} />
    </div>
  );
}
