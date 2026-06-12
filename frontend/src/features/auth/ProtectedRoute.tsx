import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth, type Role } from "@/lib/auth";

function homeFor(role: Role | null): string {
  if (role === "admin") return "/";
  if (role === "driver") return "/driver";
  return "/parent";
}

export function ProtectedRoute({
  allowedRoles,
  children,
}: {
  allowedRoles: Role[];
  children: ReactNode;
}) {
  const { user, role, loading } = useAuth();

  if (loading) {
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

  // Signed out, OR authenticated with no role row -> treat as signed out.
  if (!user || role === null) {
    return <Navigate to="/auth" replace />;
  }

  if (!allowedRoles.includes(role)) {
    return <Navigate to={homeFor(role)} replace />;
  }

  return <>{children}</>;
}
