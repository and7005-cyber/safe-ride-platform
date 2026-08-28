import { useEffect, useRef, useState } from "react";
import { Copy, ShieldCheck } from "lucide-react";
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
import { api, ApiError } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";

// Second-factor enrolment (U13/R20). Rendered by ProtectedRoute IN PLACE of
// the requested page while /me says provider && !totpEnrolled — the same
// interstitial mechanism as the forced password change, and the client
// mirror of the server's enrolment 409 allowlist (only /me, logout,
// change-password and the two totp endpoints answer until confirm).
//
// The key and otpauth:// URI appear ONCE, for manual entry — no QR imagery
// (a QR needs a frontend package; deferred by the plan's scope boundary).
// Every visit to this screen calls enrol again, which regenerates the salt:
// an abandoned half-enrolment never leaves a usable secret behind.

export function TotpEnrolPage() {
  const { refresh, signOut } = useAuth();
  const { toast } = useToast();
  const [enrolment, setEnrolment] = useState<{ secret: string; uri: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  const generate = async () => {
    setLoadError(null);
    setEnrolment(null);
    setCode("");
    setCodeError(null);
    try {
      const res = await api.post("/api/provider/totp/enrol");
      setEnrolment({ secret: res.secret, uri: res.uri });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Already enrolled — /me was stale; a refresh dismisses this screen.
        await refresh();
        return;
      }
      setLoadError((err as Error).message);
    }
  };

  // One enrol call per mount (StrictMode-safe via the ref): each call
  // regenerates the salt, so a double-fire would invalidate the shown key.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void generate();
  });

  const copyText = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast({ title: `${label} copied` });
    } catch {
      toast({
        title: "Copy failed",
        description: `Select and copy the ${label.toLowerCase()} manually.`,
        variant: "destructive",
      });
    }
  };

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) return;
    setBusy(true);
    setCodeError(null);
    try {
      await api.post("/api/provider/totp/confirm", { code });
      toast({ title: "Second factor enabled" });
      // /me now says enrolled; ProtectedRoute continues to the destination.
      await refresh();
    } catch (err) {
      setCodeError((err as Error).message);
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-2 text-center">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <ShieldCheck className="h-7 w-7" />
          </div>
          <h1 className="font-heading text-2xl font-bold text-foreground">SafeRide</h1>
        </div>
        <Card data-testid="totp-enrol-card">
          <CardHeader className="pb-2 text-center">
            <CardTitle className="font-heading">Set up your second factor</CardTitle>
            <CardDescription>
              Provider accounts sign in with an authenticator app. Add this key
              to your app now — it is shown only once — then confirm with your
              first code.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {loadError ? (
              <div className="space-y-3">
                <p className="text-sm font-medium text-destructive">{loadError}</p>
                <Button variant="outline" className="w-full" onClick={() => void generate()}>
                  Try again
                </Button>
              </div>
            ) : !enrolment ? (
              <p className="text-center text-sm text-muted-foreground">Generating your key…</p>
            ) : (
              <>
                <div className="space-y-1.5">
                  <Label>Key (enter it manually)</Label>
                  <div className="flex items-center justify-between gap-2 rounded-md border bg-muted px-3 py-2.5">
                    <span
                      className="break-all font-mono text-sm font-semibold tracking-wide"
                      data-testid="totp-secret"
                    >
                      {enrolment.secret}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void copyText("Key", enrolment.secret)}
                    >
                      <Copy className="h-4 w-4" /> Copy
                    </Button>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label>Or paste the setup link into your app</Label>
                  <div className="flex items-center justify-between gap-2 rounded-md border bg-muted px-3 py-2.5">
                    <span
                      className="break-all font-mono text-xs text-muted-foreground"
                      data-testid="totp-uri"
                    >
                      {enrolment.uri}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void copyText("Setup link", enrolment.uri)}
                    >
                      <Copy className="h-4 w-4" /> Copy
                    </Button>
                  </div>
                </div>
                <form onSubmit={confirm} className="space-y-3 border-t pt-4">
                  <div className="space-y-2">
                    <Label htmlFor="totp-enrol-code">First code from your app</Label>
                    <Input
                      id="totp-enrol-code"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="123456"
                      maxLength={6}
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                      data-testid="totp-enrol-code"
                    />
                  </div>
                  {codeError && (
                    <p
                      className="text-sm font-medium text-destructive"
                      data-testid="totp-enrol-error"
                    >
                      {codeError}
                    </p>
                  )}
                  <Button
                    type="submit"
                    className="w-full"
                    disabled={busy || code.length !== 6}
                    data-testid="totp-enrol-confirm"
                  >
                    {busy ? "Confirming…" : "Confirm and continue"}
                  </Button>
                </form>
                <p className="text-center text-xs text-muted-foreground">
                  Lost the key before confirming?{" "}
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() => void generate()}
                  >
                    Generate a new one
                  </button>
                </p>
              </>
            )}
            <div className="text-center">
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
