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
import { api, getToken, onSchoolScoped404, onUnauthorized, setToken } from "@/lib/apiClient";
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
        }
      : null,
    mustChangePassword: Boolean(me.mustChangePassword),
    hydrated: true,
  };
}

/** Reconcile the per-tab school store with the fresh /me answer (U12): the
 * store is only ever populated for staff sessions, a stale school (revoked
 * membership) is dropped, and a single membership self-selects. */
function reconcileSchoolStore(user: AuthUser): void {
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
          const stillMember = staff.some((m) => m.schoolId === schoolId);
          if (!stillMember) {
            if (getActiveSchoolId() === schoolId) clearActiveSchoolId();
            await queryClient.cancelQueries({ queryKey: ["school", schoolId] });
            queryClient.removeQueries({ queryKey: ["school", schoolId] });
            if (staff.length === 0 && mapped.role !== "parent" && mapped.role !== "driver") {
              // Nothing left to show this account: end the session cleanly.
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
    void refresh();
    return () => onSchoolScoped404(null);
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
 * A provider on this surface is stepped in, which the server resolves as
 * director — the capability checks mirror that (U12). */
export function useActiveSchoolRole(): MembershipRole | null {
  const { user } = useAuth();
  const schoolId = useActiveSchoolId();
  if (user?.provider) return "director";
  return membershipAt(user, schoolId)?.role ?? null;
}

/** Capability gate for delete buttons and the Staff surface (U12): renders
 * for directors only; the server's 403 stays the backstop. */
export function useIsDirector(): boolean {
  return useActiveSchoolRole() === "director";
}
