import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/apiClient";

// Shared admin/data hooks. Polling cadences mirror the live app's realtime
// channels (15s for admin lists/badge/alerts; 5s for live driver/parent views).
// Fleet map intentionally has NO refetchInterval (live has no channel there).

export const POLL_ADMIN = 15_000;
export const POLL_LIVE = 5_000;

// Live surfaces pass the cadence they need (`poll`, in ms); list/edit pages
// keep the one-shot fetch. A visible tab never refetches on its own — React
// Query's focus refetch fires on visibilitychange only — so a status board left
// open beside the driver's phone holds its first answer until it polls.
export function useBuses(opts?: { poll?: number }) {
  return useQuery({
    queryKey: ["buses"],
    queryFn: () => api.get("/api/fleet/buses"),
    refetchInterval: opts?.poll,
  });
}

export function useRoutes() {
  return useQuery({ queryKey: ["routes"], queryFn: () => api.get("/api/fleet/routes") });
}

export function useSchools() {
  return useQuery({ queryKey: ["schools"], queryFn: () => api.get("/api/fleet/schools") });
}

export function useStudents(opts?: { poll?: number }) {
  return useQuery({
    queryKey: ["students"],
    queryFn: () => api.get("/api/students"),
    refetchInterval: opts?.poll,
  });
}

export function useAbsences(date?: string) {
  return useQuery({
    queryKey: ["absences", date ?? "today"],
    queryFn: () => api.get("/api/students/absences", date ? { date } : undefined),
  });
}

export function useRuns(opts?: { poll?: number }) {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get("/api/runs"),
    refetchInterval: opts?.poll,
  });
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

export function useFleetPlans(schoolId: string | null | undefined) {
  // The school's open draft (full row: document + basis) plus applied/previous
  // metadata (U9). No polling — plan state changes only through this surface's
  // own mutations, which invalidate explicitly.
  return useQuery({
    queryKey: ["fleet-plans", schoolId],
    queryFn: () => api.get("/api/fleet-plans/current", { school_id: schoolId }),
    enabled: Boolean(schoolId),
  });
}

export function usePlanReview(schoolId: string | null | undefined, hasDraft: boolean) {
  // The computed review surface (U5): ride/stop times, capacity use,
  // unplaceable lists, diff vs live. 404s without an open draft, so the hook
  // is gated on one existing rather than retried into an error state.
  return useQuery({
    queryKey: ["fleet-plan-review", schoolId],
    queryFn: () => api.get("/api/fleet-plans/review", { school_id: schoolId }),
    enabled: Boolean(schoolId) && hasDraft,
  });
}

export function useDrivers() {
  return useQuery({ queryKey: ["accounts-drivers"], queryFn: () => api.get("/api/accounts/drivers") });
}

export function useParents() {
  return useQuery({ queryKey: ["accounts-parents"], queryFn: () => api.get("/api/accounts/parents") });
}
