import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/apiClient";

// Shared admin/data hooks. Polling cadences mirror the live app's realtime
// channels (15s for admin lists/badge/alerts; 5s for live driver/parent views).
// Fleet map intentionally has NO refetchInterval (live has no channel there).

export const POLL_ADMIN = 15_000;
export const POLL_LIVE = 5_000;

export function useBuses(opts?: { poll?: boolean }) {
  return useQuery({
    queryKey: ["buses"],
    queryFn: () => api.get("/api/fleet/buses"),
    // The fleet map opts into live polling so it shows all active buses moving;
    // other pages keep the one-shot fetch.
    refetchInterval: opts?.poll ? POLL_LIVE : undefined,
  });
}

export function useRoutes() {
  return useQuery({ queryKey: ["routes"], queryFn: () => api.get("/api/fleet/routes") });
}

export function useSchools() {
  return useQuery({ queryKey: ["schools"], queryFn: () => api.get("/api/fleet/schools") });
}

export function useStudents() {
  return useQuery({ queryKey: ["students"], queryFn: () => api.get("/api/students") });
}

export function useAbsences(date?: string) {
  return useQuery({
    queryKey: ["absences", date ?? "today"],
    queryFn: () => api.get("/api/students/absences", date ? { date } : undefined),
  });
}

export function useRuns() {
  return useQuery({ queryKey: ["runs"], queryFn: () => api.get("/api/runs") });
}

export function useActiveRuns() {
  // Today's (Africa/Nairobi) non-completed runs — the server owns the predicate.
  return useQuery({
    queryKey: ["runs", "active"],
    queryFn: () => api.get("/api/runs", { active: true }),
    refetchInterval: POLL_ADMIN,
  });
}

export function useIncidents() {
  return useQuery({
    queryKey: ["incidents"],
    queryFn: () => api.get("/api/incidents"),
    refetchInterval: POLL_ADMIN,
  });
}

export function useUnreadAlerts() {
  return useQuery({
    queryKey: ["unread-alerts"],
    queryFn: () => api.get("/api/incidents/unread-count"),
    refetchInterval: POLL_ADMIN,
  });
}

export function useTodayIncidentCount() {
  return useQuery({
    queryKey: ["incidents-today"],
    queryFn: () => api.get("/api/incidents/today-count"),
    refetchInterval: POLL_ADMIN,
  });
}

export function useDrivers() {
  return useQuery({ queryKey: ["accounts-drivers"], queryFn: () => api.get("/api/accounts/drivers") });
}

export function useParents() {
  return useQuery({ queryKey: ["accounts-parents"], queryFn: () => api.get("/api/accounts/parents") });
}

export function useParentStudents() {
  return useQuery({
    queryKey: ["parent-students"],
    queryFn: () => api.get("/api/accounts/parent-students"),
  });
}
