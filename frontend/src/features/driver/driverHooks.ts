import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/apiClient";
import { POLL_LIVE } from "@/lib/queries";

export interface DriverContext {
  bus: any | null;
  routes: any[];
  active_run: any | null;
  run_stops: any[];
  students: any[];
  /** Route ids of this bus already run to completion today (R28). */
  completed_route_ids_today: string[];
}

export function useDriverContext() {
  return useQuery<DriverContext>({
    queryKey: ["driver-context"],
    queryFn: () => api.get("/api/runs/driver/context"),
    refetchInterval: POLL_LIVE,
  });
}
