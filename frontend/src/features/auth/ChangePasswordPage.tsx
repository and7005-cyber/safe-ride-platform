import { useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";

// Forced password change (U12/R30, AE16). Rendered by ProtectedRoute IN PLACE
// of the requested page while /me says mustChangePassword — the URL is kept,
// so finishing the change lands on the originally requested destination.
// The server requires the CURRENT (temporary) password even here, and every
// other endpoint answers 409 until the change is done.

export function ChangePasswordPage() {
  const { user, refresh, signOut } = useAuth();
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;
  const tooShort = next.length > 0 && next.length < 6;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm || next.length < 6) return;
    setSaving(true);
    try {
      await api.post("/api/auth/change-password", {
        currentPassword: current,
        newPassword: next,
      });
      toast({ title: "Password changed" });
      // /me now answers without the flag; ProtectedRoute continues to the
      // destination on its own once the state lands.
      await refresh();
    } catch (err) {
      // 400 wrong current password / 429 rate limit — the server's message.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

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
                {tooShort && (
                  <p className="text-xs text-destructive">At least 6 characters.</p>
                )}
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
                {mismatch && (
                  <p className="text-xs text-destructive">Passwords do not match.</p>
                )}
              </div>
              <Button
                type="submit"
                className="w-full"
                disabled={saving || !current || next.length < 6 || next !== confirm}
              >
                {saving ? "Changing…" : "Change password"}
              </Button>
            </form>
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
