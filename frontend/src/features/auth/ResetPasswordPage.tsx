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
import { api } from "@/lib/apiClient";

type LinkState = "checking" | "ready" | "missing";

export function ResetPasswordPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { toast } = useToast();
  const token = params.get("token");

  // Token presence is the only signal needed locally; "checking" is brief.
  const [linkState] = useState<LinkState>(token ? "ready" : "missing");
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
      await api.post("/api/auth/reset-password", { token, password });
      toast({ title: "Password updated", description: "Please sign in with your new password." });
      navigate("/auth");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
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
              {linkState === "missing" ? "Reset link invalid" : "Set a new password"}
            </CardTitle>
            <CardDescription>
              {linkState === "missing"
                ? "Reset links can only be opened once and expire after a short time."
                : "Choose a new password for your account."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {linkState === "missing" ? (
              <Button className="w-full" onClick={() => navigate("/auth?forgot=1")}>
                Request a new reset link
              </Button>
            ) : (
              <form onSubmit={submit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="password">New password</Label>
                  <Input id="password" type="password" required minLength={6} disabled={linkState !== "ready"} value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="confirm">Confirm password</Label>
                  <Input id="confirm" type="password" required minLength={6} disabled={linkState !== "ready"} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                </div>
                <Button type="submit" className="w-full" disabled={saving || linkState !== "ready"}>
                  {saving ? "Updating…" : linkState === "checking" ? "Verifying…" : "Update Password"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
