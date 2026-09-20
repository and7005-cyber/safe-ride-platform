import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { stepUpCodeNeeded } from "@/features/provider/providerHooks";

// Step-in dialog (U13/AE29): reason (required, ≤500 — it lands verbatim on
// the school's audit trail) plus the authenticator code, shown UP FRONT when
// the session's last code is stale (>15 min / never) and always demanded on
// a `totp-step-up-required` answer (clock skew, races — the server stays the
// authority). On success the tab enters the school's admin console: /me now
// carries the support session, reconciliation points the per-tab store at
// that school, and scope resolution grants director powers there.

export function StepInDialog({
  school,
  onOpenChange,
}: {
  /** The school to step into, or null while the dialog is closed. */
  school: { schoolId: string; name: string; code: string } | null;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user, refresh } = useAuth();

  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [askCode, setAskCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Re-arm per opening: the code input pre-judges freshness from /me's
  // totpVerifiedAt; the server's refusal below can still widen it.
  useEffect(() => {
    if (school) {
      setReason("");
      setCode("");
      setError(null);
      setAskCode(stepUpCodeNeeded(user?.provider?.totpVerifiedAt));
    }
    // Deliberately keyed on the dialog opening, not on user churn mid-typing.
  }, [school?.schoolId]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!school || !reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/provider/step-in", {
        schoolId: school.schoolId,
        reason: reason.trim(),
        code: askCode && code ? code : undefined,
      });
      // Refresh FIRST: /me's supportSession is what lets the school store
      // survive reconciliation (U13's widened rule) — then enter the console.
      // Any stale cache from an earlier visit to this school is dropped.
      await qc.cancelQueries({ queryKey: ["school", school.schoolId] });
      qc.removeQueries({ queryKey: ["school", school.schoolId] });
      await refresh();
      onOpenChange(false);
      navigate("/");
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp-step-up-required") {
        // The pre-judgement was stale: reveal the code field and retry.
        setAskCode(true);
        setError(err.message);
      } else {
        setError((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={Boolean(school)} onOpenChange={(next) => (busy ? null : onOpenChange(next))}>
      <DialogContent className="max-w-md" data-testid="step-in-dialog">
        <DialogHeader>
          <DialogTitle className="pr-6">
            Step in to {school?.name ?? "this school"}
          </DialogTitle>
          <DialogDescription>
            School code <span className="font-mono">{school?.code ?? "—"}</span>. You
            will work in its console as SafeRide with director powers for up to
            four hours; everything you do is recorded on the school's audit
            trail with your name.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="step-in-reason">Reason (shown to the school)</Label>
            <Textarea
              id="step-in-reason"
              data-testid="step-in-reason"
              autoFocus
              rows={3}
              maxLength={500}
              placeholder="e.g. Support ticket #123 — fixing the morning route stops"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          {askCode && (
            <div className="space-y-1.5">
              <Label htmlFor="step-in-code">Authenticator code</Label>
              <Input
                id="step-in-code"
                data-testid="step-in-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
              <p className="text-xs text-muted-foreground">
                Your last code is older than 15 minutes — stepping in needs a
                fresh one.
              </p>
            </div>
          )}
          {error && (
            <p className="text-sm font-medium text-destructive" data-testid="step-in-error">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={busy || !reason.trim() || (askCode && code.length !== 6)}
              data-testid="step-in-submit"
            >
              {busy ? "Stepping in…" : "Step in"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
