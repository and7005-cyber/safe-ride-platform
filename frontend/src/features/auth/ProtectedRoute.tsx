import { useEffect, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { Skeleton } from "@/components/ui/skeleton";
import {
  canWorkAtSchool,
  isStaff,
  isSteppedIn,
  staffMemberships,
  useAuth,
  type AuthUser,
  type Role,
} from "@/lib/auth";
import { clearActiveSchoolId, setActiveSchoolId, useActiveSchoolId } from "@/lib/school";
import { ChangePasswordPage } from "@/features/auth/ChangePasswordPage";
import { ChooseSchoolPage } from "@/features/auth/ChooseSchoolPage";
import { OffersPage } from "@/features/auth/OffersPage";
import { TotpEnrolPage } from "@/features/provider/TotpEnrolPage";

/** The surfaces a route can admit (U13 adds the provider console). */
export type Surface = Role | "provider";

/** The account's own home, staff surface first (U12): a staff member who is
 * also a parent lands on the console and reaches the parent view from its
 * nav; drivers and parents keep their surfaces exactly as before. Providers
 * (U13) live on the provider console — the ADMIN surface opens for them only
 * through a step-in, never as a home. */
function homeFor(user: AuthUser): string {
  if (isStaff(user)) return "/";
  if (user.provider) return "/provider";
  if (user.role === "driver") return "/driver";
  if (user.role === "parent") return "/parent";
  return "/auth";
}

function LoadingScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="space-y-4 w-64">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  );
}

export function ProtectedRoute({
  allowedRoles,
  children,
}: {
  allowedRoles: Surface[];
  children: ReactNode;
}) {
  const { user, role, loading } = useAuth();
  const activeSchoolId = useActiveSchoolId();

  // A tab can carry a school the account no longer holds (seeded value,
  // revoked membership, ended step-in): drop it so the chooser/auto-select
  // take over. The AuthProvider does the same after every /me — this covers
  // the render path. U13: a provider's school is valid ONLY while their live
  // step-in names it (canWorkAtSchool).
  const staleSchool = Boolean(
    user && user.hydrated && activeSchoolId && !canWorkAtSchool(user, activeSchoolId),
  );
  // A lone membership self-selects, and a stepped-in provider's tab follows
  // the support session (the AuthProvider reconciles after every /me; this
  // covers the render path after a stale-school clear too).
  const soleSchoolId =
    user && user.hydrated && !activeSchoolId && !user.provider && staffMemberships(user).length === 1
      ? staffMemberships(user)[0].schoolId
      : null;
  const supportSchoolId =
    user && user.hydrated && !activeSchoolId && user.provider && user.supportSession
      ? user.supportSession.schoolId
      : null;
  useEffect(() => {
    if (staleSchool) clearActiveSchoolId();
    else if (supportSchoolId) setActiveSchoolId(supportSchoolId);
    else if (soleSchoolId) setActiveSchoolId(soleSchoolId);
  }, [staleSchool, soleSchoolId, supportSchoolId]);

  if (loading) return <LoadingScreen />;

  // Signed out -> the auth screen.
  if (!user) return <Navigate to="/auth" replace />;

  // Interstitial 1 (R30/AE16): a temporary password opens nothing but the
  // change screen — known from the login response itself, before any /me.
  if (user.mustChangePassword) return <ChangePasswordPage />;

  // Membership knowledge comes from /me; don't judge surfaces before it lands.
  if (!user.hydrated) return <LoadingScreen />;

  // Interstitial 2 (U13/R20): an unenrolled provider's session opens nothing
  // but second-factor enrolment — the client mirror of the server's 409
  // allowlist. Before the offers screen on purpose: answering an offer would
  // 409 for an unenrolled provider anyway (and providers hold none).
  if (user.provider && !user.provider.totpEnrolled) return <TotpEnrolPage />;

  // Interstitial 3 (AE15): pending role offers demand an explicit answer
  // before anything else, on every surface.
  if (user.pendingOffers.length > 0) return <OffersPage />;

  const allowed =
    (allowedRoles.includes("admin") && (isStaff(user) || isSteppedIn(user))) ||
    (allowedRoles.includes("provider") && Boolean(user.provider)) ||
    (allowedRoles.includes("driver") && role === "driver") ||
    (allowedRoles.includes("parent") && role === "parent");
  if (!allowed) {
    return <Navigate to={homeFor(user)} replace />;
  }

  if (allowedRoles.includes("admin") && !user.provider) {
    if (staleSchool) return <LoadingScreen />;
    if (!activeSchoolId) {
      // Interstitial 4 (R4): several schools and no school in this tab ->
      // choose one. Rendered in place, so the deep-link destination is kept.
      // A single membership is auto-selected by the AuthProvider right after
      // /me; the skeleton only covers that store-notification frame.
      if (staffMemberships(user).length > 1) return <ChooseSchoolPage />;
      return <LoadingScreen />;
    }
  }

  if (allowedRoles.includes("admin") && user.provider) {
    // A stepped-in provider's tab follows the support session: the effect
    // above sets/clears the store, the skeleton covers that frame. (Without
    // a step-in `allowed` was already false — they never reach here.)
    if (staleSchool || !activeSchoolId) return <LoadingScreen />;
  }

  return <>{children}</>;
}
