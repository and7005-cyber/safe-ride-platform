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

// Cancel-a-Ride (R14–R18): each children row carries today's parent-sourced
// cancellation as {scope, withdrawable} | null (staff-sourced absences are
// not cancellations and come through as null). `withdrawable` mirrors the
// server's withdraw guard — true while some covered half still has no run
// row today; for a merged 'day' row that means at least one half is still
// withdrawable, and the UI dialog picks the half. No central Child type
// exists; this is the one structural piece the cancel/withdraw UI needs.
export type CancelScope = "morning" | "afternoon" | "day";
export interface ChildCancellation {
  scope: CancelScope;
  withdrawable: boolean;
}

// Server-side feed windows (R5–R7): Recent is the rolling last 24 hours;
// History is 24 hours to 7 days — minAgeHours excludes the young rows on the
// server (client-side trimming of the 168h query would let a busy last 24h
// eat the 200-row cap), so the two tabs are disjoint by construction. No rows
// are deleted: both are display windows over the retained trail.
export interface FeedWindow {
  windowHours?: number;
  limit?: number;
  minAgeHours?: number;
}

export const RECENT_WINDOW: FeedWindow = { windowHours: 24, limit: 50 };
export const HISTORY_WINDOW: FeedWindow = { windowHours: 168, limit: 200, minAgeHours: 24 };

export function useParentAlerts({ windowHours, limit, minAgeHours }: FeedWindow = RECENT_WINDOW) {
  return useQuery({
    queryKey: ["parent-alerts", windowHours, limit, minAgeHours],
    queryFn: () =>
      api.get("/api/parent-portal/alerts", {
        window_hours: windowHours,
        limit,
        min_age_hours: minAgeHours,
      }),
    refetchInterval: POLL_LIVE,
  });
}

export function useParentNotifications({
  windowHours,
  limit,
  minAgeHours,
}: FeedWindow = RECENT_WINDOW) {
  return useQuery({
    queryKey: ["parent-notifications", windowHours, limit, minAgeHours],
    queryFn: () =>
      api.get("/api/push/notifications", {
        window_hours: windowHours,
        limit,
        min_age_hours: minAgeHours,
      }),
    refetchInterval: POLL_LIVE,
  });
}

export function useMarkNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/api/push/notifications/mark-read"),
    onSuccess: () => {
      // Prefix match (TanStack default): this one invalidation covers every
      // window-variant key (["parent-notifications", windowHours, limit,
      // minAgeHours]), so Recent and History both refresh their read state.
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
