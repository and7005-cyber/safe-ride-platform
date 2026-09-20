import { useState } from "react";
import { ShieldCheck } from "lucide-react";
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
import { api, ApiError } from "@/lib/apiClient";

// The provider login's second step (U13/AE19). Rendered by AuthPage after a
// password submit answered {preauth, totpRequired}: the pre-auth token lives
// ONLY in AuthPage component state — a reload forgets it and lands back on
// the password step by construction. Each wrong code burns one of the
// token's five attempts; the fifth answers the machine code `preauth-voided`
// and this screen hands control back to the password step with the distinct
// "too many attempts" message.

export function TotpChallengePage({
  preauth,
  email,
  onSuccess,
  onVoided,
  onBack,
}: {
  preauth: string;
  email: string;
  /** A session landed: same contract as the password login's response. */
  onSuccess: (token: string, user: any) => void;
  /** The token died on its fifth wrong code — back to the password step. */
  onVoided: (message: string) => void;
  onBack: () => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post("/api/auth/totp", { token: preauth, code });
      onSuccess(res.token, res.user);
    } catch (err) {
      if (err instanceof ApiError && err.code === "preauth-voided") {
        // AE19's distinct state: "Too many wrong codes. Sign in again."
        onVoided(err.message);
        return;
      }
      // Wrong code / expired token: the server's message, verbatim.
      setError((err as Error).message);
      setCode("");
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2 text-center">
        <div className="mx-auto mb-1 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <CardTitle className="font-heading">Enter your code</CardTitle>
        <CardDescription>
          Signing in as {email}. Enter the 6-digit code from your authenticator
          app.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="totp-code">Authenticator code</Label>
            <Input
              id="totp-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              placeholder="123456"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              data-testid="totp-code"
            />
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive" data-testid="totp-error">
              {error}
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={busy || code.length !== 6}
            data-testid="totp-submit"
          >
            {busy ? "Checking…" : "Verify"}
          </Button>
          <div className="text-center">
            <button
              type="button"
              className="text-sm text-muted-foreground hover:underline"
              onClick={onBack}
            >
              Back to sign in
            </button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
