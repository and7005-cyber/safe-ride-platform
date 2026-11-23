import { useState, type ReactNode } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { useToast } from "@/components/ui/use-toast";
import {
  CHANGE_PASSWORD_DIALOG_TEXT,
  CHANGE_PASSWORD_LABEL,
  PASSWORD_CHANGED_TOAST,
} from "@/features/auth/authCopy";
import { api } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";

// Two modes (hardening U9, DL13).
//
// forced — U12/R30, AE16: rendered by ProtectedRoute IN PLACE of the requested
// page while /me says mustChangePassword — the URL is kept, so finishing the
// change lands on the originally requested destination. The server requires
// the CURRENT (temporary) password even here, and every other endpoint answers
// 409 until the change is done. Its layout and words are unchanged.
//
// voluntary — "Change password" from the parent Profile or an account menu,
// for every signed-in role except a session created by PIN (it never held the
// account password). A dialog with Cancel and no Sign out link; the calling
// session stays signed in, the others are signed out, and push is kept.

type ChangePasswordProps =
  | { mode?: "forced" }
  | { mode: "voluntary"; open: boolean; onOpenChange: (open: boolean) => void };

export function ChangePasswordPage(props: ChangePasswordProps = {}) {
  if (props.mode === "voluntary") {
    return <VoluntaryChange open={props.open} onOpenChange={props.onOpenChange} />;
  }
  return <ForcedChange />;
}

/** The voluntary dialog, as the menus and the Profile page open it. */
export function ChangePasswordDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return <ChangePasswordPage mode="voluntary" open={open} onOpenChange={onOpenChange} />;
}

function ForcedChange() {
  const { user, refresh, signOut } = useAuth();
  const { toast } = useToast();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-2 text-center">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <KeyRound className="h-7 w-7" />
          </div>
          <h1 className="font-heading text-2xl font-bold text-foreground">SafeRide</h1>
        </div>
        <Card>
          <CardHeader className="pb-2 text-center">
            <CardTitle className="font-heading">Change your password</CardTitle>
            <CardDescription>
              {user?.email ? `Signed in as ${user.email}. ` : ""}
              Your temporary password must be changed before you can continue.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PasswordChangeForm
              onChanged={async () => {
                toast({ title: PASSWORD_CHANGED_TOAST });
                // /me now answers without the flag; ProtectedRoute continues to
                // the destination on its own once the state lands.
                await refresh();
              }}
              actions={(saving, ready) => (
                <Button type="submit" className="w-full" disabled={saving || !ready}>
                  {saving ? "Changing…" : "Change password"}
                </Button>
              )}
            />
            <div className="mt-4 text-center">
              <button
                type="button"
                className="text-sm text-muted-foreground hover:underline"
                onClick={() => void signOut()}
              >
                Sign out
              </button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function VoluntaryChange({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { toast } = useToast();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm" data-testid="change-password-dialog">
        <DialogHeader>
          <DialogTitle>{CHANGE_PASSWORD_LABEL}</DialogTitle>
          <DialogDescription>{CHANGE_PASSWORD_DIALOG_TEXT}</DialogDescription>
        </DialogHeader>
        {/* Mounted only while open (Radix unmounts closed content), so every
            opening starts from empty fields. */}
        <PasswordChangeForm
          onChanged={() => {
            toast({ title: PASSWORD_CHANGED_TOAST });
            onOpenChange(false);
          }}
          actions={(saving, ready) => (
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving || !ready}>
                {saving ? "Changing…" : "Change password"}
              </Button>
            </DialogFooter>
          )}
        />
      </DialogContent>
    </Dialog>
  );
}

/** The three fields and the submit, shared by both modes. */
function PasswordChangeForm({
  onChanged,
  actions,
}: {
  onChanged: () => void | Promise<void>;
  actions: (saving: boolean, ready: boolean) => ReactNode;
}) {
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;
  const tooShort = next.length > 0 && next.length < 6;
  const ready = Boolean(current) && next.length >= 6 && next === confirm;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setSaving(true);
    try {
      await api.post("/api/auth/change-password", {
        currentPassword: current,
        newPassword: next,
      });
      await onChanged();
    } catch (err) {
      // 400 wrong current password / 429 rate limit / 403 PIN session — the
      // server's message.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="current-password">Current password</Label>
        <Input
          id="current-password"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={6}
          maxLength={72}
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        {tooShort && <p className="text-xs text-destructive">At least 6 characters.</p>}
      </div>
      <div className="space-y-2">
        <Label htmlFor="confirm-password">Confirm new password</Label>
        <Input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {mismatch && <p className="text-xs text-destructive">Passwords do not match.</p>}
      </div>
      {actions(saving, ready)}
    </form>
  );
}
