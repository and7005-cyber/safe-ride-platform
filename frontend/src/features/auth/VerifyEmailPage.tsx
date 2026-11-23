import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bus, CheckCircle2 } from "lucide-react";
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
import { ApiError, api } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { useMailEnabled } from "@/lib/publicConfig";
import {
  MAIL_OFF_VERIFY,
  RESEND_COOLDOWN,
  RESEND_SENDING,
  RESEND_SENT,
  SEND_NEW_LINK,
  SIGN_IN_THEN_SEND,
  VERIFIED_TEXT,
  VERIFIED_TITLE,
  VERIFYING,
  VERIFY_LINK_INVALID_TEXT,
  VERIFY_LINK_INVALID_TITLE,
  VERIFY_LINK_MISSING_TEXT,
  VERIFY_LINK_MISSING_TITLE,
  VERIFY_SIGN_IN_PROMPT,
  WRONG_ACCOUNT_TEXT,
  WRONG_ACCOUNT_TITLE,
} from "@/features/auth/authCopy";

/**
 * /verify-email?token=… (hardening U8, DL10). Public route; the verification
 * itself is session-bound: the server verifies only when the signed-in
 * caller owns the token. The mail often opens in a browser where the parent
 * is not signed in (Safari, an in-app browser), so the page carries its own
 * sign-in form and re-posts the token after it — the token never leaves this
 * page for a redirect.
 *
 * States: missing (no token in the link), sign-in (signed out, or the
 * session died), verifying, verified, wrong-account (signed in as someone
 * else: sign out), invalid (expired or used: send a new link), error (network
 * or 5xx, with the Reference the client appends).
 */
type PageState =
  | "missing"
  | "waiting"
  | "sign-in"
  | "verifying"
  | "verified"
  | "wrong-account"
  | "invalid"
  | "error";

type ResendState = "idle" | "sending" | "sent" | "cooldown" | "error";

export function VerifyEmailPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token");
  const { user, loading, signIn, signOut, refresh } = useAuth();
  const mailEnabled = useMailEnabled();

  const [state, setState] = useState<PageState>(token ? "waiting" : "missing");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signInError, setSignInError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [resend, setResend] = useState<ResendState>("idle");
  // One post per (token, account): a re-render, or React's development
  // double effect, must not race the first post into "link already used".
  const postedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!token || loading) return;
    if (!user) {
      postedFor.current = null;
      setState((current) => (current === "verified" ? current : "sign-in"));
      return;
    }
    const key = `${token}|${user.id}`;
    if (postedFor.current === key) return;
    postedFor.current = key;
    setState("verifying");
    void (async () => {
      try {
        await api.post("/api/auth/verify-email", { token });
        setState("verified");
        // /me now says verified; Home's card and children follow.
        void refresh();
      } catch (err) {
        const code = err instanceof ApiError ? err.code : null;
        if (code === "wrong-account") setState("wrong-account");
        else if (code === "link-invalid") setState("invalid");
        else if (code === "sign-in-required") {
          // The stored session is dead server-side: sign in again here, and
          // let the next session re-post even for the same account.
          postedFor.current = null;
          setState("sign-in");
        }
        else {
          setErrorText((err as Error).message);
          setState("error");
        }
      }
    })();
  }, [token, user, loading, refresh]);

  const submitSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setSigningIn(true);
    setSignInError(null);
    try {
      const res = await api.post("/api/auth/login", { email, password });
      if (res?.preauth) {
        // Provider accounts sign in on the main page (second factor); they
        // never verify a parent address.
        setSignInError("Sign in from the SafeRide sign-in page.");
        return;
      }
      setPassword("");
      // The effect above re-posts the token once the session is in place.
      signIn(res.token, res.user);
    } catch (err) {
      setSignInError((err as Error).message);
    } finally {
      setSigningIn(false);
    }
  };

  const sendNewLink = async () => {
    setResend("sending");
    try {
      await api.post("/api/auth/resend-verification");
      setResend("sent");
    } catch (err) {
      setResend(err instanceof ApiError && err.status === 429 ? "cooldown" : "error");
    }
  };

  const title =
    state === "missing"
      ? VERIFY_LINK_MISSING_TITLE
      : state === "verified"
        ? VERIFIED_TITLE
        : state === "wrong-account"
          ? WRONG_ACCOUNT_TITLE
          : state === "invalid"
            ? VERIFY_LINK_INVALID_TITLE
            : state === "error"
              ? "Something went wrong"
              : state === "sign-in"
                ? VERIFY_SIGN_IN_PROMPT
                : VERIFYING;

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center h-14 w-14 rounded-2xl bg-primary text-primary-foreground">
            <Bus className="h-7 w-7" aria-hidden="true" />
          </div>
          <h1 className="text-2xl font-bold font-heading text-foreground">SafeRide</h1>
        </div>
        <Card>
          <CardHeader className="text-center pb-2">
            <CardTitle className="font-heading" data-testid="verify-title">
              {title}
            </CardTitle>
            {state === "sign-in" && (
              <CardDescription>Use the email and password of the account you signed up with.</CardDescription>
            )}
          </CardHeader>
          {/* One live region for every state change (DL17): the result of the
              post is announced, not only drawn. */}
          <CardContent aria-live="polite" className="space-y-4">
            {state === "missing" && (
              <p className="text-sm text-muted-foreground">{VERIFY_LINK_MISSING_TEXT}</p>
            )}

            {(state === "waiting" || state === "verifying") && (
              <p className="text-sm text-muted-foreground" role="status">
                {VERIFYING}
              </p>
            )}

            {state === "sign-in" && (
              <form onSubmit={submitSignIn} className="space-y-4">
                {signInError && (
                  <p className="text-sm font-medium text-destructive" role="alert">
                    {signInError}
                  </p>
                )}
                <div className="space-y-2">
                  <Label htmlFor="verify-email">Email</Label>
                  <Input
                    id="verify-email"
                    type="email"
                    autoComplete="email"
                    inputMode="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="verify-password">Password</Label>
                  <Input
                    id="verify-password"
                    type="password"
                    autoComplete="current-password"
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
                <Button type="submit" className="w-full" disabled={signingIn}>
                  {signingIn ? "Please wait..." : "Sign in and verify"}
                </Button>
              </form>
            )}

            {state === "verified" && (
              <>
                <p className="flex items-start gap-2 text-sm text-muted-foreground" role="status">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                  {VERIFIED_TEXT}
                </p>
                <Button className="w-full" onClick={() => navigate("/")}>
                  Go to Home
                </Button>
              </>
            )}

            {state === "wrong-account" && (
              <>
                <p className="text-sm text-muted-foreground">{WRONG_ACCOUNT_TEXT}</p>
                <Button
                  className="w-full"
                  variant="outline"
                  onClick={() => {
                    void signOut();
                  }}
                >
                  Sign out
                </Button>
              </>
            )}

            {state === "invalid" && (
              <>
                <p className="text-sm text-muted-foreground">{VERIFY_LINK_INVALID_TEXT}</p>
                {mailEnabled === false ? (
                  <p className="text-sm text-muted-foreground">{MAIL_OFF_VERIFY}</p>
                ) : user ? (
                  <>
                    <Button
                      className="w-full"
                      disabled={resend === "sending" || resend === "sent"}
                      onClick={() => void sendNewLink()}
                      data-testid="verify-send-new-link"
                    >
                      {resend === "sending" ? RESEND_SENDING : SEND_NEW_LINK}
                    </Button>
                    {resend === "sent" && (
                      <p className="text-sm text-muted-foreground" role="status">{RESEND_SENT}</p>
                    )}
                    {resend === "cooldown" && (
                      <p className="text-sm text-muted-foreground" role="status">{RESEND_COOLDOWN}</p>
                    )}
                    {resend === "error" && (
                      <p className="text-sm text-destructive" role="alert">
                        The link could not be sent. Try again shortly.
                      </p>
                    )}
                  </>
                ) : (
                  <Button className="w-full" onClick={() => setState("sign-in")}>
                    {SIGN_IN_THEN_SEND}
                  </Button>
                )}
              </>
            )}

            {state === "error" && (
              <p className="text-sm text-destructive" role="alert">
                {errorText}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
