import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Copy, KeyRound, Plus, Trash2 } from "lucide-react";
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
import { api, ApiError } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { emailError } from "@/lib/validation";
import {
  runWithStepUp,
  stepUpCodeNeeded,
  useProviderAccounts,
} from "@/features/provider/providerHooks";
import { useStepUpPrompt } from "@/features/provider/StepUpCodeDialog";

// Provider accounts (U13/R19, R20; AE17/AE24): Kuumbai's own operators.
// Create (reveal-once temporary password), remove (the last active account
// is locked — the server's 409 is surfaced verbatim) and the peer
// second-factor reset. Every one of these writes is step-up gated: a code
// verified in the last 15 minutes, or a fresh one collected here — shown up
// front in the create form when stale, prompted via dialog for row actions,
// and ALWAYS retried when the server answers totp-step-up-required.

function dateText(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : format(date, "d MMM yyyy");
}

const EMPTY = { email: "", fullName: "" };

export function ProviderAccountsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { user, refresh } = useAuth();
  const { promptCode, stepUpDialog } = useStepUpPrompt();
  const { data: accounts = [], isLoading } = useProviderAccounts();

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [createCode, setCreateCode] = useState("");
  const [askCreateCode, setAskCreateCode] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reveal, setReveal] = useState<{ who: string; password: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const verifiedAt = user?.provider?.totpVerifiedAt;
  const refetchAccounts = () => qc.invalidateQueries({ queryKey: ["provider", "accounts"] });

  const startCreate = () => {
    setForm({ ...EMPTY });
    setCreateCode("");
    setCreateError(null);
    // The code input rides in the form itself when the session's last code
    // is stale — no second dialog mid-save.
    setAskCreateCode(stepUpCodeNeeded(verifiedAt));
    setOpen(true);
  };

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

  const save = async () => {
    setSaving(true);
    setCreateError(null);
    try {
      const res = await api.post("/api/provider/accounts", {
        email: form.email.trim(),
        fullName: form.fullName.trim() || null,
        code: askCreateCode && createCode ? createCode : undefined,
      });
      await refetchAccounts();
      setOpen(false);
      setReveal({ who: res.fullName ?? res.email, password: res.temporaryPassword });
      void refresh(); // pick up the session's refreshed totpVerifiedAt
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp-step-up-required") {
        // The freshness pre-judgement was stale: reveal the code field in
        // the form and let the person retry (the server stays authority).
        setAskCreateCode(true);
        setCreateCode("");
        setCreateError(err.message);
        return;
      }
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const removeAccount = async (account: any) => {
    const self = account.userId === user?.id;
    if (
      !(await confirm({
        title: self
          ? "Remove your own provider account?"
          : `Remove ${account.fullName ?? account.email}?`,
        description: self
          ? "You will lose the provider console immediately. The last active provider account cannot be removed."
          : "They lose the provider console immediately: every session ends and their sign-in stops working. The audit trail keeps their name.",
        confirmLabel: "Remove",
      }))
    )
      return;
    setBusyId(account.userId);
    try {
      const outcome = await runWithStepUp(
        (code) => api.del(`/api/provider/accounts/${account.userId}`, code ? { code } : undefined),
        { totpVerifiedAt: verifiedAt, promptCode },
      );
      if (outcome.cancelled) return;
      await refetchAccounts();
      toast({ title: "Provider account removed" });
      void refresh();
    } catch (err) {
      // The last-provider lock (AE24) answers 409 with a friendly sentence —
      // surfaced verbatim.
      toast({ title: "Cannot remove", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyId(null);
    }
  };

  const resetTotp = async (account: any) => {
    if (
      !(await confirm({
        title: `Reset ${account.fullName ?? account.email}'s second factor?`,
        description:
          "Their current authenticator key stops working and their next sign-in walks through enrolment again. Use this when a phone is lost or replaced.",
        confirmLabel: "Reset second factor",
        destructive: false,
      }))
    )
      return;
    setBusyId(account.userId);
    try {
      const outcome = await runWithStepUp(
        (code) =>
          api.post(
            `/api/provider/accounts/${account.userId}/reset-totp`,
            code ? { code } : {},
          ),
        { totpVerifiedAt: verifiedAt, promptCode },
      );
      if (outcome.cancelled) return;
      await refetchAccounts();
      toast({ title: "Second factor reset" });
      void refresh();
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
        title="Provider accounts"
        subtitle={`${accounts.length} account${accounts.length === 1 ? "" : "s"} · Kuumbai Kenya`}
        action={
          <Button onClick={startCreate} data-testid="provider-account-add">
            <Plus className="h-4 w-4" /> Add Account
          </Button>
        }
      />

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Second factor</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : accounts.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  No provider accounts.
                </TableCell>
              </TableRow>
            ) : (
              accounts.map((account) => (
                <TableRow key={account.userId} data-testid={`provider-account-${account.userId}`}>
                  <TableCell className="font-medium">
                    {account.fullName ?? "—"}
                    {account.userId === user?.id && (
                      <span className="ml-2 text-xs text-muted-foreground">(you)</span>
                    )}
                  </TableCell>
                  <TableCell>{account.email}</TableCell>
                  <TableCell>
                    {account.totpEnrolled ? (
                      <Badge variant="success">Enrolled</Badge>
                    ) : (
                      <Badge variant="secondary">Not enrolled</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {dateText(account.createdAt)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Reset second factor"
                      aria-label={`Reset second factor for ${account.email}`}
                      disabled={busyId !== null}
                      onClick={() => resetTotp(account)}
                    >
                      <KeyRound className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      title="Remove account"
                      aria-label={`Remove ${account.email}`}
                      disabled={busyId !== null}
                      onClick={() => removeAccount(account)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={(next) => (saving ? null : setOpen(next))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Add provider account</DialogTitle>
            <DialogDescription>
              A Kuumbai operator account: they sign in with a temporary
              password shown to you once, then set their own password and
              enrol an authenticator app. The email must not already have a
              SafeRide account.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="account-email">Email</Label>
              <Input
                id="account-email"
                data-testid="provider-account-email"
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
              {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="account-name">Full name</Label>
              <Input
                id="account-name"
                data-testid="provider-account-name"
                value={form.fullName}
                onChange={(e) => setForm({ ...form, fullName: e.target.value })}
              />
            </div>
            {askCreateCode && (
              <div className="space-y-1.5">
                <Label htmlFor="account-code">Authenticator code</Label>
                <Input
                  id="account-code"
                  data-testid="provider-account-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="123456"
                  maxLength={6}
                  value={createCode}
                  onChange={(e) => setCreateCode(e.target.value.replace(/\D/g, ""))}
                />
                <p className="text-xs text-muted-foreground">
                  Your last code is older than 15 minutes — creating an account
                  needs a fresh one.
                </p>
              </div>
            )}
            {createError && (
              <p className="text-sm font-medium text-destructive" data-testid="provider-account-error">
                {createError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={saving} onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={
                saving || !form.email || !!emailErr || (askCreateCode && createCode.length !== 6)
              }
              data-testid="provider-account-save"
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reveal-once dialog: the server stores only a hash — this is the
          single chance to share the temporary password (StaffPage pattern). */}
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

      {stepUpDialog}
    </div>
  );
}
