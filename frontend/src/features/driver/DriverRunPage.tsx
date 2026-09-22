import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Flag, MapPin, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { DriverLayout } from "@/features/driver/components/DriverLayout";
import { nudgeStore } from "@/features/driver/components/nudgeStore";
import { unlockAttentionAudio } from "@/features/driver/components/useAttentionCue";
import { useDriverContext } from "@/features/driver/driverHooks";
import { usePingStreamStatus } from "@/features/driver/useRunPings";
import { WAKE_LOCK_HINT, showWakeLockHint, useWakeLockStatus, wakeLock } from "@/features/driver/useWakeLock";
import { postDriverAction } from "@/lib/actionEnvelope";
import { fixCapture, queryPermissionState } from "@/lib/geo/fixCapture";

/** The Run page's line when pings are paused on `session-mismatch` (GPS
 * plan U14/R28): the run was started from another sign-in of this PIN. */
export const PING_PAUSED_HINT =
  "Live tracking is paused: this run was started from another sign-in. Your next tap here resumes it.";

/** The location explainer (GPS plan U6: R4; F1) — shown before the browser's
 * own prompt on a phone that has not answered it yet, and re-checked every
 * run. What is collected, when, who reads it, how long it is kept, whom to
 * ask. Exported so the copy is checkable. */
export const LOCATION_EXPLAINER = {
  title: "Share the bus's location during runs",
  lead: "Your browser will ask next. Whatever you choose, the run starts.",
  points: [
    {
      label: "What",
      text:
        "Your phone's position — where, how accurate, and when — taken at each tap: "
        + "Start Run, Arrive, Board, Drop-off, Absent, Off-route and End Run.",
    },
    {
      label: "When",
      text: "Only while a run is in progress. Nothing is asked for before Start Run or after End Run.",
    },
    {
      label: "Who sees it",
      text:
        "Your school's transport office and the provider, as the bus's position on their map and "
        + "in the run's report. Parents see where the bus is, never your phone's details.",
    },
    {
      label: "How long",
      text:
        "Positions stay with the run's record for the school's retention period — 90 days unless "
        + "the school sets otherwise — then they are deleted.",
    },
    { label: "Questions", text: "Ask your transport coordinator." },
  ],
  continueLabel: "Continue",
  cancelLabel: "Cancel",
} as const;

function morningFirst(routes: any[]) {
  return [...routes].sort(
    (a, b) => Number(b.type === "morning") - Number(a.type === "morning"),
  );
}

export function DriverRunPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data } = useDriverContext();
  // No auto-selection (R27): the Select opens on its "Choose route"
  // placeholder and Start Run stays disabled until the driver picks a route.
  const [routeId, setRouteId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [explainerOpen, setExplainerOpen] = useState(false);
  const wake = useWakeLockStatus();
  const pings = usePingStreamStatus();

  const activeRun = data?.active_run;
  const routes = data?.routes ?? [];
  const completedToday: string[] = data?.completed_route_ids_today ?? [];
  // Server-computed, and polled with the rest of the context, so a name leaves
  // the list as soon as that child is resolved on the board screen.
  const blocking: { id: string; name: string }[] = data?.blocking ?? [];

  const refresh = () => qc.invalidateQueries({ queryKey: ["driver-context"] });

  // The Start Run tap, or the explainer's Continue: the GPS watch starts here,
  // inside the gesture (GPS plan U6: R1, R4, R5), so a phone that has not
  // answered the browser's prompt yet sees it now — after the explainer, not
  // instead of it. The run starts whatever the answer is; the fix, or the
  // reason there is none, rides the request.
  const startRun = async () => {
    setExplainerOpen(false);
    setBusy(true);
    fixCapture.arm();
    try {
      await postDriverAction("/api/runs/driver/start", { route_id: routeId }, { runId: null });
      // The screen wake lock (GPS plan U14/R25): asked for here, in the
      // tap's own gesture chain once the run exists; the layout keeps it
      // for as long as the context reports the run.
      void wakeLock.request();
      await refresh();
    } catch (err) {
      // No run to watch for.
      fixCapture.disarm();
      // Surfaces the server's friendly 409s too ("already completed today",
      // "no students assigned yet") — R28/R28b.
      toast({ title: "Cannot start run", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    // Inside the tap, before any await: this is the one user gesture every
    // run is guaranteed to have, and the prompt tone (GPS plan U3) can only
    // play later if the audio element was activated by one.
    unlockAttentionAudio();
    // Re-checked every run: a phone that has already answered — either way —
    // goes straight on; one that has not is told what it is about to be asked.
    const state = await queryPermissionState();
    if (state === "prompt" || state === "unknown") {
      setExplainerOpen(true);
      return;
    }
    await startRun();
  };

  const arrive = async () => {
    if (!activeRun) return;
    setBusy(true);
    try {
      // `expected_stop_order` makes a duplicate Arrive a no-op on the server
      // (R33): this tap intends to reach the next stop, not "whatever is
      // next by the time the retry lands".
      const result = await postDriverAction(
        "/api/runs/driver/arrive",
        { run_id: activeRun.id, expected_stop_order: activeRun.stops_completed + 1 },
        { runId: activeRun.id },
      );
      // Prompts this Arrive raised go straight to the queue (GPS plan U3):
      // the card shows on this response, not one poll later; the poll then
      // owns them like any other pending prompt.
      nudgeStore.ingest(result?.prompts, "response");
      await refresh();
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const end = async () => {
    if (!activeRun) return;
    // The client-side warning list is gone (U13/R11): it read the raw status
    // column, only ran on afternoon runs, and warned before ending the run
    // anyway — a second, weaker copy of a rule that now actually refuses.
    //
    // So the attempt always reaches the server, even with names still listed.
    // The gate decides whether this run may close, and its refusal is written
    // for the driver and names the children; inventing a message here would
    // recreate exactly the duplicate that was just removed.
    if (!(await confirm({
      title: "End this run?",
      description:
        blocking.length > 0
          ? `${blocking.length} ${blocking.length === 1 ? "child is" : "children are"} still unaccounted for. Ending will be refused until each one is resolved.`
          : "This marks the run as completed.",
      confirmLabel: "End Run",
      cancelLabel: "Cancel",
    }))) return;
    setBusy(true);
    try {
      await postDriverAction("/api/runs/driver/end", { run_id: activeRun.id }, { runId: activeRun.id });
      await refresh();
      toast({ title: "Run completed" });
      navigate("/driver");
    } catch (err) {
      // The gate's refusal names the children; it is written for the driver, so
      // it surfaces verbatim rather than being reworded here.
      toast({ title: "Cannot end run", description: (err as Error).message, variant: "destructive" });
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const stops = (data?.run_stops ?? []).reduce((acc: any[], s: any) => {
    if (!acc.find((x) => x.stop_order === s.stop_order)) acc.push(s);
    return acc;
  }, []);

  return (
    <DriverLayout title="Active Run">
      {!activeRun ? (
        <Card>
          <CardHeader><CardTitle className="text-lg">Start a run</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {routes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No routes assigned to your bus.</p>
            ) : (
              <>
                <Select value={routeId} onValueChange={setRouteId}>
                  <SelectTrigger><SelectValue placeholder="Choose route" /></SelectTrigger>
                  <SelectContent>
                    {morningFirst(routes).map((r: any) => {
                      // A route already run today can't be restarted (R28);
                      // the admin recovery path is deleting the mistaken run.
                      const completed = completedToday.includes(r.id);
                      return (
                        <SelectItem key={r.id} value={r.id} disabled={completed}>
                          {r.name}
                          {completed && <span className="text-muted-foreground"> · Completed today</span>}
                        </SelectItem>
                      );
                    })}
                  </SelectContent>
                </Select>
                <Button className="w-full" onClick={start} disabled={busy || !routeId}>
                  <Play className="h-4 w-4" /> Start Run
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-2 p-5">
              <Badge variant="success">Run in progress</Badge>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary" style={{ width: `${activeRun.total_stops ? (activeRun.stops_completed / activeRun.total_stops) * 100 : 0}%` }} />
              </div>
              <p className="text-sm text-muted-foreground">{activeRun.stops_completed}/{activeRun.total_stops} stops completed</p>
              {/* Two one-line hints (GPS plan U14), never blocking: the
                  browser would not keep the screen on; the ping stream is
                  paused because another sign-in started this run. */}
              {showWakeLockHint(wake) && (
                <p
                  className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/30"
                  data-testid="wake-lock-hint"
                  data-reason={wake.reason ?? ""}
                >
                  {WAKE_LOCK_HINT}
                </p>
              )}
              {pings.suspended && (
                <p
                  className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/30"
                  data-testid="ping-hint"
                >
                  {PING_PAUSED_HINT}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Stops</CardTitle></CardHeader>
            <CardContent>
              <ol className="space-y-2">
                {stops.map((s: any) => {
                  const done = s.stop_order <= activeRun.stops_completed;
                  return (
                    <li key={s.stop_order} className="flex items-center gap-2 text-sm">
                      {done ? <CheckCircle2 className="h-4 w-4 text-success" /> : <MapPin className="h-4 w-4 text-muted-foreground" />}
                      <span className={done ? "text-muted-foreground line-through" : ""}>{s.stop_order}. {s.name}</span>
                      {s.is_school_gate && <Badge variant="outline" className="ml-auto">Gate</Badge>}
                    </li>
                  );
                })}
              </ol>
            </CardContent>
          </Card>

          {blocking.length > 0 && (
            <Card className="border-destructive/40 bg-destructive/5">
              <CardHeader className="pb-2">
                <CardTitle className="text-base">
                  Still to account for ({blocking.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-sm text-muted-foreground">
                  The run cannot end until each of these children has a recorded
                  outcome. Tap a name to go to them.
                </p>
                <ul className="space-y-1" data-testid="blocking-list">
                  {/* Tappable (U13/R11): with several blockers a driver would
                      otherwise make a manual multi-screen round trip per child,
                      on a phone, at the end of every route. */}
                  {blocking.map((b) => (
                    <li key={b.id}>
                      <button
                        type="button"
                        className="w-full rounded-md border border-destructive/30 bg-background px-3 py-2 text-left text-sm font-medium"
                        onClick={() => navigate(`/driver/boarding?student=${b.id}`)}
                      >
                        {b.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Button onClick={arrive} disabled={busy || activeRun.stops_completed >= activeRun.total_stops}>
              <MapPin className="h-4 w-4" /> Arrive Next Stop
            </Button>
            {/* Deliberately not disabled while blocked: the same control
                re-attempts closure, and the server's refusal is what tells the
                driver why. A dead button explains nothing. */}
            <Button variant="destructive" onClick={end} disabled={busy}>
              <Flag className="h-4 w-4" /> End Run
            </Button>
          </div>
        </div>
      )}

      {/* Closing it any other way than Continue is the driver cancelling their
          own Start Run tap: nothing is asked for and no run starts. Continue
          is a fresh gesture, so the browser's prompt lands inside it. */}
      <Dialog open={explainerOpen} onOpenChange={(next) => (next ? null : setExplainerOpen(false))}>
        <DialogContent className="max-w-md" data-testid="location-explainer">
          <DialogHeader>
            <DialogTitle>{LOCATION_EXPLAINER.title}</DialogTitle>
            <DialogDescription>{LOCATION_EXPLAINER.lead}</DialogDescription>
          </DialogHeader>
          <dl className="space-y-2 text-sm">
            {LOCATION_EXPLAINER.points.map((point) => (
              <div key={point.label}>
                <dt className="font-semibold">{point.label}</dt>
                <dd className="text-muted-foreground">{point.text}</dd>
              </div>
            ))}
          </dl>
          <DialogFooter>
            <Button variant="outline" onClick={() => setExplainerOpen(false)}>
              {LOCATION_EXPLAINER.cancelLabel}
            </Button>
            <Button onClick={startRun} data-testid="location-explainer-continue">
              {LOCATION_EXPLAINER.continueLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DriverLayout>
  );
}
