import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "@/lib/apiClient";
import { getActiveSchoolId, setActiveSchoolId, useActiveSchoolId } from "@/lib/school";

// Shared admin/data hooks. Polling cadences mirror the live app's realtime
// channels (15s for admin lists/badge/alerts; 5s for live driver/parent views).
// Fleet map intentionally has NO refetchInterval (live has no channel there).

export const POLL_ADMIN = 15_000;
export const POLL_LIVE = 5_000;

// ---------------------------------------------------------------------------
// School-scoped cache keys (U12). Every admin query key is prefixed
// ["school", schoolId, ...] so one school's cache can be cancelled and
// removed wholesale on switch, and nothing from school A can ever satisfy a
// school B render. keepPreviousData is never used across schools.

export function schoolKeyFor(schoolId: string | null, ...parts: unknown[]): QueryKey {
  return ["school", schoolId, ...parts];
}

/** Key builder bound to the tab's active school: `key("students")` →
 * `["school", <id>, "students"]`. Pages use it for queryKey AND for every
 * invalidateQueries call so invalidation stays inside the active school. */
export function useSchoolKey(): (...parts: unknown[]) => QueryKey {
  const schoolId = useActiveSchoolId();
  return useCallback(
    (...parts: unknown[]) => schoolKeyFor(schoolId, ...parts),
    [schoolId],
  );
}

/**
 * Switch the tab to another school (U12): cancel the old school's in-flight
 * queries, drop its cache entirely (a delayed response must never repopulate
 * the new school's screen), set the store, land on the dashboard.
 */
export function useSwitchSchool(): (schoolId: string) => Promise<void> {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useCallback(
    async (nextSchoolId: string) => {
      const previous = getActiveSchoolId();
      if (previous === nextSchoolId) return;
      if (previous) {
        await qc.cancelQueries({ queryKey: ["school", previous] });
        qc.removeQueries({ queryKey: ["school", previous] });
      }
      setActiveSchoolId(nextSchoolId);
      navigate("/");
    },
    [qc, navigate],
  );
}

// ---------------------------------------------------------------------------
// Admin queries. All school-scoped: keys carry the school prefix, the school
// header rides every request (apiClient), fetches only run once a school is
// active, and the query signal is threaded so a switch aborts stale loads.

// Live surfaces pass the cadence they need (`poll`, in ms); list/edit pages
// keep the one-shot fetch. A visible tab never refetches on its own — React
// Query's focus refetch fires on visibilitychange only — so a status board left
// open beside the driver's phone holds its first answer until it polls.
export function useBuses(opts?: { poll?: number }) {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "buses"),
    queryFn: ({ signal }) => api.get("/api/fleet/buses", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: opts?.poll,
  });
}

export function useRoutes() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "routes"),
    queryFn: ({ signal }) => api.get("/api/fleet/routes", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

/** The active school's settings row (name, code, address, bells, location). */
export function useSchoolSettings() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "school-settings"),
    queryFn: ({ signal }) => api.get("/api/fleet/school", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

export function useStudents(opts?: { poll?: number }) {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "students"),
    queryFn: ({ signal }) => api.get("/api/students", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: opts?.poll,
  });
}

export function useAbsences(date?: string) {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "absences", date ?? "today"),
    queryFn: ({ signal }) =>
      api.get("/api/students/absences", date ? { date } : undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

export function useRuns(opts?: { poll?: number }) {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "runs"),
    queryFn: ({ signal }) => api.get("/api/runs", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: opts?.poll,
  });
}

export function useActiveRuns() {
  const schoolId = useActiveSchoolId();
  // Today's (Africa/Nairobi) non-completed runs — the server owns the predicate.
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "runs", "active"),
    queryFn: ({ signal }) => api.get("/api/runs", { active: true }, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: POLL_ADMIN,
  });
}

export function useIncidents() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "incidents"),
    queryFn: ({ signal }) => api.get("/api/incidents", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: POLL_ADMIN,
  });
}

export function useUnreadAlerts() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "unread-alerts"),
    queryFn: ({ signal }) => api.get("/api/incidents/unread-count", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: POLL_ADMIN,
  });
}

export function useTodayIncidentCount() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "incidents-today"),
    queryFn: ({ signal }) => api.get("/api/incidents/today-count", undefined, { signal }),
    enabled: Boolean(schoolId),
    refetchInterval: POLL_ADMIN,
  });
}

export function useFleetPlans() {
  const schoolId = useActiveSchoolId();
  // The school's open draft (full row: document + basis) plus applied/previous
  // metadata (U9). No polling — plan state changes only through this surface's
  // own mutations, which invalidate explicitly. The school comes from the
  // request scope (header); the legacy school_id param is gone.
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "fleet-plans"),
    queryFn: ({ signal }) => api.get("/api/fleet-plans/current", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

export function usePlanReview(hasDraft: boolean) {
  const schoolId = useActiveSchoolId();
  // The computed review surface (U5): ride/stop times, capacity use,
  // unplaceable lists, diff vs live. 404s without an open draft, so the hook
  // is gated on one existing rather than retried into an error state.
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "fleet-plan-review"),
    queryFn: ({ signal }) => api.get("/api/fleet-plans/review", undefined, { signal }),
    enabled: Boolean(schoolId) && hasDraft,
  });
}

export function useDrivers() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "accounts-drivers"),
    queryFn: ({ signal }) => api.get("/api/accounts/drivers", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

export function useParents() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "accounts-parents"),
    queryFn: ({ signal }) => api.get("/api/accounts/parents", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}

/** The staff list for the active school (members + open offers), U12. */
export function useStaff() {
  const schoolId = useActiveSchoolId();
  return useQuery({
    queryKey: schoolKeyFor(schoolId, "staff"),
    queryFn: ({ signal }) => api.get("/api/staff", undefined, { signal }),
    enabled: Boolean(schoolId),
  });
}
