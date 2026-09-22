import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/apiClient";
import { actionEnvelopes } from "@/lib/actionEnvelope";
import { fixCapture } from "@/lib/geo/fixCapture";
import { POLL_LIVE } from "@/lib/queries";
import type { ArrivalOffer, NudgePrompt } from "@/features/driver/components/nudgeStore";

export interface DriverContext {
  bus: any | null;
  routes: any[];
  active_run: any | null;
  run_stops: any[];
  students: any[];
  /** The closure gate's blocking set, server-computed (U13/R11). */
  blocking: { id: string; name: string }[];
  /** Route ids of this bus already run to completion today (R28). */
  completed_route_ids_today: string[];
  /** The open run's pending prompts, safety kinds first (GPS plan U3/R34).
   * Present and empty with no bus or no run. */
  pending_prompts: NudgePrompt[];
  /** The not-yet-arrived stop the trail puts the bus at (GPS plan U15/R29),
   * derived on every poll; null when there is none. Optional only for an
   * older server. */
  arrival_offer?: ArrivalOffer | null;
  /** The school's resolved tracking config (GPS plan U11): the fix-wait
   * budget, the accuracy cap and the ping interval, resolved per school on
   * every poll — a School Settings change reaches the phone on the next one.
   * Optional only for an older server; the client then keeps its defaults. */
  config?: {
    fix_wait_budget_s?: number;
    fix_accuracy_cap_m?: number;
    ping_interval_s?: number;
    [key: string]: unknown;
  } | null;
}

export function useDriverContext() {
  return useQuery<DriverContext>({
    queryKey: ["driver-context"],
    queryFn: () => api.get("/api/runs/driver/context"),
    refetchInterval: POLL_LIVE,
  });
}

/**
 * Keep the fix watch and the envelope store in step with the run (GPS plan
 * U6: R5). Mounted by the driver layout, so every driver tab runs it: while
 * the context reports a run in progress the watch is up (a reload mid-run
 * resumes it from the first poll, with no new permission prompt on an
 * already-granted origin); the moment it reports none — End Run, an office
 * close — the watch stops and the cache empties. Envelopes of any other run
 * are dropped at the same time. Nothing happens until the context has
 * answered once: an unloaded context is not "no run".
 */
export function useRunFixWatch(data: DriverContext | undefined): void {
  const runId: string | null = data?.active_run?.id ?? null;
  const loaded = data != null;
  // The two values, not the object: every poll builds a new one.
  const budget = data?.config?.fix_wait_budget_s;
  const cap = data?.config?.fix_accuracy_cap_m;
  useEffect(() => {
    if (!loaded) return;
    fixCapture.setConfig({ fix_wait_budget_s: budget, fix_accuracy_cap_m: cap });
    fixCapture.syncRun(runId);
    actionEnvelopes.prune(runId);
  }, [loaded, runId, budget, cap]);
}
