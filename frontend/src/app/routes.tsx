import { createBrowserRouter, Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { AdminLayout } from "@/app/layouts/AdminLayout";
import { ProtectedRoute } from "@/features/auth/ProtectedRoute";
import { AuthPage } from "@/features/auth/AuthPage";
import { ResetPasswordPage } from "@/features/auth/ResetPasswordPage";
import { DashboardPage } from "@/features/admin/DashboardPage";
import { FleetMapPage } from "@/features/admin/FleetMapPage";
import { BusesPage } from "@/features/admin/BusesPage";
import { RoutesPage } from "@/features/admin/RoutesPage";
import { SchoolsPage } from "@/features/admin/SchoolsPage";
import { StudentsPage } from "@/features/admin/StudentsPage";
import { RunsPage } from "@/features/admin/RunsPage";
import { ParentsPage } from "@/features/admin/ParentsPage";
import { DriversPage } from "@/features/admin/DriversPage";
import { ParentAssignmentsPage } from "@/features/admin/ParentAssignmentsPage";
import { AlertsPage } from "@/features/admin/AlertsPage";
import { DriverHomePage } from "@/features/driver/DriverHomePage";
import { DriverRunPage } from "@/features/driver/DriverRunPage";
import { DriverBoardingPage } from "@/features/driver/DriverBoardingPage";
import { DriverIncidentPage } from "@/features/driver/DriverIncidentPage";
import { ParentHomePage } from "@/features/parent/ParentHomePage";
import { ParentTrackPage } from "@/features/parent/ParentTrackPage";
import { ParentAlertsPage } from "@/features/parent/ParentAlertsPage";
import { ParentProfilePage } from "@/features/parent/ParentProfilePage";
import { NotFoundPage } from "@/features/shared/NotFoundPage";

function admin(node: ReactNode) {
  return (
    <ProtectedRoute allowedRoles={["admin"]}>
      <AdminLayout>{node}</AdminLayout>
    </ProtectedRoute>
  );
}

function driver(node: ReactNode) {
  return <ProtectedRoute allowedRoles={["driver"]}>{node}</ProtectedRoute>;
}

function parent(node: ReactNode) {
  return <ProtectedRoute allowedRoles={["parent"]}>{node}</ProtectedRoute>;
}

export const router = createBrowserRouter([
  { path: "/auth", element: <AuthPage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },

  { path: "/", element: admin(<DashboardPage />) },
  { path: "/fleet-map", element: admin(<FleetMapPage />) },
  { path: "/buses", element: admin(<BusesPage />) },
  { path: "/routes", element: admin(<RoutesPage />) },
  { path: "/students", element: admin(<StudentsPage />) },
  { path: "/runs", element: admin(<RunsPage />) },
  { path: "/schools", element: admin(<SchoolsPage />) },
  { path: "/parent-assignments", element: admin(<ParentAssignmentsPage />) },
  { path: "/parents", element: admin(<ParentsPage />) },
  { path: "/drivers", element: admin(<DriversPage />) },
  { path: "/alerts", element: admin(<AlertsPage />) },

  { path: "/driver", element: driver(<DriverHomePage />) },
  { path: "/driver/run", element: driver(<DriverRunPage />) },
  { path: "/driver/boarding", element: driver(<DriverBoardingPage />) },
  { path: "/driver/incident", element: driver(<DriverIncidentPage />) },

  { path: "/parent", element: parent(<ParentHomePage />) },
  { path: "/parent/track", element: parent(<ParentTrackPage />) },
  { path: "/parent/alerts", element: parent(<ParentAlertsPage />) },
  { path: "/parent/profile", element: parent(<ParentProfilePage />) },

  { path: "*", element: <NotFoundPage /> },
]);
