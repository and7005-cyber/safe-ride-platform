import { MailCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  MAIL_OFF_VERIFY,
  RESEND_COOLDOWN,
  RESEND_LABEL,
  RESEND_SENDING,
  RESEND_SENT,
  VERIFY_CARD_TITLE,
  VERIFY_CARD_WRONG_ADDRESS,
  verifyCardText,
} from "@/features/auth/authCopy";

export type ResendState = "idle" | "sending" | "sent" | "cooldown" | "error";

/**
 * Parent Home while the address is unverified (hardening U8, DL9): it takes
 * the empty state's place — "No children are linked" would give the wrong
 * cause. Resend shows sending, sent and the one-minute cooldown; with mail
 * off (DL1) there is no Resend and the office confirms the sign-up instead.
 * The typo dead end gets its way out: sign out and sign up again.
 *
 * Presentational: ParentHomePage owns the mutation and the polling, and
 * wraps this card in the aria-live region that announces the flip (DL17).
 */
export function VerifyEmailCard({
  email,
  mailEnabled,
  resendState,
  onResend,
  onSignOut,
}: {
  email: string;
  mailEnabled: boolean | null;
  resendState: ResendState;
  onResend: () => void;
  onSignOut: () => void;
}) {
  return (
    <Card className="border-primary/40" data-testid="verify-email-card">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <MailCheck className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="flex-1 space-y-1">
            <p className="font-heading text-base font-semibold">{VERIFY_CARD_TITLE}</p>
            <p className="text-sm text-muted-foreground">
              {mailEnabled === false ? MAIL_OFF_VERIFY : verifyCardText(email)}
            </p>
          </div>
        </div>
        {mailEnabled !== false && (
          <div className="space-y-2">
            <Button
              size="sm"
              variant="outline"
              className="min-h-11 w-full"
              disabled={resendState === "sending"}
              onClick={onResend}
              data-testid="verify-resend"
            >
              {resendState === "sending" ? RESEND_SENDING : RESEND_LABEL}
            </Button>
            {resendState === "sent" && (
              <p className="text-xs text-muted-foreground" data-testid="verify-resend-status">
                {RESEND_SENT}
              </p>
            )}
            {resendState === "cooldown" && (
              <p className="text-xs text-muted-foreground" data-testid="verify-resend-status">
                {RESEND_COOLDOWN}
              </p>
            )}
            {resendState === "error" && (
              <p className="text-xs text-destructive" data-testid="verify-resend-status">
                The link could not be sent. Try again shortly.
              </p>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{VERIFY_CARD_WRONG_ADDRESS}</p>
          <Button
            size="sm"
            variant="ghost"
            className="min-h-11"
            onClick={onSignOut}
            data-testid="verify-sign-out"
          >
            Sign out
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
