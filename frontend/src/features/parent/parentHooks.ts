import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

// Server-side feed windows (R35): the main feed shows a rolling 24 hours,
// the History tab the last 7 days (list cap raised to the server's 200 max).
export interface FeedWindow {
  windowHours?: number;
  limit?: number;
}

export const RECENT_WINDOW: Required<FeedWindow> = { windowHours: 24, limit: 50 };
export const HISTORY_WINDOW: Required<FeedWindow> = { windowHours: 168, limit: 200 };

export function useParentAlerts({ windowHours, limit }: FeedWindow = RECENT_WINDOW) {
  return useQuery({
    queryKey: ["parent-alerts", windowHours, limit],
    queryFn: () => api.get("/api/parent-portal/alerts", { window_hours: windowHours, limit }),
    refetchInterval: POLL_LIVE,
  });
}

export function useParentNotifications({ windowHours, limit }: FeedWindow = RECENT_WINDOW) {
  return useQuery({
    queryKey: ["parent-notifications", windowHours, limit],
    queryFn: () => api.get("/api/push/notifications", { window_hours: windowHours, limit }),
    refetchInterval: POLL_LIVE,
  });
}

export function useMarkNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/api/push/notifications/mark-read"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["parent-notifications"] });
    },
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
