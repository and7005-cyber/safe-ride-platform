import { useEffect, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { Skeleton } from "@/components/ui/skeleton";
import {
  isStaff,
  membershipAt,
  staffMemberships,
  useAuth,
  type AuthUser,
  type Role,
} from "@/lib/auth";
import { clearActiveSchoolId, setActiveSchoolId, useActiveSchoolId } from "@/lib/school";
import { ChangePasswordPage } from "@/features/auth/ChangePasswordPage";
import { ChooseSchoolPage } from "@/features/auth/ChooseSchoolPage";
import { OffersPage } from "@/features/auth/OffersPage";

/** The account's own home, staff surface first (U12): a staff member who is
 * also a parent lands on the console and reaches the parent view from its
 * nav; drivers and parents keep their surfaces exactly as before. */
function homeFor(user: AuthUser): string {
  if (isStaff(user)) return "/";
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
  allowedRoles: Role[];
  children: ReactNode;
}) {
  const { user, role, loading } = useAuth();
  const activeSchoolId = useActiveSchoolId();

  // A tab can carry a school the account no longer holds (seeded value,
  // revoked membership): drop it so the chooser/auto-select take over. The
  // AuthProvider does the same after every /me — this covers the render path.
  const staleSchool = Boolean(
    user &&
      user.hydrated &&
      activeSchoolId &&
      !user.provider &&
      !membershipAt(user, activeSchoolId),
  );
  // A lone membership self-selects (the AuthProvider reconciles after every
  // /me; this covers the render path after a stale-school clear too).
  const soleSchoolId =
    user && user.hydrated && !activeSchoolId && staffMemberships(user).length === 1
      ? staffMemberships(user)[0].schoolId
      : null;
  useEffect(() => {
    if (staleSchool) clearActiveSchoolId();
    else if (soleSchoolId) setActiveSchoolId(soleSchoolId);
  }, [staleSchool, soleSchoolId]);

  if (loading) return <LoadingScreen />;

  // Signed out -> the auth screen.
  if (!user) return <Navigate to="/auth" replace />;

  // Interstitial 1 (R30/AE16): a temporary password opens nothing but the
  // change screen — known from the login response itself, before any /me.
  if (user.mustChangePassword) return <ChangePasswordPage />;

  // Membership knowledge comes from /me; don't judge surfaces before it lands.
  if (!user.hydrated) return <LoadingScreen />;

  // Interstitial 2 (AE15): pending role offers demand an explicit answer
  // before anything else, on every surface.
  if (user.pendingOffers.length > 0) return <OffersPage />;

  const allowed =
    (allowedRoles.includes("admin") && isStaff(user)) ||
    (allowedRoles.includes("driver") && role === "driver") ||
    (allowedRoles.includes("parent") && role === "parent");
  if (!allowed) {
    return <Navigate to={homeFor(user)} replace />;
  }

  if (allowedRoles.includes("admin")) {
    if (staleSchool) return <LoadingScreen />;
    if (!activeSchoolId) {
      // Interstitial 3 (R4): several schools and no school in this tab ->
      // choose one. Rendered in place, so the deep-link destination is kept.
      // A single membership is auto-selected by the AuthProvider right after
      // /me; the skeleton only covers that store-notification frame.
      if (staffMemberships(user).length > 1) return <ChooseSchoolPage />;
      return <LoadingScreen />;
    }
  }

  return <>{children}</>;
}
