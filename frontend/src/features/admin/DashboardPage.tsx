import { format } from "date-fns";
import { Bus, Clock, TriangleAlert, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RunFlagBadges } from "@/features/admin/components/RunFlagBadges";
import { StatCard } from "@/features/admin/components/StatCard";
import {
  POLL_ADMIN,
  useActiveRuns,
  useBuses,
  useRuns,
  useStudents,
  useTodayIncidentCount,
} from "@/lib/queries";
import {
  BUS_STATUS_LABEL,
  BUS_STATUS_VARIANT,
  RUN_STATUS_LABEL,
  RUN_STATUS_VARIANT,
  labelFor,
  variantFor,
} from "@/lib/statusVocabulary";

// Status labels come from the shared vocabulary (U17) — no page defines its own.
export function DashboardPage() {
  // Every tile polls on the admin cadence, not only the Active Runs card. The
  // board is read beside a driver's phone and the tab stays visible, so nothing
  // else ever refetches: a bus kept reading "Active" after its run had ended
  // and the card beside it had already dropped the run.
  const { data: buses = [] } = useBuses({ poll: POLL_ADMIN });
  const { data: runs = [] } = useRuns({ poll: POLL_ADMIN });
  // Server-side predicate (non-completed, up to and including today Nairobi).
  const { data: liveRuns = [] } = useActiveRuns();
  const { data: students = [] } = useStudents({ poll: POLL_ADMIN });
  const { data: todayIncidents } = useTodayIncidentCount();

  const today = new Date().toISOString().split("T")[0];
  const todayRuns = runs.filter((r: any) => r.date === today);
  // Count the derived value (U9), not the stored column — nothing writes it any
  // more, so counting it would freeze these tiles at whatever the office last
  // typed.
  const activeBuses = buses.filter((b: any) => b.derived_status === "active").length;
  const delayed = buses.filter((b: any) => b.derived_status === "delayed").length;
  // Same rule for children (R26): the Students page beside this tile shows the
  // derived value, so counting the raw column made the two disagree for stale
  // and unassigned children.
  const studentsOnBus = students.filter((s: any) => s.display_status === "on-bus").length;
  const incidentsToday = todayIncidents?.count ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">{format(new Date(), "EEEE, MMMM d, yyyy")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Active Buses" value={activeBuses} subtitle={`of ${buses.length} total`} icon={Bus} variant="success" />
        <StatCard label="Delayed" value={delayed} subtitle="buses behind schedule" icon={TriangleAlert} variant="warning" />
        <StatCard label="Students on Bus" value={studentsOnBus} subtitle={`of ${students.length} enrolled`} icon={Users} />
        <StatCard label="Incidents Today" value={incidentsToday} subtitle={`across ${todayRuns.length} runs`} icon={TriangleAlert} variant={incidentsToday > 0 ? "destructive" : "default"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Clock className="h-5 w-5 text-primary" /> Active Runs
            </CardTitle>
          </CardHeader>
          <CardContent>
            {liveRuns.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-1 py-16 text-center">
                <Clock className="h-8 w-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">No active runs right now</p>
                <p className="text-xs text-muted-foreground">Runs will appear here once started</p>
              </div>
            ) : (
              <div className="space-y-3">
                {liveRuns.map((run: any) => (
                  <div key={run.id} className="flex items-center justify-between gap-3 rounded-lg border p-3">
                    <div>
                      <p className="font-medium">{run.bus_name ?? "Bus"} · {run.route_name ?? run.type}</p>
                      <p className="text-xs text-muted-foreground">
                        {run.stops_completed}/{run.total_stops} stops · {run.students_boarded}/{run.total_students} boarded
                      </p>
                      {/* Listed on purpose (R15): a run open past its service
                          day is invisible to every driver path, and the office
                          is who closes it. Without its date it read as the bus
                          being out right now. */}
                      {run.stale && (
                        <p className="text-xs text-muted-foreground">
                          Left open since {run.date} ·{" "}
                          <Link to="/runs" className="underline">close it in Run History</Link>
                        </p>
                      )}
                      {/* The badge beside the status carries the count (GPS
                          plan U4); the panel that explains each exception is
                          the run's report, which only Run History opens. */}
                      {Number(run.exception_count ?? 0) > 0 && (
                        <p className="text-xs text-muted-foreground">
                          Stop exceptions waiting for the office ·{" "}
                          <Link to="/runs" className="underline">open the run in Run History</Link>
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-1.5">
                      <Badge variant={variantFor(RUN_STATUS_VARIANT, run.status)}>{labelFor(RUN_STATUS_LABEL, run.status)}</Badge>
                      <RunFlagBadges run={run} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Bus className="h-5 w-5 text-primary" /> Fleet Status
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {buses.map((bus: any) => (
              <div key={bus.id} className="flex items-center justify-between gap-2">
                <div className="leading-tight">
                  <p className="font-medium">{bus.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {bus.plate_number ?? "—"}{bus.driver_name ? ` · ${bus.driver_name}` : ""}
                  </p>
                </div>
                <Badge variant={variantFor(BUS_STATUS_VARIANT, bus.derived_status)}>
                  {labelFor(BUS_STATUS_LABEL, bus.derived_status)}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
