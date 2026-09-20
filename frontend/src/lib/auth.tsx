import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
// The module-level toast fn (not the useToast hook): AuthProvider is the app
// root and must not re-render on every toast state change.
import { toast } from "@/components/ui/use-toast";
import {
  api,
  getToken,
  onSchoolScoped403,
  onSchoolScoped404,
  onTotpEnrolmentRequired,
  onUnauthorized,
  setToken,
} from "@/lib/apiClient";
import {
  clearActiveSchoolId,
  getActiveSchoolId,
  setActiveSchoolId,
  useActiveSchoolId,
} from "@/lib/school";

export type Role = "admin" | "driver" | "parent";
export type MembershipRole = "director" | "coordinator" | "driver";

/** One active school membership row from /api/auth/me (U12). */
export interface SchoolMembership {
  schoolId: string;
  schoolName: string | null;
  schoolCode: string | null;
  role: MembershipRole;
}

/** A pending role offer from /api/auth/me — enough to render its card. */
export interface PendingOffer extends SchoolMembership {
  id: string;
  offeredBy: string | null;
  offeredAt: string | null;
}

export interface ProviderState {
  isProvider: boolean;
  totpEnrolled: boolean;
  /** When the CALLING session last verified an authenticator code (ISO), or
   * null. Step-up dialogs show the code input up front once this is stale
   * (older than 15 minutes) — see stepUpCodeNeeded (U13). */
  totpVerifiedAt: string | null;
}

/** The calling session's live provider step-in from /me (U13/AE29): feeds
 * the persistent support banner and the school-store rule. */
export interface SupportSession {
  id: string;
  schoolId: string;
  schoolName: string | null;
  schoolCode: string | null;
  reason: string | null;
  startedAt: string | null;
}

export interface AuthUser {
  id: string;
  email: string;
  fullName: string | null;
  /** Legacy per-user role ('admin' for pre-tenancy staff, 'driver', 'parent').
   * Staff access comes from memberships, never from this field. */
  role: Role | null;
  memberships: SchoolMembership[];
  pendingOffers: PendingOffer[];
  /** The session's last-used school (server hint; the tab store is the truth). */
  activeSchoolId: string | null;
  provider: ProviderState | null;
  /** The session's live step-in (providers only, U13); null otherwise. */
  supportSession: SupportSession | null;
  mustChangePassword: boolean;
  /** False until the first /me answered — a fresh login knows only the login
   * response's fields, and ProtectedRoute must not judge memberships yet. */
  hydrated: boolean;
}

const STAFF_ROLES: readonly MembershipRole[] = ["director", "coordinator"];

/** The user's staff-surface memberships (director/coordinator rows). */
export function staffMemberships(user: AuthUser | null): SchoolMembership[] {
  return (user?.memberships ?? []).filter((m) =>
    STAFF_ROLES.includes(m.role),
  );
}

/** Whether the account may enter the admin console: memberships are the only
 * staff signal (the legacy 'admin' role row alone no longer opens it). */
export function isStaff(user: AuthUser | null): boolean {
  return staffMemberships(user).length > 0;
}

/** Whether this session is a provider with a LIVE step-in (U13). */
export function isSteppedIn(user: AuthUser | null): boolean {
  return Boolean(user?.provider && user.supportSession);
}

/** Whether the session may work in `schoolId`'s console: a staff membership
 * there, OR a provider's live step-in at exactly that school (U13). A
 * provider without a step-in has NO school — the console is closed. */
export function canWorkAtSchool(user: AuthUser | null, schoolId: string | null): boolean {
  if (!user || !schoolId) return false;
  if (user.provider) return user.supportSession?.schoolId === schoolId;
  return Boolean(membershipAt(user, schoolId));
}

/** The membership granting access to `schoolId`, best role first. */
export function membershipAt(
  user: AuthUser | null,
  schoolId: string | null,
): SchoolMembership | null {
  if (!schoolId) return null;
  const rows = staffMemberships(user).filter((m) => m.schoolId === schoolId);
  return rows.find((m) => m.role === "director") ?? rows[0] ?? null;
}

function mapMe(me: any): AuthUser {
  return {
    id: me.id,
    email: me.email,
    fullName: me.fullName ?? null,
    role: me.role ?? null,
    memberships: (me.memberships ?? []).map((m: any) => ({
      schoolId: m.schoolId,
      schoolName: m.schoolName ?? null,
      schoolCode: m.schoolCode ?? null,
      role: m.role,
    })),
    pendingOffers: (me.pendingOffers ?? []).map((o: any) => ({
      id: o.id,
      schoolId: o.schoolId,
      schoolName: o.schoolName ?? null,
      schoolCode: o.schoolCode ?? null,
      role: o.role,
      offeredBy: o.offeredBy ?? null,
      offeredAt: o.offeredAt ?? null,
    })),
    activeSchoolId: me.activeSchoolId ?? null,
    provider: me.provider
      ? {
          isProvider: Boolean(me.provider.isProvider ?? true),
          totpEnrolled: Boolean(me.provider.totpEnrolled),
          totpVerifiedAt: me.provider.totpVerifiedAt ?? null,
        }
      : null,
    supportSession: me.supportSession
      ? {
          id: me.supportSession.id,
          schoolId: me.supportSession.schoolId,
          schoolName: me.supportSession.schoolName ?? null,
          schoolCode: me.supportSession.schoolCode ?? null,
          reason: me.supportSession.reason ?? null,
          startedAt: me.supportSession.startedAt ?? null,
        }
      : null,
    mustChangePassword: Boolean(me.mustChangePassword),
    hydrated: true,
  };
}

/** Reconcile the per-tab school store with the fresh /me answer (U12, widened
 * by U13): the store is populated for staff memberships OR an active provider
 * step-in; a stale school (revoked membership, ended step-in) is dropped, and
 * a single membership self-selects. Exported for unit tests. */
export function reconcileSchoolStore(user: AuthUser): void {
  if (user.provider) {
    // Providers (U13): the tab's school mirrors the LIVE step-in exactly. A
    // support session names the one school this session may work in; without
    // one the store must be empty — a provider never idles inside a school.
    if (user.supportSession) setActiveSchoolId(user.supportSession.schoolId);
    else clearActiveSchoolId();
    return;
  }
  const staff = staffMemberships(user);
  const current = getActiveSchoolId();
  if (staff.length === 0) {
    clearActiveSchoolId();
    return;
  }
  if (current && !staff.some((m) => m.schoolId === current)) {
    clearActiveSchoolId();
    return;
  }
  if (!current && staff.length === 1) {
    setActiveSchoolId(staff[0].schoolId);
  }
  // Several memberships and no tab school: leave unset — ProtectedRoute
  // renders the choose-school screen (the plan's first-landing answer).
}

interface AuthContextValue {
  user: AuthUser | null;
  role: Role | null;
  loading: boolean;
  signIn: (
    token: string,
    user: {
      id: string;
      email: string;
      fullName?: string | null;
      role?: Role | null;
      mustChangePassword?: boolean;
    },
  ) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      clearActiveSchoolId();
      setLoading(false);
      return;
    }
    try {
      const me = await api.get("/api/auth/me");
      const mapped = mapMe(me);
      reconcileSchoolStore(mapped);
      setUser(mapped);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const signIn = useCallback(
    (token: string, nextUser: Parameters<AuthContextValue["signIn"]>[1]) => {
      setToken(token);
      // The login response's own fields apply immediately — above all
      // mustChangePassword (R30): the change screen must not wait for /me,
      // and nothing else would answer anyway (every other call 409s).
      setUser({
        id: nextUser.id,
        email: nextUser.email,
        fullName: nextUser.fullName ?? null,
        role: nextUser.role ?? null,
        memberships: [],
        pendingOffers: [],
        activeSchoolId: null,
        provider: null,
        supportSession: null,
        mustChangePassword: Boolean(nextUser.mustChangePassword),
        hydrated: false,
      });
      setLoading(false);
      void refresh();
    },
    [refresh],
  );

  const signOut = useCallback(async () => {
    // Release this device's push registration first (it needs the session):
    // the next user on a shared device must not receive this user's child
    // alerts or inherit a "subscribed" push state.
    try {
      const { disablePush } = await import("@/lib/push");
      await disablePush();
    } catch {
      // best effort; localStorage push mode is cleared inside disablePush
    }
    try {
      await api.post("/api/auth/logout");
    } catch {
      // ignore network errors on logout
    }
    setToken(null);
    setUser(null);
    // The active school belongs to the session that picked it (U12).
    clearActiveSchoolId();
    // Drop all cached queries so the next user can't see the previous one's data.
    queryClient.clear();
  }, [queryClient]);

  // Membership-lost detection (U12): a 404 on a school-scoped call re-checks
  // /me once at a time. If the school really left the membership set, the
  // store is cleared and the old school's cache dropped; ProtectedRoute then
  // re-routes declaratively (re-choose with memberships left, /auth with
  // none) — never a toast per failed query.
  const recheckInFlight = useRef(false);
  const signOutRef = useRef(signOut);
  signOutRef.current = signOut;

  useEffect(() => {
    onUnauthorized(() => {
      setUser(null);
      clearActiveSchoolId();
      queryClient.clear();
    });
    onSchoolScoped404((schoolId: string) => {
      if (recheckInFlight.current) return;
      recheckInFlight.current = true;
      void (async () => {
        try {
          const me = await api.get("/api/auth/me");
          const mapped = mapMe(me);
          const staff = staffMemberships(mapped);
          // Access to the school survives via a membership OR (U13) a live
          // provider step-in at that very school.
          const stillAllowed =
            staff.some((m) => m.schoolId === schoolId) ||
            Boolean(mapped.provider && mapped.supportSession?.schoolId === schoolId);
          if (!stillAllowed) {
            if (getActiveSchoolId() === schoolId) clearActiveSchoolId();
            await queryClient.cancelQueries({ queryKey: ["school", schoolId] });
            queryClient.removeQueries({ queryKey: ["school", schoolId] });
            if (
              staff.length === 0 &&
              !mapped.provider &&
              mapped.role !== "parent" &&
              mapped.role !== "driver"
            ) {
              // Nothing left to show this account: end the session cleanly.
              // (A provider always keeps the console — never signed out here.)
              await signOutRef.current();
              return;
            }
          }
          setUser(mapped);
        } catch {
          // /me failing lands in the normal 401/refresh paths.
        } finally {
          recheckInFlight.current = false;
        }
      })();
    });
    onSchoolScoped403((schoolId: string) => {
      // The stepped-in provider's mirror of the staff re-choose rule (U13):
      // school-scoped calls answer 403 the moment the support session ends
      // (Exit elsewhere, the four-hour janitor, a supersede, logout). One /me
      // re-check decides; ProtectedRoute then re-routes declaratively to the
      // provider console — never a toast per failed query.
      if (recheckInFlight.current) return;
      recheckInFlight.current = true;
      void (async () => {
        try {
          const me = await api.get("/api/auth/me");
          const mapped = mapMe(me);
          if (
            mapped.provider &&
            getActiveSchoolId() === schoolId &&
            mapped.supportSession?.schoolId !== schoolId
          ) {
            clearActiveSchoolId();
            await queryClient.cancelQueries({ queryKey: ["school", schoolId] });
            queryClient.removeQueries({ queryKey: ["school", schoolId] });
            toast({
              title: "Step-in ended",
              description:
                "Your support session at this school is no longer active. Back to the school list.",
            });
          }
          setUser(mapped);
        } catch {
          // /me failing lands in the normal 401/refresh paths.
        } finally {
          recheckInFlight.current = false;
        }
      })();
    });
    onTotpEnrolmentRequired(() => {
      // A peer reset this provider's second factor (U13): /me now says
      // unenrolled and ProtectedRoute swaps in the enrolment screen.
      if (recheckInFlight.current) return;
      recheckInFlight.current = true;
      void (async () => {
        try {
          const me = await api.get("/api/auth/me");
          setUser(mapMe(me));
        } catch {
          // /me failing lands in the normal 401/refresh paths.
        } finally {
          recheckInFlight.current = false;
        }
      })();
    });
    void refresh();
    return () => {
      onSchoolScoped404(null);
      onSchoolScoped403(null);
      onTotpEnrolmentRequired(null);
    };
  }, [refresh, queryClient]);

  const value = useMemo<AuthContextValue>(
    () => ({ user, role: user?.role ?? null, loading, signIn, signOut, refresh }),
    [user, loading, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** The caller's role at the ACTIVE school: 'director' | 'coordinator' | null.
 * A provider holds a school role ONLY through a live step-in at that very
 * school, which the server resolves as director — the capability checks
 * mirror that (U12/U13). A provider without a step-in has no role anywhere. */
export function useActiveSchoolRole(): MembershipRole | null {
  const { user } = useAuth();
  const schoolId = useActiveSchoolId();
  if (user?.provider) {
    return schoolId && user.supportSession?.schoolId === schoolId ? "director" : null;
  }
  return membershipAt(user, schoolId)?.role ?? null;
}

/** Capability gate for delete buttons and the Staff surface (U12): renders
 * for directors only; the server's 403 stays the backstop. */
export function useIsDirector(): boolean {
  return useActiveSchoolRole() === "director";
}
