import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
import { ApiError, api } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { RESET_LINK_INVALID_TEXT, RESET_LINK_INVALID_TITLE } from "@/features/auth/authCopy";

// "invalid": no token in the link, or the server refused it (expired, used,
// unknown) — DL11 shows the card, not a toast after the password was typed.
type LinkState = "ready" | "invalid";

export function ResetPasswordPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { toast } = useToast();
  const { signIn } = useAuth();
  const token = params.get("token");

  const [linkState, setLinkState] = useState<LinkState>(token ? "ready" : "invalid");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      toast({ title: "Passwords do not match", variant: "destructive" });
      return;
    }
    if (password.length < 6) {
      toast({ title: "Password must be at least 6 characters", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      const res = await api.post("/api/auth/reset-password", { token, password });
      if (res?.token && res.user) {
        // Release 1b: the reset lands signed in (the inbox proved the
        // address); the role's home takes it from "/".
        signIn(res.token, res.user);
        toast({ title: "Password updated", description: "You're signed in." });
        navigate(res.user.role === "driver" ? "/driver" : "/");
      } else {
        // A provider account still passes its second factor at sign-in.
        toast({ title: "Password updated", description: "Sign in with your new password." });
        navigate("/auth");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setLinkState("invalid");
      } else {
        toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <Card>
          <CardHeader className="text-center">
            <CardTitle className="font-heading">
              {linkState === "invalid" ? RESET_LINK_INVALID_TITLE : "Set a new password"}
            </CardTitle>
            <CardDescription>
              {linkState === "invalid"
                ? RESET_LINK_INVALID_TEXT
                : "Choose a new password for your account."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {linkState === "invalid" ? (
              <Button className="w-full" onClick={() => navigate("/auth?forgot=1")}>
                Request a new reset link
              </Button>
            ) : (
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="password">New password</Label>
                  <Input id="password" type="password" autoComplete="new-password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="confirm">Confirm password</Label>
                  <Input id="confirm" type="password" autoComplete="new-password" required minLength={6} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                </div>
                <Button type="submit" className="w-full" disabled={saving}>
                  {saving ? "Updating…" : "Update Password"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
