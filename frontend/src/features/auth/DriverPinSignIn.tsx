import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, School } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { ApiError, api } from "@/lib/apiClient";
import type { LoginResponseUser } from "@/lib/auth";
import {
  type DriverSchool,
  clearDriverSchool,
  formatDriverCode,
  isDriverCode,
  looksLikeSchoolCode,
  lookupDriverSchool,
  normaliseDriverCode,
  storeDriverSchool,
  useDriverSchool,
} from "@/lib/driverSchool";
import {
  CHANGE_SCHOOL_CONFIRM_TEXT,
  CHANGE_SCHOOL_CONFIRM_TITLE,
  CHANGE_SCHOOL_LABEL,
  CONFIRM_SCHOOL_NO,
  CONFIRM_SCHOOL_QUESTION,
  CONFIRM_SCHOOL_YES,
  DRIVER_CODE_CHECKING,
  DRIVER_CODE_CONTINUE,
  DRIVER_CODE_IS_SCHOOL_CODE,
  DRIVER_CODE_LABEL,
  DRIVER_CODE_NOT_FOUND,
  DRIVER_CODE_OFFLINE,
  DRIVER_CODE_PROMPT,
  DRIVER_CODE_THROTTLED,
  INVALID_PIN_TEXT,
  INVALID_PIN_TITLE,
  PIN_SIGN_IN_LABEL,
  pinLockedText,
} from "@/features/auth/authCopy";

/** Why a driver code lookup failed, in the words the code step shows (DL3,
 * DL5). A 5xx keeps the server's sentence, which carries the Reference. */
function lookupErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return DRIVER_CODE_NOT_FOUND;
    if (err.status === 429) return DRIVER_CODE_THROTTLED;
    if (err.status >= 500) return err.message;
    return DRIVER_CODE_NOT_FOUND;
  }
  return DRIVER_CODE_OFFLINE;
}

/**
 * The driver's PIN tab (hardening U10; DL3, DL4, DL5, DL17).
 *
 * Three states. Not set up: the code step asks for the driver code from the
 * school office ("Checking…" while it is looked up; distinct words for an
 * unknown code, a school code typed by mistake, too many tries and no
 * connection). Looked up: "Is this your school?" with the school's name —
 * on the link path too. Set up: the PIN form under the school's name, with
 * a small "Change school" link away from the Sign In button that asks first.
 *
 * A refused PIN branches on the server's code: a wrong PIN toasts "Invalid
 * PIN"; the per-school lock shows a line counted down from Retry-After; a
 * missing school goes back to the code step.
 */
export function DriverPinSignIn({
  linkCode,
  onLinkConsumed,
  onSignedIn,
}: {
  /** The `?school=` code of a sign-in link, as typed in the URL. */
  linkCode: string | null;
  /** Strip `?school=` from the address once the link has been answered. */
  onLinkConsumed: () => void;
  onSignedIn: (token: string, user: LoginResponseUser) => void;
}) {
  const { toast } = useToast();
  const binding = useDriverSchool();
  const [candidate, setCandidate] = useState<DriverSchool | null>(null);
  const [codeInput, setCodeInput] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [pin, setPin] = useState("");
  const [loading, setLoading] = useState(false);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [confirmingChange, setConfirmingChange] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  // The latest binding for the link handler without re-running it.
  const bindingRef = useRef(binding);
  bindingRef.current = binding;

  const resolveCode = useCallback(
    async (typed: string, viaLink: boolean) => {
      setCodeError(null);
      const refuse = (text: string) => {
        if (viaLink && bindingRef.current) {
          // An old or mistyped link on a phone that is already set up: the
          // phone keeps its school (a regenerated code never unbinds it).
          onLinkConsumed();
          setCodeInput("");
          return;
        }
        setCodeError(text);
      };
      if (looksLikeSchoolCode(typed)) {
        refuse(DRIVER_CODE_IS_SCHOOL_CODE);
        return;
      }
      const code = normaliseDriverCode(typed);
      if (!isDriverCode(code)) {
        refuse(DRIVER_CODE_NOT_FOUND);
        return;
      }
      setChecking(true);
      try {
        const school = await lookupDriverSchool(code);
        const current = viaLink ? bindingRef.current : null;
        if (current && current.schoolId === school.schoolId) {
          // The link names the school this phone is already set up for.
          onLinkConsumed();
          setCodeInput("");
          return;
        }
        setCandidate(school);
      } catch (err) {
        refuse(lookupErrorText(err));
      } finally {
        setChecking(false);
      }
    },
    [onLinkConsumed],
  );

  // A sign-in link (`/auth?school=<code>`): look it up straight away and
  // ask "Is this your school?" even when the phone is already set up.
  const handledLink = useRef<string | null>(null);
  useEffect(() => {
    if (!linkCode || handledLink.current === linkCode) return;
    handledLink.current = linkCode;
    setCodeInput(formatDriverCode(linkCode));
    void resolveCode(linkCode, true);
  }, [linkCode, resolveCode]);

  // DL4: the lock line counts down from Retry-After.
  useEffect(() => {
    if (lockedUntil === null) return;
    const tick = () => {
      const t = Date.now();
      setNow(t);
      if (t >= lockedUntil) setLockedUntil(null);
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [lockedUntil]);

  const confirmSchool = () => {
    if (!candidate) return;
    storeDriverSchool(candidate);
    setCandidate(null);
    setCodeInput("");
    setPin("");
    setLockedUntil(null);
    onLinkConsumed();
  };

  const rejectSchool = () => {
    setCandidate(null);
    setCodeInput("");
    onLinkConsumed();
    window.setTimeout(() => codeRef.current?.focus(), 0);
  };

  const changeSchool = () => {
    clearDriverSchool();
    setConfirmingChange(false);
    setPin("");
    setLockedUntil(null);
    setCodeError(null);
    window.setTimeout(() => codeRef.current?.focus(), 0);
  };

  const handleCode = (e: React.FormEvent) => {
    e.preventDefault();
    void resolveCode(codeInput, false);
  };

  const handlePin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!binding) return;
    setLoading(true);
    try {
      const res = await api.post("/api/auth/pin-login", { pin, schoolId: binding.schoolId });
      onSignedIn(res.token, res.user);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429 && err.code === "school-pin-locked") {
        const seconds = err.retryAfter ?? 15 * 60;
        setLockedUntil(Date.now() + seconds * 1000);
      } else if (err instanceof ApiError && err.code === "school-required") {
        // The phone's binding was not usable: set it up again.
        clearDriverSchool();
      } else if (err instanceof ApiError && err.status === 401) {
        toast({ title: INVALID_PIN_TITLE, description: INVALID_PIN_TEXT, variant: "destructive" });
      } else {
        // The per-address limit ("Too many PIN attempts…"), or a failure
        // with its Reference.
        toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      }
    } finally {
      setLoading(false);
    }
  };

  if (candidate) {
    return (
      <div className="space-y-4" role="group" aria-labelledby="confirm-school-question" data-testid="confirm-school">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <School className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="space-y-1">
            <p id="confirm-school-question" className="text-sm text-muted-foreground">
              {CONFIRM_SCHOOL_QUESTION}
            </p>
            <p className="font-heading text-lg font-semibold" data-testid="confirm-school-name">
              {candidate.name}
            </p>
          </div>
        </div>
        <Button type="button" className="h-11 w-full" onClick={confirmSchool}>
          {CONFIRM_SCHOOL_YES}
        </Button>
        <Button type="button" variant="outline" className="h-11 w-full" onClick={rejectSchool}>
          {CONFIRM_SCHOOL_NO}
        </Button>
      </div>
    );
  }

  if (!binding) {
    return (
      <form onSubmit={handleCode} className="space-y-4" data-testid="driver-code-step">
        <div className="space-y-2">
          <Label htmlFor="driver-code">{DRIVER_CODE_LABEL}</Label>
          <p className="text-sm text-muted-foreground" id="driver-code-hint">
            {DRIVER_CODE_PROMPT}
          </p>
          <Input
            id="driver-code"
            ref={codeRef}
            placeholder="XXXX-XXXX"
            autoCapitalize="characters"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            maxLength={16}
            aria-describedby={codeError ? "driver-code-error driver-code-hint" : "driver-code-hint"}
            aria-invalid={codeError ? true : undefined}
            value={codeInput}
            onChange={(e) => {
              setCodeInput(e.target.value);
              setCodeError(null);
            }}
            className="font-mono uppercase tracking-widest"
          />
          {codeError && (
            <p id="driver-code-error" role="alert" className="text-sm text-destructive">
              {codeError}
            </p>
          )}
        </div>
        <Button type="submit" className="h-11 w-full" disabled={checking || !codeInput.trim()}>
          {checking ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {DRIVER_CODE_CHECKING}
            </>
          ) : (
            DRIVER_CODE_CONTINUE
          )}
        </Button>
      </form>
    );
  }

  const locked = lockedUntil !== null && lockedUntil > now;
  return (
    <div className="space-y-4">
      <form onSubmit={handlePin} className="space-y-4">
        <div className="rounded-md border bg-muted/40 px-3 py-2">
          <p className="text-xs text-muted-foreground">School</p>
          <p className="font-medium" data-testid="driver-school-name">
            {binding.name || "Your school"}
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="pin">Driver PIN</Label>
          <Input
            id="pin"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="••••••"
            maxLength={6}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
          />
        </div>
        {locked && (
          <p role="alert" className="text-sm font-medium text-destructive" data-testid="pin-locked">
            {pinLockedText((lockedUntil! - now) / 60_000)}
          </p>
        )}
        <Button type="submit" className="h-11 w-full" disabled={loading || locked || pin.length < 4}>
          {loading ? "Please wait..." : PIN_SIGN_IN_LABEL}
        </Button>
      </form>
      <div className="border-t pt-3">
        {confirmingChange ? (
          <div className="space-y-3 rounded-md border p-3" role="alertdialog" aria-labelledby="change-school-title" aria-describedby="change-school-text">
            <p id="change-school-title" className="text-sm font-medium">{CHANGE_SCHOOL_CONFIRM_TITLE}</p>
            <p id="change-school-text" className="text-sm text-muted-foreground">{CHANGE_SCHOOL_CONFIRM_TEXT}</p>
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="sm" className="h-10 flex-1" onClick={() => setConfirmingChange(false)}>
                Cancel
              </Button>
              <Button type="button" variant="destructive" size="sm" className="h-10 flex-1" onClick={changeSchool}>
                {CHANGE_SCHOOL_LABEL}
              </Button>
            </div>
          </div>
        ) : (
          <div className="text-center">
            <button
              type="button"
              className="min-h-[44px] px-2 text-xs text-muted-foreground underline-offset-4 hover:underline"
              onClick={() => setConfirmingChange(true)}
            >
              {CHANGE_SCHOOL_LABEL}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
