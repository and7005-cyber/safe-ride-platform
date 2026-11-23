import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bus, KeyRound, MailCheck } from "lucide-react";
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
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { useAuth, type Role } from "@/lib/auth";
import { useMailEnabled } from "@/lib/publicConfig";
import { TotpChallengePage } from "@/features/auth/TotpChallengePage";
import { DriverPinSignIn } from "@/features/auth/DriverPinSignIn";
import { readDriverSchool } from "@/lib/driverSchool";
import {
  FORGOT_SENT_TITLE,
  MAIL_OFF_FORGOT,
  MAIL_OFF_FORGOT_TITLE,
  MAIL_OFF_SIGNUP,
  SIGNUP_DONE_MAIL_OFF_TITLE,
  SIGNUP_DONE_NOTE,
  SIGNUP_DONE_TITLE,
  forgotSentText,
  signupDoneText,
} from "@/features/auth/authCopy";

function homeFor(role: Role | null): string {
  // Staff access now comes from memberships, which the login response does
  // not carry — so email logins land on "/" and ProtectedRoute bounces
  // non-staff to their own surface once /me answers (staff-first for people
  // who are both staff and parent). Driver PIN logins go straight to /driver.
  if (role === "driver") return "/driver";
  return "/";
}

/** The persistent in-card end state of sign-up (DL2) and forgot password
 * (DL11): the words stay on screen while the parent goes to their inbox.
 * `role="status"` announces it once it replaces the form. */
export function AuthNotice({
  title,
  text,
  note,
  actionLabel,
  onAction,
  testId,
}: {
  title: string;
  text: string;
  note?: string;
  actionLabel: string;
  onAction: () => void;
  testId: string;
}) {
  return (
    <div className="space-y-4" role="status" data-testid={testId}>
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <MailCheck className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="space-y-1">
          <p className="font-heading text-base font-semibold">{title}</p>
          <p className="text-sm text-muted-foreground">{text}</p>
          {note && <p className="text-xs text-muted-foreground">{note}</p>}
        </div>
      </div>
      <Button type="button" className="w-full" onClick={onAction}>
        {actionLabel}
      </Button>
    </div>
  );
}

export function AuthPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { signIn } = useAuth();
  const { toast } = useToast();
  // DL1: whether a mail really goes out. null while the config loads.
  const mailEnabled = useMailEnabled();
  // DL2 / DL11: the address the sign-up or the reset request was made for,
  // once it was answered — the card then shows its end state, not the form.
  const [signupDoneFor, setSignupDoneFor] = useState<string | null>(null);
  const [forgotSentFor, setForgotSentFor] = useState<string | null>(null);

  const [isLogin, setIsLogin] = useState(true);
  const [showForgot, setShowForgot] = useState(params.get("forgot") === "1");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [loading, setLoading] = useState(false);
  // Hardening U10 (DL3): a sign-in link (`?school=<driver code>`) or a phone
  // already set up for a school opens on the Driver PIN tab.
  const linkCode = params.get("school");
  const [tab, setTab] = useState<string>(() =>
    linkCode || readDriverSchool() ? "pin" : "email",
  );
  const consumeLink = useCallback(() => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("school");
        return next;
      },
      { replace: true },
    );
  }, [setParams]);
  // Provider two-step login (U13/AE19): the pre-auth token from the password
  // step, held in COMPONENT STATE ONLY — a reload forgets it and returns to
  // the password step. While set, the code screen replaces the card below.
  const [preauth, setPreauth] = useState<string | null>(null);
  // The distinct "too many wrong codes" notice shown back on the password
  // step after the pre-auth token was voided (AE19).
  const [loginNotice, setLoginNotice] = useState<string | null>(null);

  const finishAuth = (
    token: string,
    user: {
      id: string;
      email: string;
      fullName?: string | null;
      role?: Role | null;
      mustChangePassword?: boolean;
      emailVerified?: boolean;
      signedInWithPin?: boolean;
    },
  ) => {
    const resolved: Role | null = user.role ?? null;
    signIn(token, {
      id: user.id,
      email: user.email,
      fullName: user.fullName ?? null,
      role: resolved,
      // Respected immediately (R30): the change screen renders off the login
      // response, before /me — nothing else would answer anyway.
      mustChangePassword: Boolean(user.mustChangePassword),
      emailVerified: user.emailVerified,
      // Hardening U9: a PIN session is offered no Change password.
      signedInWithPin: Boolean(user.signedInWithPin),
    });
    navigate(homeFor(resolved));
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post("/api/auth/login", { email, password });
      if (res?.preauth) {
        // A provider identity NEVER gets a session from the password alone
        // (U13/AE19): the answer is a five-minute pre-auth token plus which
        // second step comes next.
        setLoginNotice(null);
        if (res.totpEnrolmentRequired) {
          // First sign-in: no code exists yet — exchange straight away; the
          // session lands restricted to the enrolment allowlist and
          // ProtectedRoute shows the enrolment screen.
          const session = await api.post("/api/auth/totp", { token: res.preauth });
          finishAuth(session.token, session.user);
        } else {
          setPreauth(res.preauth);
        }
        return;
      }
      finishAuth(res.token, res.user);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      // Parent is the ONLY self-service role (U13): staff join through their
      // school's offers and drivers are set up by the school office.
      // Release 1b: no session either way (a new address and an existing one
      // get the same answer); the card ends on "check your email" and the
      // parent signs in with the password just chosen (DL2).
      await api.post("/api/auth/signup", { email, password, fullName, role: "parent" });
      setSignupDoneFor(email.trim());
      setPassword("");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post("/api/auth/forgot-password", { email });
      // DL11: a persistent in-card state, never a vanishing toast.
      setForgotSentFor(email.trim());
    } catch (err) {
      toast({
        title: "Error",
        description: (err as Error).message ?? "Unable to send reset link",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center h-14 w-14 rounded-2xl bg-primary text-primary-foreground">
            <Bus className="h-7 w-7" />
          </div>
          <h1 className="text-2xl font-bold font-heading text-foreground">SafeRide</h1>
          <p className="text-muted-foreground text-sm">School bus management platform</p>
        </div>

        {preauth ? (
          <TotpChallengePage
            preauth={preauth}
            email={email}
            onSuccess={(token, user) => {
              setPreauth(null);
              finishAuth(token, user);
            }}
            onVoided={(message) => {
              // AE19: the voided token's distinct message shows on the
              // password step — the next attempt starts over there.
              setPreauth(null);
              setLoginNotice(message);
            }}
            onBack={() => setPreauth(null)}
          />
        ) : (
        <Card>
          <CardHeader className="text-center pb-2">
            <CardTitle className="font-heading">
              {showForgot ? "Reset Password" : isLogin ? "Welcome back" : "Create account"}
            </CardTitle>
            <CardDescription>
              {showForgot
                ? mailEnabled === false
                  ? "Your school office can help"
                  : "Enter your email to receive a reset link"
                : isLogin
                  ? "Sign in to your account"
                  : "Parent sign-up for SafeRide"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {showForgot && forgotSentFor ? (
              <AuthNotice
                testId="forgot-sent"
                title={mailEnabled ? FORGOT_SENT_TITLE : MAIL_OFF_FORGOT_TITLE}
                text={mailEnabled ? forgotSentText(forgotSentFor) : MAIL_OFF_FORGOT}
                actionLabel="Back to sign in"
                onAction={() => {
                  setForgotSentFor(null);
                  setShowForgot(false);
                }}
              />
            ) : showForgot && mailEnabled === false ? (
              <AuthNotice
                testId="forgot-mail-off"
                title={MAIL_OFF_FORGOT_TITLE}
                text={MAIL_OFF_FORGOT}
                actionLabel="Back to sign in"
                onAction={() => setShowForgot(false)}
              />
            ) : !showForgot && !isLogin && signupDoneFor ? (
              <AuthNotice
                testId="signup-done"
                title={mailEnabled ? SIGNUP_DONE_TITLE : SIGNUP_DONE_MAIL_OFF_TITLE}
                text={mailEnabled ? signupDoneText(signupDoneFor) : MAIL_OFF_SIGNUP}
                note={mailEnabled ? SIGNUP_DONE_NOTE : undefined}
                actionLabel="Sign in"
                onAction={() => {
                  // The email stays prefilled; the password is typed again.
                  setEmail(signupDoneFor);
                  setSignupDoneFor(null);
                  setIsLogin(true);
                }}
              />
            ) : showForgot ? (
              <form onSubmit={handleForgot} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Sending…" : "Send Reset Link"}
                </Button>
                <div className="text-center">
                  <button type="button" className="text-primary text-sm font-medium hover:underline" onClick={() => setShowForgot(false)}>
                    Back to sign in
                  </button>
                </div>
              </form>
            ) : isLogin ? (
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList className="grid w-full grid-cols-2 mb-4">
                  <TabsTrigger value="email">Email &amp; Password</TabsTrigger>
                  <TabsTrigger value="pin" className="gap-1.5">
                    <KeyRound className="h-3.5 w-3.5" /> Driver PIN
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="email">
                  <form onSubmit={handleLogin} className="space-y-4">
                    {loginNotice && (
                      <p
                        className="text-sm font-medium text-destructive"
                        data-testid="login-notice"
                      >
                        {loginNotice}
                      </p>
                    )}
                    <div className="space-y-2">
                      <Label htmlFor="email">Email</Label>
                      <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="password">Password</Label>
                        <button type="button" className="text-xs text-primary hover:underline" onClick={() => setShowForgot(true)}>
                          Forgot password?
                        </button>
                      </div>
                      <Input id="password" type="password" placeholder="••••••••" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} />
                    </div>
                    <Button type="submit" className="w-full" disabled={loading}>
                      {loading ? "Please wait..." : "Sign In"}
                    </Button>
                  </form>
                </TabsContent>
                <TabsContent value="pin">
                  <DriverPinSignIn
                    linkCode={linkCode}
                    onLinkConsumed={consumeLink}
                    onSignedIn={(token, user) => finishAuth(token, user)}
                  />
                </TabsContent>
              </Tabs>
            ) : (
              <form onSubmit={handleSignup} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="fullName">Full name</Label>
                  <Input id="fullName" required value={fullName} onChange={(e) => setFullName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <Input id="password" type="password" placeholder="••••••••" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                {/* Parent is the only self-service role (U13): school staff
                    are invited by their school and drivers are set up by the
                    school office — no role choice to make here. */}
                <p className="text-xs text-muted-foreground">
                  This creates a parent account. School staff and drivers get
                  their access from their school — ask your school office.
                </p>
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Please wait..." : "Create Account"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
        )}

        {!showForgot && !preauth && !signupDoneFor && (
          <p className="text-center text-sm text-muted-foreground">
            {isLogin ? "New parent here? " : "Already have an account? "}
            <button type="button" className="text-primary font-medium hover:underline" onClick={() => setIsLogin(!isLogin)}>
              {isLogin ? "Sign up" : "Sign in"}
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
