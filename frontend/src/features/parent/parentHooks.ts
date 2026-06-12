import { useQuery } from "@tanstack/react-query";
import { Home, MapPin, Bell, User, type LucideIcon } from "lucide-react";
import { api } from "@/lib/apiClient";
import { POLL_LIVE } from "@/lib/queries";

export const PARENT_NAV: { to: string; label: string; icon: LucideIcon; end?: boolean }[] = [
  { to: "/parent", label: "Home", icon: Home, end: true },
  { to: "/parent/track", label: "Track", icon: MapPin },
  { to: "/parent/alerts", label: "Alerts", icon: Bell },
  { to: "/parent/profile", label: "Profile", icon: User },
];

export function useChildren() {
  return useQuery({
    queryKey: ["parent-children"],
    queryFn: () => api.get("/api/parent-portal/children"),
    refetchInterval: POLL_LIVE,
  });
}

export function useParentAlerts() {
  return useQuery({
    queryKey: ["parent-alerts"],
    queryFn: () => api.get("/api/parent-portal/alerts"),
    refetchInterval: POLL_LIVE,
  });
}

export function useParentProfile() {
  return useQuery({
    queryKey: ["parent-profile"],
    queryFn: () => api.get("/api/parent-portal/profile"),
  });
}

export function useTrack(studentId: string | null) {
  return useQuery({
    queryKey: ["parent-track", studentId],
    queryFn: () => api.get("/api/parent-portal/track", { student_id: studentId }),
    enabled: Boolean(studentId),
    refetchInterval: POLL_LIVE,
  });
}
